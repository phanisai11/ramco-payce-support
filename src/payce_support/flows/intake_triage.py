"""LangGraph-orchestrated inbound triage flow.

Graph::

    START → mask_extract → check_missing ─┬─→ ask_followup → END
                                          └─→ route ─┬─→ escalate → END
                                                     └─→ draft_reply → END

Invariants preserved:
- LLM nodes only *describe* (signal extraction) or *draft* (reply text).
- ``check_missing``, ``route`` and the escalate/draft branch are pure
  deterministic functions — approved YAML rules + ``RoutingPolicy.decide``
  remain the only authority on lane/queue/auto-route.
- P1 / injection / non-approved candidates always land on ``escalate``.

The flow is orchestration only: it does NOT persist tickets, call the
immutable intake service, or emit canonical events — those stay in the
existing pipeline. Wire it in behind a flag when ready.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from payce_support.extraction import mask_pii
from payce_support.models import Region
from payce_support.routing import InMemoryRoutingDecisionRepository, RoutingPolicy
from payce_support.routing_matcher import routing_candidate_from_approved_rules
from payce_support.routing_signals import RoutingSignals

# ── deterministic completeness rules ─────────────────────────────────────
# A small, explicit spec of "what the router still needs". Deliberately
# conservative: we only ask when a shipment-scoped issue lacks the
# reference/timeframe needed to action it. Extend per-tenant later.

_SHIPMENT_REF_RE = re.compile(r"\b[A-Z]{0,4}[-#]?\d{4,}\b")
_TIME_RE = re.compile(
    r"(?i)\b(today|tomorrow|tonight|am|pm|o'clock|o’clock|"
    r"\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next week|this week|by \d|asap|urgent|eod|cob)\b"
)
_DELAY_HINT_RE = re.compile(
    r"(?i)\b(delay|delayed|late|eta|arriv|reach|detain|hold|stuck|"
    r"reschedul|appointment|slot|dock|warehouse)\b"
)


def missing_fields(*, text: str, signals: RoutingSignals | None) -> list[str]:
    """Return the list of missing fields the deterministic router needs."""
    missing: list[str] = []
    haystack = text
    intent = (signals.intent or "") if signals else ""
    scoped = bool(_DELAY_HINT_RE.search(haystack)) or intent in {
        "delay", "exception", "scheduling", "booking",
    }
    if scoped and not _SHIPMENT_REF_RE.search(haystack):
        missing.append("reference_id")
    if scoped and not _TIME_RE.search(haystack):
        missing.append("time_window")
    return missing


_FOLLOW_UP_TEMPLATES = {
    "reference_id": (
        "Could you share the shipment or reference ID (e.g. DO-12345) "
        "so we can locate it?"
    ),
    "time_window": (
        "What is the expected arrival time, or by when does this need "
        "to be resolved?"
    ),
}


class TriageState(TypedDict, total=False):
    text: str
    region: str
    tenant_id: str | None
    p1: bool
    masked_text: str
    analysis: dict[str, Any]
    signals: dict[str, Any] | None
    missing: list[str]
    follow_up_questions: list[str]
    candidate: dict[str, Any]
    blocking_risk: bool
    decision: dict[str, Any] | None
    reply: str
    outcome: str


class IntakeTriageFlow:
    """Stateful triage graph. Reuses existing deterministic + LLM parts."""

    def __init__(
        self,
        *,
        extractor: Any = None,
        language_model: Any = None,
        routing_policy_data: dict[str, Any] | None = None,
        routing_policy: RoutingPolicy | None = None,
    ) -> None:
        self._extractor = extractor
        self._language_model = language_model
        self._policy_data = routing_policy_data or {"rules": [], "approved_rule_ids": []}
        self._routing_policy = routing_policy or RoutingPolicy(
            policy_version=str(self._policy_data.get("policy_version", "spike")),
            approved_rule_ids=set(self._policy_data.get("approved_rule_ids") or ()),
            repository=InMemoryRoutingDecisionRepository(),
        )
        self._graph = self._build_graph()

    # ── nodes ──────────────────────────────────────────────────────────

    async def _mask_extract(self, state: TriageState) -> dict[str, Any]:
        from payce_support.ticket_analysis import analyze_ticket_text

        masked = mask_pii(state["text"])
        region = Region(state.get("region") or "IN")
        analysis = await analyze_ticket_text(
            extractor=self._extractor,
            text=masked,
            region=region,
            tenant_id=state.get("tenant_id"),
        )
        signals = analysis.get("signals")
        return {
            "masked_text": masked,
            "analysis": analysis,
            "signals": signals,
        }

    @staticmethod
    def _signals_from_state(state: TriageState) -> RoutingSignals | None:
        raw = state.get("signals")
        if not raw:
            return None
        try:
            # JSON-dumped state has lists; the model wants tuples.
            fixed = dict(raw)
            for key in ("entities", "rag_hints"):
                if isinstance(fixed.get(key), list):
                    fixed[key] = tuple(fixed[key])
            return RoutingSignals.model_validate(fixed)
        except Exception:
            return None

    def _check_missing(self, state: TriageState) -> dict[str, Any]:
        missing = missing_fields(
            text=state["masked_text"], signals=self._signals_from_state(state)
        )
        questions = [_FOLLOW_UP_TEMPLATES.get(m, m) for m in missing]
        return {"missing": missing, "follow_up_questions": questions}

    def _route(self, state: TriageState) -> dict[str, Any]:
        candidate, blocking_risk = routing_candidate_from_approved_rules(
            text=state["masked_text"],
            region=state.get("region") or "IN",
            p1=bool(state.get("p1")),
            policy=self._policy_data,
            signals=self._signals_from_state(state),
            tenant_id=state.get("tenant_id"),
        )
        fp_text = state["masked_text"]
        import hashlib

        decision = self._routing_policy.decide(
            tenant_id=state.get("tenant_id") or "C-0000",
            event_id=uuid4(),
            canonical_version=1,
            input_fingerprint=hashlib.sha256(fp_text.encode("utf-8")).hexdigest(),
            candidate=candidate,
            tenant_resolved=bool(state.get("tenant_id")),
            schema_valid=True,
            blocking_risk=blocking_risk,
        )
        return {
            "candidate": {
                "lane": candidate.lane.value,
                "queue_id": candidate.queue_id,
                "rule_id": candidate.rule_id,
                "reason_codes": list(candidate.reason_codes),
                "confidence": candidate.confidence,
                "decided_by": candidate.decided_by.value,
            },
            "blocking_risk": blocking_risk,
            "decision": {
                "auto_route": decision.auto_route,
                "lane": decision.lane.value,
                "queue_id": decision.queue_id,
                "rule_id": decision.rule_id,
                "reason_codes": list(decision.reason_codes),
                "decided_by": decision.decided_by.value,
            },
        }

    def _ask_followup(self, state: TriageState) -> dict[str, Any]:
        questions = state.get("follow_up_questions") or ["Could you provide more details?"]
        return {
            "outcome": "follow_up",
            "reply": " ".join(questions),
        }

    def _escalate(self, state: TriageState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        reasons = ", ".join(decision.get("reason_codes") or ["policy abstain"])
        return {
            "outcome": "escalated",
            "reply": (
                "Thanks for the details — I've flagged this for a human "
                f"agent to review ({reasons}). You'll hear back shortly."
            ),
        }

    async def _draft_reply(self, state: TriageState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        queue = decision.get("queue_id") or "the right team"
        lane = decision.get("lane") or "draft_for_agent"
        summary = ((state.get("signals") or {}).get("summary") or "").strip()

        if self._language_model is not None and summary:
            reply = await self._llm_draft(state, decision, summary)
        else:
            reply = (
                f"Thanks — I've logged your request and routed it to {queue} "
                f"({lane})." + (f" Summary: {summary}" if summary else "")
            )
        return {"outcome": "routed", "reply": reply}

    async def _llm_draft(
        self, state: TriageState, decision: dict[str, Any], summary: str
    ) -> str:
        from payce_support.ai_contracts import (
            ChatMessage,
            CompletionRequest,
            MessageRole,
        )

        prompt = (
            "Draft a short (<=60 words), polite customer-facing acknowledgement "
            "for a support ticket. Do not promise outcomes, dates, or refunds. "
            f"Queue: {decision.get('queue_id')}. Lane: {decision.get('lane')}. "
            f"Ticket summary: {summary}"
        )
        try:
            result = await self._language_model.complete(
                CompletionRequest(
                    messages=(
                        ChatMessage(role=MessageRole.SYSTEM, content="You are a concise support assistant."),
                        ChatMessage(role=MessageRole.USER, content=prompt),
                    ),
                    max_output_tokens=200,
                    temperature=0.2,
                )
            )
            text = (result.text or "").strip()
            if text:
                return text
        except Exception:
            pass
        return (
            f"Thanks — I've logged your request and routed it to "
            f"{decision.get('queue_id') or 'the right team'}. Summary: {summary}"
        )

    # ── graph wiring ───────────────────────────────────────────────────

    def _build_graph(self):
        g = StateGraph(TriageState)
        g.add_node("mask_extract", self._mask_extract)
        g.add_node("check_missing", self._check_missing)
        g.add_node("ask_followup", self._ask_followup)
        g.add_node("route", self._route)
        g.add_node("escalate", self._escalate)
        g.add_node("draft_reply", self._draft_reply)

        g.add_edge(START, "mask_extract")
        g.add_edge("mask_extract", "check_missing")
        g.add_conditional_edges(
            "check_missing",
            lambda s: "ask_followup" if s.get("missing") else "route",
            {"ask_followup": "ask_followup", "route": "route"},
        )
        g.add_edge("ask_followup", END)
        g.add_conditional_edges(
            "route",
            lambda s: "escalate"
            if (s.get("blocking_risk") or not (s.get("decision") or {}).get("auto_route"))
            else "draft_reply",
            {"escalate": "escalate", "draft_reply": "draft_reply"},
        )
        g.add_edge("escalate", END)
        g.add_edge("draft_reply", END)
        return g.compile()

    async def triage(
        self,
        *,
        text: str,
        region: str | Region = "IN",
        tenant_id: str | None = None,
        p1: bool = False,
    ) -> dict[str, Any]:
        """Run the triage graph; returns outcome + reply + audit fields."""
        region_value = region.value if isinstance(region, Region) else str(region)
        result = await self._graph.ainvoke(
            {
                "text": text,
                "region": region_value,
                "tenant_id": tenant_id,
                "p1": p1,
            }
        )
        return {
            "outcome": result.get("outcome"),
            "reply": result.get("reply"),
            "missing": result.get("missing") or [],
            "decision": result.get("decision"),
            "candidate": result.get("candidate"),
            "analysis_status": (result.get("analysis") or {}).get("status"),
            "signals": result.get("signals"),
        }


def load_flow_from_policy_file(path: str) -> IntakeTriageFlow:
    """Convenience: build a flow from an approved-routing YAML file."""
    from payce_support.routing_matcher import load_approved_routing_policy

    return IntakeTriageFlow(routing_policy_data=load_approved_routing_policy(path))


__all__ = ["IntakeTriageFlow", "TriageState", "load_flow_from_policy_file", "missing_fields"]
