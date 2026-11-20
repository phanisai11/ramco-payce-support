"""PostgreSQL adapters for object manifests and intake idempotency."""

from __future__ import annotations

import json
from typing import Any, Never
from uuid import UUID

import psycopg
from psycopg import errors as pg_errors

from payce_support.intake import IdempotencyConflictError, IntakeRepository, IntakeResult
from payce_support.models import ObjectManifest, Region
from payce_support.storage import ObjectAlreadyExistsError, ObjectManifestRepository, ObjectNotFoundError


class PostgresObjectManifestRepository(ObjectManifestRepository):
    """
    Persists per-object rows from migration ``002_storage_object_and_idempotency.sql``.

    Tenant rows use ``SET LOCAL app.tenant_id`` in the same transaction as the insert.

    Requires: psycopg (optional dependency group ``gcp``).
    """

    def __init__(self, conninfo: str) -> None:
        self._psycopg = psycopg
        self._conninfo = conninfo

    def append(self, manifest: ObjectManifest) -> None:
        try:
            with self._psycopg.connect(self._conninfo) as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        if manifest.tenant_id is None:
                            if manifest.ingress_id is None:
                                raise ValueError("quarantine manifest requires ingress_id")
                            cur.execute(
                                """
                                INSERT INTO intake_quarantine.storage_object (
                                  object_id, ingress_id, ingress_region, event_id,
                                  attachment_id, object_path, object_generation,
                                  sha256, size_bytes
                                )
                                VALUES (
                                  %s, %s, %s, %s,
                                  %s, %s, %s,
                                  %s, %s
                                )
                                """,
                                (
                                    manifest.object_id,
                                    manifest.ingress_id,
                                    manifest.region.value,
                                    manifest.event_id,
                                    manifest.attachment_id,
                                    manifest.object_path,
                                    int(manifest.generation),
                                    manifest.sha256,
                                    manifest.size_bytes,
                                ),
                            )
                        else:
                            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (manifest.tenant_id,))
                            cur.execute(
                                """
                                INSERT INTO app.storage_object (
                                  tenant_id, object_id, region, event_id,
                                  attachment_id, object_path, object_generation,
                                  sha256, size_bytes
                                )
                                VALUES (
                                  %s, %s, %s, %s,
                                  %s, %s, %s,
                                  %s, %s
                                )
                                """,
                                (
                                    manifest.tenant_id,
                                    manifest.object_id,
                                    manifest.region.value,
                                    manifest.event_id,
                                    manifest.attachment_id,
                                    manifest.object_path,
                                    int(manifest.generation),
                                    manifest.sha256,
                                    manifest.size_bytes,
                                ),
                            )
        except pg_errors.UniqueViolation as exc:
            raise ObjectAlreadyExistsError(str(manifest.object_id)) from exc

    def get_for_tenant(self, *, object_id: UUID, tenant_id: str) -> ObjectManifest:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    cur.execute(
                        """
                        SELECT
                          tenant_id, object_id, region, object_path,
                          object_generation, sha256, size_bytes, event_id,
                          attachment_id
                        FROM app.storage_object
                        WHERE tenant_id = %s AND object_id = %s
                        """,
                        (tenant_id, object_id),
                    )
                    row = cur.fetchone()
        if row is None:
            raise ObjectNotFoundError(str(object_id))
        return ObjectManifest(
            tenant_id=row[0],
            object_id=row[1],
            region=Region(row[2]),
            object_path=row[3],
            generation=str(int(row[4])),
            sha256=row[5],
            size_bytes=int(row[6]),
            event_id=row[7],
            attachment_id=row[8],
            ingress_id=None,
        )


def _record_to_manifest(record: dict[str, Any]) -> ObjectManifest:
    scope = record["storage_scope"]
    if scope == "quarantine":
        return ObjectManifest(
            object_id=record["object_id"],
            tenant_id=None,
            region=Region(record["region"]),
            object_path=record["object_path"],
            generation=str(int(record["object_generation"])),
            sha256=record["sha256"],
            size_bytes=int(record["size_bytes"]),
            event_id=record["event_id"],
            attachment_id=record["attachment_id"],
            ingress_id=record["ingress_id"],
        )
    if scope == "tenant":
        return ObjectManifest(
            object_id=record["object_id"],
            tenant_id=record["tenant_id"],
            region=Region(record["region"]),
            object_path=record["object_path"],
            generation=str(int(record["object_generation"])),
            sha256=record["sha256"],
            size_bytes=int(record["size_bytes"]),
            event_id=record["event_id"],
            attachment_id=record["attachment_id"],
            ingress_id=None,
        )
    unreachable: Never = scope  # type: ignore[assignment]
    raise RuntimeError(f"unknown storage_scope {unreachable!r}")


class PostgresIntakeRepository(IntakeRepository):
    """Idempotency in ``intake_quarantine.intake_idempotency`` (pre-tenant key)."""

    def __init__(self, conninfo: str) -> None:
        self._psycopg = psycopg
        self._conninfo = conninfo

    def get(self, idempotency_key: str) -> IntakeResult | None:
        with self._psycopg.connect(self._conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT result_json FROM intake_quarantine.intake_idempotency
                    WHERE idempotency_key = %s
                    """,
                    (idempotency_key,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            return IntakeResult.model_validate_json(payload)
        return IntakeResult.model_validate(json.dumps(payload))

    def append(self, idempotency_key: str, result: IntakeResult) -> None:
        fingerprint = result.request_fingerprint
        payload = result.model_dump_json()
        try:
            with self._psycopg.connect(self._conninfo) as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO intake_quarantine.intake_idempotency (
                              idempotency_key, request_fingerprint, result_json
                            )
                            VALUES (%s, %s, %s::jsonb)
                            """,
                            (idempotency_key, fingerprint, payload),
                        )
        except pg_errors.UniqueViolation:
            existing = self.get(idempotency_key)
            if existing is None:
                raise RuntimeError(  # pragma: no cover
                    "idempotency unique violation but row not readable",
                )
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflictError(
                    "source_event_id was reused with different body or attachments",
                ) from None
