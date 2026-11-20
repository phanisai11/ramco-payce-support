"""SLA / penalty rules — separate from routing rules.

``routing_policy/sla.yaml`` defines unanswered-time thresholds that map a
stale ticket to a penalty amount. Evaluation is read-only: it never mutates
tickets, it reports which SLA rule each ticket currently breaches.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from payce_support.models import ImmutableModel
from payce_support.pipeline import TicketCurrentRow


class SlaRule(ImmutableModel):
    id: str = Field(min_length=1, max_length=128)
    unanswered_hours: float = Field(gt=0)
    penalty_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=8)
    region: str | None = Field(default=None, pattern=r"^(IN|UAE)$")
    tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    valid_from: date | None = None
    valid_to: date | None = None
    status: str = Field(default="approved", pattern=r"^(approved|in_progress|outdated|draft|expired)$")
    applies_to_statuses: tuple[str, ...] = ("open",)
    applies_to_priorities: tuple[str, ...] = ()
    description: str | None = Field(default=None, max_length=256)

    @field_validator("valid_from", "valid_to", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> object:
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    @field_validator("penalty_amount", mode="before")
    @classmethod
    def _coerce_decimal(cls, value: object) -> object:
        return Decimal(str(value)) if not isinstance(value, Decimal) else value

    @field_validator("applies_to_statuses", "applies_to_priorities", mode="before")
    @classmethod
    def _list_to_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class SlaBreach(ImmutableModel):
    sla_rule_id: str
    unanswered_hours: float
    age_hours: float
    penalty_amount: Decimal
    currency: str


class SlaTicketEvaluation(ImmutableModel):
    tenant_id: str
    ticket_id: str
    status: str
    queue_id: str | None
    rule_id: str | None
    age_hours: float
    unanswered: bool
    breach: SlaBreach | None = None
    upcoming: SlaBreach | None = None
    month: str = ""


class SlaReport(ImmutableModel):
    sla_policy_version: str
    total_tickets: int
    unanswered_count: int
    breached_count: int
    penalties_by_currency: dict[str, str]
    evaluations: tuple[SlaTicketEvaluation, ...]


def load_sla_policy(path: Path | str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("sla policy must contain a rules list")
    return data


_SLA_RULE_COLUMNS = (
    "rule_id, tenant_id, region, unanswered_hours, penalty_amount, currency,"
    " valid_from, valid_to, status, applies_to_statuses, applies_to_priorities,"
    " description, policy_version"
)


def load_sla_policy_from_db(dsn: str) -> dict[str, Any]:
    """Load SLA rules from ``app.sla_rule`` — Postgres is the source of truth.

    ``tenant_id`` is a FK to ``app.tenant_profile``; NULL means a
    region-generic rule. Returns the same dict shape as ``load_sla_policy``.
    """
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SLA_RULE_COLUMNS} FROM app.sla_rule"
            " WHERE deleted_at IS NULL ORDER BY rule_id"
        )
        rows = cur.fetchall()

    rules: list[dict[str, Any]] = []
    version = "sla-db-v1"
    for (
        rule_id,
        tenant_id,
        region,
        hours,
        amount,
        currency,
        valid_from,
        valid_to,
        status,
        statuses,
        priorities,
        description,
        policy_version,
    ) in rows:
        version = str(policy_version or version)
        rules.append(
            {
                "id": rule_id,
                "tenant_id": tenant_id,
                "region": region,
                "unanswered_hours": float(hours),
                "penalty_amount": str(amount),
                "currency": currency,
                "valid_from": valid_from.isoformat() if valid_from else None,
                "valid_to": valid_to.isoformat() if valid_to else None,
                "status": status,
                "applies_to_statuses": list(statuses or []),
                "applies_to_priorities": list(priorities or []),
                "description": description,
            }
        )
    return {"sla_policy_version": version, "rules": rules}


def active_sla_rules_for_llm(
    sla_policy: dict[str, Any],
    *,
    tenant_id: str | None,
    region: str | None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Compact view of the SLA rules that apply to a tenant — tenant-specific
    rules plus region-generic fallbacks — for LLM analysis context."""
    rules = _parse_rules(sla_policy)
    today = today or datetime.now(timezone.utc).date()
    out: list[dict[str, Any]] = []
    for rule in rules:
        if not _rule_active(rule, today=today):
            continue
        if rule.tenant_id is not None and rule.tenant_id != tenant_id:
            continue
        if rule.tenant_id is None and (
            rule.region is None or (region is not None and rule.region != region)
        ):
            continue
        out.append(
            {
                "id": rule.id,
                "scope": "tenant" if rule.tenant_id else "region_fallback",
                "tenant_id": rule.tenant_id,
                "region": rule.region,
                "unanswered_hours": rule.unanswered_hours,
                "penalty_amount": str(rule.penalty_amount),
                "currency": rule.currency,
                "applies_to_statuses": list(rule.applies_to_statuses),
                "applies_to_priorities": list(rule.applies_to_priorities),
            }
        )
    return out


