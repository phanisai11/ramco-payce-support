-- Priority-scoped SLA rules (Schedule 3 response targets): a rule may
-- restrict itself to derived priorities (p1/p2/p3). Empty array = all.
ALTER TABLE app.sla_rule
  ADD COLUMN IF NOT EXISTS applies_to_priorities text[] NOT NULL DEFAULT '{}';
