# Quartermaster: report on four ideas, and what is still missing

**Note (October 2026):** this report is from earlier in the project. Most of what it proposes is built now (cleanup steps, What's New, discovery, ticket links, the nightly run). README.md says what works today; this page is kept to show why.

Written for the product owner. No code was changed for this report. Sizes are rough:
**S** is a few days, **M** one to two weeks, **L** several weeks.

## Part A. The four ideas

### 1. Cleaning up test data after runs

**The problem.** A test like Create Location leaves a real Location on the pod. Run it every night
and the pod fills with junk. Worse, a second run can fail because the name already exists.

**What exists today.** Every run gets a unique `${RUN_ID}`, so names never clash. Nothing removes
what a run created.

**Options, from safest to most powerful.**

| Option | How it works | Risk |
|---|---|---|
| A. Unique names only (done) | `QM-LOC-${RUN_ID}` never clashes | Junk piles up |
| B. Cleanup steps in the test | An optional `cleanup:` list at the end of a test. It runs even when the test failed. Steps are normal steps (a click, or a REST `DELETE`) | The tester must write them. Not every Fusion object can be deleted |
| C. Remember what was created | A REST step can `save` the new id (it already can). A later `cleanup` step deletes by that id | Needs the REST service for that object |
| D. Weekly sweep | A schedule that finds records whose name starts with `QM-` and older than N days, and removes them | A wrong filter deletes real data. Needs a dry run first, and the production guard on |

**Recommendation.** Build B and C together: a `cleanup:` block that always runs, plus a Cleanup
report in the run ("2 of 2 records removed"). Add D later and only as a dry run that lists
what it would delete, with a button to confirm. Never delete anything whose name does not start
with the Quartermaster prefix. **Size: M.**

### 2. Reading Oracle's "What's New" for a release

**What exists today.** You can import the Readiness feature-listing spreadsheet (or a CSV, or
JSON/YAML). Release impact then ranks which tests are at risk. That covers the structured part.

**What is missing.** The detail that matters for tests lives in the "What's New" pages and
documents: what changed on a screen, what is opt-in, what needs setup. The spreadsheet has only
a short description per feature.

**Plan.**

1. Let the user paste a What's New link or upload the saved page or PDF. Quartermaster does not
   crawl Oracle by itself.
2. Split it into features (deterministic: headings and tables first).
3. Optional AI step, using the AI provider set in Settings: for each feature, say which screens
   or processes it touches, whether it is opt-in, and the risk to existing tests. Every AI answer
   is shown next to the source text so a person can check it. Personal data is masked first, and
   nothing is sent if AI is off.
4. Feed the result into Release impact like the spreadsheet import does today.

**Open questions for you.** Which Oracle pages do you use today (Readiness site, release notes
PDF, or both)? Are they reachable without an Oracle login on your laptop? **Size: M.**

### 3. Opt-in discovery of the pod

**The idea.** Quartermaster looks at your pod and learns what you actually use (which menus and
pages exist, which opt-in features are switched on, which personas see what). Then Release impact
can say "this change hits a page you really use" instead of guessing.

**Safety rules (non-negotiable).**

- Off by default. A person switches it on per environment, in Settings.
- Read only: opens pages, never saves, submits or deletes. REST calls are `GET` only.
- Only test pods. The production guard stays on.
- A visible list of what it looked at, and an audit log entry for every discovery run.
- Results are kept per client, like everything else.

**Plan.** Step 1: read the Navigator tree and record the page names (small, safe). Step 2: read
the list of enabled opt-in features through the pod's own REST services, if the test user may.
Step 3: show "used by you" next to each feature in Release impact. **Size: M for steps 1 and 3,
L for step 2** (depends on what your pod's services expose, which needs trying on a real pod).

### 4. Shared test libraries

**The idea.** Ready-made tests (Hire, Absence, Create Location) that every client can use, kept
up to date by one person, instead of each client re-recording the same flow.

**Today.** Each client has its own tests folder and nothing is shared. The first client copies
the example tests at the start. That is correct for data safety.

**Plan.**

- A **library** is a folder of tests with a version. It holds no client data, only steps and
  placeholders such as `${employee_name}`.
- A client **adds** a library. Quartermaster copies its tests into the client's folder and records
  which library version they came from (so the copy can be updated later).
- **Update available** shows a diff of what changed. A person accepts it. Local edits are never
  overwritten without asking.
- Libraries are plain folders in Git, so they can be shared between your clients and, later,
  sold as content (the plan's phase 5).
- Test data (users, names, dates) is filled in per client, in the existing Test data tab.

**Risk.** A shared test must not carry one client's locators or data. Libraries need a check
before publishing. **Size: M.**

### Suggested order for Part A

1. Cleanup after runs (the most pain for the least work, and it protects your pod).
2. Shared libraries (fits the multi-client design you already have).
3. What's New reading.
4. Pod discovery (needs the most trying on a real pod).

## Part B. What else is missing in the project

I read the plan (`docs/PLAN.md`), the code map, the tests and CI. This is what stands out. The
labels say how much each one matters for an enterprise edition.

### Must have before anyone else uses it

| # | Gap | Why it matters | Size |
|---|---|---|---|
| 1 | **Quartermaster has no sign-in of its own.** Anyone at the computer can see every client, run and audit entry | Clients are separate on disk but not by person. The plan asks for named approvers and roles | M |
| 2 | **Approvals and sign-off are a table to fill in on paper.** There is no "approve this release" step with a named person | The certification pack needs a real, recorded sign-off (the plan's principle 4) | M |
| 3 | **Backup and restore of `.qm`, `clients/`, `my_tests` and evidence.** If the folder is lost, history and imported scripts are gone | One button: "Download a backup" and "Restore" | S |
| 4 | **Notifications.** A failed nightly run is only seen if someone opens the page | E-mail, Teams or Slack message on failure, set in Settings | M |
| 5 | **Retries and flaky test tracking.** One slow page fails a whole run. The plan wants under 2 percent flakiness and nothing measures it | Retry a failed step once, mark a test "flaky" if it passes on retry, and report the flaky rate | M |

### Should have soon

| # | Gap | Notes | Size |
|---|---|---|---|
| 6 | **Runs go one at a time.** Several clients or a big suite wait in line | Parallel runs (each in its own browser) with a limit set in Settings | M |
| 7 | **Only 4 tests are checked on a real pod.** ERP and the larger HCM flows are in `examples/unverified/` | Content is what customers buy. Needs your pod time to verify, and a test per Oracle pillar | L |
| 8 | **The web pages have no automatic tests.** Python is well covered (29 test files), the JavaScript pages are tested by hand | A few Node Playwright page checks in CI would catch broken pages before you do | M |
| 9 | **No nightly real-pod run in CI.** The plan's "golden suite against a real test pod" is manual | Needs a safe way to give CI the pod secrets, or a self-hosted runner | M |
| 10 | **No ticket links.** A failure can draft an Oracle SR, but not a Jira or ServiceNow item | Optional, per client | M |
| 11 | **AI quality is not measured.** Plan section 10 asks for fixed evaluation sets for drafting and triage | Without them a model change can silently get worse | M |
| 12 | **"Export a test to Playwright code"** (plan principle 6, open format) is not built | Good for trust and for leaving the tool | S |

### Later

| # | Gap | Notes |
|---|---|---|
| 13 | Hybrid and SaaS mode: a runner inside the client network talking to a hosted control plane. Everything runs on one computer today | Large. Do after 1 to 5 |
| 14 | A Docker image or installer for teams (the double-click start covers one Windows user) | S to M |
| 15 | Release-to-release screen comparison ("this page looked different in 26C") | M, depends on item 3 of Part A |
| 16 | Per-client AI mode ("AI off", "own cloud account") from the plan section 9.1. Today every provider can be set in Settings, but the mode is not per client | S |
| 17 | Screens and docs in other languages | Only if a client asks |

### Already open from earlier

- You still need to retest on your pod: Wait for process, Create Location, Clients and
  environments, the Setup guide and the desktop icon.
- Not tested on Windows yet: DPAPI password encryption and the `.bat` start files. These are the
  highest risk for the "plug and play" goal, so test them before adding features.
- Duplicate cards in the by-hand view: could not be reproduced. The most likely cause is the same
  workbook imported twice under two file names. See Settings, Imported files.

### My recommendation

Do the open retests first (your side, no coding). Then, in this order: **cleanup after runs**,
**backup and restore**, **retries and flaky tracking**, **notifications**, and **sign-in and
approvals**. That list makes the tool safe to leave running every night for a real client. Shared
libraries, What's New and discovery add value after that.
