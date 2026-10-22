"""Provider-neutral error taxonomy.

Each provider maps its own failure modes onto these so the rest of the app
(and the dashboard) can react without knowing which backend is in use.
"""


class ProviderError(Exception):
    """Base class for model-provider failures."""

    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    """The backend could not be reached (not running, network error)."""

    code = "provider_unavailable"


class ProviderTimeoutError(ProviderUnavailableError):
    """The backend accepted the request but did not answer in time."""

    code = "provider_timeout"


class ProviderResponseError(ProviderError):
    """The backend responded, but with an error or an unusable payload."""

    code = "provider_error"


class ModelNotFoundError(ProviderResponseError):
    """The backend is up, but the requested model is not installed/available."""

    code = "model_unavailable"

    def __init__(self, model: str, hint: str | None = None) -> None:
        self.model = model
        self.hint = hint
        super().__init__(f"Model '{model}' is not available." + (f" {hint}" if hint else ""))


class UnknownProviderError(ProviderError):
    """Configuration names a provider that is not registered."""

    code = "provider_misconfigured"
