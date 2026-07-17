import httpx


class ProviderError(RuntimeError):
    code = "PROVIDER_ERROR"
    retryable = False

    def __init__(self, message: str = "Model provider request failed") -> None:
        super().__init__(message)


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH_FAILED"

    def __init__(self) -> None:
        super().__init__("Model provider authentication failed")


class ProviderRateLimitError(ProviderError):
    code = "PROVIDER_RATE_LIMITED"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Model provider rate limited the request")


class ProviderTimeoutError(ProviderError):
    code = "PROVIDER_TIMEOUT"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Model provider request timed out")


class ProviderUnavailableError(ProviderError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Model provider is unavailable")


class ProviderBadResponseError(ProviderError):
    code = "PROVIDER_BAD_RESPONSE"

    def __init__(self) -> None:
        super().__init__("Model provider returned an invalid response")


def raise_for_provider_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise ProviderAuthError
    if response.status_code == 429:
        raise ProviderRateLimitError
    if response.status_code >= 500:
        raise ProviderUnavailableError
    if response.status_code >= 400:
        raise ProviderBadResponseError

