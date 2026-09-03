"""Safe Hero API exception hierarchy."""


class HeroError(Exception):
    """Base error that never includes authentication material."""


class HeroAuthenticationError(HeroError):
    """Hero authentication failed."""


class HeroConnectionError(HeroError):
    """Hero service could not be reached."""


class HeroRateLimitError(HeroConnectionError):
    """Hero temporarily rate limited a request."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("Hero temporarily rate limited the request")
        self.retry_after = retry_after


class HeroApiError(HeroError):
    """Hero service returned an unexpected API response."""


class HeroDispenseError(HeroError):
    """A safety-sensitive dispensing operation failed."""
