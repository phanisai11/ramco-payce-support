"""Read/write helpers for the YAML policy files (routing + SLA).

Edits are persisted back to the YAML file and the in-memory policy dict is
mutated in place, so running processors/routers see the change immediately
without a restart. Note: ``yaml.safe_dump`` does not preserve comments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def update_yaml_rule(
    path: Path,
    *,
    rule_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Apply ``updates`` to the rule with ``id == rule_id`` and save the file.

    Returns the updated policy document. Raises KeyError if the rule or the
    rules list is missing.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise KeyError("policy file has no rules list")
    found = False
    for rule in data["rules"]:
        if isinstance(rule, dict) and rule.get("id") == rule_id:
            rule.update(updates)
            found = True
            break
    if not found:
        raise KeyError(f"rule {rule_id!r} not found")
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return data
