from __future__ import annotations

from ..config import settings


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Operational estimate from configured prices (USD). Not an invoice."""
    return round(input_tokens / 1e6 * settings.price_input_per_mtok
                 + output_tokens / 1e6 * settings.price_output_per_mtok, 6)
