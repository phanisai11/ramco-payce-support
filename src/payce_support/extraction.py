"""L1 deterministic extraction — raw envelope payload → CanonicalExtraction.

No LLM, no vectors: regex + dictionaries only (PRODUCTION_DESIGN_GCP.md §3).
This is the v1 payload contract for ``envelope.payload``::

    {
      "ticket_id": "PN-101253",          # required
      "case_id":   "CASE-8812",          # required
      "subject":   "PF not credited",    # optional
      "body":      "...",                # optional
      "language":  "en|hi|hi-Latn|ar",   # optional, else detected
      "payroll_period": "2026-08",       # optional YYYY-MM
      "payroll_week":  true,             # optional
      "run_id":    "RUN-778",            # optional
      "refund_ref": "RF-123",            # optional (or refund_refs list)
      "customer_message_at": "RFC3339",  # optional
      "t_as_of":   "RFC3339",            # optional
      "p1":        true                  # optional priority flag (routing only)
    }

Customer-supplied ``tenant_id``/``region``/``queue`` fields are ignored —
tenant identity comes only from the resolved envelope.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from payce_support.canonical import (
    CanonicalExtraction,
    CanonicalFacts,
)
from payce_support.models import RawIntakeEnvelope


class CanonicalExtractionError(ValueError):
    """Payload cannot produce a schema-valid canonical event."""


# -- PII masking (applied before content enters canonical records) ---------

_MASK_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)"), "[REDACTED:PHONE_IN]"),
    (re.compile(r"\b\+971[-\s]?5\d{2}[-\s]?\d{3}[-\s]?\d{4}\b"), "[REDACTED:PHONE_AE]"),
    (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[REDACTED:PAN_LIKE]"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{2}\b"), "[REDACTED:AADHAAR_LIKE]"),
    (re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?[A-Z]\b", re.IGNORECASE), "[REDACTED:EID]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[REDACTED:IBAN_LIKE]"),
)


def mask_pii(text: str) -> str:
    """Deterministic PII redaction for canonical masked content."""
    masked = text
    for pattern, replacement in _MASK_RULES:
        masked = pattern.sub(replacement, masked)
    return masked


# -- prompt-injection screen (P6 — Model Armor stand-in) --------------------

_INJECTION_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+"
        r"(instructions?|prompts?|rules?|directives?)",
        r"disregard\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|your|all)\s+"
        r"(instructions?|rules?|guidelines?|programming)",
        r"forget\s+(everything|all|your)\b.{0,40}\b(instructions?|rules?)",
        r"you\s+are\s+now\s+(a|an|in)\s+\w+",
        r"new\s+(system\s+)?instructions?\s*:",
        r"system\s+prompt",
        r"reveal\s+(your|the)\s+(system|initial|original)\s+"
        r"(prompt|instructions?)",
        r"jailbreak",
        r"\bDAN\b.{0,20}\bmode\b",
        r"\bmark\b.{0,40}\bapproved\b",
        r"\bapprove\b.{0,30}\b(refund|credit| payout|payment)\b",
        r"\blist\s+all\s+(customer\s+|tenant\s+)?(admins?|users?|credentials?)",
        r"\bexfiltrat|\bexport\s+all\s+(data|records|tickets)\b",
    )
)


def screen_injection(text: str) -> bool:
    """Deterministic prompt-injection heuristic — flags hostile ticket
    text so it diverts to a human with no model call (P6)."""
    return any(p.search(text) for p in _INJECTION_RES)


# -- language / time helpers -----------------------------------------------

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$",
)
_PAYROLL_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_JIRA_KEY = re.compile(r"\bPNJ-\d+\b")
_KB_ID = re.compile(r"\bKB-\d+\b")
_ERROR_CODE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")
_REFUND_REF = re.compile(r"\b(?:RF|REF|RFD)-[A-Za-z0-9-]+\b")


def detect_language(text: str) -> str:
    if _DEVANAGARI.search(text):
        return "hi"
    if _ARABIC.search(text):
        return "ar"
    return "en"


def _parse_dt(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _ISO_DATETIME.match(value.strip()):
        raise CanonicalExtractionError(f"{field} must be an RFC3339 datetime")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CanonicalExtractionError(f"{field} must be an RFC3339 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _payroll_period_end(period: str) -> datetime:
    match = _PAYROLL_PERIOD.match(period)
    if not match:
        raise CanonicalExtractionError("payroll_period must match YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    from datetime import timedelta

    return nxt - timedelta(microseconds=1)


def _str_field(payload: dict[str, Any], key: str, *, required: bool) -> str:
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise CanonicalExtractionError(f"payload.{key} is required")
        return ""
    if not isinstance(value, str):
        raise CanonicalExtractionError(f"payload.{key} must be a string")
    return value


def _unique_sorted(matches: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(matches)))


def extract_canonical_envelope(
    envelope: RawIntakeEnvelope,
) -> tuple[CanonicalExtraction, bool]:
    """Build ``CanonicalExtraction`` from the envelope's parsed payload.

    Returns ``(extraction, p1)``. Raises ``CanonicalExtractionError`` when the
    payload lacks required ticket/case identity.
    """
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}

    ticket_id = _str_field(payload, "ticket_id", required=True).strip()
    case_id = _str_field(payload, "case_id", required=True).strip()
    subject_raw = _str_field(payload, "subject", required=False)
    body_raw = _str_field(payload, "body", required=False)
    if not subject_raw and not body_raw:
        # Widget-style payloads may carry a single message field.
        message = _str_field(payload, "message", required=False)
        body_raw = message

    subject_masked = mask_pii(subject_raw)
    body_masked = mask_pii(body_raw)

    language_raw = payload.get("language")
    if language_raw is None:
        language = detect_language(f"{subject_raw}\n{body_raw}")
    elif language_raw in ("en", "hi", "hi-Latn", "ar"):
        language = language_raw
    else:
        raise CanonicalExtractionError("payload.language must be en|hi|hi-Latn|ar")

    payroll_period_raw = payload.get("payroll_period")
    payroll_period: str | None = None
    if payroll_period_raw is not None:
        if not isinstance(payroll_period_raw, str) or not _PAYROLL_PERIOD.match(
            payroll_period_raw,
        ):
            raise CanonicalExtractionError("payroll_period must match YYYY-MM")
        payroll_period = payroll_period_raw

    payroll_week = payload.get("payroll_week") is True
    customer_message_at = _parse_dt(
        payload.get("customer_message_at") or payload.get("message_at"),
        "customer_message_at",
    )
    t_as_of = _parse_dt(payload.get("t_as_of"), "t_as_of")
    if t_as_of is None and payroll_period is not None:
        t_as_of = _payroll_period_end(payroll_period)

    combined_text = f"{subject_raw}\n{body_raw}"
    from payce_support.category_map import normalize_category

    facts = CanonicalFacts(
        run_id=(
            payload.get("run_id")
            if isinstance(payload.get("run_id"), str)
            else None
        ),
        jira_keys=_unique_sorted(_JIRA_KEY.findall(combined_text)),
        kb_ids=_unique_sorted(_KB_ID.findall(combined_text)),
        error_codes=_unique_sorted(_ERROR_CODE.findall(combined_text)),
        refund_refs=_unique_sorted(
            _REFUND_REF.findall(combined_text)
            + _as_str_list(payload.get("refund_refs")),
        ),
        category=normalize_category(
            payload.get("category")
            if isinstance(payload.get("category"), str)
            else payload.get("queue")
            if isinstance(payload.get("queue"), str)
            else None,
            text=combined_text,
        ),
    )

    extraction = CanonicalExtraction(
        ticket_id=ticket_id[:64],
        case_id=case_id[:64],
        subject_masked=subject_masked[:1000],
        body_masked=body_masked[:100000],
        language=language,  # type: ignore[arg-type]
        customer_message_at=customer_message_at,
        payroll_period=payroll_period,
        payroll_week=payroll_week,
        t_as_of=t_as_of,
        facts=facts,
    )
    p1 = payload.get("p1") is True or payload.get("priority") == "p1"
    return extraction, p1


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, str)]
    return []
