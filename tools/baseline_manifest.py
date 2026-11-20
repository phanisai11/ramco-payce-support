#!/usr/bin/env python3
"""Print baseline fixture stats from eval/fixtures/manifest.json (P1/C0 measurement hook).

Run locally or in CI before tuning thresholds. Python argparse: https://docs.python.org/3/library/argparse.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize eval fixture manifest for baselines.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("eval/fixtures/manifest.json"),
        help="Path to manifest.json",
    )
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))

    boundary = data.get("g2_boundary", {})
    print("Eval fixtures baseline (measurement-driven thresholds added elsewhere)")
    print(f"  schema_version: {data.get('schema_version')}")
    print(f"  tenant_a: {data.get('tenant_ids', {}).get('tenant_a')}")
    print(f"  tenant_b: {data.get('tenant_ids', {}).get('tenant_b')}")
    for key in sorted(boundary):
        print(f"  g2_boundary.{key}: {boundary[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
