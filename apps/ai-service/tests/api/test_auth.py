import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.agent.graph import AgentDependencies
from app.api.models import WorkOrderRecord, WorkOrderSearchPage
from app.config import Settings
from app.knowledge.models import ActiveKnowledgeChunk, RetrievalHit, RetrievalResult
from app.llm.gateway import LLMGateway
from app.llm.offline import OfflineTemplateProvider
from app.main import create_app


class _Index:
    async def search(
        self,
        tenant_id: object,
        query: str,
        limit: int = 5,
    ) -> RetrievalResult:
        chunk = ActiveKnowledgeChunk(
            chunk_id="auth-policy:0",
            document_id="auth-policy",
            document_key="auth-policy",
            title="认证制度",
            section="规则",
            text="认证后的租户可以检索制度。",
            ordinal=0,
            document_version=1,
            content_hash="a" * 64,
        )
        return RetrievalResult(
            hits=(
                RetrievalHit(
                    **chunk.model_dump(),
                    bm25_rank=1,
                    vector_rank=1,
                    rrf_score=2 / 61,
                ),
            ),
            mode="hybrid",
        )


class _WorkOrders:
    async def get_work_order(self, work_order_no: str) -> WorkOrderRecord:
        raise AssertionError("not expected")

    async def get_rework_chain(self, work_order_no: str) -> list[WorkOrderRecord]:
        raise AssertionError("not expected")

    async def search_work_orders(self, filters: dict[str, str]) -> WorkOrderSearchPage:
        raise AssertionError("not expected")


def app_dependencies() -> AgentDependencies:
    offline = OfflineTemplateProvider()
    return AgentDependencies(
        index=_Index(),
        work_order_client=_WorkOrders(),
        gateway=LLMGateway(
            provider=offline,
            fallback_provider=offline,
            max_retries=0,
            fallback_enabled=True,
        ),
    )


def _encode(value: object) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    audience: str = "work-order-service",
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    expires_offset: int = 300,
) -> str:
    now = int(time.time())
    header = _encode({"alg": "RS256", "typ": "JWT"})
    claims = _encode(
        {
            "iss": "https://issuer.example",
            "aud": audience,
            "sub": "synthetic-user",
            "tenant_id": tenant_id,
            "nbf": now - 1,
            "exp": now + expires_offset,
        }
    )
    signature = private_key.sign(
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{header}.{claims}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _settings(tmp_path: Path) -> tuple[Settings, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_path = tmp_path / "jwt-public.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return (
        Settings(
            jwt_issuer="https://issuer.example",
            jwt_audience="work-order-service",
            jwt_public_key_path=public_path,
            _env_file=None,
        ),
        private_key,
    )


@pytest.mark.asyncio
async def test_chat_accepts_verified_jwt_tenant(tmp_path: Path) -> None:
    settings, private_key = _settings(tmp_path)
    app = create_app(settings=settings, dependencies=app_dependencies())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            headers={"Authorization": f"Bearer {_token(private_key)}"},
            json={"session_id": "auth-1", "message": "返工规则是什么？"},
        )

    assert response.status_code == 200
    assert response.json()["retrieval_mode"] == "hybrid"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"audience": "wrong-audience"},
        {"expires_offset": -120},
        {"tenant_id": "not-a-uuid"},
    ],
)
@pytest.mark.asyncio
async def test_chat_rejects_invalid_verified_claims(
    tmp_path: Path,
    token_kwargs: dict[str, object],
) -> None:
    settings, private_key = _settings(tmp_path)
    app = create_app(settings=settings, dependencies=app_dependencies())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            headers={"Authorization": f"Bearer {_token(private_key, **token_kwargs)}"},
            json={"session_id": "auth-2", "message": "返工规则是什么？"},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTHENTICATED_TENANT_REQUIRED"


def test_partial_jwt_configuration_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JWT authentication configuration"):
        create_app(
            settings=Settings(
                jwt_issuer="https://issuer.example",
                jwt_public_key_path=tmp_path / "missing.pem",
                _env_file=None,
            ),
            dependencies=app_dependencies(),
        )
