"""PostgreSQL writers for canonical events, routing decisions, and ticket_current.

All tenant-owned writes run ``SET LOCAL app.tenant_id`` inside the same
transaction so FORCE ROW LEVEL SECURITY policies apply (migration 001).
Append-only tables reject UPDATE/DELETE by trigger; ``ticket_current`` is the
mutable projection and is upserted, never used as evidence.

Requires: psycopg (optional dependency groups ``dev``/``gcp``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

import psycopg

from payce_support.attachment_extract import AttachmentExtraction
from payce_support.canonical import (
    CanonicalAttachment,
    CanonicalExtraction,
    CanonicalTicketEvent,
    build_canonical_event,
)
from payce_support.models import (
    DecidedBy,
    Lane,
    ObjectManifest,
    RawIntakeEnvelope,
    RoutingDecision,
)
from payce_support.pipeline import TicketAnalysis, TicketCurrentRow, TicketReply


class PostgresRoutingDecisionRepository:
    """Append-only ``app.routing_decision`` writer/reader (tenant-scoped)."""

    def __init__(self, conninfo: str) -> None:
        self._psycopg = psycopg
        self._conninfo = conninfo
        # Decisions are persisted atomically by PostgresTicketPipelineStore
        # (the routing_decision FK requires the canonical row first, which
        # only exists inside record_ticket_event's transaction). append()
        # therefore buffers for supersede validation; get*() check pending
        # rows before querying the table.
        self._pending: dict[UUID, RoutingDecision] = {}

    def append(self, decision: RoutingDecision) -> None:
        if decision.decision_id in self._pending:
            raise ValueError("routing decisions are append-only")
        if decision.supersedes_decision_id is not None:
            superseded = self._pending.get(decision.supersedes_decision_id)
            if superseded is None:
                try:
                    self.get(decision.supersedes_decision_id,
                             tenant_id=decision.tenant_id)
                except KeyError as exc:
                    raise ValueError(
                        "superseded routing decision does not exist",
                    ) from exc
        self._pending[decision.decision_id] = decision

    def _drop_pending(self, decision_id: UUID) -> None:
        self._pending.pop(decision_id, None)

    def get(self, decision_id: UUID, *, tenant_id: str) -> RoutingDecision:
        pending = self._pending.get(decision_id)
        if pending is not None:
            if pending.tenant_id != tenant_id:
                raise KeyError(decision_id)
            return pending
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(_DECISION_SELECT + " WHERE tenant_id = %s AND decision_id = %s",
                                (tenant_id, decision_id))
                    row = cur.fetchone()
        if row is None:
            raise KeyError(decision_id)
        return _decision_from_row(row)

    def get_for_event(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None:
        for pending in reversed(tuple(self._pending.values())):
            if (
                pending.tenant_id == tenant_id
                and pending.event_id == event_id
                and pending.canonical_version == canonical_version
            ):
                return pending
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        _DECISION_SELECT
                        + """
                        WHERE tenant_id = %s AND event_id = %s
                          AND canonical_version = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, event_id, canonical_version),
                    )
                    row = cur.fetchone()
        return _decision_from_row(row) if row else None


_TICKET_CURRENT_SELECT = """
    SELECT tc.tenant_id, tc.ticket_id, tc.event_id, tc.canonical_version,
           tc.current_decision_id, tc.queue_id, tc.status, tc.updated_at,
           rd.rule_id, tc.human_intervened_at, tc.source,
           tc.contact_id, tc.symptom_fingerprint, tc.dedup_of, tc.text_fp,
           tc.csat_score, tc.csat_at, tc.csat_comment, tc.resolved_at
    FROM app.ticket_current tc
    LEFT JOIN app.routing_decision rd
      ON rd.tenant_id = tc.tenant_id
     AND rd.decision_id = tc.current_decision_id
"""


def _ticket_current_from_row(row) -> TicketCurrentRow:
    return TicketCurrentRow(
        tenant_id=row[0],
        ticket_id=row[1],
        event_id=row[2],
        canonical_version=int(row[3]),
        current_decision_id=row[4],
        queue_id=row[5],
        status=row[6],
        updated_at=row[7],
        rule_id=row[8],
        human_intervened_at=row[9],
        source=row[10],
        contact_id=row[11],
        symptom_fingerprint=row[12],
        dedup_of=row[13],
        text_fp=row[14],
        csat_score=row[15],
        csat_at=row[16],
        csat_comment=row[17],
        resolved_at=row[18],
    )


