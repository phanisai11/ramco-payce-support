"""Server-side tenant resolution. Untrusted payload fields are never consulted."""

from __future__ import annotations

from collections.abc import Iterable

from payce_support.models import (
    AuthenticatedIngressContext,
    TenantRecord,
    TenantResolution,
    TenantResolutionMethod,
    TenantResolutionStatus,
)


class TenantResolver:
    def __init__(
        self,
        tenants: Iterable[TenantRecord],
        verified_contacts: dict[str, set[str]] | None = None,
        *,
        mapping_version: str,
    ) -> None:
        self._tenants = {tenant.tenant_id: tenant for tenant in tenants}
        self._verified_contacts = verified_contacts or {}
        self._mapping_version = mapping_version

    def resolve(self, context: AuthenticatedIngressContext) -> TenantResolution:
        if context.authenticated_tenant_id is not None:
            return self._resolve_tenant(
                context.authenticated_tenant_id,
                context,
                TenantResolutionMethod.AUTHENTICATED_SESSION,
            )

        if context.verified_contact_id is None:
            return self._unresolved("no authenticated tenant or verified contact")

        candidates = self._verified_contacts.get(context.verified_contact_id, set())
        if len(candidates) == 0:
            return self._unresolved("verified contact has no tenant mapping")
        if len(candidates) > 1:
            return TenantResolution(
                status=TenantResolutionStatus.CONFLICT,
                method=None,
                mapping_version=self._mapping_version,
                reason="verified contact maps to multiple tenants",
            )

        return self._resolve_tenant(
            next(iter(candidates)),
            context,
            TenantResolutionMethod.VERIFIED_CONTACT,
        )

    def _resolve_tenant(
        self,
        tenant_id: str,
        context: AuthenticatedIngressContext,
        method: TenantResolutionMethod,
    ) -> TenantResolution:
        tenant = self._tenants.get(tenant_id)
        if tenant is None or not tenant.active:
            return self._unresolved("tenant does not exist or is inactive")
        if tenant.region is not context.ingress_region:
            return TenantResolution(
                status=TenantResolutionStatus.CONFLICT,
                method=None,
                mapping_version=self._mapping_version,
                reason="tenant region conflicts with authenticated ingress region",
            )
        return TenantResolution(
            status=TenantResolutionStatus.RESOLVED,
            method=method,
            mapping_version=self._mapping_version,
            tenant=tenant,
        )

    def _unresolved(self, reason: str) -> TenantResolution:
        return TenantResolution(
            status=TenantResolutionStatus.UNRESOLVED,
            method=None,
            mapping_version=self._mapping_version,
            reason=reason,
        )
