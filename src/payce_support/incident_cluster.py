"""Symptom-fingerprint incident clustering (G4) — deterministic threshold, no LLM.

Design note: `PRODUCTION_DESIGN_GCP.md` recommends fingerprint + count before optional embeddings.
datetime (UTC): https://docs.python.org/3/library/datetime.html
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from re import sub


UTC = timezone.utc


@dataclass(frozen=True)
class ClusterTicketSignal:
    """Inbound signal for clustering (after normalisation in a real pipeline)."""

    received_at: datetime
    symptom_fingerprint: str
    tenant_id: str
    channel: str
    ticket_id: str = ""

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware (use UTC)")
        if not self.symptom_fingerprint.strip():
            raise ValueError("symptom_fingerprint must be non-empty")


@dataclass(frozen=True)
class IncidentDeclaration:
    symptom_fingerprint: str
    fifth_ticket_at: datetime
    declared_at: datetime
    tenant_id: str = ""
    ticket_count: int = 0
    channel: str = ""
    ticket_ids: tuple[str, ...] = ()

    def delay_after_fifth(self) -> timedelta:
        return self.declared_at - self.fifth_ticket_at


_TOKEN = __import__("re").compile(r"[a-z]{4,}")
_STOP = {
    "that", "this", "with", "from", "have", "been", "they", "them",
    "what", "when", "your", "please", "there", "their", "about",
}


def symptom_fingerprint_for_ticket(
    *,
    category: str | None,
    error_codes: tuple[str, ...] | list[str],
    subject: str,
    body: str,
) -> str:
    """Coarse symptom bucket for auto-clustering: canonical category +
    error codes, falling back to the top subject keywords. Deliberately
    ignores ticket-unique wording so similar reports collide."""
    parts = [f"cat:{(category or 'other').lower()}"]
    codes = [c.strip().upper() for c in error_codes if c and c.strip()]
    if codes:
        parts.extend(f"code:{c}" for c in sorted(set(codes))[:2])
    else:
        tokens = [
            t
            for t in _TOKEN.findall(normalise_text_for_fingerprint(subject))
            if t not in _STOP
        ]
        # Stable top-3 keyword bucket — order-independent.
        parts.extend(f"kw:{t}" for t in sorted(set(tokens))[:3])
    return "|".join(parts)


def normalise_text_for_fingerprint(text: str) -> str:
    """Collapse whitespace and case for stable keyword/error-code buckets."""
    collapsed = sub(r"\s+", " ", text.strip().lower())
    return collapsed


def symptom_fingerprint_from_text(*, error_code: str | None, subject: str, body: str) -> str:
    """Build a synthetic fingerprint: explicit code wins; else hashed bucket from subject+body."""
    parts: list[str] = []
    if error_code is not None and error_code.strip():
        parts.append(f"code:{error_code.strip().upper()}")
    blob = normalise_text_for_fingerprint(f"{subject}\n{body}")
    parts.append(blob)
    return "|".join(parts)


@dataclass
class IncidentClusterEngine:
    """Declare at most one incident per (tenant, fingerprint) after N signals (cross-channel)."""

    def __init__(
        self,
        *,
        threshold: int = 5,
        max_declare_delay_after_fifth: timedelta = timedelta(minutes=5),
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self._threshold = threshold
        self._max_delay = max_declare_delay_after_fifth
        self._times: dict[str, list[ClusterTicketSignal]] = {}
        self._declared_key: set[str] = set()
        self._declarations: list[IncidentDeclaration] = []

    @staticmethod
    def _cluster_key(signal: ClusterTicketSignal) -> str:
        return f"{signal.tenant_id}::{signal.symptom_fingerprint}"

    def on_signal(self, signal: ClusterTicketSignal) -> IncidentDeclaration | None:
        key = self._cluster_key(signal)
        if key in self._declared_key:
            return None

        signals = self._times.setdefault(key, [])
        signals.append(signal)
        if len(signals) < self._threshold:
            return None

        fifth_at = signals[self._threshold - 1].received_at
        declared_at = fifth_at
        delay = declared_at - fifth_at
        if delay > self._max_delay:
            raise RuntimeError(
                f"incident declaration delay {delay} exceeds max {self._max_delay} (configuration error)",
            )

        self._declared_key.add(key)
        declaration = IncidentDeclaration(
            symptom_fingerprint=signal.symptom_fingerprint,
            fifth_ticket_at=fifth_at,
            declared_at=declared_at,
            tenant_id=signal.tenant_id,
            ticket_count=len(signals),
            channel=signal.channel,
            ticket_ids=tuple(s.ticket_id for s in signals if s.ticket_id),
        )
        self._declarations.append(declaration)
        return declaration

    def recent(self, *, limit: int = 20) -> tuple[IncidentDeclaration, ...]:
        return tuple(self._declarations[-limit:])

    def reset(self) -> None:
        self._times.clear()
        self._declared_key.clear()
        self._declarations.clear()
