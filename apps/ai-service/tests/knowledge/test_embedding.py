import asyncio
import json
import math
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.knowledge.embedding.base import (
    EmbeddingCapabilityError,
    EmbeddingConfigurationError,
    EmbeddingProvider,
    EmbeddingProviderBadResponseError,
    EmbeddingProviderTimeoutError,
    EmbeddingProviderUnavailableError,
    EmbeddingValidationError,
    normalize_embeddings,
)
from app.knowledge.embedding.deterministic import DeterministicEmbeddingProvider
from app.knowledge.embedding.fastembed_provider import (
    FASTEMBED_MODEL_NAME,
    FastEmbedEmbeddingProvider,
)
from app.knowledge.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.knowledge.embedding.registry import build_embedding_provider

DIMENSIONS = 512
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def unit_vector(position: int) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[position] = 3.0
    return vector


class FakeFastEmbedModel:
    def __init__(self, transform: Callable[[str], Sequence[float]] | None = None) -> None:
        self.transform = transform or (lambda text: unit_vector(len(text) % DIMENSIONS))
        self.calls: list[list[str]] = []

    def embed(self, documents: Iterable[str]) -> Iterable[Sequence[float]]:
        texts = list(documents)
        self.calls.append(texts)
        return [self.transform(text) for text in texts]


class WrongCountFastEmbedModel(FakeFastEmbedModel):
    def embed(self, documents: Iterable[str]) -> Iterable[Sequence[float]]:
        return [unit_vector(0)]


def fake_fastembed(model: FakeFastEmbedModel | None = None) -> FastEmbedEmbeddingProvider:
    fake_model = model or FakeFastEmbedModel()
    return FastEmbedEmbeddingProvider(
        cache_path=Path("synthetic-cache"),
        model_factory=lambda **_: fake_model,
    )


def fake_openai() -> OpenAICompatibleEmbeddingProvider:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": unit_vector(index)}
                    for index, _ in enumerate(body["input"])
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="synthetic-key",
        model="synthetic-model",
        timeout_seconds=2,
        client=client,
    )


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda: DeterministicEmbeddingProvider(DIMENSIONS),
        fake_fastembed,
        fake_openai,
    ],
)
@pytest.mark.asyncio
async def test_provider_returns_one_normalized_512_vector_per_text(
    provider_factory: Callable[[], EmbeddingProvider],
) -> None:
    provider = provider_factory()
    try:
        vectors = await provider.embed(["返工规则", "紧急工单"])
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    assert [len(vector) for vector in vectors] == [DIMENSIONS, DIMENSIONS]
    assert all(
        math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-12)
        for vector in vectors
    )


@pytest.mark.asyncio
async def test_deterministic_provider_is_stable_across_processes_and_input_order() -> None:
    provider = DeterministicEmbeddingProvider(DIMENSIONS)
    first = await provider.embed(["返工规则", "紧急工单"])
    reordered = await provider.embed(["紧急工单", "返工规则"])
    script = (
        "import asyncio, json; "
        "from app.knowledge.embedding.deterministic import DeterministicEmbeddingProvider; "
        "print(json.dumps(asyncio.run(DeterministicEmbeddingProvider(512).embed(['返工规则']))))"
    )
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "random"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert first == [reordered[1], reordered[0]]
    assert first[0] == json.loads(result.stdout)[0]
    assert first[0] != first[1]
    assert provider.loaded is True
    assert provider.dimensions == DIMENSIONS


@pytest.mark.asyncio
async def test_deterministic_provider_supports_empty_batch_and_rejects_other_dimensions() -> None:
    assert await DeterministicEmbeddingProvider(DIMENSIONS).embed([]) == []
    with pytest.raises(EmbeddingConfigurationError) as error:
        DeterministicEmbeddingProvider(384)
    assert error.value.code == "EMBEDDING_CONFIGURATION_INVALID"


@pytest.mark.parametrize(
    ("vectors", "count"),
    [
        ([], 1),
        ([unit_vector(0), unit_vector(1)], 1),
        ([[1.0] * 511], 1),
        ([[0.0] * DIMENSIONS], 1),
        ([[float("nan")] + [1.0] * 511], 1),
        ([[float("inf")] + [1.0] * 511], 1),
        ([[True] + [1.0] * 511], 1),
    ],
)
def test_central_validation_rejects_malformed_output_before_use(
    vectors: Sequence[Sequence[object]], count: int
) -> None:
    with pytest.raises(EmbeddingValidationError) as error:
        normalize_embeddings(vectors, expected_count=count, dimensions=DIMENSIONS)
    assert error.value.code == "EMBEDDING_VECTOR_INVALID"
    assert str(error.value) == "Embedding provider returned invalid vectors"