_DECISION_COLUMNS = """
                          tenant_id, decision_id, event_id, canonical_version,
                          input_fingerprint, policy_version, lane, queue_id,
                          auto_route, decided_by, rule_id, reason_codes,
                          confidence, supersedes_decision_id, created_at
"""

_DECISION_SELECT = f"SELECT {_DECISION_COLUMNS} FROM app.routing_decision"


def _decision_from_row(row) -> RoutingDecision:
    return RoutingDecision(
        decision_id=row[1],
        tenant_id=row[0],
        event_id=row[2],
        canonical_version=int(row[3]),
        input_fingerprint=row[4],
        policy_version=row[5],
        lane=Lane(row[6]),
        queue_id=row[7],
        auto_route=bool(row[8]),
        decided_by=DecidedBy(row[9]),
        rule_id=row[10],
        reason_codes=tuple(row[11]),
        confidence=float(row[12]),
        supersedes_decision_id=row[13],
        created_at=row[14],
    )


class PostgresTicketPipelineStore:
    """Single-transaction writer for the tenant-bound pipeline tables."""

    def __init__(self, conninfo: str) -> None:
        self._psycopg = psycopg
        self._conninfo = conninfo

    def record_ticket_event(
        self,
        *,
        envelope: RawIntakeEnvelope,
        raw_body_manifest: ObjectManifest,
        attachment_manifests: Sequence[ObjectManifest],
        extraction: CanonicalExtraction,
        decision: RoutingDecision,
        ticket_status: str = "open",
        updated_at: datetime | None = None,
    ) -> CanonicalTicketEvent:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (decision.tenant_id,),
                    )
                    canonical_version = self._latest_version(
                        cur,
                        tenant_id=decision.tenant_id,
                        event_id=envelope.event_id,
                    ) + 1
                    if decision.canonical_version != canonical_version:
                        raise ValueError(
                            "decision.canonical_version does not match "
                            "next canonical version",
                        )
                    if decision.event_id != envelope.event_id:
                        raise ValueError(
                            "decision.event_id does not match envelope event_id",
                        )

                    event = build_canonical_event(
                        envelope=envelope,
                        extraction=extraction,
                        canonical_version=canonical_version,
                    )
                    if event.ticket.tenant_id != decision.tenant_id:
                        raise ValueError(
                            "decision tenant does not match resolved envelope tenant",
                        )

                    self._insert_raw_event(cur, envelope=envelope, event=event,
                                           raw_body=raw_body_manifest)
                    self._insert_attachments(
                        cur,
                        envelope=envelope,
                        event=event,
                        manifests=attachment_manifests,
                    )
                    self._insert_canonical(cur, event)
                    self._insert_decision(cur, decision)
                    self._upsert_ticket_current(
                        cur,
                        event=event,
                        decision=decision,
                        status=ticket_status,
                        updated_at=updated_at,
                        source=envelope.source.value,
                    )
        return event

    def get_canonical(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> CanonicalTicketEvent | None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        SELECT canonical_json
                        FROM app.canonical_ticket_event
                        WHERE tenant_id = %s AND event_id = %s
                          AND canonical_version = %s
                        """,
                        (tenant_id, event_id, canonical_version),
                    )
                    row = cur.fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        # JSON round-trip yields str datetimes / list tuples; the model is
        # strict=True for intake, so re-validate non-strict on read.
        return CanonicalTicketEvent.model_validate(payload, strict=False)

    def latest_canonical_version(self, *, tenant_id: str, event_id: UUID) -> int:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    return self._latest_version(cur, tenant_id=tenant_id,
                                                event_id=event_id)

    def get_ticket_current(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> TicketCurrentRow | None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        _TICKET_CURRENT_SELECT
                        + " WHERE tc.tenant_id = %s AND tc.ticket_id = %s",
                        (tenant_id, ticket_id),
                    )
                    row = cur.fetchone()
        if row is None:
            return None
        return _ticket_current_from_row(row)

    def get_decision(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        canonical_version: int,
    ) -> RoutingDecision | None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        _DECISION_SELECT
                        + """
                        WHERE tenant_id = %s AND event_id = %s
                          AND canonical_version = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, event_id, canonical_version),
                    )
                    row = cur.fetchone()
        return _decision_from_row(row) if row else None

    def decisions_for_rows(self, rows) -> dict:
        """Batch-fetch current decisions for a page of ticket rows.

        Returns ``{(tenant_id, decision_id): RoutingDecision}`` — one query
        per tenant instead of one per ticket (dashboard N+1 fix).
        """
        ids_by_tenant: dict[str, list] = {}
        for r in rows:
            if r.current_decision_id is not None:
                ids_by_tenant.setdefault(r.tenant_id, []).append(
                    r.current_decision_id
                )
        out: dict = {}
        for tid, ids in ids_by_tenant.items():
            with self._psycopg.connect(self._conninfo) as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('app.tenant_id', %s, true)",
                            (tid,),
                        )
                        cur.execute(
                            _DECISION_SELECT
                            + " WHERE tenant_id = %s AND decision_id = ANY(%s)",
                            (tid, ids),
                        )
                        for row in cur.fetchall():
                            d = _decision_from_row(row)
                            out[(d.tenant_id, d.decision_id)] = d
        return out

    def signal_priorities_for_rows(self, rows) -> dict:
        """Batch-fetch latest ``llm_signals`` priority per ticket.

        Returns ``{(tenant_id, ticket_id): priority}`` — one DISTINCT ON
        query per tenant.
        """
        tids = {r.tenant_id for r in rows}
        out: dict = {}
        for tid in tids:
            with self._psycopg.connect(self._conninfo) as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('app.tenant_id', %s, true)",
                            (tid,),
                        )
                        cur.execute(
                            """
                            SELECT DISTINCT ON (ticket_id)
                                   ticket_id,
                                   payload_json #>> '{signals,priority}'
                            FROM app.ticket_analysis
                            WHERE tenant_id = %s AND kind = 'llm_signals'
                            ORDER BY ticket_id, created_at DESC, analysis_id DESC
                            """,
                            (tid,),
                        )
                        for ticket_id, pri in cur.fetchall():
                            if pri:
                                out[(tid, ticket_id)] = str(pri).upper()
        return out

    def list_ticket_current(
        self,
        *,
        tenant_id: str | None = None,
    ) -> tuple[TicketCurrentRow, ...]:
        # RLS has no cross-tenant reads: enumerate active tenants from
        # tenant_profile, then query each under its own app.tenant_id.
        if tenant_id is None:
            with self._psycopg.connect(self._conninfo) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tenant_id FROM app.tenant_profile WHERE active"
                    )
                    tenant_ids = [r[0] for r in cur.fetchall()]
            rows = [
                row
                for tid in tenant_ids
                for row in self.list_ticket_current(tenant_id=tid)
            ]
            rows.sort(key=lambda r: r.updated_at, reverse=True)
            return tuple(rows)
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        _TICKET_CURRENT_SELECT
                        + " WHERE tc.tenant_id = %s ORDER BY tc.updated_at DESC",
                        (tenant_id,),
                    )
                    rows = cur.fetchall()
        return tuple(_ticket_current_from_row(row) for row in rows)

    def close_stale_resolved(self, *, older_than_hours: float = 48.0) -> list[str]:
        """Close ``resolved`` tickets older than the threshold, per tenant
        (RLS requires a tenant context per UPDATE)."""
        closed: list[str] = []
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id FROM app.tenant_profile WHERE active"
                )
                tenant_ids = [r[0] for r in cur.fetchall()]
            for tid in tenant_ids:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('app.tenant_id', %s, true)",
                            (tid,),
                        )
                        cur.execute(
                            """
                            UPDATE app.ticket_current
                            SET status = 'closed', updated_at = now()
                            WHERE tenant_id = %s
                              AND status = 'resolved'
                              AND resolved_at < now() - (%s * interval '1 hour')
                            RETURNING ticket_id
                            """,
                            (tid, older_than_hours),
                        )
                        closed.extend(r[0] for r in cur.fetchall())
        return closed

    def update_ticket_status(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        status: str,
        queue_id: str | None = None,
        touch_human: bool = False,
    ) -> TicketCurrentRow | None:
        now = datetime.now(timezone.utc)
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        UPDATE app.ticket_current
                        SET status = %s,
                            queue_id = COALESCE(%s, queue_id),
                            updated_at = %s,
                            resolved_at = CASE
                              WHEN %s = 'resolved' THEN %s
                              WHEN %s IN ('open', 'in_progress', 'pending_customer') THEN NULL
                              ELSE resolved_at END,
                            human_intervened_at = CASE
                              WHEN %s THEN COALESCE(human_intervened_at, %s)
                              ELSE human_intervened_at END
                        WHERE tenant_id = %s AND ticket_id = %s
                        """,
                        (
                            status,
                            queue_id,
                            now,
                            status,
                            now,
                            status,
                            touch_human,
                            now,
                            tenant_id,
                            ticket_id,
                        ),
                    )
                    cur.execute(
                        _TICKET_CURRENT_SELECT
                        + " WHERE tc.tenant_id = %s AND tc.ticket_id = %s",
                        (tenant_id, ticket_id),
                    )
                    row = cur.fetchone()
        if row is None:
            return None
        return _ticket_current_from_row(row)

    def mark_ticket_dedup(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        contact_id: str | None,
        symptom_fingerprint: str | None,
        dedup_of: str | None = None,
        text_fp: str | None = None,
    ) -> None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        UPDATE app.ticket_current
                        SET contact_id = %s,
                            symptom_fingerprint = %s,
                            dedup_of = %s,
                            text_fp = %s
                        WHERE tenant_id = %s AND ticket_id = %s
                        """,
                        (contact_id, symptom_fingerprint, dedup_of, text_fp,
                         tenant_id, ticket_id),
                    )

    def find_open_duplicate(
        self,
        *,
        tenant_id: str,
        contact_id: str,
        symptom_fingerprint: str | None = None,
        text_fp: str | None = None,
        exclude_ticket_id: str,
        window_hours: float = 72.0,
    ) -> str | None:
        clauses = [
            "tenant_id = %s",
            "contact_id = %s",
            "dedup_of IS NULL",
            "ticket_id <> %s",
            "status NOT IN ('resolved', 'closed')",
            "updated_at > now() - (%s * interval '1 hour')",
        ]
        params: list = [tenant_id, contact_id, exclude_ticket_id, window_hours]
        fp_clauses = []
        if symptom_fingerprint:
            fp_clauses.append("symptom_fingerprint = %s")
            params.append(symptom_fingerprint)
        if text_fp:
            fp_clauses.append("text_fp = %s")
            params.append(text_fp)
        if not fp_clauses:
            return None
        clauses.append("(" + " OR ".join(fp_clauses) + ")")
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        "SELECT ticket_id FROM app.ticket_current WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY updated_at DESC LIMIT 1",
                        tuple(params),
                    )
                    row = cur.fetchone()
        return row[0] if row else None

    def add_reply(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        author: str,
        body: str,
    ) -> TicketReply | None:
        reply = TicketReply(
            reply_id=uuid4(),
            author=author,
            body=body,
            created_at=datetime.now(timezone.utc),
        )
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        INSERT INTO app.ticket_reply (
                          tenant_id, reply_id, ticket_id, author, body, created_at
                        )
                        SELECT %s, %s, %s, %s, %s, %s
                        WHERE EXISTS (
                          SELECT 1 FROM app.ticket_current
                          WHERE tenant_id = %s AND ticket_id = %s
                        )
                        """,
                        (
                            tenant_id, reply.reply_id, ticket_id,
                            author, body, reply.created_at,
                            tenant_id, ticket_id,
                        ),
                    )
                    if cur.rowcount == 0:
                        return None
                    if author in {"agent", "customer"}:
                        cur.execute(
                            """
                            UPDATE app.ticket_current
                            SET human_intervened_at = COALESCE(
                              human_intervened_at, %s
                            )
                            WHERE tenant_id = %s AND ticket_id = %s
                            """,
                            (reply.created_at, tenant_id, ticket_id),
                        )
        return reply

    def list_replies(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
    ) -> tuple[TicketReply, ...]:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        SELECT reply_id, author, body, created_at
                        FROM app.ticket_reply
                        WHERE tenant_id = %s AND ticket_id = %s
                        ORDER BY created_at, reply_id
                        """,
                        (tenant_id, ticket_id),
                    )
                    rows = cur.fetchall()
        return tuple(
            TicketReply(
                reply_id=row[0], author=row[1], body=row[2], created_at=row[3]
            )
            for row in rows
        )

    def record_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str,
        canonical_version: int,
        payload: dict,
        model_id: str | None = None,
    ) -> TicketAnalysis | None:
        analysis = TicketAnalysis(
            analysis_id=uuid4(),
            kind=kind,
            canonical_version=canonical_version,
            payload=payload,
            model_id=model_id,
            created_at=datetime.now(timezone.utc),
        )
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        INSERT INTO app.ticket_analysis (
                          tenant_id, analysis_id, ticket_id, canonical_version,
                          kind, payload_json, model_id, created_at
                        )
                        SELECT %s, %s, %s, %s, %s, %s, %s, %s
                        WHERE EXISTS (
                          SELECT 1 FROM app.ticket_current
                          WHERE tenant_id = %s AND ticket_id = %s
                        )
                        """,
                        (
                            tenant_id, analysis.analysis_id, ticket_id,
                            canonical_version, kind,
                            json.dumps(payload), model_id,
                            analysis.created_at,
                            tenant_id, ticket_id,
                        ),
                    )
                    if cur.rowcount == 0:
                        return None
                    self._record_llm_usage(
                        cur,
                        tenant_id=tenant_id,
                        ticket_id=ticket_id,
                        analysis=analysis,
                        model_id=model_id,
                        payload=payload,
                    )
        return analysis

    @staticmethod
    def _record_llm_usage(
        cur: Any,
        *,
        tenant_id: str,
        ticket_id: str,
        analysis: TicketAnalysis,
        model_id: str | None,
        payload: dict,
    ) -> None:
        """Append one ``app.llm_usage`` row when the analysis payload carries
        token counts — same transaction as the analysis, so usage can never
        be lost or double-counted."""
        try:
            input_tokens = int(payload.get("input_tokens") or 0)
            output_tokens = int(payload.get("output_tokens") or 0)
        except (TypeError, ValueError):
            input_tokens = output_tokens = 0
        effective_model = model_id or payload.get("model_id")
        if input_tokens <= 0 and output_tokens <= 0 and not effective_model:
            return
        from payce_support.llm_pricing import estimate_cost_usd

        cur.execute(
            """
            INSERT INTO app.llm_usage (
              usage_id, tenant_id, ticket_id, analysis_id, kind, model_id,
              input_tokens, output_tokens, cost_usd, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                tenant_id,
                ticket_id,
                analysis.analysis_id,
                analysis.kind,
                effective_model,
                max(0, input_tokens),
                max(0, output_tokens),
                estimate_cost_usd(
                    effective_model,
                    input_tokens=max(0, input_tokens),
                    output_tokens=max(0, output_tokens),
                ),
                analysis.created_at,
            ),
        )

    def latest_analysis(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        kind: str | None = None,
    ) -> TicketAnalysis | None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    if kind is None:
                        cur.execute(
                            """
                            SELECT analysis_id, kind, canonical_version,
                                   payload_json, model_id, created_at
                            FROM app.ticket_analysis
                            WHERE tenant_id = %s AND ticket_id = %s
                            ORDER BY created_at DESC, analysis_id DESC
                            LIMIT 1
                            """,
                            (tenant_id, ticket_id),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT analysis_id, kind, canonical_version,
                                   payload_json, model_id, created_at
                            FROM app.ticket_analysis
                            WHERE tenant_id = %s AND ticket_id = %s
                              AND kind = %s
                            ORDER BY created_at DESC, analysis_id DESC
                            LIMIT 1
                            """,
                            (tenant_id, ticket_id, kind),
                        )
                    row = cur.fetchone()
        if row is None:
            return None
        payload = row[3]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return TicketAnalysis(
            analysis_id=row[0],
            kind=row[1],
            canonical_version=row[2],
            payload=payload,
            model_id=row[4],
            created_at=row[5],
        )

    def record_attachment_extractions(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        extractions: dict[UUID, AttachmentExtraction],
    ) -> None:
        """Append modality-processor output to ``app.attachment_processing``.

        The manifest itself is append-only; each processor run is a new
        row keyed by (tenant, attachment, manifest_version, processor).
        """
        if not extractions:
            return
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (tenant_id,),
                    )
                    for attachment_id, ext in extractions.items():
                        cur.execute(
                            """
                            INSERT INTO app.attachment_processing (
                              tenant_id, attachment_id, manifest_version,
                              event_id, processor, filename,
                              extracted_text, extracted_summary
                            )
                            SELECT tenant_id, attachment_id, manifest_version,
                                   %s, %s, %s, %s, %s
                            FROM app.attachment_manifest
                            WHERE tenant_id = %s AND event_id = %s
                              AND attachment_id = %s
                            ON CONFLICT DO NOTHING
                            """,
                            (
                                event_id,
                                ext.processor,
                                ext.filename or None,
                                ext.extracted_text or None,
                                ext.summary or None,
                                tenant_id,
                                event_id,
                                attachment_id,
                            ),
                        )

    def list_attachments(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
    ) -> tuple[dict, ...]:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (tenant_id,),
                    )
                    cur.execute(
                        """
                        SELECT m.attachment_id, m.original_filename,
                               m.detected_media_type, m.size_bytes, m.sha256,
                               m.scan_status, m.pii_class, m.processing_status,
                               m.object_id,
                               p.processor, p.extracted_summary, p.extracted_text
                        FROM app.attachment_manifest m
                        LEFT JOIN LATERAL (
                          SELECT processor, extracted_summary, extracted_text
                          FROM app.attachment_processing p
                          WHERE p.tenant_id = m.tenant_id
                            AND p.attachment_id = m.attachment_id
                            AND p.manifest_version = m.manifest_version
                          ORDER BY p.processed_at DESC
                          LIMIT 1
                        ) p ON true
                        WHERE m.tenant_id = %s AND m.event_id = %s
                        ORDER BY m.created_at, m.attachment_id
                        """,
                        (tenant_id, event_id),
                    )
                    rows = cur.fetchall()
        return tuple(
            {
                "attachment_id": str(r[0]),
                "filename": r[1],
                "detected_media_type": r[2],
                "size_bytes": r[3],
                "sha256": r[4],
                "scan_status": r[5],
                "pii_class": r[6],
                "processing_status": r[7],
                "object_id": str(r[8]) if r[8] else None,
                "processor": r[9],
                "extracted_summary": r[10],
                "extracted_text": r[11],
            }
            for r in rows
        )

    # -- internal helpers -------------------------------------------------

    @staticmethod
    def _latest_version(cur: psycopg.Cursor, *, tenant_id: str,
                        event_id: UUID) -> int:
        cur.execute(
            """
            SELECT COALESCE(MAX(canonical_version), 0)
            FROM app.canonical_ticket_event
            WHERE tenant_id = %s AND event_id = %s
            """,
            (tenant_id, event_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _insert_raw_event(cur: psycopg.Cursor, *, envelope: RawIntakeEnvelope,
                          event: CanonicalTicketEvent,
                          raw_body: ObjectManifest) -> None:
        if raw_body.tenant_id != event.ticket.tenant_id:
            raise ValueError("raw body manifest tenant does not match event tenant")
        if raw_body.event_id != envelope.event_id:
            raise ValueError("raw body manifest event does not match envelope")
        cur.execute(
            """
            INSERT INTO app.raw_event_manifest (
              tenant_id, event_id, region, source, source_event_id,
              object_id, object_path, object_generation, sha256, size_bytes,
              received_at, quarantine_event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (tenant_id, event_id) DO NOTHING
            """,
            (
                event.ticket.tenant_id,
                envelope.event_id,
                event.ticket.region.value,
                envelope.source.value,
                envelope.source_event_id,
                envelope.raw_body_ref.object_id,
                raw_body.object_path,
                int(raw_body.generation),
                envelope.raw_body_ref.sha256,
                raw_body.size_bytes,
                envelope.received_at,
            ),
        )

    @staticmethod
    def _insert_attachments(
        cur: psycopg.Cursor,
        *,
        envelope: RawIntakeEnvelope,
        event: CanonicalTicketEvent,
        manifests: Sequence[ObjectManifest],
    ) -> None:
        canonical_by_id = {a.attachment_id: a for a in event.attachments}
        if len(manifests) != len(canonical_by_id):
            raise ValueError(
                "attachment manifest count does not match canonical event",
            )
        receipts = {r.attachment_id: r for r in envelope.attachment_receipts}
        for manifest in manifests:
            attachment = canonical_by_id.get(manifest.attachment_id)
            if attachment is None:
                raise ValueError("attachment manifest has no canonical attachment")
            receipt = receipts.get(manifest.attachment_id)
            PostgresTicketPipelineStore._insert_one_attachment(
                cur,
                envelope=envelope,
                event=event,
                manifest=manifest,
                attachment=attachment,
                original_filename=(
                    receipt.original_filename if receipt is not None else None
                ),
            )

    @staticmethod
    def _insert_one_attachment(
        cur: psycopg.Cursor,
        *,
        envelope: RawIntakeEnvelope,
        event: CanonicalTicketEvent,
        manifest: ObjectManifest,
        attachment: CanonicalAttachment,
        original_filename: str | None = None,
    ) -> None:
        if manifest.tenant_id != event.ticket.tenant_id:
            raise ValueError("attachment manifest tenant does not match event tenant")
        cur.execute(
            """
            INSERT INTO app.attachment_manifest (
              tenant_id, attachment_id, event_id, manifest_version, region,
              object_id, object_path, object_generation, sha256, size_bytes,
              detected_media_type, scan_status, pii_class, processing_status,
              original_filename
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, attachment_id, manifest_version) DO NOTHING
            """,
            (
                event.ticket.tenant_id,
                attachment.attachment_id,
                envelope.event_id,
                attachment.manifest_version,
                event.ticket.region.value,
                manifest.object_id,
                manifest.object_path,
                int(manifest.generation),
                manifest.sha256,
                manifest.size_bytes,
                attachment.detected_media_type,
                attachment.scan_status.value,
                attachment.pii_class.value,
                attachment.processing_status.value,
                original_filename,
            ),
        )

    @staticmethod
    def _insert_canonical(cur: psycopg.Cursor, event: CanonicalTicketEvent) -> None:
        cur.execute(
            """
            INSERT INTO app.canonical_ticket_event (
              tenant_id, event_id, canonical_version, ticket_id, case_id,
              region, raw_sha256, canonical_json, normalizer_version,
              dlp_policy_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                event.ticket.tenant_id,
                event.event_id,
                event.canonical_version,
                event.ticket.ticket_id,
                event.ticket.case_id,
                event.ticket.region.value,
                event.raw_ref.sha256,
                event.model_dump_json(by_alias=True),
                event.provenance.normalizer_version,
                event.provenance.dlp_policy_version,
            ),
        )

    @staticmethod
    def _insert_decision(cur: psycopg.Cursor, decision: RoutingDecision) -> None:
        cur.execute(
            """
            INSERT INTO app.routing_decision (
              tenant_id, decision_id, event_id, canonical_version,
              input_fingerprint, policy_version, lane, queue_id,
              auto_route, decided_by, rule_id, reason_codes,
              confidence, supersedes_decision_id, created_at
            )
            VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s
            )
            """,
            (
                decision.tenant_id,
                decision.decision_id,
                decision.event_id,
                decision.canonical_version,
                decision.input_fingerprint,
                decision.policy_version,
                decision.lane.value,
                decision.queue_id,
                decision.auto_route,
                decision.decided_by.value,
                decision.rule_id,
                list(decision.reason_codes),
                decision.confidence,
                decision.supersedes_decision_id,
                decision.created_at,
            ),
        )

    @staticmethod
    def _upsert_ticket_current(
        cur: psycopg.Cursor,
        *,
        event: CanonicalTicketEvent,
        decision: RoutingDecision,
        status: str,
        updated_at: datetime | None = None,
        source: str | None = None,
    ) -> None:
        cur.execute(
            """
            INSERT INTO app.ticket_current (
              tenant_id, ticket_id, event_id, canonical_version,
              current_decision_id, queue_id, status, updated_at, source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, ticket_id) DO UPDATE SET
              event_id = EXCLUDED.event_id,
              canonical_version = EXCLUDED.canonical_version,
              current_decision_id = EXCLUDED.current_decision_id,
              queue_id = EXCLUDED.queue_id,
              status = EXCLUDED.status,
              updated_at = EXCLUDED.updated_at,
              source = COALESCE(app.ticket_current.source, EXCLUDED.source)
            """,
            (
                event.ticket.tenant_id,
                event.ticket.ticket_id,
                event.event_id,
                event.canonical_version,
                decision.decision_id,
                decision.queue_id,
                status,
                updated_at or datetime.now(timezone.utc),
                source,
            ),
        )
