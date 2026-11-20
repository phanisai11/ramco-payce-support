"""Per-tenant routing context injected into every LLM signal-extraction call.

Each ``routing_policy/tenant_context/<tenant_id>.yaml`` file describes that
tenant's queues, priority guidance, and knowledge-base hints. The LLM sees
this context on every call so priority and entity extraction follow tenant
rules — the deterministic rule engine still makes the final decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from payce_support.models import ImmutableModel


class TenantKbHint(ImmutableModel):
    """Pointer telling retrieval which KB slices are relevant."""

    kb_id: str = Field(min_length=1, max_length=128)
    topics: tuple[str, ...] = ()
    years: tuple[int, ...] = ()
    note: str | None = Field(default=None, max_length=256)

    @field_validator("topics", "years", mode="before")
    @classmethod
    def _lists_to_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class TenantRoutingContext(ImmutableModel):
    tenant_id: str = Field(pattern=r"^C-[0-9]{4,}$")
    display_name: str | None = Field(default=None, max_length=128)
    region: str = Field(pattern=r"^(IN|UAE)$")
    queue_hints: dict[str, str] = {}
    priority_guidance: tuple[str, ...] = ()
    kb_hints: tuple[TenantKbHint, ...] = ()
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("priority_guidance", "kb_hints", mode="before")
    @classmethod
    def _lists_to_tuples(cls, value: object) -> object:
        # YAML parses sequences as lists; strict mode wants tuples.
        return tuple(value) if isinstance(value, list) else value


def load_tenant_contexts(directory: Path | str) -> dict[str, TenantRoutingContext]:
    """Load every ``*.yaml`` tenant context file; filename should match tenant_id."""
    root = Path(directory)
    contexts: dict[str, TenantRoutingContext] = {}
    if not root.is_dir():
        return contexts
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"tenant context {path} must be a YAML mapping")
        ctx = TenantRoutingContext.model_validate(data)
        if ctx.tenant_id in contexts:
            raise ValueError(f"duplicate tenant context for {ctx.tenant_id}")
        contexts[ctx.tenant_id] = ctx
    return contexts
