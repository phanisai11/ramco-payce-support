# GCP implementation design index

| Document | Role |
|----------|------|
| [`SAAS_TENANT_DATA_CONTRACT.md`](SAAS_TENANT_DATA_CONTRACT.md) | **Build contract:** immutable input, tenant/file isolation, canonical payloads, composite keys + FORCE RLS, routing decision schema |
| [`PRODUCTION_DESIGN_GCP.md`](PRODUCTION_DESIGN_GCP.md) | **Prod norm:** rule-first routing (no vector neighbor routing), 99% per approved rule, triage default |
| [`EVAL_GATES_GCP.md`](EVAL_GATES_GCP.md) | **Ship gates G0–G7**, repo data mapping, CI (pytest, Ragas, Promptfoo) |
| [`ARCHITECTURE_GCP.html`](ARCHITECTURE_GCP.html) | Layer diagram, services, flows, § Production economics |
| [`TICKET_INTELLIGENCE_DESIGN.html`](TICKET_INTELLIGENCE_DESIGN.html) | Intake, multimodal phasing, classifier cascade, agent flow |
| [`AIFlowDesign.md`](AIFlowDesign.md) | Product constraints: copilot scope, USD 4,500 cap, risks |
| [`CLIENT_PAIN_POINTS_AND_ARCHITECTURE_REQUIREMENTS.md`](CLIENT_PAIN_POINTS_AND_ARCHITECTURE_REQUIREMENTS.md) | Evidence-backed requirements |

**Machine-readable contracts:** `schemas/raw-intake-event.schema.json`, `schemas/canonical-ticket-event.schema.json`, `schemas/routing-decision.schema.json`

**Eval scaffold:** `eval/gates/`, `eval/promptfoo/`, `cloudbuild/pr-eval.yaml`
