"""Safe Hero API exception hierarchy."""


class HeroError(Exception):
    """Base error that never includes authentication material."""


class HeroAuthenticationError(HeroError):
    """Hero authentication failed."""


class HeroConnectionError(HeroError):
    """Hero service could not be reached."""


class HeroApiError(HeroError):
    """Hero service returned an unexpected API response."""


class HeroDispenseError(HeroError):
    """A safety-sensitive dispensing operation failed."""
