"""Exceptions raised by AquaWatch water providers."""


class ProviderError(Exception):
    """Base exception for provider errors."""


class AuthError(ProviderError):
    """Raised when authentication with the provider fails."""


class ScrapingError(ProviderError):
    """Raised when the provider's response no longer matches the expected structure."""


class ProviderUnavailable(ProviderError):
    """Raised when calling a provider that is not yet implemented."""