def test_central_validation_preserves_order_and_normalizes() -> None:
    vectors = normalize_embeddings(
        [[3.0] + [0.0] * 511, [0.0, 4.0] + [0.0] * 510],
        expected_count=2,
        dimensions=DIMENSIONS,
    )
    assert vectors[0][0] == 1.0
    assert vectors[1][1] == 1.0


@pytest.mark.asyncio
async def test_fastembed_uses_exact_model_cache_and_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls: list[dict[str, object]] = []
    thread_calls: list[str] = []
    model = FakeFastEmbedModel()

    def factory(**kwargs: object) -> FakeFastEmbedModel:
        factory_calls.append(kwargs)
        return model

    async def recording_to_thread(function: Callable[..., Any], *args: object) -> Any:
        thread_calls.append(function.__name__)
        return function(*args)

    monkeypatch.setattr(
        "app.knowledge.embedding.fastembed_provider.asyncio.to_thread", recording_to_thread
    )
    provider = FastEmbedEmbeddingProvider(
        cache_path=Path("C:/synthetic/cache"), model_factory=factory
    )

    assert provider.loaded is False
    vectors = await provider.embed(["甲", "乙"])

    assert factory_calls == [
        {"model_name": FASTEMBED_MODEL_NAME, "cache_dir": str(Path("C:/synthetic/cache"))}
    ]
    assert FASTEMBED_MODEL_NAME == "BAAI/bge-small-zh-v1.5"
    assert model.calls == [["embedding dimension probe"], ["甲", "乙"]]
    assert thread_calls == ["_construct_and_probe", "_infer"]
    assert provider.loaded is True
    assert vectors[0][1] == 1.0
    assert vectors[1][1] == 1.0


@pytest.mark.asyncio
async def test_fastembed_load_is_concurrency_safe() -> None:
    construction_count = 0
    model = FakeFastEmbedModel()

    def factory(**_: object) -> FakeFastEmbedModel:
        nonlocal construction_count
        construction_count += 1
        return model

    provider = FastEmbedEmbeddingProvider(cache_path=Path("synthetic-cache"), model_factory=factory)

    results = await asyncio.gather(
        provider.embed(["甲"]),
        provider.embed(["乙"]),
        provider.embed(["丙"]),
    )

    assert construction_count == 1
    assert provider.loaded is True
    assert len(results) == 3


@pytest.mark.asyncio
async def test_fastembed_waiter_cancellation_does_not_duplicate_inflight_load() -> None:
    entered = threading.Event()
    release = threading.Event()
    construction_count = 0

    def blocking_factory(**_: object) -> FakeFastEmbedModel:
        nonlocal construction_count
        construction_count += 1
        entered.set()
        assert release.wait(timeout=2)
        return FakeFastEmbedModel()

    provider = FastEmbedEmbeddingProvider(
        cache_path=Path("synthetic-cache"), model_factory=blocking_factory
    )
    cancelled_waiter = asyncio.create_task(provider.embed(["cancelled"]), name="cancelled")
    assert await asyncio.to_thread(entered.wait, 2)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    surviving_waiter = asyncio.create_task(provider.embed(["surviving"]), name="surviving")
    await asyncio.sleep(0.05)
    release.set()
    vectors = await surviving_waiter

    assert construction_count == 1
    assert provider.loaded is True
    assert len(vectors) == 1


@pytest.mark.asyncio
async def test_fastembed_coalesces_one_failing_concurrent_load_wave_then_allows_retry() -> None:
    entered = threading.Event()
    release = threading.Event()
    construction_count = 0
    sentinel = "factory-secret-must-not-leak"

    def failing_factory(**_: object) -> FakeFastEmbedModel:
        nonlocal construction_count
        construction_count += 1
        entered.set()
        assert release.wait(timeout=2)
        raise RuntimeError(sentinel)

    provider = FastEmbedEmbeddingProvider(
        cache_path=Path("synthetic-cache"), model_factory=failing_factory
    )
    tasks = [asyncio.create_task(provider.embed([text])) for text in ["甲", "乙", "丙"]]
    assert await asyncio.to_thread(entered.wait, 2)
    await asyncio.sleep(0)
    release.set()

    failures = await asyncio.gather(*tasks, return_exceptions=True)

    assert construction_count == 1
    assert provider.loaded is False
    assert all(isinstance(error, EmbeddingProviderUnavailableError) for error in failures)
    assert {str(error) for error in failures} == {"Embedding provider is unavailable"}
    assert all(sentinel not in str(error) for error in failures)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(["later retry"])
    assert construction_count == 2
    assert provider.loaded is False


