"""Dev dashboard: ticket_current projections."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import (
    DashboardAnalyticsResponse,
    DashboardSummaryResponse,
    DashboardTicket,
    DashboardTicketDetail,
    KbArticleCreateRequest,
    SlaRuleCreateRequest,
    SlaRuleUpdateRequest,
    TicketDraftResponse,
    TicketReplyRequest,
    TicketStatusUpdateRequest,
)
from payce_support.answer_gate import AnswerVerdict, classify_answerability
from payce_support.models import AccessContext, Lane, Region
from payce_support.rag import RagIntent, RagRequest
from payce_support.pipeline import TicketCurrentRow
from payce_support.sla import (
    SlaReport,
    SlaRule,
    evaluate_sla,
    sla_rule_status,
)

router = APIRouter(
    prefix="/v1",
    tags=["dashboard"],
    dependencies=[Depends(require_authenticated_v1)],
)


def _priority_for(state, row: TicketCurrentRow) -> str | None:
    """Latest saved LLM-signal priority for the ticket row (read-only)."""
    analysis = state.pipeline.latest_analysis(
        tenant_id=row.tenant_id,
        ticket_id=row.ticket_id,
        kind="llm_signals",
    )
    if analysis is None:
        return None
    signals = (analysis.payload or {}).get("signals") or {}
    pri = signals.get("priority")
    return str(pri).upper() if pri else None


def _ticket_priorities(state, rows) -> dict[str, str]:
    """ticket_id → derived priority (lowercase) for SLA priority scoping."""
    batch = _priorities_for_rows(state, rows)
    return {
        row.ticket_id: batch[(row.tenant_id, row.ticket_id)].lower()
        for row in rows
        if (row.tenant_id, row.ticket_id) in batch
    }


_PRIORITY_UNSET = object()


def _ticket(state, row: TicketCurrentRow, decision=None, priority=_PRIORITY_UNSET) -> DashboardTicket:
    return DashboardTicket(
        tenant_id=row.tenant_id,
        ticket_id=row.ticket_id,
        status=row.status,
        queue_id=row.queue_id,
        rule_id=row.rule_id,
        lane=decision.lane.value if decision else None,
        auto_route=decision.auto_route if decision else None,
        decided_by=decision.decided_by.value if decision else None,
        canonical_version=row.canonical_version,
        event_id=str(row.event_id),
        updated_at=row.updated_at,
        human_intervened_at=row.human_intervened_at,
        source=row.source,
        priority=(
            _priority_for(state, row) if priority is _PRIORITY_UNSET else priority
        ),
        dedup_of=row.dedup_of,
        csat_score=row.csat_score,
        csat_at=row.csat_at,
        csat_comment=row.csat_comment,
    )


def _decision_for(state, row: TicketCurrentRow):
    return state.pipeline.get_decision(
        tenant_id=row.tenant_id,
        event_id=row.event_id,
        canonical_version=row.canonical_version,
    )


def _decisions_for_rows(state, rows) -> dict:
    """``{(tenant_id, decision_id): decision}`` — batched when the store
    supports it, per-row fallback otherwise."""
    fn = getattr(state.pipeline, "decisions_for_rows", None)
    if fn is not None:
        return fn(rows)
    return {
        (r.tenant_id, r.current_decision_id): _decision_for(state, r)
        for r in rows
        if r.current_decision_id is not None
    }


def _priorities_for_rows(state, rows) -> dict:
    """``{(tenant_id, ticket_id): PRIORITY}`` — batched when supported."""
    fn = getattr(state.pipeline, "signal_priorities_for_rows", None)
    if fn is not None:
        return fn(rows)
    return {
        (r.tenant_id, r.ticket_id): _priority_for(state, r)
        for r in rows
        if _priority_for(state, r)
    }


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    state: AppStateDep,
    tenant_id: str | None = Query(default=None, pattern=r"^C-[0-9]{4,}$"),
) -> DashboardSummaryResponse:
    try:
        rows = state.pipeline.list_ticket_current(tenant_id=tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    by_status = Counter(row.status for row in rows)
    by_queue = Counter(row.queue_id or "unassigned" for row in rows)
    by_channel = Counter(row.source or "unknown" for row in rows)
    decisions = _decisions_for_rows(state, rows)
    priorities = _priorities_for_rows(state, rows)
    return DashboardSummaryResponse(
        total=len(rows),
        by_status=dict(by_status),
        by_queue=dict(by_queue),
        by_channel=dict(by_channel),
        auto_routed=sum(1 for row in rows if row.queue_id is not None),
        tickets=tuple(
            _ticket(
                state,
                row,
                decisions.get((row.tenant_id, row.current_decision_id)),
                priorities.get((row.tenant_id, row.ticket_id)),
            )
            for row in rows
        ),
    )


_BOT_LANES = {"auto_answer", "draft_for_agent"}
_RESOLVED_STATUSES = {"resolved", "closed"}


def _group_by(rows, key):
    groups: dict = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return groups


def _sla_by_tenant(evaluations) -> dict:
    """Per-tenant SLA rollup: unanswered/breached counts + penalty totals."""
    tenants: dict[str, dict] = {}
    for e in evaluations:
        t = tenants.setdefault(
            e.tenant_id,
            {"tenant_id": e.tenant_id, "unanswered": 0, "breached": 0,
             "penalties": {}},
        )
        if e.unanswered:
            t["unanswered"] += 1
        if e.breach is not None:
            t["breached"] += 1
            cur = e.breach.currency
            try:
                t["penalties"][cur] = round(
                    t["penalties"].get(cur, 0.0) + float(e.breach.penalty_amount), 2
                )
            except (TypeError, ValueError):
                pass
    return sorted(tenants.values(), key=lambda t: -t["breached"])


@router.get("/dashboard/analytics", response_model=DashboardAnalyticsResponse)
def dashboard_analytics(
    state: AppStateDep,
    tenant_id: str | None = Query(default=None, pattern=r"^C-[0-9]{4,}$"),
) -> DashboardAnalyticsResponse:
    """Aggregate analytics for the Home dashboard: KPIs, breakdowns by
    status/queue/channel/day, SLA risk, and recent tickets."""
    try:
        rows = state.pipeline.list_ticket_current(tenant_id=tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _batch_decisions = _decisions_for_rows(state, rows)
    pairs = [
        (
            row,
            _batch_decisions.get((row.tenant_id, row.current_decision_id)),
        )
        for row in rows
    ]
    lane_of = {
        (row.tenant_id, row.ticket_id): (d.lane.value if d is not None else None)
        for row, d in pairs
    }
    decision_of = {(row.tenant_id, row.ticket_id): d for row, d in pairs}

    by_status = Counter(row.status for row in rows)
    by_queue = Counter(row.queue_id or "unassigned" for row in rows)
    by_channel = Counter(row.source or "unknown" for row in rows)

    now = datetime.now(timezone.utc)
    day_counts: Counter[str] = Counter()
    for row in rows:
        ts = row.updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        day_counts[ts.date().isoformat()] += 1
    by_day = tuple(
        {"date": d.date().isoformat(), "count": day_counts.get(d.date().isoformat(), 0)}
        for d in (now - timedelta(days=i) for i in range(13, -1, -1))
    )

    resolved = sum(by_status.get(s, 0) for s in _RESOLVED_STATUSES)
    bot_solved = sum(
        1
        for row in rows
        if row.status in _RESOLVED_STATUSES
        and lane_of.get((row.tenant_id, row.ticket_id)) == "auto_answer"
    )
    human_solved = resolved - bot_solved
    bot_involved = sum(1 for lane in lane_of.values() if lane in _BOT_LANES)
    automation_rate = round(bot_involved / len(rows), 3) if rows else 0.0

    tenant_regions = {
        tid: ctx.region for tid, ctx in state.tenant_contexts.items()
    }
    sla_report = evaluate_sla(
        rows=rows,
        sla_policy=state.sla_policy,
        tenant_regions=tenant_regions,
        ticket_priorities=_ticket_priorities(state, rows),
    )
    at_risk = sorted(
        sla_report.evaluations,
        key=lambda e: (e.unanswered, e.age_hours),
        reverse=True,
    )[:8]
    sla_by_key = {
        (e.tenant_id, e.ticket_id): e for e in sla_report.evaluations
    }

    return DashboardAnalyticsResponse(
        generated_at=now,
        total=len(rows),
        open_count=sum(
            by_status.get(s, 0)
            for s in ("open", "in_progress", "pending_customer")
        ),
        pending_agent_count=sum(
            1
            for row in rows
            if row.status in {"open", "in_progress", "pending_customer"}
            and lane_of.get((row.tenant_id, row.ticket_id)) != "auto_answer"
        ),
        resolved_count=by_status.get("resolved", 0),
        closed_count=by_status.get("closed", 0),
        bot_solved_count=bot_solved,
        human_solved_count=human_solved,
        automation_rate=automation_rate,
        by_status=dict(by_status),
        by_queue=dict(by_queue),
        by_channel=dict(by_channel),
        channel_stats=tuple(
            sorted(
                (
                    {
                        "channel": ch,
                        "total": len(group),
                        "open": sum(1 for r in group if r.status in _OPEN_STATUSES),
                        "assigned": sum(
                            1
                            for r in group
                            if r.status in _OPEN_STATUSES
                            and lane_of.get((r.tenant_id, r.ticket_id))
                            != "auto_answer"
                        ),
                        "resolved": sum(
                            1 for r in group if r.status in _RESOLVED_STATUSES
                        ),
                        "bot_solved": sum(
                            1
                            for r in group
                            if r.status in _RESOLVED_STATUSES
                            and lane_of.get((r.tenant_id, r.ticket_id))
                            == "auto_answer"
                        ),
                        "sla_breached": sum(
                            1
                            for r in group
                            if (e := sla_by_key.get((r.tenant_id, r.ticket_id)))
                            is not None
                            and e.breach is not None
                        ),
                    }
                    for ch, group in _group_by(rows, lambda r: r.source or "unknown").items()
                ),
                key=lambda c: c["total"],
                reverse=True,
            )
        ),
        by_day=by_day,
        sla_breached_count=sla_report.breached_count,
        sla_unanswered_count=sla_report.unanswered_count,
        sla_attainment_pct=(
            round(
                (len(rows) - sla_report.breached_count) / len(rows) * 100, 1
            )
            if rows
            else None
        ),
        sla_penalties_by_currency=sla_report.penalties_by_currency,
        sla_by_tenant=tuple(
            _sla_by_tenant(sla_report.evaluations)
        ),
        sla_at_risk=tuple(
            {
                "tenant_id": e.tenant_id,
                "ticket_id": e.ticket_id,
                "status": e.status,
                "queue_id": e.queue_id,
                "rule_id": e.rule_id,
                "age_hours": round(e.age_hours, 1),
                "unanswered": e.unanswered,
                "breach": (
                    e.breach.model_dump(mode="json") if e.breach else None
                ),
            }
            for e in at_risk
        ),
        recent=tuple(
            _ticket(state, row, decision_of[(row.tenant_id, row.ticket_id)])
            for row in rows[:3]
        ),
    )


@router.patch(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/status",
    response_model=DashboardTicket,
)
def update_ticket_status(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
    body: TicketStatusUpdateRequest,
) -> DashboardTicket:
    try:
        row = state.pipeline.update_ticket_status(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status=body.status,
            queue_id=body.queue_id,
            touch_human=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return _ticket(state, row, _decision_for(state, row))


@router.get("/dashboard/kb/articles", response_model=dict)
def dashboard_kb_articles(state: AppStateDep) -> dict:
    """List KB articles backing RAG retrieval (published + drafts)."""
    if state.kb_store is None:
        raise HTTPException(status_code=503, detail="KB store not configured")
    articles = state.kb_store.articles()
    # Which retrieval chunks each article produced (region fan-out).
    chunk_ids: dict[str, list[str]] = {}
    for chunk in state.kb_store.chunks():
        base = chunk.chunk_id.rsplit(":", 1)[0]
        chunk_ids.setdefault(base, []).append(chunk.chunk_id)
    return {
        "snapshot_version": state.kb_store.snapshot_version,
        "articles": [
            {
                **art,
                "retrievable": str(art.get("status", "Published")).strip().lower()
                in {"", "published"},
                "chunk_ids": chunk_ids.get(str(art.get("id", "")), []),
            }
            for art in articles
        ],
    }


def _kb_article_payload(body: KbArticleCreateRequest) -> dict:
    article = body.model_dump(mode="json", exclude_none=True)
    if isinstance(article.get("years"), list):
        article["years"] = [int(y) for y in article["years"]]
    if article.get("status"):
        article["status"] = article["status"].capitalize()
    return article


@router.post("/dashboard/kb/articles", response_model=dict)
def create_kb_article(
    state: AppStateDep,
    body: KbArticleCreateRequest,
) -> dict:
    """Add a KB article — persisted to ``app.kb_article`` (Postgres) when
    configured, else the JSON file; RAG store hot-reloads either way."""
    if state.kb_store is None:
        raise HTTPException(status_code=503, detail="KB store not configured")
    article = _kb_article_payload(body)
    if any(a.get("id") == article["id"] for a in state.kb_store.articles()):
        raise HTTPException(status_code=409, detail=f"KB article {article['id']!r} already exists")
    try:
        if state.settings.postgres_dsn:
            from payce_support.kb_local import upsert_kb_article_db

            upsert_kb_article_db(state.settings.postgres_dsn, article)
        else:
            state.kb_store.add_article(article)
        state.kb_store.reload()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc).splitlines()[0]) from exc
    return {"article": article, "snapshot_version": state.kb_store.snapshot_version}


@router.put("/dashboard/kb/articles/{article_id}", response_model=dict)
def update_kb_article(
    state: AppStateDep,
    article_id: str,
    body: KbArticleCreateRequest,
) -> dict:
    """Edit a KB article — upsert into ``app.kb_article`` / JSON and
    hot-reload the RAG store.

    Published articles are read-only: the only permitted change is a
    status transition to Draft or Archived (unpublish). All content
    fields are preserved from the stored record.
    """
    if state.kb_store is None:
        raise HTTPException(status_code=503, detail="KB store not configured")
    existing = next(
        (a for a in state.kb_store.articles() if a.get("id") == article_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="KB article not found")
    article = _kb_article_payload(body)
    article["id"] = article_id
    if str(existing.get("status") or "Published").lower() == "published":
        new_status = str(article.get("status") or "").lower()
        if new_status not in {"draft", "archived"}:
            raise HTTPException(
                status_code=409,
                detail="Published KB articles are read-only — set status "
                "to Draft or Archived to unpublish first.",
            )
        # Freeze content: keep every stored field, flip status only.
        frozen = dict(existing)
        frozen["status"] = str(article["status"]).capitalize()
        frozen["id"] = article_id
        article = frozen
    try:
        if state.settings.postgres_dsn:
            from payce_support.kb_local import upsert_kb_article_db

            upsert_kb_article_db(state.settings.postgres_dsn, article)
        else:
            state.kb_store.update_article(article_id, article)
        state.kb_store.reload()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc).splitlines()[0]) from exc
    return {"article": article, "snapshot_version": state.kb_store.snapshot_version}


@router.post("/dashboard/kb/rebuild", response_model=dict)
def rebuild_kb_index(state: AppStateDep) -> dict:
    """Force a full RAG index rebuild — reloads articles from the source
    of truth (Postgres/JSON) and regenerates retrieval chunks."""
    if state.kb_store is None:
        raise HTTPException(status_code=503, detail="KB store not configured")
    state.kb_store.reload()
    return {
        "snapshot_version": state.kb_store.snapshot_version,
        "articles": len(state.kb_store.articles()),
        "chunks": len(state.kb_store.chunks()),
    }


@router.get("/dashboard/sla", response_model=SlaReport)
def dashboard_sla(
    state: AppStateDep,
    tenant_id: str | None = Query(default=None, pattern=r"^C-[0-9]{4,}$"),
) -> SlaReport:
    """Evaluate SLA/penalty rules against current ticket ages."""
    try:
        rows = state.pipeline.list_ticket_current(tenant_id=tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tenant_regions = {
        tid: ctx.region for tid, ctx in state.tenant_contexts.items()
    }
    return evaluate_sla(
        rows=rows,
        sla_policy=state.sla_policy,
        tenant_regions=tenant_regions,
        ticket_priorities=_ticket_priorities(state, rows),
    )


@router.get("/dashboard/sla/quarters", response_model=dict)
def dashboard_sla_quarters(
    state: AppStateDep,
    tenant_id: str | None = Query(default=None, pattern=r"^C-[0-9]{4,}$"),
    year: int | None = Query(default=None, ge=2000, le=2100),
) -> dict:
    """SLA exposure grouped by calendar quarter (Q1–Q4) of ticket
    ``updated_at`` for a selected year — per-tenant and overall totals of
    tickets, currently-unanswered, currently-breached, and penalty exposure.
    Breach/penalty are *current-state* evaluations bucketed by the quarter
    the ticket was last touched."""
    try:
        rows = state.pipeline.list_ticket_current(tenant_id=tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tenant_regions = {
        tid: ctx.region for tid, ctx in state.tenant_contexts.items()
    }
    report = evaluate_sla(
        rows=rows,
        sla_policy=state.sla_policy,
        tenant_regions=tenant_regions,
        ticket_priorities=_ticket_priorities(state, rows),
    )
    breach_by_ticket = {
        (e.tenant_id, e.ticket_id): e for e in report.evaluations
    }

    years = sorted({r.updated_at.year for r in rows})
    sel_year = year or (max(years) if years else datetime.now(timezone.utc).year)

    def _bucket() -> dict:
        return {
            "tickets": 0, "open": 0, "unanswered": 0,
            "breached": 0, "penalties": {},
        }

    quarters: dict[tuple[str, int], dict] = {}
    overall: dict[int, dict] = {q: _bucket() for q in range(1, 5)}
    for r in rows:
        if r.updated_at.year != sel_year:
            continue
        q = (r.updated_at.month - 1) // 3 + 1
        e = breach_by_ticket.get((r.tenant_id, r.ticket_id))
        for b in (overall[q], quarters.setdefault((r.tenant_id, q), _bucket())):
            b["tickets"] += 1
            if r.status in {"open", "in_progress", "pending_customer"}:
                b["open"] += 1
            if e is not None:
                if e.unanswered:
                    b["unanswered"] += 1
                if e.breach is not None:
                    b["breached"] += 1
                    cur = e.breach.currency
                    b["penalties"][cur] = round(
                        b["penalties"].get(cur, 0.0)
                        + float(e.breach.penalty_amount), 2
                    )

    def _qrow(q: int, b: dict, tid: str | None = None) -> dict:
        return {
            "tenant_id": tid,
            "quarter": f"Q{q}",
            "year": sel_year,
            **b,
        }

    return {
        "year": sel_year,
        "years": years,
        "quarters": [
            _qrow(q, overall[q]) for q in range(1, 5)
        ],
        "by_tenant": [
            _qrow(q, b, tid)
            for (tid, q), b in sorted(quarters.items())
        ],
    }


@router.post("/dashboard/sla/rules", response_model=dict)
def create_sla_rule(
    state: AppStateDep,
    body: SlaRuleCreateRequest,
) -> dict:
    """Create a new SLA rule — tenant-specific or region-generic fallback —
    persisted to ``app.sla_rule`` (tenant_id FK enforced by Postgres)."""
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="sla rules require postgres")
    from payce_support.sla import SlaRule, create_sla_rule_db

    fields = {
        k: (list(v) if isinstance(v, tuple) else v)
        for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None
    }
    try:
        data = create_sla_rule_db(dsn, fields=fields)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # FK violation (unknown tenant) or invalid date/decimal → 422.
        raise HTTPException(status_code=422, detail=str(exc).splitlines()[0]) from exc
    state.sla_policy.clear()
    state.sla_policy.update(data)
    # Return the created rule (last inserted = highest rule_id sort is not
    # guaranteed; match by generated id pattern via description/fields).
    from decimal import Decimal

    created = next(
        (
            r
            for r in data["rules"]
            if r.get("tenant_id") == fields.get("tenant_id")
            and r.get("region") == fields.get("region")
            and float(r.get("unanswered_hours", -1)) == float(fields["unanswered_hours"])
            and Decimal(str(r.get("penalty_amount")))
            == Decimal(str(fields["penalty_amount"]))
        ),
        data["rules"][-1] if data["rules"] else None,
    )
    if created is None:
        raise HTTPException(status_code=500, detail="rule created but not found")
    rule = SlaRule.model_validate(created)
    dumped = rule.model_dump(mode="json")
    dumped["effective_status"] = sla_rule_status(rule)
    return dumped


@router.put("/dashboard/sla/rules/{rule_id}", response_model=dict)
def update_sla_rule(
    state: AppStateDep,
    rule_id: str,
    body: SlaRuleUpdateRequest,
) -> dict:
    """Edit an SLA/penalty rule — persisted to app.sla_rule (Postgres),
    falling back to sla.yaml when no database is configured."""
    updates = {
        k: (list(v) if isinstance(v, tuple) else v)
        for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None
    }
    for field_name in ("tenant_id", "region", "valid_from", "valid_to", "description"):
        if field_name in body.model_fields_set and getattr(body, field_name) is None:
            updates[field_name] = None

    dsn = state.settings.postgres_dsn
    if dsn:
        from payce_support.sla import load_sla_policy_from_db, update_sla_rule_db

        try:
            data = update_sla_rule_db(dsn, rule_id=rule_id, updates=updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        from payce_support.policy_editor import update_yaml_rule

        path = state.sla_policy_path
        if path is None:
            raise HTTPException(status_code=503, detail="sla policy file not configured")
        try:
            data = update_yaml_rule(path, rule_id=rule_id, updates=updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    state.sla_policy.clear()
    state.sla_policy.update(data)
    rule = SlaRule.model_validate(
        next(r for r in data["rules"] if r["id"] == rule_id)
    )
    dumped = rule.model_dump(mode="json")
    dumped["effective_status"] = sla_rule_status(rule)
    return dumped


@router.delete("/dashboard/sla/rules/{rule_id}", response_model=dict)
def delete_sla_rule(
    state: AppStateDep,
    rule_id: str,
) -> dict:
    """Soft-delete an SLA rule — sets ``deleted_at`` in ``app.sla_rule``;
    the row is retained for audit and disappears from evaluation, the rule
    list, and LLM SLA context immediately."""
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="sla rules require postgres")
    from payce_support.sla import delete_sla_rule_db

    try:
        data = delete_sla_rule_db(dsn, rule_id=rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    state.sla_policy.clear()
    state.sla_policy.update(data)
    return {"deleted": rule_id, "rules_remaining": len(data["rules"])}


@router.get("/dashboard/sla/rules", response_model=dict)
def dashboard_sla_rules(state: AppStateDep) -> dict:
    """SLA/penalty rule definitions (separate from routing rules)."""
    data = state.sla_policy
    rules = []
    for r in data["rules"]:
        rule = SlaRule.model_validate(r)
        dumped = rule.model_dump(mode="json")
        dumped["effective_status"] = sla_rule_status(rule)
        rules.append(dumped)
    return {
        "sla_policy_version": str(data.get("sla_policy_version", "sla-v1")),
        "rules": rules,
    }


@router.get("/dashboard/costs", response_model=dict)
def dashboard_costs(state: AppStateDep) -> dict:
    """Per-tenant LLM usage + estimated cost, aggregated from the
    append-only ``app.llm_usage`` ledger (written inside record_analysis).

    RLS has no cross-tenant reads, so each active tenant is queried under
    its own ``app.tenant_id`` and merged here — same pattern as
    ``list_ticket_current``.
    """
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="cost metrics require postgres")
    import psycopg
    from decimal import Decimal

    def _tenant_rows(cur) -> list[tuple]:
        cur.execute(
            """
            SELECT kind, model_id, COUNT(*),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(cost_usd), 0)
            FROM app.llm_usage
            GROUP BY kind, model_id
            """
        )
        return cur.fetchall()

    def _ticket_rows(cur) -> list[tuple]:
        cur.execute(
            """
            SELECT ticket_id, kind, model_id, COUNT(*),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(cost_usd), 0),
                   MAX(created_at)
            FROM app.llm_usage
            GROUP BY ticket_id, kind, model_id
            """
        )
        return cur.fetchall()

    def _day_rows(cur) -> list[tuple]:
        cur.execute(
            """
            SELECT date_trunc('day', created_at) AS day, COUNT(*),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(cost_usd), 0)
            FROM app.llm_usage
            GROUP BY day ORDER BY day DESC LIMIT 14
            """
        )
        return cur.fetchall()

    by_tenant: list[dict] = []
    by_model: dict[tuple[str, str], dict] = {}
    by_day_map: dict[str, dict] = {}
    by_ticket_map: dict[tuple[str, str], dict] = {}
    total = Decimal("0")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM app.tenant_profile WHERE active"
            )
            tenant_ids = [r[0] for r in cur.fetchall()]
        for tid in tenant_ids:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (tid,)
                    )
                    rows = _tenant_rows(cur)
                    days = _day_rows(cur)
                    tix = _ticket_rows(cur)
            calls = sum(r[2] for r in rows)
            in_tok = sum(r[3] for r in rows)
            out_tok = sum(r[4] for r in rows)
            cost = sum((Decimal(str(r[5])) for r in rows), Decimal("0"))
            total += cost
            by_tenant.append({
                "tenant_id": tid, "calls": calls,
                "input_tokens": in_tok, "output_tokens": out_tok,
                "cost_usd": str(cost),
            })
            for kind, model, n, i_tok, o_tok, cst in rows:
                key = (model or "unknown", kind)
                slot = by_model.setdefault(key, {
                    "model_id": model or "unknown", "kind": kind,
                    "calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "cost_usd": Decimal("0"),
                })
                slot["calls"] += n
                slot["input_tokens"] += i_tok
                slot["output_tokens"] += o_tok
                slot["cost_usd"] += Decimal(str(cst))
            for day, n, i_tok, o_tok, cst in days:
                d = day.date().isoformat()
                slot = by_day_map.setdefault(d, {
                    "date": d, "calls": 0, "input_tokens": 0,
                    "output_tokens": 0, "cost_usd": Decimal("0"),
                })
                slot["calls"] += n
                slot["input_tokens"] += i_tok
                slot["output_tokens"] += o_tok
                slot["cost_usd"] += Decimal(str(cst))
            for ticket_id, kind, model, n, i_tok, o_tok, cst, last_at in tix:
                slot = by_ticket_map.setdefault(
                    (tid, ticket_id, kind, model or ""),
                    {
                        "tenant_id": tid, "ticket_id": ticket_id,
                        "kind": kind, "model_id": model,
                        "calls": 0, "input_tokens": 0, "output_tokens": 0,
                        "cost_usd": Decimal("0"), "last_call_at": None,
                    },
                )
                slot["calls"] += n
                slot["input_tokens"] += i_tok
                slot["output_tokens"] += o_tok
                slot["cost_usd"] += Decimal(str(cst))
                if slot["last_call_at"] is None or last_at > slot["last_call_at"]:
                    slot["last_call_at"] = last_at

    by_tenant.sort(key=lambda r: Decimal(r["cost_usd"]), reverse=True)
    by_ticket = sorted(
        by_ticket_map.values(),
        key=lambda r: r["cost_usd"],
        reverse=True,
    )
    for r in by_ticket:
        r["cost_usd"] = str(r["cost_usd"])
        r["last_call_at"] = (
            r["last_call_at"].isoformat() if r["last_call_at"] else None
        )
    return {
        "by_tenant": by_tenant,
        "by_ticket": by_ticket,
        "by_model": [
            {**m, "cost_usd": str(m["cost_usd"])}
            for m in sorted(by_model.values(), key=lambda m: m["cost_usd"], reverse=True)
        ],
        "by_day": [
            {**d, "cost_usd": str(d["cost_usd"])}
            for d in sorted(by_day_map.values(), key=lambda d: d["date"], reverse=True)
        ],
        "total_cost_usd": str(total),
    }


@router.post("/dashboard/tickets/{tenant_id}/{ticket_id}/csat", response_model=dict)
def submit_csat(
    tenant_id: str,
    ticket_id: str,
    state: AppStateDep,
    payload: dict,
) -> dict:
    """Record a post-resolution CSAT rating (1–5) for a ticket.

    Simulates the customer survey response channel; in production this is
    fed by the survey webhook. One rating per ticket — resubmits update
    ``csat_at``/``csat_comment`` but keep the latest score.
    """
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="csat requires postgres")
    import psycopg

    score = payload.get("score")
    if not isinstance(score, int) or not 1 <= score <= 5:
        raise HTTPException(status_code=422, detail="score must be an integer 1-5")
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise HTTPException(status_code=422, detail="comment must be a string")

    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", (tenant_id,)
                )
                cur.execute(
                    """
                    UPDATE app.ticket_current
                    SET csat_score = %s,
                        csat_at = now(),
                        csat_comment = %s
                    WHERE tenant_id = %s AND ticket_id = %s
                    RETURNING ticket_id
                    """,
                    (score, comment, tenant_id, ticket_id),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404, detail="ticket not found"
                    )
    return {"ticket_id": ticket_id, "csat_score": score}


@router.get("/dashboard/csat", response_model=dict)
def dashboard_csat(state: AppStateDep) -> dict:
    """Per-tenant CSAT across all tickets: average score, response rate,
    1–5 distribution, daily trend, decile rank among tenants, and
    progress (recent-half vs earlier-half average)."""
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="csat requires postgres")
    import psycopg

    tenants: list[dict] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM app.tenant_profile WHERE active"
            )
            tenant_ids = [r[0] for r in cur.fetchall()]
        for tid in tenant_ids:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (tid,)
                    )
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM app.ticket_current
                        WHERE tenant_id = %s
                        """,
                        (tid,),
                    )
                    total = cur.fetchone()[0]
                    cur.execute(
                        """
                        SELECT csat_score, csat_at::date AS d
                        FROM app.ticket_current
                        WHERE tenant_id = %s AND csat_score IS NOT NULL
                        ORDER BY csat_at
                        """,
                        (tid,),
                    )
                    ratings = cur.fetchall()
                    cur.execute(
                        """
                        SELECT csat_at::date AS d, AVG(csat_score), COUNT(*)
                        FROM app.ticket_current
                        WHERE tenant_id = %s AND csat_score IS NOT NULL
                        GROUP BY d ORDER BY d
                        """,
                        (tid,),
                    )
                    by_day = cur.fetchall()

            rated = len(ratings)
            scores = [r[0] for r in ratings]
            avg = sum(scores) / rated if rated else None
            dist = {str(s): 0 for s in range(1, 6)}
            for s in scores:
                dist[str(s)] += 1

            # Progress: average of the most recent half of ratings vs the
            # earlier half (by rating time). Positive = improving.
            progress = None
            earlier_avg = recent_avg = None
            if rated >= 4:
                half = rated // 2
                earlier = scores[:half] if rated % 2 == 0 else scores[:half + 1]
                recent = scores[-(rated - len(earlier)):]
                earlier_avg = sum(earlier) / len(earlier)
                recent_avg = sum(recent) / len(recent)
                progress = round(recent_avg - earlier_avg, 3)

            tenants.append({
                "tenant_id": tid,
                "total_tickets": total,
                "rated": rated,
                "response_rate": round(rated / total, 4) if total else 0.0,
                "avg_csat": round(avg, 3) if avg is not None else None,
                "distribution": dist,
                "progress_delta": progress,
                "earlier_avg": round(earlier_avg, 3) if earlier_avg is not None else None,
                "recent_avg": round(recent_avg, 3) if recent_avg is not None else None,
                "by_day": [
                    {"date": d.isoformat(), "avg": round(float(a), 3), "count": n}
                    for d, a, n in by_day
                ],
            })

    # Decile rank: sort tenants by avg_csat desc; decile 1 = top 10%.
    ranked = sorted(
        (t for t in tenants if t["avg_csat"] is not None),
        key=lambda t: t["avg_csat"],
        reverse=True,
    )
    n = len(ranked)
    for i, t in enumerate(ranked):
        t["decile"] = max(1, min(10, (i * 10) // max(n, 1) + 1))
    for t in tenants:
        t.setdefault("decile", None)

    overall_rated = sum(t["rated"] for t in tenants)
    overall_avg = (
        sum(t["avg_csat"] * t["rated"] for t in tenants if t["avg_csat"] is not None)
        / overall_rated
        if overall_rated
        else None
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "avg_csat": round(overall_avg, 3) if overall_avg is not None else None,
            "rated": overall_rated,
            "total_tickets": sum(t["total_tickets"] for t in tenants),
        },
        "tenants": tenants,
        "top_by_score": sorted(
            (t for t in tenants if t["avg_csat"] is not None),
            key=lambda t: t["avg_csat"],
            reverse=True,
        )[:5],
        "top_by_progress": sorted(
            (t for t in tenants if t["progress_delta"] is not None),
            key=lambda t: t["progress_delta"],
            reverse=True,
        )[:5],
    }


@router.get("/dashboard/csat/quarters", response_model=dict)
def dashboard_csat_quarters(
    state: AppStateDep,
    tenant_id: str | None = Query(default=None, pattern=r"^C-[0-9]{4,}$"),
    year: int | None = Query(default=None, ge=2000, le=2100),
) -> dict:
    """CSAT grouped by calendar quarter — ratings bucketed by ``csat_at``,
    ticket volume by ``updated_at`` — per-tenant and overall for a
    selected year."""
    dsn = state.settings.postgres_dsn
    if not dsn:
        raise HTTPException(status_code=503, detail="csat requires postgres")
    import psycopg

    tenant_filter = tenant_id

    def _bucket() -> dict:
        return {
            "rated": 0,
            "distribution": {str(s): 0 for s in range(1, 6)},
        }

    years: set[int] = set()
    per_year: dict[tuple[str | None, int, int], dict] = {}
    sums: dict[tuple[str | None, int, int], list[float]] = {}
    vols: dict[tuple[str | None, int, int], int] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM app.tenant_profile WHERE active"
            )
            all_tenants = [r[0] for r in cur.fetchall()]
        if tenant_filter:
            all_tenants = [t for t in all_tenants if t == tenant_filter]
        for tid in all_tenants:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (tid,)
                    )
                    cur.execute(
                        """
                        SELECT EXTRACT(YEAR FROM updated_at)::int,
                               EXTRACT(QUARTER FROM updated_at)::int, COUNT(*)
                        FROM app.ticket_current WHERE tenant_id = %s
                        GROUP BY 1, 2
                        """,
                        (tid,),
                    )
                    for y, q, n in cur.fetchall():
                        years.add(int(y))
                        for key_tid in (tid, None):
                            key = (key_tid, int(y), int(q))
                            vols[key] = vols.get(key, 0) + n
                    cur.execute(
                        """
                        SELECT EXTRACT(YEAR FROM csat_at)::int,
                               EXTRACT(QUARTER FROM csat_at)::int, csat_score
                        FROM app.ticket_current
                        WHERE tenant_id = %s AND csat_score IS NOT NULL
                        """,
                        (tid,),
                    )
                    for y, q, s in cur.fetchall():
                        years.add(int(y))
                        for key_tid in (tid, None):
                            key = (key_tid, int(y), int(q))
                            b = per_year.setdefault(key, _bucket())
                            b["rated"] += 1
                            b["distribution"][str(s)] += 1
                            sums.setdefault(key, []).append(float(s))

    sel_year = year or (max(years) if years else datetime.now(timezone.utc).year)

    def _row(tid: str | None, q: int) -> dict:
        key = (tid, sel_year, q)
        b = per_year.get(key, _bucket())
        tickets = vols.get(key, 0)
        vals = sums.get(key, [])
        avg = sum(vals) / len(vals) if vals else None
        return {
            "tenant_id": tid,
            "quarter": f"Q{q}",
            "year": sel_year,
            "tickets": tickets,
            "rated": b["rated"],
            "response_rate": round(b["rated"] / tickets, 4) if tickets else 0.0,
            "avg_csat": round(avg, 3) if avg is not None else None,
            "distribution": b["distribution"],
        }

    return {
        "year": sel_year,
        "years": sorted(years),
        "quarters": [_row(None, q) for q in range(1, 5)],
        "by_tenant": [
            _row(tid, q)
            for tid in all_tenants
            for q in range(1, 5)
        ],
    }


