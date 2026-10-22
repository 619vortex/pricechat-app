"""X API failure taxonomy. Messages never contain credentials."""

from datetime import datetime


class XError(Exception):
    code = "x_error"


class XNotConfiguredError(XError):
    code = "x_not_configured"


class XAuthError(XError):
    """401: credentials invalid, revoked or missing."""

    code = "x_auth"


class XForbiddenError(XError):
    """403: valid credentials but this endpoint/feature is not available to this app or access level."""

    code = "x_forbidden"


class XNotFoundError(XError):
    code = "x_not_found"


class XBadRequestError(XError):
    code = "x_bad_request"


class XRateLimitError(XError):
    """429 rate-limit-exceeded: wait until reset."""

    code = "x_rate_limited"

    def __init__(self, message: str, reset_at: datetime | None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class XUsageCapError(XRateLimitError):
    """429 usage-capped: the billing-cycle cap or the console spending limit was reached."""

    code = "x_usage_capped"


class XCreditsError(XUsageCapError):
    """402 credits depleted: the X developer account has no pay-per-use credit left."""

    code = "x_no_credit"


class XBudgetExceededError(XError):
    """Price Chat's own local spending guard stopped the request before it was sent."""

    code = "x_budget"


class XUnavailableError(XError):
    """Network failure, timeout or X server error."""

    code = "x_unavailable"
