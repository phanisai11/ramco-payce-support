"""LLM rate card — estimated USD cost per 1M tokens, per model.

App-side estimate only; the authoritative bill comes from the cloud billing
export (``cost_ledger.reconcile_cost_ledger``). Rates are USD per 1M tokens
(input, output); unknown models price at zero so usage is still recorded.
"""

from __future__ import annotations

from decimal import Decimal

# USD per 1M tokens: (input, output). Update when provider pricing changes.
_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "openai/gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "openai/gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "google/gemini-2.0-flash": (Decimal("0.10"), Decimal("0.40")),
    "google/gemini-1.5-flash": (Decimal("0.075"), Decimal("0.30")),
    # Amazon Bedrock Nova (verify at aws.amazon.com/bedrock/pricing)
    "amazon.nova-micro-v1:0": (Decimal("0.035"), Decimal("0.14")),
    "amazon.nova-lite-v1:0": (Decimal("0.06"), Decimal("0.24")),
    "amazon.nova-pro-v1:0": (Decimal("0.80"), Decimal("2.40")),
}

_MILLION = Decimal(1_000_000)


def estimate_cost_usd(
    model_id: str | None,
    *,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """Estimated USD for one call; 0 for unpriced/zero-token calls."""
    if not model_id or (input_tokens <= 0 and output_tokens <= 0):
        return Decimal("0")
    in_rate, out_rate = _RATES.get(model_id, (Decimal("0"), Decimal("0")))
    cost = (
        Decimal(input_tokens) * in_rate + Decimal(output_tokens) * out_rate
    ) / _MILLION
    return cost.quantize(Decimal("0.000001"))