def _ticket_llm_cost(
    state: AppStateDep, tenant_id: str, ticket_id: str
) -> dict | None:
    """Per-ticket LLM spend rollup from ``app.llm_usage`` (RLS-scoped)."""
    dsn = state.settings.postgres_dsn
    if not dsn:
        return None
    import psycopg

    try:
        with psycopg.connect(dsn) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (tenant_id,),
                    )
                    cur.execute(
                        """
                        SELECT COUNT(*),
                               COALESCE(SUM(input_tokens), 0),
                               COALESCE(SUM(output_tokens), 0),
                               COALESCE(SUM(cost_usd), 0)
                        FROM app.llm_usage
                        WHERE tenant_id = %s AND ticket_id = %s
                        """,
                        (tenant_id, ticket_id),
                    )
                    calls, i_tok, o_tok, cost = cur.fetchone()
    except Exception:
        return None
    if not calls:
        return None
    return {
        "calls": calls,
        "input_tokens": i_tok,
        "output_tokens": o_tok,
        "cost_usd": str(cost),
    }


@router.get(
    "/dashboard/tickets/{tenant_id}/{ticket_id}",
    response_model=DashboardTicketDetail,
)
def ticket_detail(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> DashboardTicketDetail:
    """Full ticket view: current projection + canonical event + decision."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    canonical = state.pipeline.get_canonical(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )
    decision = state.pipeline.get_decision(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )
    tenant_regions = {
        tid: ctx.region for tid, ctx in state.tenant_contexts.items()
    }
    report = evaluate_sla(
        rows=(current,),
        sla_policy=state.sla_policy,
        tenant_regions=tenant_regions,
        ticket_priorities=_ticket_priorities(state, (current,)),
    )
    sla_eval = report.evaluations[0] if report.evaluations else None
    replies = state.pipeline.list_replies(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    analysis = state.pipeline.latest_analysis(
        tenant_id=tenant_id, ticket_id=ticket_id, kind="llm_signals"
    )
    llm_answer = state.pipeline.latest_analysis(
        tenant_id=tenant_id, ticket_id=ticket_id, kind="llm_answer"
    )
    llm_queue = state.pipeline.latest_analysis(
        tenant_id=tenant_id, ticket_id=ticket_id, kind="llm_queue"
    )
    llm_cost = _ticket_llm_cost(state, tenant_id, ticket_id)

    def _analysis_json(a):
        if a is None:
            return None
        return {
            "analysis_id": str(a.analysis_id),
            "kind": a.kind,
            "canonical_version": a.canonical_version,
            "payload": a.payload,
            "model_id": a.model_id,
            "created_at": a.created_at.isoformat(),
        }

    return DashboardTicketDetail(
        ticket=_ticket(state, current),
        canonical=(
            canonical.model_dump(mode="json", by_alias=True)
            if canonical is not None
            else None
        ),
        decision=(
            decision.model_dump(mode="json") if decision is not None else None
        ),
        sla=sla_eval.model_dump(mode="json") if sla_eval is not None else None,
        replies=tuple(
            {
                "reply_id": str(r.reply_id),
                "author": r.author,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
            }
            for r in replies
        ),
        analysis=_analysis_json(analysis),
        llm_answer=_analysis_json(llm_answer),
        llm_queue=_analysis_json(llm_queue),
        llm_cost=llm_cost,
    )


@router.get("/dashboard/tickets/{tenant_id}/{ticket_id}/attachments")
def ticket_attachments(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> dict:
    """Attachment manifest rows incl. modality-processor output."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    rows = state.pipeline.list_attachments(
        tenant_id=tenant_id, event_id=current.event_id
    )
    return {
        "attachments": [
            {**r, "has_content": bool(r.get("object_id"))} for r in rows
        ]
    }


