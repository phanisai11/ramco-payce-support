"""Reusable ticket-analysis tool.

Wraps the configured ``SignalExtractor`` (LLM when keyed, deterministic
fallback otherwise) so any caller — reply handling, dashboard, future
pipeline stages — can produce and persist a saved analysis snapshot.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from payce_support.models import Region
from payce_support.routing_signals import SignalExtractor


async def analyze_ticket_text(
    *,
    extractor: SignalExtractor | None,
    text: str,
    region: Region,
    tenant_id: str | None,
    sla_rules: list[dict] | None = None,
) -> dict[str, Any]:
    """Run signal extraction over masked ticket text.

    Returns a JSON-safe payload for ``ticket_analysis.payload_json``.
    Never raises on provider failure — records a degraded analysis instead.
    """
    base = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "region": region.value,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    if extractor is None:
        return {**base, "status": "skipped", "reason": "llm_not_configured"}

    # Deterministic preflight — inputs that can't benefit from an LLM
    # (injection attempts, trivially short text) never reach the model.
    from payce_support.signal_preflight import (
        heuristic_signals,
        preflight_reason,
    )

    skip = preflight_reason(text)
    if skip is not None:
        signals = heuristic_signals(text=text, region=region)
        return {
            **base,
            "status": "preflight_skip",
            "reason": skip,
            "extractor": "preflight",
            "signals": signals.model_dump(mode="json"),
        }

    try:
        result = await extractor.extract_verbose(
            text=text,
            region=region,
            tenant_id=tenant_id,
            extra_context={"sla_rules": sla_rules} if sla_rules else None,
        )
        signals = result.signals
        return {
            **base,
            "status": "ok",
            "extractor": "llm",
            "model_id": result.model_id,
            "signals": signals.model_dump(mode="json"),
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        }
    except Exception as exc:
        # LLM unavailable/unparseable → deterministic signals, never a
        # dead end. The record stays honest about which extractor ran.
        signals = heuristic_signals(text=text, region=region)
        return {
            **base,
            "status": "fallback",
            "reason": type(exc).__name__,
            "detail": str(exc)[:500],
            "extractor": "heuristic",
            "signals": signals.model_dump(mode="json"),
        }
