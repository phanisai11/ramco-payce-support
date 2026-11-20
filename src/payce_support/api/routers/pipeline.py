"""Ticket pipeline entrypoints — HTTP process + Pub/Sub push consumer."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from datetime import datetime, timezone

logger = logging.getLogger("payce_support.api.pipeline")

from fastapi import APIRouter, Depends, HTTPException

from payce_support.api.deps import AppStateDep, require_authenticated_v1
from payce_support.api.schemas import (
    PipelineProcessRequest,
    PipelineProcessResponse,
    PubSubPushRequest,
    PubSubPushResponse,
)
from payce_support.extraction import CanonicalExtractionError
from payce_support.intake import IdempotencyConflictError
from payce_support.pipeline import TenantUnresolvedError
from payce_support.pubsub_consumer import (
    PointerFormatError,
    TenantUnresolvedPointerError,
)
from payce_support.storage import ObjectNotFoundError, TenantAccessDeniedError

router = APIRouter(
    prefix="/v1/pipeline",
    tags=["pipeline"],
    dependencies=[Depends(require_authenticated_v1)],
)


def _pivot_en(pre_analysis: dict | None) -> str:
    """English pivot for non-English tickets (pivot_en, classification only).

    Returns the LLM's English summary only when the detected language is
    not English; English tickets route on their own text unchanged.
    """
    if not pre_analysis:
        return ""
    sig = pre_analysis.get("signals") or {}
    lang = str(sig.get("language") or "en").lower()
    if lang.startswith("en"):
        return ""
    return str(sig.get("summary") or "").strip()


@router.post("/process", response_model=PipelineProcessResponse)
async def process_event(
    state: AppStateDep,
    body: PipelineProcessRequest,
) -> PipelineProcessResponse:
    ctx = body.to_context()
    cmd = body.to_command()

    # Pre-classification LLM analysis on the raw payload text. Runs before
    # the routing decision (wall-clock) and is persisted after the ticket
    # row exists — every ticket gets a saved analysis for audit.
    pre_text = " ".join(
        str(body.parsed_payload.get(k, ""))
        for k in ("subject", "body", "message", "text", "description")
    ).strip()
    if body.priority:
        pre_text = f"Declared priority: {body.priority.upper()}. {pre_text}"
    tenant_id_hint = body.authenticated_tenant_id

    # Pre-LLM dedup: an identical re-report from the same verified contact
    # is caught on a normalized-text fingerprint before any LLM spend —
    # signals, answer, and queue suggestion are all skipped downstream.
    import hashlib

    from payce_support.incident_cluster import normalise_text_for_fingerprint

    contact_id = ctx.verified_contact_id
    text_fp = (
        hashlib.sha256(
            normalise_text_for_fingerprint(pre_text).encode("utf-8")
        ).hexdigest()
        if pre_text
        else None
    )
    early_dedup_of: str | None = None
    if tenant_id_hint and contact_id and text_fp:
        try:
            early_dedup_of = state.pipeline.find_open_duplicate(
                tenant_id=tenant_id_hint,
                contact_id=contact_id,
                text_fp=text_fp,
                exclude_ticket_id=str(
                    body.parsed_payload.get("ticket_id") or ""
                ),
            )
        except Exception:
            early_dedup_of = None

    pre_analysis = None
    if pre_text:
        from payce_support.ticket_analysis import analyze_ticket_text

        # Dedupe: identical masked text for the same ticket reuses the
        # persisted analysis instead of paying for another LLM call.
        text_sha = hashlib.sha256(pre_text.encode("utf-8")).hexdigest()
        ticket_hint = str(body.parsed_payload.get("ticket_id") or "")
        prior = None
        if tenant_id_hint and ticket_hint:
            try:
                prior = state.pipeline.latest_analysis(
                    tenant_id=tenant_id_hint,
                    ticket_id=ticket_hint,
                    kind="llm_signals",
                )
            except Exception:
                prior = None
        if early_dedup_of:
            # Duplicate re-report — zero-token record with heuristic signals
            # so routing/pivot still work; the real answer lives on the
            # primary ticket.
            from payce_support.signal_preflight import heuristic_signals

            pre_analysis = {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": tenant_id_hint,
                "region": body.ingress_region.value,
                "text_sha256": text_sha,
                "status": "duplicate_skip",
                "reason": f"DUPLICATE_OF_{early_dedup_of}",
                "extractor": "dedup",
                "signals": heuristic_signals(
                    text=pre_text, region=body.ingress_region
                ).model_dump(mode="json"),
                "input_tokens": 0,
                "output_tokens": 0,
            }
        elif prior is not None and (prior.payload or {}).get("text_sha256") == text_sha:
            pre_analysis = dict(prior.payload)
            pre_analysis["reused"] = True
        else:
            from payce_support.sla import active_sla_rules_for_llm

            sla_ctx = active_sla_rules_for_llm(
                state.sla_policy,
                tenant_id=tenant_id_hint,
                region=body.ingress_region.value,
            )
            pre_analysis = await analyze_ticket_text(
                extractor=state.signal_extractor,
                text=pre_text,
                region=body.ingress_region,
                tenant_id=tenant_id_hint,
                sla_rules=sla_ctx,
            )
        if body.priority:
            pre_analysis["declared_priority"] = body.priority.upper()

    async with state.intake_semaphore:
        try:
            intake_result = await asyncio.to_thread(
                state.intake.ingest,
                context=ctx,
                command=cmd,
            )
            result = await asyncio.to_thread(
                state.processor.process,
                intake_result=intake_result,
                p1=body.p1 or body.priority == "p1",
                ticket_status=body.ticket_status,
                ticket_age_hours=body.ticket_age_hours,
                route_context=_pivot_en(pre_analysis),
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TenantUnresolvedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CanonicalExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (ObjectNotFoundError, TenantAccessDeniedError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event = result.event
    decision = result.decision
    tenant_id = event.ticket.tenant_id
    ticket_id = event.ticket.ticket_id
    response_queue_id = decision.queue_id

    # Persist modality-processor output (PDF text, screenshot layer,
    # transcripts) onto the attachment manifest rows.
    if result.attachment_extractions:
        try:
            state.pipeline.record_attachment_extractions(
                tenant_id=tenant_id,
                event_id=event.event_id,
                extractions=result.attachment_extractions,
            )
        except Exception:
            logger.warning("attachment extraction persist failed", exc_info=True)

    # Symptom fingerprint — deterministic, computed once and shared by
    # incident clustering (cross-customer) and per-customer dedup.
    from payce_support.incident_cluster import symptom_fingerprint_for_ticket

    fp = symptom_fingerprint_for_ticket(
        category=event.facts.category,
        error_codes=event.facts.error_codes,
        subject=event.content.subject_masked,
        body=event.content.body_masked[:2000],
    )

    # Per-customer dedup: same verified contact + same symptom within the
    # window links this ticket to the open primary instead of spawning
    # parallel work (and skips the duplicate LLM answer below).
    dedup_of: str | None = early_dedup_of
    try:
        if dedup_of is None and contact_id:
            dedup_of = state.pipeline.find_open_duplicate(
                tenant_id=tenant_id,
                contact_id=contact_id,
                symptom_fingerprint=fp,
                text_fp=text_fp,
                exclude_ticket_id=ticket_id,
            )
        state.pipeline.mark_ticket_dedup(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            contact_id=contact_id,
            symptom_fingerprint=fp,
            dedup_of=dedup_of,
            text_fp=text_fp,
        )
        if dedup_of:
            state.pipeline.add_reply(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                author="system",
                body=(
                    f"This looks like the same issue you're already tracking "
                    f"under {dedup_of} — we've linked this ticket to it. "
                    "No need to resend details; updates will appear there."
                ),
            )
    except Exception:
        logger.warning("ticket dedup failed for %s", ticket_id, exc_info=True)

    # Rule base abstained (or landed on the catch-all) → LLM picks the
    # closest live queue. Persisted as an llm_queue analysis on the
    # ticket_current projection; the immutable routing decision is kept.
    # Duplicates skip this too — zero LLM spend on re-reports.
    if (
        state.queue_suggester is not None
        and dedup_of is None
        and (
            decision.queue_id is None
            or decision.rule_id == "route-general-fallback-v1"
        )
    ):
        prior_queue = state.pipeline.latest_analysis(
            tenant_id=tenant_id, ticket_id=ticket_id, kind="llm_queue"
        )
        if (
            prior_queue is None
            or prior_queue.canonical_version != event.canonical_version
        ):
            from payce_support.models import Region
            from payce_support.queue_suggester import suggest_queue_for_ticket

            ctx_region = state.tenant_contexts.get(tenant_id)
            region_enum = (
                Region(ctx_region.region)
                if ctx_region is not None
                else body.ingress_region
            )
            live_queues = [
                r.queue_id
                for r in state.pipeline.list_ticket_current()
                if r.queue_id
            ]
            try:
                qpayload = await suggest_queue_for_ticket(
                    suggester=state.queue_suggester,
                    pipeline=state.pipeline,
                    tenant_contexts=state.tenant_contexts,
                    policy_data=state.routing_policy_data,
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    region=region_enum,
                    live_queue_ids=live_queues,
                )
            except Exception:
                logger.warning("queue suggestion failed", exc_info=True)
                qpayload = None
            if qpayload and qpayload.get("applied"):
                response_queue_id = qpayload["queue_id"]

    # P4 — feed every processed ticket into incident clustering so a
    # threshold of matching symptoms declares an incident at ingest time
    # instead of hours later.
    incident_declared = False
    try:
        from payce_support.incident_cluster import ClusterTicketSignal

        signal = ClusterTicketSignal(
            received_at=datetime.now(timezone.utc),
            symptom_fingerprint=fp,
            tenant_id=tenant_id,
            channel=(
                intake_result.envelope.source.value
                if hasattr(intake_result.envelope.source, "value")
                else str(intake_result.envelope.source)
            ),
            ticket_id=ticket_id,
        )
        async with state.incident_lock:
            decl = state.incident.on_signal(signal)
        if decl is not None:
            incident_declared = True
            # P8 — one holding reply to every linked ticket so customers
            # stop repeating themselves while the incident is investigated.
            for linked_id in decl.ticket_ids:
                try:
                    state.pipeline.add_reply(
                        tenant_id=tenant_id,
                        ticket_id=linked_id,
                        author="system",
                        body=(
                            "We're investigating a broader issue affecting "
                            "multiple reports (possible incident "
                            f"{decl.symptom_fingerprint[:48]}). Your ticket "
                            "is linked — no need to resend details."
                        ),
                    )
                except Exception:
                    logger.warning(
                        "holding reply failed for %s", linked_id, exc_info=True
                    )
    except Exception:
        logger.warning("incident clustering failed", exc_info=True)

    # Persist the pre-classification analysis now that ticket_current exists.
    if pre_analysis is not None:
        state.pipeline.record_analysis(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            kind="llm_signals",
            canonical_version=event.canonical_version,
            payload=pre_analysis,
            model_id=pre_analysis.get("model_id"),
        )

    # Post-routing LLM answer attempt: grounded RAG draft gated by the
    # answerability policy. Saved as kind="llm_answer" for audit/UI —
    # every ticket gets a record, including human-lane skips. Reused
    # verbatim when this canonical version was already answered.
    llm_answer = None
    _prior_answer = state.pipeline.latest_analysis(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        kind="llm_answer",
    )
    if (
        _prior_answer is not None
        and _prior_answer.canonical_version == event.canonical_version
    ):
        llm_answer = dict(_prior_answer.payload)
        llm_answer["reused"] = True
    if llm_answer is None and dedup_of is not None:
        # Duplicate of an open ticket — the primary already has (or will
        # get) the grounded answer; don't pay for a second LLM call.
        llm_answer = {
            "status": "skipped",
            "reason_code": f"DUPLICATE_OF_{dedup_of}",
            "verdict": "human_only",
            "verdict_reasons": ["DUPLICATE_TICKET"],
            "answer": None,
            "citations": [],
            "retrieved_chunk_ids": [],
            "model_id": None,
            "cache_hit": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "dedup_of": dedup_of,
            "composed_at": datetime.now(timezone.utc).isoformat(),
        }
        state.pipeline.record_analysis(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            kind="llm_answer",
            canonical_version=event.canonical_version,
            payload=llm_answer,
            model_id=None,
        )
    elif llm_answer is None and state.rag_composer is not None and decision.lane.value not in {
        "auto_answer", "draft_for_agent",
    }:
        llm_answer = {
            "status": "skipped",
            "reason_code": "HUMAN_LANE_ROUTING",
            "verdict": "human_only",
            "verdict_reasons": ["ROUTED_TO_HUMAN_LANE"],
            "answer": None,
            "citations": [],
            "retrieved_chunk_ids": [],
            "model_id": None,
            "cache_hit": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "composed_at": datetime.now(timezone.utc).isoformat(),
        }
        state.pipeline.record_analysis(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            kind="llm_answer",
            canonical_version=event.canonical_version,
            payload=llm_answer,
            model_id=None,
        )
    elif llm_answer is None and state.rag_composer is not None:
        from payce_support.ai_contracts import AiCallError
        from payce_support.answer_gate import classify_answerability
        from payce_support.models import AccessContext, Lane, Region
        from payce_support.rag import RagIntent, RagRequest

        masked = (
            f"{event.content.subject_masked}\n{event.content.body_masked}"
        ).strip()
        lowered = masked.lower()
        intent = (
            RagIntent.HOW_TO
            if any(h in lowered for h in ("how do", "how to", "steps to"))
            else RagIntent.PROCESS
        )
        ctx_region = state.tenant_contexts.get(tenant_id)
        region = (
            Region(ctx_region.region)
            if ctx_region is not None
            else body.ingress_region
        )
        rag_request = RagRequest(
            context=AccessContext(
                principal_id="pipeline",
                tenant_id=tenant_id,
                region=region,
            ),
            question_masked=masked[:20_000],
            intent=intent,
            lane=Lane(decision.lane.value),
            # §5A: retrieval time = canonical t_as_of (payroll-period end or
            # latest customer message), capped at now inside the composer.
            as_of=event.time.t_as_of,
            payroll_week=event.time.payroll_week,
        )
        try:
            rag = await state.rag_composer.compose(rag_request)
        except AiCallError:
            fallback = state.rag_composer_fallback
            rag = (
                await fallback.compose(rag_request)
                if fallback is not None and fallback is not state.rag_composer
                else None
            )
        if rag is not None:
            _sig = (pre_analysis or {}).get("signals") or {}
            gated = classify_answerability(
                text=masked,
                intent=intent,
                rag=rag,
                ticket_intent=_sig.get("intent"),
                # LLM signals may escalate (never de-escalate): a p1
                # flag or p1 priority forces human-only regardless of
                # how friendly the ticket sounds.
                p1=bool(_sig.get("p1"))
                or str(_sig.get("priority") or "").lower() == "p1",
            )
            verdict = gated[0].value if gated else "human_only"
            reasons = list(gated[1]) if gated else ["NO_RAG_RESULT"]
            llm_answer = {
                "status": rag.status.value,
                "reason_code": rag.reason_code,
                "verdict": verdict,
                "verdict_reasons": reasons,
                "answer": rag.answer,
                "citations": list(rag.citation_chunk_ids),
                "retrieved_chunk_ids": list(rag.retrieved_chunk_ids),
                "model_id": rag.model_id,
                "cache_hit": rag.cache_hit,
                "input_tokens": rag.input_tokens,
                "output_tokens": rag.output_tokens,
                "composed_at": datetime.now(timezone.utc).isoformat(),
            }
            state.pipeline.record_analysis(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                kind="llm_answer",
                canonical_version=event.canonical_version,
                payload=llm_answer,
                model_id=rag.model_id,
            )
        else:
            state.pipeline.record_analysis(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                kind="llm_answer",
                canonical_version=event.canonical_version,
                payload={
                    "status": "degraded",
                    "reason_code": "PROVIDER_UNAVAILABLE",
                    "verdict": "human_only",
                    "verdict_reasons": ["LLM_PROVIDER_FAILED"],
                    "answer": None,
                    "citations": [],
                    "retrieved_chunk_ids": [],
                    "model_id": None,
                    "cache_hit": False,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "composed_at": datetime.now(timezone.utc).isoformat(),
                },
                model_id=None,
            )
    elif llm_answer is None:
        # No RAG composer configured — still record the skip for audit.
        state.pipeline.record_analysis(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            kind="llm_answer",
            canonical_version=event.canonical_version,
            payload={
                "status": "skipped",
                "reason_code": "RAG_NOT_CONFIGURED",
                "verdict": "human_only",
                "verdict_reasons": ["RAG_UNAVAILABLE"],
                "answer": None,
                "citations": [],
                "retrieved_chunk_ids": [],
                "model_id": None,
                "cache_hit": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "composed_at": datetime.now(timezone.utc).isoformat(),
            },
            model_id=None,
        )

    # Answer-gate upgrade: deterministic routing sent this to
    # draft_for_agent, but the grounded answer cleared the auto-answer
    # bar — treat it like an AUTO_ANSWER ticket: post the reply and
    # resolve. The immutable routing decision is unchanged; this is a
    # recorded post-decision action, not a re-route.
    _upgrade_answer = (
        str(llm_answer.get("answer") or "").strip()
        if llm_answer is not None
        else ""
    )
    _upgrade_lower = _upgrade_answer.lower()
    _is_non_answer = any(
        phrase in _upgrade_lower
        for phrase in (
            "do not provide",
            "does not provide",
            "no information available",
            "cannot find information",
            "don't have information",
        )
    )
    if (
        llm_answer is not None
        and llm_answer.get("verdict") == "auto_answer"
        and len(_upgrade_answer) >= 40
        and not _is_non_answer
        and any(ch.isalnum() for ch in _upgrade_answer)
        and decision.lane.value == "draft_for_agent"
    ):
        state.pipeline.add_reply(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            author="bot",
            body=_upgrade_answer,
        )
        state.pipeline.add_reply(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            author="system",
            body="Auto-resolved: grounded KB answer met the auto-answer bar.",
        )
        state.pipeline.update_ticket_status(
            tenant_id=tenant_id, ticket_id=ticket_id, status="resolved"
        )
    elif decision.lane.value == "auto_answer" and llm_answer is not None:
        # AUTO_ANSWER lane tickets are recorded already resolved at
        # intake (pipeline.py). The customer still needs the bot's
        # answer posted as a reply. If the answer gate downgraded the
        # verdict, reopen so a human picks it up instead of leaving a
        # silent resolved ticket.
        _lane_answer = str(llm_answer.get("answer") or "").strip()
        _lane_ok = (
            llm_answer.get("verdict") == "auto_answer"
            and len(_lane_answer) >= 40
            and not _is_non_answer
            and any(ch.isalnum() for ch in _lane_answer)
        )
        if _lane_ok:
            state.pipeline.add_reply(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                author="bot",
                body=_lane_answer,
            )
        else:
            state.pipeline.update_ticket_status(
                tenant_id=tenant_id, ticket_id=ticket_id, status="open"
            )

    return PipelineProcessResponse(
        event_id=str(event.event_id),
        canonical_version=event.canonical_version,
        ticket_id=event.ticket.ticket_id,
        case_id=event.ticket.case_id,
        auto_route=decision.auto_route,
        lane=decision.lane.value,
        queue_id=response_queue_id,
        rule_id=decision.rule_id,
        reason_codes=decision.reason_codes,
        decided_by=decision.decided_by.value,
        attachment_count=len(event.attachments),
        incident_declared=incident_declared,
    )


@router.post("/ticket-ingested", response_model=PubSubPushResponse)
async def pubsub_ticket_ingested(
    state: AppStateDep,
    body: PubSubPushRequest,
) -> PubSubPushResponse:
    """Pub/Sub push endpoint for ``ticket.ingested`` pointers.

    Always ACKs (2xx) for terminal outcomes — unresolved pointers stay in
    quarantine and malformed pointers must not be retried forever. Unexpected
    errors propagate as 5xx so Pub/Sub redelivers.
    """
    try:
        data = base64.b64decode(body.message.data, validate=True)
    except (binascii.Error, ValueError):
        return PubSubPushResponse(status="rejected", reason="invalid base64 data")

    try:
        result = await asyncio.to_thread(
            state.pubsub_consumer.handle_message,
            data=data,
            attributes=body.message.attributes,
        )
    except TenantUnresolvedPointerError:
        return PubSubPushResponse(status="skipped", reason="unresolved tenant")
    except PointerFormatError as exc:
        return PubSubPushResponse(status="rejected", reason=str(exc))
    except (ObjectNotFoundError, TenantAccessDeniedError) as exc:
        return PubSubPushResponse(status="rejected", reason=str(exc))
    except CanonicalExtractionError as exc:
        return PubSubPushResponse(status="rejected", reason=str(exc))

    return PubSubPushResponse(
        status="processed",
        event_id=str(result.event.event_id),
        canonical_version=result.event.canonical_version,
    )