@pytest.mark.asyncio
async def test_fastembed_probe_rejects_wrong_dimension_and_does_not_mark_loaded() -> None:
    provider = fake_fastembed(FakeFastEmbedModel(lambda _: [1.0] * 384))

    with pytest.raises(EmbeddingValidationError):
        await provider.embed(["never persisted"])

    assert provider.loaded is False


@pytest.mark.asyncio
async def test_fastembed_rejects_wrong_output_count_and_preserves_order() -> None:
    missing = FastEmbedEmbeddingProvider(
        cache_path=Path("synthetic-cache"), model_factory=lambda **_: WrongCountFastEmbedModel()
    )
    with pytest.raises(EmbeddingValidationError):
        await missing.embed(["first", "second"])

    positions = {"embedding dimension probe": 3, "first": 1, "second": 2}
    ordered = fake_fastembed(FakeFastEmbedModel(lambda text: unit_vector(positions[text])))
    vectors = await ordered.embed(["second", "first"])
    assert vectors[0][2] == 1.0
    assert vectors[1][1] == 1.0


@pytest.mark.asyncio
async def test_fastembed_empty_batch_is_deliberate_and_does_not_load() -> None:
    provider = fake_fastembed()
    assert await provider.embed([]) == []
    assert provider.loaded is False


@pytest.mark.asyncio
async def test_fastembed_close_releases_model_and_rejects_new_inference() -> None:
    provider = fake_fastembed()
    await provider.embed(["加载"])
    assert provider.loaded is True

    await provider.close()
    await provider.close()

    assert provider.loaded is False
    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(["关闭后拒绝"])


@pytest.mark.asyncio
async def test_openai_provider_sends_standard_request_and_restores_index_order() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://embedding.example/api/v1/embeddings"
        assert request.headers.get_list("authorization") == ["Bearer synthetic-key"]
        assert json.loads(request.content) == {
            "model": "synthetic-model",
            "input": ["甲", "乙"],
            "dimensions": DIMENSIONS,
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": unit_vector(1)},
                    {"index": 0, "embedding": unit_vector(0)},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/api/v1/",
            api_key="synthetic-key",
            model="synthetic-model",
            timeout_seconds=1.5,
            client=client,
        )
        vectors = await provider.embed(["甲", "乙"])

    assert vectors[0][0] == 1.0
    assert vectors[1][1] == 1.0
    assert provider.loaded is True


@pytest.mark.asyncio
async def test_openai_provider_preserves_percent_encoded_base_path() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/api%2Ftenant/embeddings"
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": unit_vector(0)}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/api%2Ftenant",
            api_key="synthetic-key",
            model="synthetic-model",
            timeout_seconds=1,
            client=client,
        )
        vectors = await provider.embed(["甲"])

    assert vectors[0][0] == 1.0


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://embedding.example/v1",
        "https:///v1",
        "https://sentinel-secret@embedding.example/v1",
        "https://embedding.example/v1?token=sentinel-secret",
        "https://embedding.example/v1#sentinel-secret",
        "http://host:sentinel-secret",
    ],
)
def test_openai_rejects_unsafe_or_invalid_base_urls_without_leaking_them(
    base_url: str,
) -> None:
    with pytest.raises(EmbeddingConfigurationError) as captured:
        OpenAICompatibleEmbeddingProvider(
            base_url=base_url,
            api_key="sentinel-api-key",
            model="sentinel-model",
            timeout_seconds=1,
        )

    assert captured.value.code == "EMBEDDING_CONFIGURATION_INVALID"
    assert str(captured.value) == "Embedding provider configuration is invalid"
    assert "sentinel" not in str(captured.value)


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), 0.0, -1.0, 121.0])
def test_openai_rejects_nonfinite_nonpositive_or_excessive_timeout(
    timeout_seconds: float,
) -> None:
    with pytest.raises(EmbeddingConfigurationError):
        OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="synthetic-key",
            model="synthetic-model",
            timeout_seconds=timeout_seconds,
        )


