from __future__ import annotations

from ..config import settings
from .base import Provider, ProviderError, ProviderMisconfigured, ProviderTimeout, ProviderUnavailable  # noqa: F401


def get_provider() -> Provider:
    """Provider adapter factory. Tests monkeypatch this to inject scripted providers."""
    if settings.resolved_provider == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if settings.resolved_provider == "sarvam":
        from .sarvam import SarvamProvider
        return SarvamProvider()
    from .fake import FakeProvider
    return FakeProvider()


def check_ready() -> None:
    """Fail fast (at startup, before anyone clicks 'Roast me') if the chosen provider cannot work in THIS Python."""
    import sys
    if settings.resolved_provider == "sarvam":
        try:
            import sarvamai  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"PROVIDER is sarvam but the 'sarvamai' package is not installed for this Python ({sys.executable}). "
                               f"Install it into the environment that runs the server and worker: "
                               f"{sys.executable} -m pip install -r requirements.txt") from e