@router.get("/dashboard/tickets/{tenant_id}/{ticket_id}/attachments/{attachment_id}/content")
def ticket_attachment_content(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
    attachment_id: str,
) -> Response:
    """Stream the immutable original bytes through the manifest-checked path."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    rows = state.pipeline.list_attachments(
        tenant_id=tenant_id, event_id=current.event_id
    )
    row = next(
        (r for r in rows if r["attachment_id"] == attachment_id), None
    )
    if row is None or not row.get("object_id"):
        raise HTTPException(status_code=404, detail="attachment not found")
    if state.manifests is None or state.object_store is None:
        raise HTTPException(status_code=503, detail="object storage unavailable")
    manifest = state.manifests.get_for_tenant(
        object_id=UUID(row["object_id"]), tenant_id=tenant_id
    )
    body = state.object_store.read_internal(manifest.object_path)
    filename = row.get("filename") or "attachment"
    return Response(
        content=body,
        media_type=row.get("detected_media_type") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


_CLOSED_STATUSES = {"resolved", "closed"}
_OPEN_STATUSES = {"open", "in_progress", "pending_customer"}


def _masked_text(canonical) -> str:
    return (
        f"{canonical.content.subject_masked}\n{canonical.content.body_masked}"
    ).strip()


async def _run_and_save_analysis(state, *, tenant_id, ticket_id, current, canonical) -> None:
    """Reusable LLM re-analysis → persisted to ticket_analysis."""
    from payce_support.sla import active_sla_rules_for_llm
    from payce_support.ticket_analysis import analyze_ticket_text

    ctx = state.tenant_contexts.get(tenant_id)
    region = Region(ctx.region) if ctx is not None else Region.INDIA
    text = _masked_text(canonical)
    sla_ctx = active_sla_rules_for_llm(
        state.sla_policy, tenant_id=tenant_id, region=region.value
    )
    payload = await analyze_ticket_text(
        extractor=state.signal_extractor,
        text=text,
        region=region,
        tenant_id=tenant_id,
        sla_rules=sla_ctx,
    )
    state.pipeline.record_analysis(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        kind="llm_signals",
        canonical_version=current.canonical_version,
        payload=payload,
        model_id=payload.get("model_id"),
    )


@router.post(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/reply",
    response_model=DashboardTicketDetail,
)
async def ticket_reply(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
    body: TicketReplyRequest,
) -> DashboardTicketDetail:
    """Append a reply; agent replies close human-loop tickets, customer
    replies on closed tickets reopen them into the human loop with a
    fresh LLM re-analysis saved to ticket_analysis."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    reply = state.pipeline.add_reply(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        author=body.author,
        body=body.body,
    )
    if reply is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    canonical = state.pipeline.get_canonical(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )

    if body.author == "agent" and current.status in _OPEN_STATUSES:
        # Agent answered → ball is in the customer's court. The ticket
        # stays open-loop as pending_customer until the customer replies
        # or an agent explicitly resolves it.
        state.pipeline.update_ticket_status(
            tenant_id=tenant_id, ticket_id=ticket_id,
            status="pending_customer", touch_human=True,
        )
        state.pipeline.add_reply(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            author="system",
            body="Replied — waiting for customer response.",
        )
    elif body.author == "customer" and current.status == "pending_customer":
        # Customer responded while we waited → back in the agent queue.
        state.pipeline.update_ticket_status(
            tenant_id=tenant_id, ticket_id=ticket_id, status="open",
        )
        state.pipeline.add_reply(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            author="system",
            body="Customer replied — ticket back in queue.",
        )
    elif body.author == "customer" and current.status in _CLOSED_STATUSES:
        # Customer responded after close → reopen into the human loop.
        state.pipeline.update_ticket_status(
            tenant_id=tenant_id, ticket_id=ticket_id, status="open"
        )
        state.pipeline.add_reply(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            author="system",
            body="Reopened — customer replied after resolution.",
        )
        if canonical is not None:
            await _run_and_save_analysis(
                state,
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                current=current,
                canonical=canonical,
            )

    return ticket_detail(state, tenant_id, ticket_id)


