class GatewayError(Exception):
    """Base error for gateway failures."""

    status_code = 502

    def __init__(self, message: str, *, provider: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        if status_code is not None:
            self.status_code = status_code


class ModelNotFound(GatewayError):
    status_code = 404


class ProviderNotConfigured(GatewayError):
    """A provider is registered but its API key / endpoint is missing."""

    status_code = 503


class ProviderError(GatewayError):
    status_code = 502


class BadRequest(GatewayError):
    status_code = 400
