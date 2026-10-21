from __future__ import annotations

from ..config import settings
from .base import Provider, ProviderError, ProviderTimeout, ProviderUnavailable  # noqa: F401


def get_provider() -> Provider:
    """Provider adapter factory. Tests monkeypatch this to inject scripted providers."""
    if settings.provider == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if settings.provider == "sarvam":
        from .sarvam import SarvamProvider
        return SarvamProvider()
    from .fake import FakeProvider
    return FakeProvider()
