# Quartermaster - Product & Architecture Plan

> **Note (October 2026):** this is the original plan. README.md says what works today and docs/COMPETITORS.md says where we stand against other tools.

> Working title: **Quartermaster**. An autonomous, AI-assisted regression
> testing service that certifies each Oracle Fusion Cloud quarterly update before it reaches
> production.
>
> Status: v0.2 draft · 2026-09-26 (owner decisions recorded in §13)

---

## 0. TL;DR

Oracle pushes four mandatory Fusion Cloud updates a year (A/B/C/D releases, e.g. 26D in
November 2026). Non-production pods get the update roughly two weeks before production. In that
window customers must prove that their configured business processes, integrations, reports,
roles and personalizations still work. Most teams do this with spreadsheets and manual
clicking, or buy an expensive codeless tool.

Quartermaster's approach is **"deterministic core, AI at the edges"**:

1. **Know what changed.** Ingest Oracle's release readiness content (What's New feature lists)
   and the tenant's own configuration, and turn them into a **risk-ranked impact map**.
2. **Test only what matters, but test it fully.** Choose the smallest regression set that covers
   the impacted business processes, and add fresh tests for new or opt-in features.
3. **Run tests that can be replayed exactly.** Tests are versioned, human-readable specs
   (YAML/DSL) executed by a deterministic Playwright + REST engine. The AI never "free-drives"
   a production-like tenant without guardrails.
4. **Use AI where people spend their time.** That means writing tests from plain English or
   recordings, fixing broken locators after a UI change (with a human approving the fix),
   sorting failures into causes, and summarising the release.
5. **Produce audit-ready evidence** (screenshots, step logs, sign-offs) for SOX and internal
   controls.

---

## 1. Problem deep-dive (why Oracle Fusion is hard to test)

| Pain | Root cause | Implication for our design |
|---|---|---|
| Fixed, vendor-driven cadence | 4 quarterly updates a year, plus monthly maintenance packs; roughly 2 weeks between stage and prod | Speed-to-certify is the headline metric. Tests must run unattended, in parallel, overnight. |
| Brittle UI locators | Classic pages are Oracle ADF with generated IDs (`pt1:_FOr1:1:_FONSr2:0:...`). Redwood pages (Oracle JET/VBCS) replace them module by module | We need a **multi-strategy locator model** (label, ARIA role, relative anchors, visual) and self-healing. We can't rely on raw XPath/CSS. |
| Redwood migration | Oracle is moving pages from classic to Redwood UX across releases | The same business step can have two different UIs. Tests must describe **intent** ("Create Invoice"), not clicks. |
| Opt-in vs. mandatory features | What's New marks features as *ready to use* vs. *customer must take action*, and UI vs. process changes | The impact analyzer must read these attributes. |
| Configuration drift | Descriptive flexfields, lookups, approval rules (BPM), Page Composer / VB Studio personalizations, security roles | Tests should be parameterized by tenant configuration. Discovery must pull setup data. |
| Test data consumption | Invoices, POs, journals are one-shot; periods open or close; approvals route to real users | We need a **Test Data Manager**: generators, unique-key strategies, and period/approval preconditions. |
| Asynchronous processing | ESS scheduled processes, BIP/OTBI reports, FBDI/HDL imports, OIC integrations | The engine needs "wait for job" primitives and non-UI assertions (REST/SOAP/BIP). |
| Role-based security | Duty/job roles change between releases | Run key flows as each persona. Add role-regression checks. |
| Compliance | SOX and internal controls need evidence | Keep an immutable run record: screenshots, DOM snapshots, video, approvals. |

---

## 2. Market analysis (from your list, plus positioning)

