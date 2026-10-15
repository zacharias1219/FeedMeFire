from __future__ import annotations

from dataclasses import dataclass


class ProviderError(Exception):
    retryable = True


class ProviderUnavailable(ProviderError):
    """Provider returned an error or could not be reached (no work was done)."""


class ProviderTimeout(ProviderError):
    """Outcome unknown: the provider may have processed (and billed) the request."""


@dataclass
class ProviderResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider:
    name = "base"
    model = "unset"

    def generate(self, system: str, user: str, *, timeout: float) -> ProviderResult:  # pragma: no cover
        raise NotImplementedError

    def moderate(self, text: str, *, timeout: float) -> bool:  # pragma: no cover
        """Optional second pass: True = acceptable. Default: no opinion."""
        return True
