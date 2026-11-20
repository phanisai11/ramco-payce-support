"""G7 — cost governor ledger + billing-export reconciliation.

Cloud Billing concepts: https://cloud.google.com/billing/docs
BigQuery billing export: https://cloud.google.com/billing/docs/how-to/export-data-bigquery

``CostLedger`` tracks app-side charges for a bounded window (e.g. pilot week).
``reconcile_cost_ledger`` compares the ledger total against the authoritative
billing export total so drift between app telemetry and the invoice is caught
before the cap is breached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol


@dataclass
class CostLedger:
    """Running sum of USD charges for a bounded window (e.g. pilot week)."""

    cap_usd: float
    charges_usd: list[float] = field(default_factory=list)

    def record(self, usd: float) -> None:
        if usd < 0:
            raise ValueError("charge must be non-negative")
        self.charges_usd.append(usd)

    def total_usd(self) -> float:
        return sum(self.charges_usd, start=0.0)

    def is_over_cap(self) -> bool:
        return self.total_usd() > self.cap_usd


@dataclass(frozen=True)
class BillingWindow:
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        start = self.start if self.start.tzinfo else self.start.replace(tzinfo=timezone.utc)
        end = self.end if self.end.tzinfo else self.end.replace(tzinfo=timezone.utc)
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        return start <= moment < end


class BillingExportSource(Protocol):
    """Authoritative billed-cost totals for a window (USD)."""

    def total_cost_usd(self, *, window: BillingWindow) -> Decimal: ...


@dataclass(frozen=True)
class ReconciliationReport:
    window_start: datetime
    window_end: datetime
    ledger_total_usd: Decimal
    billed_total_usd: Decimal
    drift_usd: Decimal
    within_tolerance: bool
    ledger_over_cap: bool


class JsonlBillingExport:
    """Reads a billing-export JSONL extract (one row per cost line).

    Expected row fields (BigQuery export column names):
    ``cost`` (string/number), ``currency_code``, ``usage_start_time`` (ISO).
    Only ``USD`` rows inside the window are summed.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def total_cost_usd(self, *, window: BillingWindow) -> Decimal:
        total = Decimal("0")
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                if str(row.get("currency_code", "USD")).upper() != "USD":
                    continue
                usage_raw = row.get("usage_start_time")
                if usage_raw is None:
                    continue
                try:
                    usage = datetime.fromisoformat(str(usage_raw).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if not window.contains(usage):
                    continue
                try:
                    total += Decimal(str(row.get("cost", "0")))
                except Exception:
                    continue
        return total


class BigQueryBillingExport:
    """Live billing-export reader via google-cloud-bigquery (optional dep).

    Requires: ``google-cloud-bigquery`` (not in default extras; install for GCP).
    Table ref format: ``project.dataset.table``.
    """

    def __init__(self, *, project: str, dataset: str, table: str) -> None:
        from google.cloud import bigquery  # noqa: PLC0415 - optional dep

        self._client = bigquery.Client(project=project)
        self._table = f"`{project}.{dataset}.{table}`"

    def total_cost_usd(self, *, window: BillingWindow) -> Decimal:
        query = f"""
            SELECT COALESCE(SUM(cost), 0) AS total
            FROM {self._table}
            WHERE currency_code = 'USD'
              AND usage_start_time >= @start
              AND usage_start_time < @end
        """
        from google.cloud import bigquery  # noqa: PLC0415

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "TIMESTAMP", window.start),
                bigquery.ScalarQueryParameter("end", "TIMESTAMP", window.end),
            ],
        )
        rows = self._client.query(query, job_config=job_config).result()
        row = next(iter(rows), None)
        return Decimal(str(row["total"])) if row else Decimal("0")


def reconcile_cost_ledger(
    *,
    ledger: CostLedger,
    source: BillingExportSource,
    window: BillingWindow,
    tolerance_usd: Decimal = Decimal("0.01"),
) -> ReconciliationReport:
    """Compare app-side ledger charges with the billing export total."""
    ledger_total = Decimal(str(ledger.total_usd()))
    billed_total = source.total_cost_usd(window=window)
    drift = ledger_total - billed_total
    return ReconciliationReport(
        window_start=window.start,
        window_end=window.end,
        ledger_total_usd=ledger_total,
        billed_total_usd=billed_total,
        drift_usd=drift,
        within_tolerance=abs(drift) <= tolerance_usd,
        ledger_over_cap=ledger.is_over_cap(),
    )