| Segment | Examples | Strength | Gap we exploit |
|---|---|---|---|
| ERP-specialist, AI-native | Opkey, ACCELQ, Panaya, Worksoft, Tricentis Tosca, CloudTestr, Testsigma | Pre-built Oracle test libraries, change impact | Expensive, closed, per-seat licensing; AI is often marketing over opaque heuristics; weak explainability for why a test was chosen |
| Generic AI testing | Mabl, Functionize, testRigor, Virtuoso, Katalon, Leapwork, Subject7 | Good NL authoring and self-healing | Know nothing about Oracle release notes, ESS jobs, FBDI, roles, periods |
| RPA-adjacent | UiPath Test Suite, Copado | Automation reuse | Heavy platform lock-in |
| Code-first OSS | Playwright, Selenium, Cypress, Robot, WebdriverIO, Appium | Free, flexible | Every customer rebuilds Oracle plumbing from scratch; high maintenance |
| Niche | Applitools (visual), ReadyAPI/SoapUI and Kusho (API) | Deep in one layer | Not end-to-end business-process aware |
| Legacy | UFT One, Oracle OATS | Installed base | OATS is effectively end of life; poor fit for SaaS cadence |

**Our wedge:** release-aware impact analysis combined with explainable AI and an open,
code-exportable test format (tests export to plain Playwright/pytest, so customers are not
locked in). Pricing per release cycle or per tenant, not per seat.

---

## 3. Product principles (non-negotiables)

1. **Never touch production.** Every environment is classified. The engine refuses any
   production URL unless there is an explicit, dual-approved, read-only smoke profile.
   (Implemented: `safety/guards.py`.)
2. **Deterministic by default.** A passing test replays the same steps every run. AI changes
   (healed locators, generated steps) are **proposals**. They are versioned, diffed and
   approved before they become part of the suite.
3. **Explainable.** Every "this test was selected" and every "this failure is a product change"
   comes with evidence and a confidence score.
4. **Human-in-the-loop sign-off.** Release certification is a workflow with named approvers
   per process owner.
5. **Data minimization.** Mask PII and financial values before sending anything to an LLM. Model
   usage is configurable per tenant, including "no external LLM" mode.
6. **Open format.** Tests are YAML specs in Git and can be exported to Playwright code.

---

## 4. Personas & core user journeys

- **Release Manager**: "26D lands in stage on Friday. Which processes are at risk, and are we
  ready to go live in 2 weeks?" → Impact map → Regression plan → Run → Certification report.
- **Functional Consultant / Process Owner**: "Write a test for our 3-way-match invoice flow in
  plain English." → AI drafts spec → Record or verify on stage → Approve.
- **QA Engineer**: "Last night 7 tests failed. Which are real defects?" → Triage view (product
  change / config / data / environment / script) → Accept heals → Raise SR with Oracle.
- **Auditor**: "Show evidence that AP controls were tested for 26D." → Immutable evidence pack.

---

## 5. System architecture

```
                     ┌──────────────────────── Control Plane (SaaS) ────────────────────────┐
  Oracle Readiness → │ Release Intelligence ─┐                                              │
  (What's New)       │                       ├─> Impact Analyzer ─> Regression Planner      │
  Tenant config   →  │ Tenant Discovery ─────┘         │                    │               │
  (REST, BIP, FSM)   │                                 v                    v               │
                     │  Test Repository (Git-backed YAML specs) <── AI Author / Healer      │
                     │                                 │                                    │
                     │  Orchestrator / Scheduler ──────┼──> Results Store ─> Triage Agent   │
                     │  Web UI · API · RBAC · Audit    │          │            │           │
                     └─────────────────────────────────┼──────────┼────────────┼───────────┘
                                                       v          ^            v
                     ┌──────────── Execution Plane (customer-near, containerised) ──────────┐
                     │ Runner workers: Playwright (UI) · REST/SOAP · ESS job waiter ·       │
                     │ BIP report checker · Test Data Manager · Evidence capture            │
                     └──────────────────────────────┬──────────────────────────────────────┘
                                                    v
                                     Oracle Fusion non-prod pod (DEV/TEST/STAGE)
```

### 5.1 Components