@pytest.mark.asyncio
async def test_openai_empty_batch_does_not_make_request() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("empty embedding batch must not make a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="synthetic-key",
            model="synthetic-model",
            timeout_seconds=1,
            client=client,
        )
        assert await provider.embed([]) == []
        assert provider.loaded is False


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        json.dumps({"data": "wrong"}).encode(),
        json.dumps({"data": []}).encode(),
        json.dumps({"data": [{"index": 1, "embedding": unit_vector(0)}]}).encode(),
        json.dumps(
            {
                "data": [
                    {"index": 0, "embedding": unit_vector(0)},
                    {"index": 0, "embedding": unit_vector(1)},
                ]
            }
        ).encode(),
        json.dumps({"data": [{"index": 0, "embedding": [1.0] * 384}]}).encode(),
    ],
)
@pytest.mark.asyncio
async def test_openai_rejects_invalid_json_count_index_and_dimension(body: bytes) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="synthetic-key",
            model="synthetic-model",
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises((EmbeddingProviderBadResponseError, EmbeddingValidationError)):
            await provider.embed(["sentinel input"])
        assert provider.loaded is False


@pytest.mark.asyncio
async def test_openai_maps_timeout_network_and_status_to_stable_redacted_errors() -> None:
    secret = "sentinel-secret"
    text = "sentinel-input-text"
    response_body = "sentinel-response-body"

    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"{secret} {text} {request.url}", request=request)

    async def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"{secret} {text} {request.url}", request=request)

    async def status_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=response_body)

    cases = [
        (timeout_handler, EmbeddingProviderTimeoutError, "EMBEDDING_PROVIDER_TIMEOUT"),
        (network_handler, EmbeddingProviderUnavailableError, "EMBEDDING_PROVIDER_UNAVAILABLE"),
        (status_handler, EmbeddingProviderUnavailableError, "EMBEDDING_PROVIDER_UNAVAILABLE"),
    ]
    for handler, expected_type, expected_code in cases:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleEmbeddingProvider(
                base_url="https://embedding.example/v1",
                api_key=secret,
                model="synthetic-model",
                timeout_seconds=0.1,
                client=client,
            )
            with pytest.raises(expected_type) as captured:
                await provider.embed([text])
        rendered = str(captured.value)
        assert captured.value.code == expected_code
        assert secret not in rendered
        assert text not in rendered
        assert response_body not in rendered


@pytest.mark.asyncio
async def test_owned_openai_client_can_be_closed_without_closing_injected_client() -> None:
    injected = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    injected_provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="synthetic-key",
        model="synthetic-model",
        timeout_seconds=1,
        client=injected,
    )
    await injected_provider.close()
    assert injected.is_closed is False
    await injected.aclose()

    owned_provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="synthetic-key",
        model="synthetic-model",
        timeout_seconds=1,
    )
    await owned_provider.close()
    assert owned_provider.closed is True


@pytest.mark.asyncio
async def test_disabled_provider_is_explicit_and_never_returns_zero_vectors() -> None:
    provider = await build_embedding_provider(
        Settings(embedding_provider="disabled", _env_file=None)
    )
    assert provider.loaded is False
    with pytest.raises(EmbeddingCapabilityError) as error:
        await provider.embed(["must not become a zero vector"])
    assert error.value.code == "EMBEDDING_CAPABILITY_DISABLED"


@pytest.mark.asyncio
async def test_registry_selects_exact_local_provider_and_probes_with_settings_cache() -> None:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeFastEmbedModel:
        calls.append(kwargs)
        return FakeFastEmbedModel()

    provider = await build_embedding_provider(
        Settings(
            embedding_provider="local",
            embedding_model=FASTEMBED_MODEL_NAME,
            fastembed_cache_path=Path("C:/configured/cache"),
            _env_file=None,
        ),
        fastembed_factory=factory,
    )

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.loaded is True
    assert provider.model_key == FASTEMBED_MODEL_NAME
    assert calls == [
        {"model_name": FASTEMBED_MODEL_NAME, "cache_dir": str(Path("C:/configured/cache"))}
    ]


@pytest.mark.asyncio
async def test_registry_can_construct_local_provider_without_startup_download() -> None:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeFastEmbedModel:
        calls.append(kwargs)
        return FakeFastEmbedModel()

    provider = await build_embedding_provider(
        Settings(
            embedding_provider="local",
            embedding_model=FASTEMBED_MODEL_NAME,
            fastembed_cache_path=Path("C:/configured/cache"),
            _env_file=None,
        ),
        fastembed_factory=factory,
        probe=False,
    )

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.loaded is False
    assert calls == []


@pytest.mark.asyncio
async def test_registry_selects_and_probes_openai_compatible_provider() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": unit_vector(4)}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = await build_embedding_provider(
            Settings(
                embedding_provider="openai_compatible",
                embedding_model="synthetic-model",
                embedding_base_url="https://embedding.example/v1",
                embedding_api_key="synthetic-key",
                embedding_timeout_seconds=4,
                _env_file=None,
            ),
            client=client,
        )

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.loaded is True
    assert provider.model_key == "synthetic-model"


