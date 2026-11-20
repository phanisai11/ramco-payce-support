"""Deterministic preflight + heuristic fallback for LLM signal extraction.

Guarantees every ticket gets *valid* signals without paying for an LLM
call when it isn't needed:

- ``preflight_reason`` — skip the LLM entirely for inputs that cannot
  benefit from it (empty/short text, prompt-injection attempts).
- ``heuristic_signals`` — pure-code signal extraction (language, entities,
  intent, priority) used for preflight skips, LLM outages, and as the
  degraded path. Confidence is deliberately modest: heuristics describe,
  they never decide.
"""

from __future__ import annotations

import re

from payce_support.category_map import normalize_category
from payce_support.extraction import (
    _ERROR_CODE,
    _JIRA_KEY,
    _KB_ID,
    _REFUND_REF,
    detect_language,
    mask_pii,
    screen_injection,
)
from payce_support.models import Region
from payce_support.routing_signals import RoutingSignals

_MIN_LLM_CHARS = 12
_MAX_HEURISTIC_CHARS = 4_000

_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("pf", r"\b(?:pf|epf|eps|provident)\b"),
    ("esi", r"\besi\b"),
    ("pt", r"\b(?:pt|professional\s+tax|labour\s+welfare|lwf)\b"),
    ("tds", r"\b(?:tds|tax|form\s*16|90c|regime)\b"),
    ("wps", r"\b(?:wps|wps\s+sif|sif\s+file|emirates\s+id\s+file)\b"),
    ("ecr", r"\b(?:ecr|epfo?\s+return)\b"),
    ("payroll_run", r"\b(?:payroll\s+run|salary\s+run|pay\s+run|disbursement|\brun[- ]?\d+)\b"),
    ("payslip", r"\b(?:payslip|pay\s*slip|salary\s+slip)\b"),
    ("salary", r"\b(?:salary|salaries|wages?|ctc|net\s+pay|gross)\b"),
    ("login", r"\b(?:login|log\s*in|password|signin|sso|otp|locked\s+out)\b"),
    ("refund", r"\b(?:refund|reimburse|chargeback|duplicate\s+(?:charge|payment))\b"),
    ("invoice", r"\b(?:invoice|billing|subscription|vendor\s+payment)\b"),
    ("leave", r"\b(?:leave|holiday|attendance|roster|shift)\b"),
    ("onboarding", r"\b(?:onboard|new\s+joiner|bulk\s+upload|import|migration)\b"),
    ("api", r"\b(?:api|webhook|integration|endpoint|http\s*\d{3}|json)\b"),
    ("bank", r"\b(?:bank|neft|rtgs|imps|account\s+number|ifsc|iban)\b"),
    ("bonus", r"\b(?:bonus|incentive|arrears|increment)\b"),
    ("gratuity", r"\b(?:gratuity|esop|full\s*(?:and|&)\s*final|fnf|exit)\b"),
)
_KEYWORD_RES = tuple((tag, re.compile(pat, re.IGNORECASE)) for tag, pat in _KEYWORDS)

_P1_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:legal|lawsuit|court|subpoena|data\s+breach|security\s+incident|"
        r"payroll[- ]wide\s+outage|emergency)\b",
        r"\b(?:salary|salaries|pay|wages?)\s+(?:not\s+)?(?:credited|paid|received)\b"
        r".{0,30}\b(?:not|never|failed|missing|late|delay)",
        r"\bnot\s+(?:been\s+)?(?:paid|credited|received)\b.{0,40}"
        r"\b(?:salary|salaries|wages?|pay)\b",
        r"\b(?:all|every|entire)\s+(?:employees?|staff|company)\b.{0,40}"
        r"\b(?:not\s+paid|affected|down|locked)\b",
    )
)
_P2_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:urgent|asap|immediately|critical|high\s+priority)\b",
        r"\b(?:deadline|due\s+(?:date|today|tomorrow)|filing\s+(?:due|deadline))\b",
        r"\b(?:penalty|fine|interest|rejection\s+letter)\b",
    )
)
_INTENT_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("how_to", re.compile(r"\b(?:how\s+(?:do|can|to)\b|steps?\b|guide|process\s+for)\b", re.I)),
    ("billing_refund", re.compile(r"\b(?:refund|reimburse|duplicate\s+charge|wrong\s+charge|billing)\b", re.I)),
    ("status_enquiry", re.compile(r"\b(?:status|where\s+is|when\s+will|track|update\s+on)\b", re.I)),
    ("change_request", re.compile(r"\b(?:please\s+(?:change|update|correct|fix|modify)|need\s+to\s+(?:change|update|correct))\b", re.I)),
    ("problem", re.compile(r"\b(?:not\s+working|error|failed|failure|issue|problem|stuck|unable|cannot|can't|doesn'?t|wrong|missing|rejected|broken)\b", re.I)),
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def preflight_reason(text: str) -> str | None:
    """Why this input should skip the LLM, or None to proceed.

    Injection attempts never reach the model; trivially short texts have
    nothing to extract.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_LLM_CHARS:
        return "too_short"
    if screen_injection(stripped):
        return "injection_flagged"
    return None


def heuristic_signals(
    *,
    text: str,
    region: Region,
) -> RoutingSignals:
    """Deterministic signal extraction — no model call, always valid."""
    masked = mask_pii(text[:_MAX_HEURISTIC_CHARS])
    lower = masked.lower()

    entities: list[str] = []
    for tag, pattern in _KEYWORD_RES:
        if pattern.search(masked) and tag not in entities:
            entities.append(tag)
    for rx in (_ERROR_CODE, _JIRA_KEY, _KB_ID, _REFUND_REF):
        entities.extend(m.group(0).lower() for m in rx.finditer(masked))
    category = normalize_category(None, text=masked)
    if category != "other" and category not in entities:
        entities.insert(0, category)

    intent = next(
        (name for name, rx in _INTENT_RES if rx.search(masked)),
        "question",
    )
    p1 = any(rx.search(masked) for rx in _P1_RES)
    priority = "p1" if p1 else (
        "p2" if any(rx.search(masked) for rx in _P2_RES) else "p3"
    )

    first = _SENTENCE_SPLIT.split(masked.strip(), maxsplit=1)[0].strip()
    summary = (first or masked.strip())[:240]

    return RoutingSignals(
        p1=p1,
        priority=priority,
        language=detect_language(masked),
        entities=tuple(entities[:8]),
        intent=intent[:64],
        confidence=0.4 if detect_language(masked) == "en" else 0.3,
        summary=summary,
        rag_hints=(),
    )