@router.post(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/reopen",
    response_model=DashboardTicketDetail,
)
async def ticket_reopen(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> DashboardTicketDetail:
    """Agent authority: reopen a closed (incl. bot-resolved) ticket."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    if current.status not in _CLOSED_STATUSES:
        raise HTTPException(status_code=409, detail="ticket is not closed")
    state.pipeline.update_ticket_status(
        tenant_id=tenant_id, ticket_id=ticket_id, status="open",
        touch_human=True,
    )
    state.pipeline.add_reply(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        author="system",
        body="Reopened by agent.",
    )
    return ticket_detail(state, tenant_id, ticket_id)


@router.post(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/suggest-queue",
    response_model=DashboardTicketDetail,
)
async def ticket_suggest_queue(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> DashboardTicketDetail:
    """LLM queue suggestion for tickets the rule base could not classify.

    Picks the closest queue from the live queue set (policy + observed)
    or proposes a new one; applies it to ticket_current and records an
    ``llm_queue`` analysis. The routing decision is never rewritten.
    """
    if state.queue_suggester is None:
        raise HTTPException(status_code=503, detail="LLM queue suggester not configured")
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    from payce_support.models import Region
    from payce_support.queue_suggester import suggest_queue_for_ticket

    ctx = state.tenant_contexts.get(tenant_id)
    region = Region(ctx.region) if ctx is not None else Region.INDIA
    live_queues = [
        r.queue_id for r in state.pipeline.list_ticket_current() if r.queue_id
    ]
    try:
        payload = await suggest_queue_for_ticket(
            suggester=state.queue_suggester,
            pipeline=state.pipeline,
            tenant_contexts=state.tenant_contexts,
            policy_data=state.routing_policy_data,
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            region=region,
            live_queue_ids=live_queues,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"queue suggestion failed: {exc}") from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="ticket content unavailable")
    return ticket_detail(state, tenant_id, ticket_id)


@router.post(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/analyze",
    response_model=DashboardTicketDetail,
)
async def ticket_analyze(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> DashboardTicketDetail:
    """Run the reusable LLM analysis tool and persist the snapshot."""
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    canonical = state.pipeline.get_canonical(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )
    if canonical is None:
        raise HTTPException(status_code=404, detail="canonical event not found")
    await _run_and_save_analysis(
        state,
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        current=current,
        canonical=canonical,
    )
    return ticket_detail(state, tenant_id, ticket_id)


_HOWTO_HINTS = ("how do", "how to", "steps to", "step-by-step", "guide")


def _guess_intent(text: str) -> RagIntent:
    lowered = text.lower()
    return (
        RagIntent.HOW_TO
        if any(h in lowered for h in _HOWTO_HINTS)
        else RagIntent.PROCESS
    )


@router.post(
    "/dashboard/tickets/{tenant_id}/{ticket_id}/draft",
    response_model=TicketDraftResponse,
)
async def ticket_draft(
    state: AppStateDep,
    tenant_id: str,
    ticket_id: str,
) -> TicketDraftResponse:
    """Compose a grounded bot draft for a ticket and gate bot-vs-human.

    Flow: masked canonical content → KB retrieval (region-scoped) →
    grounded draft → answerability gate (statutory/P1/low-grounding →
    human; how-to/process with citations → auto_answer; else agent draft).
    """
    if state.rag_composer is None:
        raise HTTPException(status_code=503, detail="KB store not configured")
    current = state.pipeline.get_ticket_current(
        tenant_id=tenant_id, ticket_id=ticket_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    canonical = state.pipeline.get_canonical(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )
    if canonical is None:
        raise HTTPException(status_code=404, detail="canonical event not found")

    ctx = state.tenant_contexts.get(tenant_id)
    region = Region(ctx.region) if ctx is not None else Region.INDIA
    question = (
        f"{canonical.content.subject_masked}\n{canonical.content.body_masked}"
    ).strip()[:20_000]
    if not question:
        raise HTTPException(status_code=422, detail="ticket has no content")

    decision = state.pipeline.get_decision(
        tenant_id=tenant_id,
        event_id=current.event_id,
        canonical_version=current.canonical_version,
    )
    lane = (
        Lane(decision.lane) if decision is not None else Lane.DRAFT_FOR_AGENT
    )
    from payce_support.ai_contracts import AiCallError

    composer = state.rag_composer
    request = RagRequest(
        context=AccessContext(
            principal_id="dashboard",
            tenant_id=tenant_id,
            region=region,
        ),
        question_masked=question,
        intent=_guess_intent(question),
        lane=lane if lane in {Lane.AUTO_ANSWER, Lane.DRAFT_FOR_AGENT}
        else Lane.DRAFT_FOR_AGENT,
        # §5A: retrieval time = canonical t_as_of, capped at now in composer.
        as_of=canonical.time.t_as_of,
        payroll_week=canonical.time.payroll_week,
    )
    used_fallback = False
    try:
        rag = await composer.compose(request)
    except AiCallError:
        fallback = state.rag_composer_fallback
        if fallback is None or fallback is composer:
            raise HTTPException(
                status_code=502, detail="LLM draft provider unavailable"
            ) from None
        used_fallback = True
        rag = await fallback.compose(request)

    gated = classify_answerability(
        text=question,
        intent=_guess_intent(question),
        rag=rag,
    )
    if gated is None:
        verdict, reasons = AnswerVerdict.HUMAN_ONLY, ("NO_RAG_RESULT",)
    else:
        verdict, reasons = gated

    kb_articles = []
    for cid in rag.citation_chunk_ids:
        kb_articles.append({"chunk_id": cid, "kb_id": cid})

    return TicketDraftResponse(
        verdict=verdict.value,
        verdict_reasons=reasons,
        rag_status=rag.status.value,
        rag_reason_code=rag.reason_code,
        answer=rag.answer if verdict is not AnswerVerdict.HUMAN_ONLY else None,
        citations=rag.citation_chunk_ids,
        retrieved_chunk_ids=rag.retrieved_chunk_ids,
        kb_articles=tuple(kb_articles),
        model_id=rag.model_id,
        cache_hit=rag.cache_hit,
        input_tokens=rag.input_tokens,
        output_tokens=rag.output_tokens,
        llm_provider=(
            "local_extractive"
            if used_fallback or not state.settings.openrouter_api_key
            else "openrouter"
        ),
    )
