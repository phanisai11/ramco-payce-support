# Production design · GCP · Ramco Payce Support Intelligence

**Status:** Implementation guide for prod-bound build (not demo-only).  
**Artifacts:** `SAAS_TENANT_DATA_CONTRACT.md` (**normative data contract**), `ARCHITECTURE_GCP.html`, `TICKET_INTELLIGENCE_DESIGN.html`, `EVAL_GATES_GCP.md`, `AIFlowDesign.md` (product constraints).

This document closes the gaps from the FDE evals note and addresses **why most enterprise AI support projects fail**, with concrete **routing, cost, extraction, latency, and tool** rules.

---

## 0. Build sign-off: SaaS invariants

The next phase must implement `SAAS_TENANT_DATA_CONTRACT.md` before any UI or LLM feature:

1. `tenant_id` and region are **server-derived** from authenticated channel/account mappings; customer payload fields and model output are never authorization evidence.
2. Raw ticket events and original files are **write-once**. No service edits received input; corrections and reprocessing create append-only events/versions.
3. Files are stored in **regional buckets under tenant-bound UUID paths** and read only through a StorageBroker that verifies principal, tenant, resource, and purpose. There is no direct bucket access or caller-supplied object path.
4. Every tenant-owned database row uses `tenant_id NOT NULL`, composite tenant foreign keys, and **FORCE RLS**; runtime roles cannot bypass RLS.
5. Unresolved/conflicting tenant identity is quarantined and gets **no retrieval, file, cache, engine, or other tenant-scoped tool access**.
6. Routing is “perfect” only by **abstaining**: G3-approved decisions may auto-route; all ambiguity and risk go to human triage. Cross-tenant access target is **zero**.

---

## 1. Why AI support projects fail (and our countermeasures)

| Failure mode | Typical symptom | Payce evidence | Design countermeasure |
|--------------|-----------------|----------------|----------------------|
| **Facts from RAG** | Confident wrong PF/WPS numbers | KB-0142, KB-0201 | **Extraction ladder** (§2); engine for all rates |
| **Deflection without quality** | “38% resolved”, 41% abandon | Converso post-mortem | Success = **7-day reopen**, not bot close |
| **LLM on every step** | Cost/latency explode at peak | June USD 41k | **Code router + abstain**; Flash-only on hot path |
| **Retries = 2× spend** | Pub/Sub redelivery re-embeds | — | **Pipeline fingerprint + stage cache** (§4) |
| **No CI gates** | Model swap silently breaks routing | FDE note | **G0–G7** in `EVAL_GATES_GCP.md` |
| **Stale vectors** | Old KB chunks rank high | Aug credits spike | **valid_to** filter + exclude stale at index time |
| **Agent bypass** | Sidebar ignored | L1 paste wrong KB | Copilot **draft + sources**; same verifier as auto |
| **Peak polling** | 429, 2 AM pages | Meera macro | Webhooks + Redis; **no LLM on status** |
| **Scope = general chatbot** | Money/refund automation | PN-101129 | **Lanes** block tools; human for money |

---

## 2. Extraction-first answer ladder (RAG is last, not first)

Order every ticket resolution attempt **top to bottom**; stop at first sufficient layer. **Do not call RAG** if a higher layer already answers.

```text
L0  Policy gate     → block / human-only (injection, ID doc, refund)     [0 LLM]
L1  Structured extract → regex + dictionaries on canonical text            [0 LLM]
      run_id, period, Jira PNJ-*, KB-id mention, error codes, refund ref
L2  Lookup tables   → Cloud SQL: tenant, tier, SLA, queue roster           [0 LLM]
L3  Redis facts     → run status (webhook), statutory cache (region+as_of) [0 LLM]
L4  Engine API      → /statutory/rates, read-only payroll facts           [0 LLM]
L5  File validators → WPS SIF / ECR / bank layout vs versioned spec       [0 LLM]
L6  Template fill   → statutory/status answers from L3+L4 fields           [0–1 LLM phrase only]
L7  Trust-ranked RAG → how-to only; chunks with valid_from ≤ t_as_of       [embed + retrieve]
L8  Compose         → Gemini drafts **from supplied context only**         [1 LLM]
L9  Verifier        → numbers=c engine; citations present; DLP out          [0 LLM]
```