| Component | Responsibility | Tech (proposed) |
|---|---|---|
| **Release Intelligence** | Ingest What's New (HTML/Excel), normalize into `Feature` records (module, product, feature, UI or process, opt-in flag, customer action) | Python, scrapers and parsers, LLM extraction with structured output |
| **Tenant Discovery** | Pull enabled offerings, opt-ins, roles, personalizations, integrations, flexfields | Fusion REST APIs, BIP catalog queries, FSM exports |
| **Impact Analyzer** | Map features to business processes to tests. Score risk. | Deterministic scoring + knowledge graph + LLM for fuzzy text match |
| **Regression Planner** | Choose a minimal test set under a time budget; order by risk | Greedy weighted set cover (ILP later) |
| **Test Repository** | Versioned YAML specs, shared step libraries, data profiles | Git + Pydantic schema |
| **AI Author** | NL or recording → spec; suggests assertions | OpenAI Decisions / LLM with structured outputs; validated against the schema |
| **Runner** | Executes specs step by step with retries, waits, evidence | Playwright (Python), httpx; containerized; horizontally scaled |
| **Locator Resolver / Healer** | Multi-strategy element resolution. Proposes heals when the primary strategy breaks. | Ranked strategies + DOM similarity + LLM tie-break |
| **Test Data Manager** | Unique keys, seeded data, preconditions (period open, supplier exists) | Templates + REST setup/teardown |
| **Triage Agent** | Classify failures; cluster duplicates; draft Oracle SR text | LLM over step log, DOM diff, screenshot, release notes |
| **Reporting** | Readiness dashboard, certification pack (PDF), trend | Web UI (Next.js), PDF export |
| **Security** | SSO (SAML/OIDC), RBAC, vault-stored Fusion credentials, audit log, PII masking | Vault/KMS, row-level tenancy |

### 5.2 Why AI "at the edges" rather than a fully autonomous agent

Fully autonomous LLM browser agents are non-deterministic, slow, costly, and hard to audit.
Those are bad properties for SOX evidence and for a 2-week window. We use agents where the
task is judgment-heavy and a human can review the output:

| Agent | Input | Output | Human gate |
|---|---|---|---|
| Release Analyst | What's New + tenant profile | Impacted features with rationale and risk | Release manager reviews the plan |
| Test Author | NL description / recording | Draft YAML spec | Consultant approves |
| Healer | Failing step + old/new DOM | Proposed locator patch | QA approves (auto-accept above a threshold, configurable) |
| Triage | Failure bundle | Category + confidence + evidence | QA confirms |
| Explorer (later) | New Redwood page | Suggested new tests | QA approves |

An autonomous "exploratory mode" is a later phase and runs only in sandboxed environments.

---

## 6. Domain model (core)

- `Environment` (name, url, kind: DEV/TEST/STAGE/PROD, pod release)
- `Release` (id e.g. `26D`, features[])
- `Feature` (id, module, product, title, description, ui_or_process, opt_in, customer_action_required, tags)
- `BusinessProcess` (e.g. Procure-to-Pay → Create Requisition → Approve → PO → Receipt → Invoice → Pay)
- `TestCase` (id, title, process, module, tags, priority, persona, data_profile, steps[])
- `Step` (action: navigate | click | fill | select | assert | wait_job | api_call | ...; target: `Locator`; value)
- `Locator` (ordered strategies: `label`, `role`, `test_id`, `css`, `xpath`, `text`, `near`)
- `Run` → `StepResult` (status, duration, evidence refs, healing proposals)
- `ImpactAssessment` (feature → tests, score, reasons)

Implemented in `src/quartermaster/domain/models.py`.

---

## 7. Impact scoring (v1, deterministic and explainable)

For each feature *f* and test *t*:

```
match(f,t)   = max( module/product match, tag overlap (Jaccard), keyword overlap in title/description )
severity(f)  = base(UI=0.6 | process=0.8 | both=1.0)
             + 0.2 if customer action required
             + 0.1 if opt-in enabled in tenant
match(f,t)  ×= 0.5 if f and t are in different modules       # shared words/tags across pillars are weak
covers(t,f)  = match(f,t) ≥ 0.5                              # only strong matches count as coverage
risk(t)      = 1 - Π_f (1 - match(f,t) · severity(f))       # noisy-OR over features
priority(t)  = risk(t) · business_criticality(t)
```

