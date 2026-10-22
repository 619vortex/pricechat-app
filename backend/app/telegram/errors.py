"""Telegram failure taxonomy. Messages never contain the bot token or request URL."""


class TelegramError(Exception):
    code = "telegram_error"
    retryable = True


class TelegramNotConfiguredError(TelegramError):
    code = "telegram_not_configured"
    retryable = False


class TelegramAuthError(TelegramError):
    """The bot token was rejected (revoked, mistyped)."""

    code = "telegram_auth"
    retryable = False


class TelegramConflictError(TelegramError):
    """Another client is polling with this token, or a webhook is set."""

    code = "telegram_conflict"


class TelegramRateLimitError(TelegramError):
    code = "telegram_rate_limited"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramUnavailableError(TelegramError):
    """Network failure, timeout or Telegram server error."""

    code = "telegram_unavailable"


class TelegramRequestError(TelegramError):
    """Telegram rejected the request (e.g. unknown chat)."""

    code = "telegram_bad_request"
    retryable = False


class MalformedUpdateError(TelegramError):
    code = "telegram_malformed_update"
    retryable = False
