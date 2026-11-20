"""Statutory rule resolution — authoritative numbers are not from RAG (G5a).

Rule pack format: `data-room/integrations/statutory_rules.json` (simulated teaching data).
datetime.date: https://docs.python.org/3/library/datetime.html#date-objects
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict


class StatutoryRuleRecord(BaseModel):
    # JSON uses ISO date strings and JSON arrays; allow coercion (not strict).
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    region: str
    topic: str
    title: str
    effective_from: date
    old_value: str
    new_value: str
    affects_kb_topics: list[str] = []
    source: str
    status: str


class StatutoryFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disclaimer: str
    rules: list[StatutoryRuleRecord]


class StatutoryResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule_id: str
    region: str
    topic: str
    effective_from: date
    value_applied: str
    source: str


class StatutoryEngine:
    """Load rules from JSON; resolve value for a jurisdiction + topic + as-of date."""

    def __init__(self, facts: StatutoryFacts) -> None:
        self._facts = facts

    @classmethod
    def load(cls, path: Path | str) -> StatutoryEngine:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(facts=StatutoryFacts.model_validate(raw))

    def resolve(
        self,
        *,
        region: str,
        topic: str,
        as_of: date,
    ) -> StatutoryResolution:
        candidates = [r for r in self._facts.rules if r.region == region and r.topic == topic]
        if not candidates:
            raise LookupError(f"no statutory rule for region={region!r} topic={topic!r}")

        ordered = sorted(candidates, key=lambda r: r.effective_from)
        chosen: StatutoryRuleRecord | None = None
        for rule in reversed(ordered):
            if as_of >= rule.effective_from:
                chosen = rule
                break

        if chosen is not None:
            return StatutoryResolution(
                rule_id=chosen.rule_id,
                region=chosen.region,
                topic=chosen.topic,
                effective_from=chosen.effective_from,
                value_applied=chosen.new_value,
                source=chosen.source,
            )

        first = ordered[0]
        return StatutoryResolution(
            rule_id=first.rule_id,
            region=first.region,
            topic=first.topic,
            effective_from=first.effective_from,
            value_applied=first.old_value,
            source=first.source,
        )

    def raw_rules(self) -> Sequence[StatutoryRuleRecord]:
        return tuple(self._facts.rules)