def create_sla_rule_db(
    dsn: str,
    *,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Insert a new row into ``app.sla_rule`` and return the reloaded policy.

    ``fields`` keys map to columns; ``rule_id`` is generated when absent.
    Raises ``KeyError`` on duplicate id, ``ValueError`` on bad input —
    FK violations surface as psycopg errors for the caller to translate.
    """
    import psycopg

    rule_id = fields.get("rule_id") or _generate_rule_id(fields)
    columns = [
        "rule_id",
        "tenant_id",
        "region",
        "unanswered_hours",
        "penalty_amount",
        "currency",
        "valid_from",
        "valid_to",
        "status",
        "applies_to_statuses",
        "applies_to_priorities",
        "description",
        "policy_version",
    ]
    values = {
        "rule_id": rule_id,
        "tenant_id": fields.get("tenant_id"),
        "region": fields.get("region"),
        "unanswered_hours": fields.get("unanswered_hours"),
        "penalty_amount": fields.get("penalty_amount"),
        "currency": fields.get("currency"),
        "valid_from": fields.get("valid_from") or None,
        "valid_to": fields.get("valid_to") or None,
        "status": fields.get("status") or "approved",
        "applies_to_statuses": list(
            fields.get("applies_to_statuses") or ["open"]
        ),
        "applies_to_priorities": list(fields.get("applies_to_priorities") or []),
        "description": fields.get("description"),
        "policy_version": fields.get("policy_version") or "sla-db-v1",
    }
    if values["tenant_id"] is None and values["region"] is None:
        raise ValueError("SLA rule needs a tenant_id or a region")

    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                f"INSERT INTO app.sla_rule ({col_list}) VALUES ({placeholders})",
                [values[c] for c in columns],
            )
        except psycopg.errors.UniqueViolation as exc:
            raise KeyError(f"sla rule {rule_id!r} already exists") from exc
        conn.commit()
    return load_sla_policy_from_db(dsn)


def _generate_rule_id(fields: dict[str, Any]) -> str:
    scope = str(fields.get("tenant_id") or fields.get("region") or "all")
    scope_slug = re.sub(r"[^a-z0-9]+", "", scope.lower())[:16] or "all"
    hours = str(fields.get("unanswered_hours", "0")).replace(".", "p")
    return f"sla-{scope_slug}-{hours}h-{secrets.token_hex(2)}"


_SLA_UPDATABLE_COLUMNS = frozenset(
    {
        "tenant_id",
        "region",
        "unanswered_hours",
        "penalty_amount",
        "currency",
        "valid_from",
        "valid_to",
        "status",
        "applies_to_statuses",
        "applies_to_priorities",
        "description",
    }
)


def update_sla_rule_db(
    dsn: str,
    *,
    rule_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Apply ``updates`` to ``app.sla_rule`` and return the reloaded policy."""
    import psycopg

    sets: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key not in _SLA_UPDATABLE_COLUMNS:
            continue
        if key == "applies_to_statuses" and value is not None:
            value = list(value)
        sets.append(f"{key} = %s")
        params.append(value)
    if not sets:
        raise ValueError("no updatable SLA rule fields supplied")
    sets.append("updated_at = now()")
    params.append(rule_id)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE app.sla_rule SET {', '.join(sets)}"
            " WHERE rule_id = %s AND deleted_at IS NULL",
            params,
        )
        if cur.rowcount == 0:
            raise KeyError(f"sla rule {rule_id!r} not found")
        conn.commit()
    return load_sla_policy_from_db(dsn)


