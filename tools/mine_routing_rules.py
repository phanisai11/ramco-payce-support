#!/usr/bin/env python3
"""Offline routing rule mining placeholder (P2).

Full implementation: join `eval/datasets/routing_gold.jsonl` with feature extractors,
emit `routing_policy/candidates.yaml` with precision/recall/support per rule, then promote
to `routing_policy/approved.yaml` only if precision ≥ 0.99 on holdout and support ≥ 20
(`eval/datasets/README.md`).

argparse: https://docs.python.org/3/library/argparse.html
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine routing rule candidates from labelled gold (stub).",
    )
    parser.add_argument(
        "--gold",
        default="eval/datasets/routing_gold.jsonl",
        help="Path to routing_gold.jsonl",
    )
    parser.add_argument(
        "--out",
        default="routing_policy/candidates.yaml",
        help="Output path for candidates (not written in stub)",
    )
    args = parser.parse_args()
    print("mine_routing_rules: stub — implement feature extraction + holdout metrics.")
    print(f"  gold={args.gold} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
