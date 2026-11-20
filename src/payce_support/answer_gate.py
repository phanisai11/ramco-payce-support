"""Bot-answer vs human-escalation gate.

Case-study basis (Ramco Payce data room):
- 42 agents across L1/L2/L3; SLA penalty *credits* are paid to customers for
  missed commitments → wrong automated answers on statutory/billing topics
  are directly costly.
- Payroll weeks (25th–3rd) are peak load; deflecting how-to/process tickets
  is where the 30% headcount saving actually comes from.
- PF/WPS/statutory, arrears, F&F, and billing queries need authoritative or
  account-specific data the KB excerpts cannot supply → human lane.
- P1, blocking attachments, unresolved tenant, and low retrieval confidence
  always escalate.

The gate is deliberately conservative: the bot only *auto-answers* how-to /
process questions with strong KB grounding; everything else gets a draft for
an agent or goes straight to human.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from payce_support.models import ImmutableModel, Lane
from payce_support.rag import RagIntent, RagResult, RagStatus


class AnswerVerdict(StrEnum):
    AUTO_ANSWER = "auto_answer"
    DRAFT_FOR_AGENT = "draft_for_agent"
    HUMAN_ONLY = "human_only"


class AnswerGatePolicy(ImmutableModel):
    """Thresholds for the bot/human decision (tunable per deployment)."""

    auto_answer_min_score: float = Field(default=0.55, ge=0.0, le=1.0)
    draft_min_score: float = Field(default=0.30, ge=0.0, le=1.0)
    # Auto-answer is reserved for genuine how-to questions. PROCESS is
    # deliberately excluded: the pipeline's fallback intent is PROCESS
    # for anything that isn't phrased as a question, so allowing it
    # would auto-answer problems, outages and change requests.
    auto_answer_intents: tuple[RagIntent, ...] = (RagIntent.HOW_TO,)


# Topics that always need a human: statutory correctness, money, identity.
_HUMAN_ONLY_KEYWORDS = (
    "pf", "epf", "provident", "wps", "sif", "tds", "esi", "gratuity",
    "arrears", "full and final", "fnf", "refund", "billing", "invoice",
    "salary not credited", "not credited", "wrong deduction", "penalty",
    "legal", "compliance notice", "data breach", "termination",
    # Statutory filings & deductions — a wrong automated answer here is
    # directly costly (SLA credits, regulatory exposure).
    "form 16", "form16", "statutory", "professional tax", "ecr",
    "challan", "filing deadline", "compliance filing", "tds return",
    "form 24q", "form24q", "gstr", "gst return",
    # Access/identity outages — cohort lockouts need ops, not a KB echo.
    "cannot access", "can't access", "could not access", "locked out",
    "all employees", "every employee", "employees cannot", "invalid audience",
    "idp migration", "identity provider", "login broken", "sign-in fails",
    "signin fails", "sso fails", "sso broken",
    # Wrong-data / privacy-adjacent — per escalation policy these notify
    # the DPO; never auto-answer.
    "wrong data", "incorrect data", "data mix-up", "data mixup",
    "shows wrong", "showing wrong", "previous month", "another customer",
    "someone else's", "other customer", "belongs to",
    # Defects & failed operations — bugs need engineering, not KB text.
    "computed twice", "duplicate", "rejected by", "rejected the",
    "failed to generate", "generation is failing", "generation failing",
    "not working", "doesn't work", "isn't working",
    # Outages & API failures — production-down reports need ops, and a
    # non-technical customer can't act on debug steps anyway.
    "http 500", "error 500", "500s", "api returning", "api is down",
    "api down", "service down", "service outage", "platform down",
    "blocking our entire", "blocking the entire", "entire disbursement",
    "payroll api", "production api",
)

# Signal intents (from llm_signals/heuristic extraction) that describe a
# problem or change rather than a question — these get a draft for an
# agent at best, never an auto-answer.
_NON_QUESTION_INTENTS = frozenset({
    "problem", "change_request", "billing_refund", "incident", "outage",
    "defect", "bug",
})


def classify_answerability(
    *,
    text: str,
    intent: RagIntent,
    rag: RagResult | None,
    blocking_risk: bool = False,
    p1: bool = False,
    ticket_intent: str | None = None,
    policy: AnswerGatePolicy | None = None,
) -> tuple[AnswerVerdict, tuple[str, ...]] | None:
    """Return (verdict, reason_codes) or None when no RAG result exists.

    Order matters: hard stops first, then confidence bands.
    ``ticket_intent`` is the extractor's intent label (how_to,
    status_enquiry, problem, change_request, …) — problems and changes
    are never auto-answered even when the KB has relevant text.
    """
    policy = policy or AnswerGatePolicy()
    if rag is None:
        return None
    codes: list[str] = []

    if p1:
        return AnswerVerdict.HUMAN_ONLY, ("P1_PRIORITY",)
    if blocking_risk:
        return AnswerVerdict.HUMAN_ONLY, ("BLOCKING_RISK",)

    lowered = text.lower()
    if any(k in lowered for k in _HUMAN_ONLY_KEYWORDS):
        return AnswerVerdict.HUMAN_ONLY, ("STATUTORY_OR_FINANCIAL_TOPIC",)

    if ticket_intent and ticket_intent.lower() in _NON_QUESTION_INTENTS:
        return AnswerVerdict.DRAFT_FOR_AGENT, ("NON_QUESTION_INTENT",)

    if rag.status is RagStatus.ABSTAINED or not rag.answer:
        return AnswerVerdict.HUMAN_ONLY, (f"RAG_ABSTAIN:{rag.reason_code}",)

    # Degenerate drafts (",", "...", one-word echoes) must never reach a
    # customer — they carry no information even when citations exist.
    _ans = rag.answer.strip()
    if len(_ans) < 20 or len(_ans.split()) < 4 or not any(
        ch.isalnum() for ch in _ans
    ):
        return AnswerVerdict.HUMAN_ONLY, ("DEGENERATE_ANSWER",)

    grounded = len(rag.citation_chunk_ids) > 0
    if not grounded:
        return AnswerVerdict.HUMAN_ONLY, ("NO_GROUNDING",)

    if intent in policy.auto_answer_intents:
        return AnswerVerdict.AUTO_ANSWER, ("GROUNDED_KB_ANSWER", "HOW_TO_INTENT")
    return AnswerVerdict.DRAFT_FOR_AGENT, ("NON_HOWTO_INTENT",)


def verdict_to_lane(verdict: AnswerVerdict) -> Lane:
    return {
        AnswerVerdict.AUTO_ANSWER: Lane.AUTO_ANSWER,
        AnswerVerdict.DRAFT_FOR_AGENT: Lane.DRAFT_FOR_AGENT,
        AnswerVerdict.HUMAN_ONLY: Lane.HUMAN,
    }[verdict]