def delete_sla_rule_db(dsn: str, *, rule_id: str) -> dict[str, Any]:
    """Soft-delete an SLA rule — sets ``deleted_at``; the row stays for audit
    and every read path (evaluation, LLM context, rule list) filters it out."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app.sla_rule SET deleted_at = now(), updated_at = now()"
            " WHERE rule_id = %s AND deleted_at IS NULL",
            (rule_id,),
        )
        if cur.rowcount == 0:
            raise KeyError(f"sla rule {rule_id!r} not found or already deleted")
        conn.commit()
    return load_sla_policy_from_db(dsn)


def sla_rule_status(rule: SlaRule, *, today: date | None = None) -> str:
    """Lifecycle status: expired/inactive windows win over stored status."""
    today = today or datetime.now(timezone.utc).date()
    if rule.valid_to is not None and rule.valid_to < today:
        return "expired"
    if rule.valid_from is not None and rule.valid_from > today:
        return "scheduled"
    return rule.status


def _rule_active(rule: SlaRule, *, today: date) -> bool:
    """Active = stored status is approved/in_progress AND inside validity window."""
    if rule.status not in {"approved", "in_progress"}:
        return False
    if rule.valid_from is not None and today < rule.valid_from:
        return False
    if rule.valid_to is not None and today > rule.valid_to:
        return False
    return True


def _parse_rules(data: dict[str, Any]) -> tuple[SlaRule, ...]:
    return tuple(SlaRule.model_validate(r) for r in data["rules"])


def evaluate_sla(
    *,
    rows: tuple[TicketCurrentRow, ...],
    sla_policy: dict[str, Any],
    tenant_regions: dict[str, str],
    ticket_priorities: dict[str, str] | None = None,
    now: datetime | None = None,
) -> SlaReport:
    """Map each ticket_current row to its governing SLA rule (if breached).

    Scope merge: tenant-specific rules apply on top of region-generic
    ones — a tenant rule wins only at the *same* threshold, so generic
    tiers still cover gaps the tenant hasn't contracted. Priority-scoped
    rules match only when the ticket's derived priority is listed.
    """
    rules = _parse_rules(sla_policy)
    now = now or datetime.now(timezone.utc)
    today = now.date()
    evaluations: list[SlaTicketEvaluation] = []
    unanswered = 0
    breached = 0
    penalties: dict[str, Decimal] = {}

    for row in rows:
        age_hours = max(
            0.0, (now - row.updated_at).total_seconds() / 3600.0
        )
        # SLA clock runs only while the ticket awaits OUR response.
        # pending_customer means we already replied — the ball is in the
        # customer's court, so no penalty accrues until they respond.
        is_unanswered = row.status == "open"
        if is_unanswered:
            unanswered += 1

        breach: SlaBreach | None = None
        upcoming: SlaBreach | None = None
        if is_unanswered:
            region = tenant_regions.get(row.tenant_id)
            priority = (ticket_priorities or {}).get(row.ticket_id)
            status_match = [
                r
                for r in rules
                if _rule_active(r, today=today)
                and (r.region is None or r.region == region)
                and row.status in r.applies_to_statuses
                and (
                    not r.applies_to_priorities
                    or (priority is not None and priority in r.applies_to_priorities)
                )
            ]
            tenant_rules = [
                r for r in status_match if r.tenant_id == row.tenant_id
            ]
            tenant_thresholds = {r.unanswered_hours for r in tenant_rules}
            candidates = tenant_rules + [
                r
                for r in status_match
                if r.tenant_id is None
                and r.unanswered_hours not in tenant_thresholds
            ]
            if candidates:
                # Highest threshold wins (most severe applicable tier).
                hit = [r for r in candidates if age_hours >= r.unanswered_hours]
                if hit:
                    best = max(hit, key=lambda r: r.unanswered_hours)
                    breach = SlaBreach(
                        sla_rule_id=best.id,
                        unanswered_hours=best.unanswered_hours,
                        age_hours=round(age_hours, 2),
                        penalty_amount=best.penalty_amount,
                        currency=best.currency,
                    )
                    breached += 1
                    penalties[best.currency] = (
                        penalties.get(best.currency, Decimal("0"))
                        + best.penalty_amount
                    )
                # Next tier not yet reached — shown so agents see the SLA clock.
                remaining = [
                    r for r in candidates if r.unanswered_hours > age_hours
                ]
                if remaining:
                    nxt = min(remaining, key=lambda r: r.unanswered_hours)
                    upcoming = SlaBreach(
                        sla_rule_id=nxt.id,
                        unanswered_hours=nxt.unanswered_hours,
                        age_hours=round(age_hours, 2),
                        penalty_amount=nxt.penalty_amount,
                        currency=nxt.currency,
                    )

        evaluations.append(
            SlaTicketEvaluation(
                tenant_id=row.tenant_id,
                ticket_id=row.ticket_id,
                status=row.status,
                queue_id=row.queue_id,
                rule_id=row.rule_id,
                age_hours=round(age_hours, 2),
                unanswered=is_unanswered,
                breach=breach,
                upcoming=upcoming,
                month=row.updated_at.strftime("%Y-%m"),
            )
        )

    return SlaReport(
        sla_policy_version=str(sla_policy.get("sla_policy_version", "sla-v1")),
        total_tickets=len(rows),
        unanswered_count=unanswered,
        breached_count=breached,
        penalties_by_currency={
            cur: str(amount) for cur, amount in sorted(penalties.items())
        },
        evaluations=tuple(evaluations),
    )