**RAG eligibility (all must hold):** intent ∈ {how_to, process}, no money movement, no statutory number question, retrieval confidence ≥ τ, at least one chunk passes date filter.

**Anti-pattern removed:** “embed ticket → retrieve → hope” for “What is PF ceiling for June 2026?” → must be **L4 only**.

### 2A. RAG design: region, effective time, and ticket timing (normative)

How-to RAG (L7–L8 only) must be **partitioned and filtered** so retrieval cannot mix jurisdictions or effective dates. This matches operational reality (IN vs UAE rules, monthly statutory changes) and aligns with external RAG engineering practice: Barnett et al. (“Seven Failure Points When Engineering a Retrieval Augmented Generation System”, CAIN 2024; [DOI 10.1145/3644815.3644945](https://doi.org/10.1145/3644815.3644945), [arXiv:2401.05856](https://arxiv.org/abs/2401.05856)) — e.g. missing or wrong-ranked context (FP1–FP2), consolidation dropping the right chunk (FP3), and contradictory context confusing the LLM (FP4). **G5b/G5c/G6** are our gates for retrieval, faithfulness, and numeric verification.

| Dimension | Rule |
|-----------|------|
| **Region** | **Separate retrieval scope per tenant region** (IN vs UAE at minimum). Never retrieve across regions in one query. Chunk metadata carries `region`; index names or `WHERE` filters enforce isolation. |
| **Effective time (`as_of`)** | Derive **`t_as_of`** for KB search: (1) payroll **period** from L1 (e.g. “June 2026 run”) when present; else (2) **question time** from the ticket (canonical `received_at` / last message UTC); use `min(t_as_of, now_utc)` to avoid future-dated policy. Statutory **values** still come only from **L4 engine** with the same `as_of` — RAG never supplies numbers. |
| **Arrival period (payroll week)** | Tag tickets and optionally chunk metadata with **payroll_week** (e.g. calendar flag 25th→3rd) for observability and load shedding. In peak week: **stricter** retrieval (lower top-k or higher τ), **prefer template/status paths** (L3–L6); do not add extra retrieve rounds. |
| **Ticket input timing** | Persist **per-message timestamps** in the canonical ticket. Re-classification and re-retrieve use the **latest customer message time** as `t_as_of` when the customer refines the period (“actually August not June”). |
| **Metadata on every chunk** | `kb_id`, `snapshot_version`, `valid_from`, `valid_to`, `region`, `topic`, `language` (where applicable). Stale chunks excluded at index time or filtered in SQL/pgvector. |
| **Consolidation** | Top-k is small (≤5). If chunks disagree on facts, **do not compose** — hand to human (FP4). Numbers in draft must still pass **G6** vs engine when any statutory digit appears. |

**Research takeaway:** Barnett et al. stress that RAG robustness **evolves in operation** — continuous monitoring and labeled evals (our G5b/G5c + Promptfoo cases) are mandatory, not optional.

### Multimodal (prod phasing)

| Phase | Scope |
|-------|--------|
| **MVP (go-live)** | Text + metadata; attachment **metadata only** (type, hash); payslip images → human queue |
| **M1** | Document AI OCR for error screenshots; still **L1 extract** before RAG |
| **M2** | Payslip PDF OCR → L1 entities only; never index raw OCR into vector store |

---

## 3. Smart routing — 99% on **proven rules only** (realistic data)

### 3.0 What we actually have today

| Asset | Location | Usable for auto-route? |
|-------|----------|------------------------|
| Ticket export (Q3 sample, messy categories) | `data-room/data/tickets_export.json` | **No** as labels — customer/agent dropdown is wrong ~13%+ |
| Customers / tier | `customers.csv` | **Yes** — lookup only |
| SLA | `process/sla_contract_excerpt.md` | **Yes** — lookup only |
| KB, rules | `kb_articles.json`, `statutory_rules.json` | **Answers**, not queue labels |
| **Human adjudicated gold** | **You must create** `eval/datasets/routing_gold.jsonl` | **Only** basis for claiming 99% |

**Do not treat the raw export category field as ground truth.** Mining rules from export means: label a subset with Support leads, then mine **high-precision patterns** from text + entities, not “similar ticket voted.”

### 3.1 Decision tree (normative — same as ops review)

```text
For each (axis, queue) decision:

Is there a RULE with ≥99% precision on gold holdout?
├── YES → Ship rule in routing_policy vN. auto_route allowed. No ML.
└── NO  → Is there an OFFLINE scorer (see §3.3) calibrated to ≥99% precision
          on holdout with ≥N support tickets per class?
          ├── YES → Ship scorer artifact vN; auto_route only when score ≥ τ and G3 passes.
          └── NO  → auto_route = FALSE. Human triage (prefilled hints OK). Weekly: promote
                    agent-confirmed patterns into NEW rule candidates → re-mine → G3.
```

**Embedding-based similarity routing** (nearest-neighbor voting on ticket text) **is not used** for production auto-queue. Reasons:

- Q3 sample is small, biased, and **mislabeled** in source fields.  
- “Similar wording” drifts with new product language; one bad resolved ticket poisons neighbors.  
- Requires continuous embedding spend on every ticket for uncertain accuracy gain.  
- Hard to explain to Compliance (“why this queue?” cannot be “another ticket looked alike”).  
- Conflicts with DPIA posture: do not treat historical ticket bodies as a growing training store in prod.

**pgvector stays in architecture for:** KB retrieval (L7), **incident clustering** (symptom **fingerprints first**, optional embedding only on ambiguous ties) — **not** for automatic queue assignment from ticket similarity.

### 3.2 Production routing cascade (v1 ship)

| Step | Mechanism | auto_route? | Cost |
|------|-----------|-------------|------|
| ① | **Hard rules** — regex, dictionaries, error codes, attachment class, injection/money detectors, tenant region from lookup | **Yes**, if rule id ∈ `g3_approved_rules` | ~0 |
| ② | **Policy tables** — mined decision stubs (IF entities + keywords + channel → queue) shipped as YAML/SQL, each row proven ≥99% on holdout | **Yes**, per row approval | ~0 |
| ③ | **Offline routing scorer** (optional v1.1+) — linear/multinomial model trained **only** on `routing_gold.jsonl` in CI, artifact in GCS; **not** neighbor search | **Yes**, if `p ≥ τ` and row in G3 manifest | ~0 at runtime |
| ④ | **Suggest** — keyword buckets from export frequency (no queue write) | No | ~0 |
| ⑤ | **LLM hint** (Flash, closed labels) — sidebar text only | No | ≤10% tickets |
| ⑥ | **Human triage** — default for long tail | No | human |

**Hard rules:** `decided_by ∈ {rule, policy_table, routing_scorer}` only for `auto_route=true`. LLM and suggest layers **never** write `queue_id`.

**Realistic coverage at go-live:** expect **40–60%** of tickets auto-routed by rules (high precision, narrow patterns), **40–60%** triage — that is acceptable; **wrong auto-route near 0%** matters more than coverage.

### 3.3 Training path when rules are not enough (no similarity routing)

When the decision tree reaches “NO rule ≥99%”:

1. **Expand human gold** — leads label more export tickets into `routing_gold.jsonl` (target 400–800 with queue + domain).  
2. **Rule induction offline** — mine candidate rules (decision tree → export as Python/YAML); keep only leaves with ≥99% precision and ≥20 support on holdout.  
3. **If still uncovered** — train a **small supervised scorer** offline (e.g. hashed n-grams + region + channel → queue), version as `routing_scorer_{hash}.pkl`, run **only** in CI against G3; deploy if ≥99% precision at chosen τ.  
4. **Never** deploy “maintain a vector index of tickets and assign queue by neighbor vote” as the auto-route engine.

Agent overrides in prod feed **`rule_candidate`** queue (weekly review), not live index updates.

### 3.4 Modes (unchanged intent, realistic mix)

| Mode | Accuracy target | AI |
|------|-----------------|-----|
| **Auto-route** | **≥99% precision** on holdout per shipped rule/scorer row | None |
| **Suggest-route** | Measured; agent accepts/overrides | Optional Flash hint |
| **Triage** | Default | None required |

**Steady-state target:** ≥**90%** of auto-routes from **rules/policy tables**; ≤**10%** from offline scorer rows; **LLM routing hints ≤5%** of all tickets.

### 3.5 Lane → machinery (cost caps)

| Lane | Max LLM calls / ticket event | Max tool calls | Target p95 latency (peak week) |
|------|------------------------------|----------------|--------------------------------|
| block / incident | 0 | 0 | &lt; 500 ms ack |
| status | 0 | 1 (Redis; engine on miss via Tasks) | **&lt; 3 s** first reply |
| file_check | 0 | 0 (validator lib) | &lt; 5 s |
| auto_answer | 0–1 (phrase) if template; else 1 compose | 0–1 retrieve | **&lt; 3 s** |
| draft_for_agent | 1 compose | 1 retrieve batch | &lt; 8 s (async OK) |
| review / human | 0 until agent opens sidebar | 0 | N/A |

### 3.6 If you want better than this (without changing the whole architecture)

The rule-first + triage-default model is the right **prod baseline** given only JSON/CSV and bad dropdown labels. To **achieve** 99% where it matters **and** save cost, prefer these upgrades **in order** (each is optional; do not do all at once):

| Priority | Better than… | Why it helps |
|----------|----------------|--------------|
| **1** | Expecting high auto-route % on day one | **Two-speed routing:** auto = G3-approved rules only; everything else = **assist** (prefilled queue + one-click confirm). You get measured accuracy and faster handles without claiming 99% on the full stream. |
| **2** | Multiclass offline scorer early | **Stack of binary rule packs** (`is_wps_file?`, `is_statutory_rate?`, `is_run_status?`) each gated at 99% on gold — easier for Compliance to audit than one opaque model. |
| **3** | Mining labels from `category` field | Use export **final queue after reassignments** (weak label) only to **propose** candidates; Support lead must still approve before G3. |
| **4** | LLM routing hints | **Structured intake** (required topic + run_id on widget/partner) — removes ambiguity before classification; cheapest latency win. |
| **5** | Curated exemplars later | If gold grows: **fixed exemplar lists** (≤50 curated texts per queue, versioned in repo), high-threshold embedding match + **human confirm** only — not a growing production index of all tickets. |
| **6** | Chasing 99% on all tickets | Keep the metric on **`auto_route=true` only**; P1/risk/money **100% human**; accept 40–60% triage at launch — **wrong auto-route ≈ 0%** is the product goal. |

**Do not add next:** live embedding per ticket for queue vote, or LLM-as-router, until you have hundreds of **human** labels per queue and G3 still passes.

---

## 3A. When AI is allowed (intelligence, not trend)

Use AI **only** when cheaper deterministic layers cannot decide safely.

| Task | Prefer | AI (Vertex) only if |
|------|--------|---------------------|
| Queue / domain | **Approved rules + policy tables** | Scorer row or suggest only; else triage |
| Priority / P1 | Rules + asymmetric “raise not lower” | Never lower priority via LLM |
| Statutory **values** | Engine API + template | **Never** — LLM may rephrase template only |
| Run / refund **status** | Redis + webhook | **Never** |
| File format errors | Validators | **Never** |
| Incident cluster | **Symptom fingerprint** (error code + keyword bucket + window count); embed cluster only if fingerprint ties | No LLM for threshold |
| How-to **draft** | Retrieve + **one** compose | Sources insufficient → hand off |
| OCR / image | Document AI → **L1 fields** | Not for routing |
| Ambiguous text hint | Keyword buckets / optional Flash | **Suggest only**, never auto-route |

**Forbidden in prod v1:** vector / embedding similarity as the **auto-queue** decision; LLM on every message; RAG for rates; autonomous money/statutory send.

---

## 4. Idempotent pipeline — reruns must not cost more

Every **`ticket_event`** (create, message, attachment ready, reopen) gets:

```text
pipeline_run_id   = uuid
content_fingerprint = SHA256(tenant_id + raw_event_sha256 + canonical_version)
decision_fingerprint = SHA256(content_fingerprint + policy_version + model_config_hash)
stage_cache_key     = SHA256(decision_fingerprint + stage_name + stage_config_version)
```

**Rules:**

0. Raw event/file objects are created with `ifGenerationMatch=0`, hash + generation recorded, and cannot be updated/deleted by runtime roles. Canonical/event/routing versions are append-only; mutable `*_current` rows are projections only.
1. Before any **paid** step (embed, LLM, Document AI), read `stage_result` from Cloud SQL or Redis (`TTL 7d`). If hit → **skip API**, attach cached output to audit log with `"cache_hit": true`.  
2. Pub/Sub **at-least-once** delivery: handler must be **idempotent** on `pipeline_run_id` + stage.  
3. **Retries** (transient 503): exponential backoff, **max 2** attempts per stage; third failure → human queue, **no further LLM**.  
4. **Re-open** or new customer message → bump `canonical_ticket_version` → new fingerprint; only **downstream** stages re-run; reuse embed if body unchanged (fingerprint of body text only).  
5. **Agent clicks “Regenerate draft”** → new `pipeline_run_id` but **bill only delta** if context unchanged (same retrieve cache); if user edits context, new retrieve allowed once per click (rate limit 3/hour/ticket).

**Audit field (required):** `{ stage, cache_hit, tokens_in, tokens_out, cost_usd, model_id }` → BigQuery `ds_audit.events`.

This directly fixes “reruns should not cost more.”

---

## 5. Semantic cache (cheap repeats, safe thresholds)

Separate from statutory Redis cache:

| Cache | Key | Value | Invalidation |
|-------|-----|-------|--------------|
| **Answer template** | `env + region + tenant_id + tenant_tier + rule_version + intent + normalized_q_hash` | Approved text + source ids | Rule change event |
| **Retrieve set** | `env + region + tenant_id_or_global + t_as_of + payroll_week_flag + q_embed_hash` | chunk ids + scores | KB snapshot version or rule change |
| **Embed once** | `env + region + tenant_id + body_redacted_hash` | vector | Never for same tenant + hash |

**Semantic near-duplicate:** cosine ≥ **0.92** (tune on Q3 offline; **lower τ increases wrong-answer risk**). Only for **auto_answer** lane, PII-free templates, non-statutory. Statutory/status **never** use semantic cache—use L3 Redis + engine only.

---

## 6. Tool broker (better usage, hard limits)

Single **ToolBroker** service; composer **does not** call APIs directly.

```text
Authenticated context supplies tenant_id + region (never model/request payload)
Router emits ToolPlan { allowed: [redis_get_run, engine_statutory, retrieve_kb], max_calls: N }
Composer requests tool by name → broker resolves resource tenant and checks
principal + tenant + region + allowlist + purpose + rate limit
```

| Tool | When allowed | Rate |
|------|--------------|------|
| `engine_statutory` | L4 statutory lane only | Cache-first; ≤1 engine call per fingerprint |
| `redis_run_status` | status lane | Unlimited read |
| `retrieve_kb` | L7 eligible | 1 call returns top-k ≤5 chunks |
| `jira_known_issue` | defect lane | 1 call |
| **Forbidden** | refund, approve, write payroll | — |

**Parallelism:** `redis_run_status` + `jira_known_issue` may run in parallel in **draft_for_agent** only; auto path stays sequential for predictable latency.

**No tool loops:** composer `max_tool_rounds = 1` on auto path, `2` on agent draft.

---

## 7. Latency budget (peak week, 25th–3rd)

End-to-end **first customer-visible reply** (auto lanes):

| Segment | Budget |
|---------|--------|
| Ingest + DLP + normalise | 400 ms |
| Classify cascade (no LLM) | 150 ms |
| Classify Flash (if needed) | 800 ms |
| Retrieve + Redis/engine | 600 ms |
| Compose Flash (if needed) | 900 ms |
| Verifier + persist | 150 ms |
| **Total target p95** | **≤ 3.0 s** |

Heavy OCR/voice: **async** — customer gets “We received your file; an agent will confirm” within 500 ms; processing continues via Workflows.

**Load shedding:** if `spend:{day}` &gt; 90% cap or LLM quota high → disable compose; serve **template + human queue** (intake never stops).

---

## 8. Cost efficiency vs today’s ~USD 6/ticket

| Line | Planning note |
|------|----------------|
| **Benchmark** | ~USD 6.16/ticket all-in (Sep cost sheet); pitch **savings vs this**, not vs USD 4,500 cap alone |
| **AI incremental cap** | USD 4,500/mo hard stop (`AIFlowDesign.md`) |
| **Target AI unit cost** | ≤ USD 0.05 per **ticket ingested** (routing+incident); ≤ USD 0.25 per **eligible compose** |
| **Pilot run-rate (order of magnitude)** | USD 400–800/mo at 15k tickets if ladder enforced (most tickets never hit L8) |
| **One-off build** | Engineering + DPIA + eval harness (budget separately in business case) |

**Cost drivers to eliminate:**

- Re-embedding unchanged text on redelivery  
- Flash on every message for routing  
- RAG + compose for status/statutory  
- Ungoverned “regenerate” in sidebar  

---

## 9. Build order (snake, smoke test each step)

1. Tenant registry + authenticated channel mapping + regional buckets + StorageBroker  
2. Immutable raw envelope/file manifest (`ifGenerationMatch=0`, hash, generation) + tenant isolation gate G0  
3. Cloud SQL schema: composite tenant keys + FORCE RLS + append-only event/decision tables  
4. Ingest/normalise + strict JSON Schema/boundary tests (G2)  
5. PII/malware/attachment gates (G1)  
6. Lookup (customers, SLA) — deterministic  
7. **Rule pack v1** mined from gold + hand-written hard rules; G3 per rule id  
8. Code router + append-only routing decisions in **shadow mode** (no UI required)  
9. Dedupe/incident + G4 (fingerprint cluster; optional embed for ties — not queue similarity)  
10. Statutory resolver + Redis + G5a  
11. KB indexer with region + `valid_from`/`valid_to` + retriever + G5b/G5c  
12. Composer + verifier + G6  
13. Cost governor + tenant-scoped semantic/stage cache + G7  
14. Promptfoo in Cloud Build PR → gated rollout per approved rule → UI after backend gates pass  

---

## 10. Enhancements merged into HTML architecture

The following are **normative** for implementation (reflected in `ARCHITECTURE_GCP.html` § Production economics):

- Extraction ladder before RAG  
- `content_fingerprint` / stage cache / retry caps  
- ToolBroker allowlist  
- Immutable raw input + tenant-bound file broker (`SAAS_TENANT_DATA_CONTRACT.md`)  
- Peak p95 3 s for auto lanes  
- Semantic cache rules (non-statutory only)  

---

## 11. What we are not building in v1

- **Vector / nearest-neighbor routing** for queue assignment (ticket text → similar tickets → vote)  
- Autonomous customer replies on money/statutory/P1  
- Fine-tune on ticket text (DPIA)  
- Voice bot replies  
- Unlimited multimodal LLM on every attachment  
- LLM-based router  

---

## Quick reference: one ticket, two paths

**“Has salary hit the bank?” (28th, peak)**  
L1 extract run_id → L3 Redis (webhook) → status lane → template reply → **0 LLM**, &lt; 3 s.

**“PF ceiling for August 2026?”**  
L1 period → L4 engine `as_of=2026-08-31` → L6 template → optional Flash phrase → G6 verifier → **no RAG**.

**“How do I add approver?” (how-to)**  
L7 retrieve (dated KB) → L8 compose once → G6 → agent approves in copilot mode first.