The planner selects tests by descending priority until the time budget is used up, always
including tests flagged `critical`. Each decision records its `reasons[]`. v2 adds an
LLM-based semantic matcher for features whose text doesn't share keywords with test titles,
plus historical failure rate.

---

## 8. Self-healing locator strategy

1. Every step targets a **Locator** with several strategies in priority order: user-facing label
   → ARIA role + name → stable attributes → text → CSS/XPath (last resort).
2. At runtime the resolver tries strategies in order. The first **unique** match wins.
3. If the primary strategy fails but a fallback succeeds, the runner records a
   **HealingProposal** (old → new, with a confidence score) and the test **still passes, marked "healed"**.
4. If every strategy fails, the Healer agent gets the step intent, the last known DOM snippet
   and the current DOM, and proposes a new locator. The step fails; the proposal is queued for
   review.
5. Approved proposals are committed to the spec, so the next run is deterministic again.

---

## 9. Security, privacy & compliance

- Fusion credentials sit in a vault. Runners get short-lived secrets. Use a dedicated test
  user per persona.
- Environment guard (hard block on PROD) and an allow-list of pod hostnames per tenant.
- PII masking before LLM calls (names, emails, bank accounts, amounts optional). Per-tenant
  LLM opt-out.
- Immutable audit log of runs, approvals and heal acceptances. Signed evidence bundles.
- Target SOC 2 Type II; data residency options (US/EU).

### 9.1 AI data policy (proposed, per tenant)

| Mode | Where AI requests go | For whom |
|---|---|---|
| **Standard** (default) | OpenAI Decisions API / provider, after PII masking | Most customers and internal use |
| **In-region cloud** | LLM via the customer's own AWS Bedrock / Google Vertex / Azure Foundry account in their region | Customers with EU/regional data residency or cloud-procurement rules |
| **AI off** | No AI calls. Release analysis, planning and running tests still work (all deterministic). | Highly regulated customers |

In every mode, masking runs first. Screenshots and raw HCM records (salaries, national IDs) are
never sent to an LLM.

**Where test scripts live:** test specs are YAML files in a Git repository per tenant, either
ours (SaaS) or the customer's own GitHub/GitLab (hybrid). Run results and evidence go in the
tenant's database and object storage in their chosen region.

---

## 10. Quality strategy for *our own* tool ("defect-free" in practice)

- Strict typing (Pydantic models, mypy) and linting (ruff) in CI.
- Unit tests for all pure logic (impact scoring, planner, locator resolution, guards, DSL).
- Contract tests for the Oracle adapters, using recorded fixtures.
- Nightly end-to-end runs against a real Fusion **test pod** with a small golden suite.
- **LLM evals**: fixed datasets for extraction, authoring and triage accuracy. Gate prompt and
  model changes on eval scores.
- Chaos testing on runners (network loss, session timeouts, slow ESS jobs).
- Dogfood: each Oracle release is a "release of our own", with a retro.

---

## 11. Roadmap

| Phase | Weeks | Scope | Exit criteria |
|---|---|---|---|
| **0. Foundations** *(this commit)* | 1–2 | Domain model, YAML DSL, env guard, impact analyzer + planner, locator resolver, runner with pluggable driver, AI provider interface, CLI | `pytest` green; `qm plan` produces a ranked plan from sample data |
| **1. Oracle runner MVP** | 3–6 | Playwright driver for Fusion (login/SSO, navigator, ADF waits, Redwood waits), ESS job waiter, REST step, evidence capture | 10 golden Hire-to-Retire tests pass on a real pod, with less than 2% flakiness over 20 runs |
| **2. Release Intelligence** | 5–8 | What's New ingestion (HTML/XLSX), LLM extraction, tenant opt-in discovery | 26D feature list ingested with ≥95% field accuracy vs. manual |
| **3. AI authoring + healing** | 7–11 | NL → spec, recorder, healing proposals + review UI | ≥70% of drafted specs run green after ≤1 human edit |
| **4. Web app + triage** | 9–14 | Dashboard, runs, triage agent, certification report, RBAC, SSO | Pilot customer certifies one quarterly release end-to-end |
| **5. Scale & content** | 14+ | Pre-built libraries: HCM first (Hire-to-Retire, Absence, Compensation), then ERP (P2P, R2R), then SCM (O2C, Inventory); multi-tenant SaaS, SOC 2 | 3 paying tenants |

