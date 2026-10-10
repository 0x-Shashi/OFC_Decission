# Quartermaster compared with other tools

Written 3 October 2026. Two honest limits:

- The competitor columns come from general knowledge of their public marketing and docs. I did not read their current
  websites for this page, so before it goes in a sales deck, check each claim against the vendor's own site.
- The Quartermaster column is what the code does today. "Not tested on a real pod" is marked where it applies.

Key: **Yes** = built. **Part** = built in a smaller form. **No** = not built.

## 1. What Quartermaster does that is its own wedge

| Idea | Quartermaster | Typical Oracle-specialist tools | Typical generic AI test tools |
|---|---|---|---|
| Read Oracle's What's New and rank what to test (opt-in, change type, Redwood) | Yes (HTML or pasted text; no PDF yet) | Part: usually through their own release content | No |
| Say which features exist on this pod (opt-in, read only, audited) | Yes (not tested on a real pod) | Part | No |
| Tests are plain YAML you own, exportable to Playwright and pytest | Yes (export not tested on a real pod) | No: stored in their platform | Part: some export code |
| Runs on your own machine or server, no per-seat licence | Yes | No: SaaS plus licence | No: SaaS plus licence |
| AI only proposes; fixes are checked and never applied alone | Yes | Varies | Often applies fixes by itself |
| AI quality check on invented screens, scored per model | Yes | No | No |
| Production is refused unless explicitly safe | Yes | Varies | Varies |

## 2. Feature by feature

| Feature | Quartermaster | Gap to close? |
|---|---|---|
| Multiple ways to find an item, with healing suggestions | Yes | No |
| Screen change fixes you accept (shared steps updated at the source) | Yes | No |
| Retries, flaky marking, stability rate | Yes | No |
| Word evidence and certification pack with approval record | Yes | No |
| Release approval by a named person | Yes | No |
| Roles, sign-in, Google sign-in, audit log | Yes | No |
| Parallel runs (up to 4) | Yes | Maybe higher limit later |
| Nightly run, notifications (Slack, Teams, webhook, e-mail) | Yes (workflow not tested with real secrets) | No |
| Backups, automatic backups, restore | Yes | No |
| Ticket links (no tracker login) | Yes | Part: no two-way Jira or ServiceNow sync |
| REST steps, waiting for scheduled processes (ESS) | Yes | No |
| Record a test by clicking through | Yes | No |
| Tests from a manual scenario | Yes | No |
| Shared steps (library) | Yes | No |
| Test data: unique values per run, dates from today, values that belong to each pod, a clear stop when a pod lacks one | Yes (not tested on a real pod) | No |
| Setup steps that check or make data first (for example "the period is open"); a setup that is not met is told apart from a broken release | Yes (not tested on a real pod) | No |
| Saved suites by tag, folder, module, product, priority or named tests, run from a page, a schedule or the command line | Yes (not tested on a real pod) | No |
| Audit trail: every line chained by hash so a change shows, a filtered export (CSV or JSON lines) with a manifest, offline check | Yes | No (stronger: send the lines to a company log service) |
| Single sign-on with a company provider (OpenID Connect: Okta, Entra ID, Keycloak, ...), groups to roles, enforce SSO | Yes (tested against a stand-in provider, not a real one) | No (SAML and SCIM: not built) |
| FBDI or HDL file import steps | No | Not needed: decided not to build (test scripts with evidence are how this is tested; only if a client asks) |
| BIP or OTBI report checks | No | Not needed: decided not to build (a script that opens and runs the report, with the evidence document, covers it) |
| Screenshot comparison before and after a release | No | Not needed: decided not to build (the evidence document already shows what each step saw) |
| Run the same flow as several roles | Part: personas exist, no role-regression report | Probably |
| Mobile and cross-browser | Part: Chromium only | Maybe |
| Ready-made Oracle test library | Part: 63 read-only checks of Oracle REST services in 5 packs, run on one test pod; no screen tests, nothing that creates data | **Yes**: screen flows (Hire to Retire, Procure to Pay, Order to Cash), which need to be run on a pod before they can be trusted |
| Explorer mode (finds new pages and suggests tests) | No | Later, as planned |
| Server mode for a team | No | Last step before go live, you will do it |

## 3. Where we are ahead, where behind

Ahead: price model and ownership, open and exportable tests, explainable release ranking, careful AI use, evidence that
auditors can read, and tests you can keep if you leave.

Behind: no ready-made screen tests, no two-way tracker link, and nothing has been proved on many real pods. A tool with years of Oracle customers has seen
far more strange pages than we have.

## 4. Suggested order if you want to close gaps

1. Screen and create flows for the test library (Hire to Retire, Procure to Pay, Order to Cash). The read-only service checks are done.
2. Two-way ticket sync, only if a client asks for it.

Decided not to build: BIP or OTBI report checks, FBDI or HDL import steps and visual comparison. Tests are scripts with an evidence document, and a script can open and run a report like any other page.
