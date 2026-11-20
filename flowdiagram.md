┌─────────────┐
│ 1. pii_gate │
│ Mask PII    │
│ Vault store │
└──────┬──────┘
       │
       ▼
┌────────────────┐
│ 2. normalize   │
│ Rename fields  │
│ Standard shape │
└──────┬─────────┘
       │
       ▼
┌─────────────────┐
│3. resolveTenant │
│ Find customer   │
│ Load SLA        │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 4. classify     │
│ Category        │
│ Urgency         │
│ Injection check │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│5. dedupeCluster │
│ Similar issues  │
│ Incident merge  │
└──────┬──────────┘
       │
       ▼
             ◇
      ┌──── Route ────┐
      │   Rules R1-R9 │
      ◇─────┬─────┬───◇
            │     │
            │     │
            │     │
            │     └────────────────────┐
            │                          │
            ▼                          ▼

      AUTO ANSWER                 INCIDENT PATH
      ───────────                 ─────────────

┌─────────────────┐       ┌─────────────────┐
│7. cache_lookup  │       │11.humanApproval │
│ Existing answer │       │ Reviewer queue  │
└──────┬──────────┘       └──────┬──────────┘
       │                         │
       ▼                         │
┌─────────────────┐             │
│ 8. retrieve     │◄────────────┘
│ Knowledge fetch │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 9. Agent (LLM)  │
│ Draft response  │
│ Tool calls      │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│10.output_guard  │
│ Safety checks   │
│ Validation      │
└───┬─────────┬───┘
    │         │
    │Fail     │Pass
    ▼         ▼

┌─────────────────┐      ┌─────────────────┐
│11.humanApproval │      │12. act          │
│ Manual review   │─────►│ Send response   │
└──────┬──────────┘      │ Create ticket   │
       │                 │ Perform action  │
       └────────────────►└──────┬──────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │13.log_and_learn │
                        │ Logs            │
                        │ Feedback        │
                        │ Memory update   │
                        └──────┬──────────┘
                               │
                               ▼

══════════════════════════════════════════════════
                    MEMORY
══════════════════════════════════════════════════

┌───────────────────────────────────────────────┐
│ Masked Text Only (Customer Scoped)            │
│                                               │
│ ┌───────────┐ ┌────────────┐ ┌─────────────┐ │
│ │Short-term │ │Customer    │ │Learned      │ │
│ │State      │ │Facts       │ │Responses    │ │
│ └─────┬─────┘ └─────┬──────┘ └──────┬──────┘ │
└───────┼─────────────┼───────────────┼─────────┘
        │             │               ▲
        │             │               │
        └─────────────┴──────┬────────┘
                             │
                             ▼
                     ┌───────────────┐
                     │9.Agent (LLM)  │
                     │Read / Write   │
                     └───────────────┘


══════════════════════════════════════════════════
                     TOOLS
══════════════════════════════════════════════════

┌─────────────────────────────┐
│ Read-only Business Systems  │
│ CRM                         │
│ Ticketing                   │
│ Order Status                │
│ Customer Data               │
└─────────────┬───────────────┘
              │
              ▼
       ┌───────────────┐
       │9.Agent (LLM)  │
       └───────────────┘


NOTE:
After Step 1 (PII Gate),
ALL downstream components see ONLY masked data.

pii_gate
   ↓
normalize
   ↓
resolveTenant
   ↓
classify
   ↓
dedupeCluster
   ↓
route
 ├─ Auto Answer → Cache → Retrieve → LLM → Guard → Act
 ├─ Draft for Agent → Retrieve → LLM → Guard → Human → Act
 └─ Incident/Block → Human → Act
                        ↓
                  Log & Learn
                        ↓
                     Memory