@pytest.mark.asyncio
async def test_registry_rejects_a_wrong_dimension_startup_probe() -> None:
    provider_settings = Settings(
        embedding_provider="local",
        embedding_model=FASTEMBED_MODEL_NAME,
        fastembed_cache_path=Path("synthetic-cache"),
        _env_file=None,
    )
    with pytest.raises(EmbeddingValidationError):
        await build_embedding_provider(
            provider_settings,
            fastembed_factory=lambda **_: FakeFastEmbedModel(lambda _: [1.0] * 384),
        )


@pytest.mark.parametrize("provider_name", ["LOCAL", " local", "local ", "disabled "])
@pytest.mark.asyncio
async def test_registry_rejects_provider_name_case_and_whitespace_aliases(
    provider_name: str,
) -> None:
    with pytest.raises(EmbeddingConfigurationError):
        await build_embedding_provider(
            Settings(embedding_provider=provider_name, _env_file=None),
            fastembed_factory=lambda **_: FakeFastEmbedModel(),
        )


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), 0.0, -1.0, 121.0])
@pytest.mark.asyncio
async def test_registry_rejects_invalid_openai_timeout_before_request(
    timeout_seconds: float,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid timeout must fail before any request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingConfigurationError):
            await build_embedding_provider(
                Settings(
                    embedding_provider="openai_compatible",
                    embedding_model="synthetic-model",
                    embedding_base_url="https://embedding.example/v1",
                    embedding_api_key="synthetic-key",
                    embedding_timeout_seconds=timeout_seconds,
                    _env_file=None,
                ),
                client=client,
            )


@pytest.mark.asyncio
async def test_registry_closes_owned_openai_client_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[Any] = []

    class FakeOwnedClient:
        def __init__(self, **_: object) -> None:
            self.is_closed = False
            clients.append(self)

        async def post(self, *_: object, **__: object) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 384}]})

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(
        "app.knowledge.embedding.openai_compatible.httpx.AsyncClient", FakeOwnedClient
    )
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_model="synthetic-model",
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key="synthetic-key",
        _env_file=None,
    )

    with pytest.raises(EmbeddingValidationError):
        await build_embedding_provider(settings)

    assert len(clients) == 1
    assert clients[0].is_closed is True


@pytest.mark.parametrize(
    "settings",
    [
        Settings(embedding_provider="unknown", _env_file=None),
        Settings(embedding_provider="local", embedding_model="wrong-model", _env_file=None),
        Settings(
            embedding_provider="openai_compatible",
            embedding_model="synthetic-model",
            embedding_base_url="",
            embedding_api_key="synthetic-key",
            _env_file=None,
        ),
        Settings(
            embedding_provider="openai_compatible",
            embedding_model="synthetic-model",
            embedding_base_url="https://embedding.example/v1",
            embedding_api_key="",
            _env_file=None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_registry_rejects_unknown_or_incomplete_provider_configuration(
    settings: Settings,
) -> None:
    with pytest.raises(EmbeddingConfigurationError) as error:
        await build_embedding_provider(settings)
    assert error.value.code == "EMBEDDING_CONFIGURATION_INVALID"


def test_embedding_settings_have_safe_public_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_base_url == ""
    assert settings.embedding_api_key_value() == ""
    assert settings.embedding_timeout_seconds == 30.0


def test_compose_passes_public_openai_embedding_configuration() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "EMBEDDING_BASE_URL": "https://embedding.example/api/v1",
            "EMBEDDING_API_KEY": "synthetic-compose-key",
            "EMBEDDING_TIMEOUT_SECONDS": "17.5",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(REPOSITORY_ROOT / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    compose = json.loads(result.stdout)
    ai_environment = compose["services"]["ai-service"]["environment"]

    assert {
        "EMBEDDING_BASE_URL": ai_environment["EMBEDDING_BASE_URL"],
        "EMBEDDING_API_KEY": ai_environment["EMBEDDING_API_KEY"],
        "EMBEDDING_TIMEOUT_SECONDS": ai_environment["EMBEDDING_TIMEOUT_SECONDS"],
    } == {
        "EMBEDDING_BASE_URL": "https://embedding.example/api/v1",
        "EMBEDDING_API_KEY": "synthetic-compose-key",
        "EMBEDDING_TIMEOUT_SECONDS": "17.5",
    }