**MVP scope (decided): HCM first**, then ERP, then SCM.

| Order | Pillar | First business flows |
|---|---|---|
| 1 | **HCM** | Hire an employee, promote/transfer, change salary, submit and approve an absence, terminate. Self-service (employee/manager) and HR Specialist personas. |
| 2 | ERP | Supplier invoice (AP), manual journal (GL), purchase order (Procurement) |
| 3 | SCM | Sales order (Order Management), inventory transfer, receipt |

Why HCM needs extra care:
- **Most sensitive data:** names, national IDs, salaries, bank details. PII masking and the
  AI data policy (§9.1) are MVP requirements, not later add-ons.
- **Effective dating:** every HCM change has an effective date, and future-dated rows affect
  what you see today. Test data must pin dates and clean up after itself.
- **Approvals everywhere:** most HCM transactions route to a manager or HR for approval. The
  runner needs an "act as approver" step (log in as the approver persona, then approve).
- **Role-driven UI:** Employee, Line Manager and HR Specialist see different pages, so
  personas are first-class in test specs.
- **Redwood first:** Oracle moved much of HCM to Redwood early, so tests target Redwood pages
  and fall back to classic ones.

---

## 12. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| No access to a Fusion pod for development | Get an Oracle partner/demo environment early. Record DOM fixtures for offline tests. |
| Oracle UI changes faster than the healing logic | Intent-level steps, multiple locator strategies, library-level fixes shared across tenants |
| LLM cost or latency | Deterministic first; LLM only on failure or authoring; caching; smaller models for triage |
| Trademark | Don't use "Oracle" or "Fusion" in the product name. Describe the product as "for Oracle Fusion Cloud Applications" only. |
| Customer trust in AI | Explainability, approval gates, open export format |

---

## 13. Owner decisions & remaining questions

**Decided (2026-09-26)**

1. **Business model: both.** A commercial multi-tenant SaaS product, also used internally.
   Consequences:
   - Strict tenant isolation (data, credentials, test repos, AI usage) from day one.
   - Two deployment modes:
     - **SaaS:** we host everything.
     - **Hybrid:** we host the control plane, and the runner sits inside the customer's
       network so their Oracle credentials never leave it.
   - Internal use is just "tenant #1".
2. **Pillar order:** HCM → ERP → SCM (see §11).
3. **Test environment:** the owner has a Fusion non-prod pod for development. Credentials go
   in environment secrets (`QM_FUSION_URL`, `QM_FUSION_USER`, `QM_FUSION_PASSWORD`), never in
   chat, specs or Git.

**Still open**

4. **AI data policy.** When the AI writes or triages a test, it sees step details, error
   messages and sometimes page content. We need to decide where that data may go. The
   proposed default is in §9.1.
5. **Team:** who builds the web UI? The plan assumes Next.js; the core engine is Python.

---

## 14. Name ideas

| Name | Rationale |
|---|---|
| **Quartermaster** *(working title)* | "Quarter" (quarterly releases) + the officer responsible for readiness and supplies |
| Cadence | Built around Oracle's release cadence |
| PatchPilot | Guides you through each patch |
| ReleaseWarden | Guards production from bad updates |
| Q-Certify | Says the outcome: quarterly certification |
| Keel | Keeps the ERP steady through change |

Before committing to a name, check trademarks and domains. Avoid "Oracle" and "Fusion" in the
brand.
