"""G6 — verify draft numbers against statutory engine; require citations on statutory path.

Amount extraction covers INR, AED, USD, EUR, GBP and percentages so non-INR
statutory formats (UAE WPS/gratuity rules, rate changes) verify the same way.
Matching is currency/kind-aware and decimal-exact; bare numbers without a
currency marker or percent sign are ignored to avoid false positives.

re module: https://docs.python.org/3/library/re.html
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, Sequence

from payce_support.statutory_engine import StatutoryEngine, StatutoryResolution


AmountKind = Literal["INR", "AED", "USD", "EUR", "GBP", "PCT"]

_NUMBER = (
    r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?"
    r"|[0-9]{1,2}(?:,[0-9]{2})*,[0-9]{3}(?:\.[0-9]+)?"
    r"|[0-9]+(?:\.[0-9]+)?)"
)

_AMOUNT_PATTERNS: tuple[tuple[AmountKind, re.Pattern[str]], ...] = (
    ("INR", re.compile(rf"(?:INR|Rs\.?|₹)\s*{_NUMBER}(?:\s*(?:per|/)\s*month)?", re.IGNORECASE)),
    ("AED", re.compile(rf"(?:AED|د\.إ\.?|Dhs?\.?)\s*{_NUMBER}", re.IGNORECASE)),
    ("USD", re.compile(rf"(?:USD|US\$|\$)\s*{_NUMBER}", re.IGNORECASE)),
    ("EUR", re.compile(rf"(?:EUR|€)\s*{_NUMBER}", re.IGNORECASE)),
    ("GBP", re.compile(rf"(?:GBP|£)\s*{_NUMBER}", re.IGNORECASE)),
    ("PCT", re.compile(rf"{_NUMBER}\s*(?:%|percent|per\s+cent)(?![0-9])", re.IGNORECASE)),
)

_INR_AMOUNT = re.compile(
    r"(?:INR|Rs\.?|₹)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\s*(?:per|/)\s*month)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Amount:
    """A currency-marked or percentage value extracted from text."""

    kind: AmountKind
    value: Decimal
    raw: str


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def extract_amounts(text: str) -> tuple[Amount, ...]:
    """All currency/percentage amounts in order of appearance."""
    found: list[tuple[int, Amount]] = []
    for kind, pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            # group(1) is the numeric capture for currency patterns; PCT uses
            # the same single group layout.
            digits = match.group(1)
            value = _to_decimal(digits)
            if value is None:
                continue
            found.append((match.start(), Amount(kind=kind, value=value, raw=match.group(0))))
    found.sort(key=lambda item: item[0])
    return tuple(amount for _, amount in found)


def extract_inr_amounts(text: str) -> tuple[int, ...]:
    """Return integer rupee amounts found in text (comma-separated thousands allowed)."""
    out: list[int] = []
    for match in _INR_AMOUNT.finditer(text):
        digits = match.group(1).replace(",", "")
        out.append(int(digits))
    return tuple(out)


@dataclass(frozen=True)
class StatutoryDraftVerification:
    passed: bool
    engine_value: str
    draft_inr: tuple[int, ...]
    has_citation: bool
    reason: str
    engine_amount: Amount | None = None
    draft_amounts: tuple[Amount, ...] = ()


def verify_statutory_draft(
    *,
    draft_text: str,
    citations: Sequence[str],
    engine: StatutoryEngine,
    region: str,
    topic: str,
    as_of: date,
) -> StatutoryDraftVerification:
    """Block when engine numbers disagree with draft or citations are missing."""
    resolution = engine.resolve(region=region, topic=topic, as_of=as_of)
    return verify_statutory_draft_with_resolution(
        draft_text=draft_text,
        citations=citations,
        resolution=resolution,
    )


def verify_statutory_draft_with_resolution(
    *,
    draft_text: str,
    citations: Sequence[str],
    resolution: StatutoryResolution,
) -> StatutoryDraftVerification:
    draft_inr = extract_inr_amounts(draft_text)
    draft_amounts = extract_amounts(draft_text)
    engine_amounts = extract_amounts(resolution.value_applied)
    engine_amount = engine_amounts[-1] if engine_amounts else None
    has_citation = len(tuple(citations)) > 0

    if not has_citation:
        return StatutoryDraftVerification(
            passed=False,
            engine_value=resolution.value_applied,
            draft_inr=draft_inr,
            has_citation=False,
            reason="MISSING_CITATION",
            engine_amount=engine_amount,
            draft_amounts=draft_amounts,
        )

    if engine_amount is None:
        return StatutoryDraftVerification(
            passed=False,
            engine_value=resolution.value_applied,
            draft_inr=draft_inr,
            has_citation=True,
            reason="ENGINE_AMOUNT_NOT_PARSEABLE",
            engine_amount=None,
            draft_amounts=draft_amounts,
        )

    matched = any(
        amount.kind == engine_amount.kind and amount.value == engine_amount.value
        for amount in draft_amounts
    )
    if not matched:
        return StatutoryDraftVerification(
            passed=False,
            engine_value=resolution.value_applied,
            draft_inr=draft_inr,
            has_citation=True,
            reason="NUMBER_MISMATCH",
            engine_amount=engine_amount,
            draft_amounts=draft_amounts,
        )

    return StatutoryDraftVerification(
        passed=True,
        engine_value=resolution.value_applied,
        draft_inr=draft_inr,
        has_citation=True,
        reason="OK",
        engine_amount=engine_amount,
        draft_amounts=draft_amounts,
    )
