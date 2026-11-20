-- tenant_profile is the tenant registry, not tenant data: the app role
-- must enumerate active tenants WITHOUT a tenant context (RLS has no
-- cross-tenant reads — see PostgresTicketPipeline.list_ticket_current).
-- Locally this was invisible because the embedded pgserver user is a
-- superuser (superusers bypass RLS); on RDS payce_app is not.
-- SELECT-only, permissive policy: writes remain governed by
-- tenant_profile_isolation.
CREATE POLICY tenant_profile_registry_read ON app.tenant_profile
  FOR SELECT TO payce_app
  USING (true);
