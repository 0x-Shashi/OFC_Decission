# How to test Quartermaster

This guide is for someone using Quartermaster for the first time. Follow the parts in order.

Every step has two lines of thinking: **what to do**, and **what you should see**. If you see something else, write it down (see "Reporting a problem" at the end).

Commands are for Windows PowerShell. Type them in the `veyar` folder.

**Short on time?** Do Parts 1 to 6.2, then pick the sections you care about. Each section in Part 6 stands on its own.

## Part 1. What you need

- A Windows laptop that can open your Oracle Fusion test pod in a browser.
- Python 3.11 or newer. Check with `python --version`.
- Git. Check with `git --version`.
- The pod address, a test user name and its password. Use a test pod only, never production.

## The easy way: no terminal

1. Get the `veyar` folder onto the laptop once (the `git clone` lines in Part 2, or a copy of the folder).
2. Open the `veyar` folder and double-click **Start Quartermaster**.

You should see: a black window. The first time it says it is installing (a few minutes, only once), then it puts a **Quartermaster** icon on the desktop. Your browser opens Quartermaster. If nothing is set up yet, the setup guide opens (see 6.1).

- From then on, double-click the **Quartermaster** icon on the desktop to start it.
- Keep the black window open while you use Quartermaster. Close it to stop.
- Double-clicking the icon again while it runs just opens the browser.
- To get the latest version: close the black window, then double-click **Update Quartermaster** in the `veyar` folder.

The parts below do the same things by hand in a terminal, for testers and developers.

## Part 2. One-time setup

Do this once on a new laptop.

```powershell
git clone <your-repo-url>
cd veyar
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,browser]"
python -m playwright install chromium
```

You should see: no red error lines. The last command downloads a browser and can take a few minutes.

## Part 3. Every time you open a new terminal

1. Go to the folder and switch on the Python environment:

   ```powershell
   cd veyar
   .venv\Scripts\activate
   ```

   You should see `(.venv)` at the start of the line.

2. Get the latest version:

   ```powershell
   git pull
   ```

3. Nothing else is needed. The pod and its users are set up in the web app (6.1). If you used the terminal variables before (`QM_FUSION_URL`, `QM_FUSION_USER`, `QM_FUSION_PASSWORD`), the first start of `qm serve` copies that pod into the app as "My first client", so you can stop setting them. The command line test in Part 5 still needs them.

## Part 4. Automatic checks (no pod needed)

```powershell
python -m pytest -q
```

You should see: a line like `NNN passed`, with no `failed`. Some tests may say `skipped`. That is fine.

If anything says `failed`, stop here and report it.

## Part 5. Sign in to the pod from the terminal

```powershell
qm run examples/smoke/login.yaml --headed
```

You should see: a browser opens, signs in to the pod and closes. The terminal prints `PASSED` (or `HEALED`) for `smoke.login`, and `1/1 passed`.

If it says it cannot sign in, check the three values from Part 3.

## Part 6. Open the web app

```powershell
qm serve
```

You should see: the terminal says `Quartermaster is running at http://127.0.0.1:8765`, and your browser opens that page. The first time, it also says `Created my_tests`. Your own tests are kept in the `my_tests` folder, which starts with copies of the example tests.

Do not start it with `--tests examples/tests`. Your tests would then be saved among the examples, and the automatic checks in Part 4 would fail.

Keep this terminal open while you test. Press Ctrl+C in it to stop.

The menu on the left has: Overview, Runs, Tests, Suites, Test library, Shared steps, Test data, Release impact, Needs attention, Schedules, Record a test, Settings, Audit log.

### 6.1 Settings: clients and environments

The very first time (nothing set up, and no pod set in the terminal), Quartermaster opens the **Set up Quartermaster** guide by itself. It has four steps: Client, Pod, Sign-in, Check. Fill them in and click **Save and check**. If the pod does not answer, check the address or your VPN and click **Check again**. To add another client the same way later, open Settings and click **Setup guide**.

Settings has four tabs: **Clients and environments**, **Evidence**, **AI assistant** and **General** (folders, theme, keyboard shortcuts, backups, tickets).

1. Click **Settings**. On the left, **In use now** shows the pod runs use. **Clients and environments** lists your clients and their pods.
2. If it is empty, click **Add a client**, type the client's name (for example `Acme Corp`) and click **Save**. The New environment panel opens by itself.
3. Fill in:
   - Name (`DEV2`)
   - Oracle release (`26C`)
   - Pod address (copy it from the browser when the pod's sign-in page is open)
   - Kind (Development, Test or Stage / UAT)
   - Sign-in (User name and password)

   Under **Test users**, type the default user and its password. If a test switches user (for example a Line Manager approves), click **Add persona** and fill in that user too.
4. Click **Test connection**, then **Save**.

You should see: the environment in the list with "In use", and "Signs in as <user>". The password is never shown again. The field says "Saved. Type to change". It is saved encrypted by Windows for your Windows user only.

5. Add a second client the same way (**Add client** at the top of the card). To switch, click the environment box at the bottom left of the menu and pick it under **Switch to**, or click **Use** next to it in Settings. Runs, recordings and Prepare then go to that pod.

   Each client is kept apart: its own tests, manual scripts, evidence, runs, schedules, Needs attention and audit log. Switch to the second client. **Tests** shows only its tests (a new client starts with copies of the example tests), and **Runs** shows none of the first client's runs. Switch back: everything of the first client is still there.

   The first client keeps the folders you had before (`my_tests`, `evidence`). Every other client's files are in `clients/<name>-<code>/`. Schedules of every client run even when another client is in use.
6. Try adding a production pod (an address without dev, test, stage or uat, such as `https://acme.fa.us6.oraclecloud.com`). Quartermaster refuses it. If a real test pod has no such word in its address, tick **This is a test pod, not production** and save again.
7. In the **Evidence** tab, choose Screenshots and Video, and whether to show the browser. Each change is saved at once. Every run you start afterwards uses them.
8. **Highlight clicks** is on by default. Start a run with **Show the browser while it runs** on. Before each click, a red box and a small red dot appear on the item for a moment (also in the video), and the screenshots have a red box round the item each step used. Turn it off and run again: no red marks anywhere.

### 6.2 Run one test

1. Click **New run** (top right).
2. Choose **One test**, and pick "Search for a worker in Person Management and open their record".
3. Leave the other options as they are. Click **Start run**.

You should see: the run page with live progress ("Executing step 2 of ...") and then a result. When it ends, each step has a screenshot. There is a link to the Word evidence document and a summary document. Open both. They open in Word and show the steps and pictures.

### 6.3 Run all tests

1. Click **New run**, choose **All tests**, click **Start run**.

You should see: 4 tests run one after another. The result shows how many passed.

### 6.4 Runs

1. Click **Runs**.

You should see: every run so far, newest first, with result, release, who ran it and how long it took. Click one to open it again.

### 6.5 Tests

1. Click **Tests**.
2. Type `worker` in the search box. Then clear it.
3. Click **Columns** and tick one more column, for example Owner.
4. Click a test name.

You should see: the search filters the list. The extra column stays after you reload the page. The test page shows its steps in plain words, its test data, its run history and the file.

### 6.6 Release impact

This page answers: "Oracle is updating the pod. Which of our tests should we run, and what is not tested at all?"

1. Click **Release impact**. The example release `26D` opens.
2. Look at the four cards at the top: features in the release, tests at risk, suggested run, features with no test.
3. In the **Tests to run** tab, click **Covers ...** on a row to see why that test was picked.
4. Type `3` in **Time limit (minutes)** and press Enter. The suggested tests change to what fits in 3 minutes.
5. Click the **Features** tab, then **No test**. This lists features that no test covers.
6. Click **Import feature list**. Choose the file `examples\releases\26C_sample_features.csv`. Type `26C` as the Oracle release if it is empty.
7. Read the preview: how many features it found and which columns it used. Click **Save feature list**.
8. The page now shows release 26C. Click the box in the table header until every test is unticked, then tick two tests.
9. Click **Run 2 tests**.

You should see: the run page named "Release 26C impact, 2 tests", and only those 2 tests run. In **Runs**, the run has the same name.

To try a real Oracle file: download the feature listing spreadsheet for an update from Oracle Cloud Readiness and import it the same way. It must have a Feature column. Product and Product Family columns make the matching better.

### 6.7 Manual test scripts: import and run

Your Excel test scripts can be added once and run every quarter. The workbooks themselves are only read, never changed.

**Import**

1. Click **Tests**, then **Import manual scripts**.
2. Choose one or more `.xlsx` files. You can select all of them at once.
3. They are saved straight away and the list of scenarios opens. They stay in Quartermaster (also after the next quarterly release) until you remove them.
4. Only if Quartermaster cannot tell a workbook's module and product from its file name, the window stays open. Type them and click **Save**.

**Run a scenario the first time: by hand**

5. On **My Compensation**, click **Run by hand**. A browser opens, already signed in, and Quartermaster shows the scenario's steps.
6. Do step 1 in that browser. Then click **Pass** on step 1 in Quartermaster (or **Fail**, type what went wrong, and click **Mark failed**). Quartermaster takes a picture of the screen.
7. Do the same for every step. On the last page, click **Add check** and then click a value that proves the page is right (for example "Current Salary").
8. Click **Finish**.

You should see: "Every step passed" (or "At least one step failed"), a button for the Word evidence document, and **Open the run**. The run is in **Runs** as "By hand: My Compensation".

**Run it again: by itself**

9. Go back to **Tests**, **Manual scenarios**. The button on My Compensation now says **Run**.
10. Click **Run**.

You should see: the run page, named "Automatic: My Compensation". Quartermaster plays your clicks from steps 6 and 7 without you, and makes a new evidence document.

**Run** uses the evidence choices in **Settings, Evidence**: Screenshots, Video, and Show the browser while it runs. They are the same choices as in **New run**. Changing one changes both.

To do a scenario by hand again (for example after Oracle changed the screen), open it and click **Do it by hand**. Only run scenarios by hand that are safe to repeat on the test pod. Whatever you click, including Save or Submit, is played again every time.

**Let an AI prepare a scenario instead (optional)**

This needs an AI provider, set once (see "Choosing an AI" below).

11. On **Employment Info**, click **Prepare**. A browser opens, already signed in. The AI reads each written step, clicks on the pod by itself, and takes a picture when a step is done. Quartermaster shows which step it is on.
12. When it has finished, click **See what the AI did** and compare each picture with its step.
13. If every picture is right, click **Approve**. From then on **Run** plays it by itself. If a picture is wrong, click **Do it by hand** instead.

When the script lists fields to check (for example "Current Salary (Salary, Annual Salary)"), the AI opens a closed section and checks those fields. If the page does not show them (for example "There's nothing here so far" because the test user has no salary), it stops and says so instead of passing. Use a test user who has that data, or do it by hand.

You should see: before you approve, the button on the scenario says **Review**, and **Run** is refused. If the AI cannot do a step (the script gives no value to type, or the step would press Save or Submit), it stops, says why, and nothing is saved to run. Do that scenario by hand.

**What the AI answered** opens the AI's diary: for every step, what was on the screen, what the AI chose and what did not work. Send it when you report a problem with Prepare. It is also kept in the run's folder in `evidence\` as `ai-diary.txt`.

**The evidence document of a scenario that plays by itself**

When **Run** plays a scenario, its Word document follows the steps of your script (Login, Me, Personal Information, Select My Compensation), each with the actions done for it and their pictures. A step without its own action says why, for example "Quartermaster signed in before the test started" or "done as part of step 3". A click has two pictures: just before it (with what it clicks boxed in red) and after it. Scenarios saved before this change keep the old layout until you prepare them again or do them by hand again.

**Check what a scenario does, and prepare it again**

- The written steps: in **Manual scenarios**, click the scenario's name. The panel lists each test case with its steps and expected results.
- What Run plays: open one of its runs and click **Open test**. It lists the saved actions, for example "Open Me > Personal Information", "Click My Compensation" and "Check My Compensation is shown". Login is not listed, because Quartermaster signs in before every run.
- If the saved actions are wrong or incomplete, click the scenario's name, then **Prepare again** (or **Do it by hand**). The new version waits in **To review** until you approve it. If the AI stops, the current version stays.

**Scenarios that submit a scheduled process**

Many payroll and finance scripts submit a process (an ESS job) and then wait for it. While doing such a scenario by hand (or recording a test), submit the process in the browser, then click **Wait for process**. When the test plays, Quartermaster reads the process number from Oracle's confirmation ("Process 1234567 was submitted") and asks the pod for its status every 15 seconds. The step passes only when the process ends with **Succeeded** (it waits up to 30 minutes). When the AI prepares a scenario, it adds this step itself when a step says to wait for the process. The status comes from Oracle's ERP integration service, so the test user needs access to it. If not, the step says so.

**Fill in test data the script does not give**

When a scenario shows **Values not written** or **Test data missing**, click its name. Each step that needs a value has a box (**TEST DATA NEEDED**). Type the value, for example "Start Date: 01/10/2026; Absence Type: Vacation", and click **Save test data**. The badge goes away, Prepare all includes the scenario, and the AI and the tester see the value as part of the step. The workbook is not changed. The values are kept in `.qm`. Never type a password there. Do steps that need a sign-in by hand.

**Prepare many at once (optional)**

14. In **Manual scenarios**, click **Prepare all (N)**. N is the number of scenarios that do not play by themselves yet, are not waiting for review, and that the AI can finish. Scenarios marked **Test data missing**, **Values not written** or **No steps** are left out, because the AI never makes values up and would stop there. Do those by hand, or write the values in the workbook and import it again. Click OK.
15. The **To review** page opens. It shows each scenario: waiting, preparing now, ready to review, or stopped (with the reason). You do not need to watch. **Watch the AI** shows the browser steps, and **Stop** stops after the scenario being prepared now. While it runs, Prepare and Run by hand wait, because the AI uses the browser.
16. When it has finished, every prepared scenario is listed with the picture of each step. Click a picture to see it full size. Tick the scenarios where every picture is right (or **Select all** and untick the wrong ones), then click **Approve selected**.

You should see: the approved scenarios now say **Run** in Manual scenarios. Stopped ones still say **Prepare** and **By hand**. Clicking **Prepare all** again tries the stopped ones again. For a scenario whose pictures are wrong, click **Do it by hand** in To review.

**Choosing an AI**

1. In **Settings, AI assistant**, choose the **Provider** and type the **Model** name your account offers (for example the model id shown in your OpenAI or OpenRouter account).
2. Paste your key in **API key** and click **Save**. You should see "Ready: ... Key saved (hidden)".
3. Click **Test the AI**. You should see "... answered in ... ms".

The key is kept in memory only. It is never written to a file and never shown again, so paste it again after you restart `qm serve`. To keep it across restarts, set it on the computer instead, like the pod password, before starting: `$env:OPENAI_API_KEY="your key"` (the variable name for each provider is under **Advanced**). Ollama on your own computer needs no key.

If Anthropic answers "This API key is not scoped to a workspace": open the Anthropic Console, go to Workspaces and copy the workspace ID (it starts with `wrkspc_`). In Settings, open **Advanced**, paste it in **Workspace ID**, click **Save**, then **Test the AI** again. Or make a new key inside a workspace in the Console, and then the ID is not needed.

Any provider that offers an OpenAI-compatible service works: choose **Other** and enter its address. What is sent to the AI: the written steps and the names of the buttons, links and headings on the pod screen. E-mail addresses and long numbers are hidden first. Never typed values, never the password. Quartermaster never stores the key.

**Other things to check**

- The **Result** column shows Quartermaster's own last result. "Workbook: Pass" in Notes only repeats what someone typed in the workbook's Pass / Fail column.
- Scenarios with `<>` in their steps are marked "Test data missing".
- In **Release impact**, the **Manual scenarios** tab lists the scenarios that cover a feature of the release. In the **Features** tab, a feature only a manual script covers is "Manual only".
- To remove a workbook: **Imported files**, then **Remove**. To update one, import it again.

**Type a new scenario (no Excel needed)**

1. Click **Tests**, then **Manual scenarios**, then **New scenario**.
2. Name: `Update my home address`. Module: `HCM`. Product: `Global Human Resources`.
3. In the first step box, paste these four lines at once:

   ```
   1. Click Me
   2. Click Personal Information
   3. Enter 10 Main Street in Address Line 1
   4. Click Cancel
   ```

   You should see: four steps, one per line, without the numbers.

4. Click **Save**. The scenario opens, marked "Typed in Quartermaster". It is in the list like an imported one: **Run by hand** or **Prepare** work the same.
5. Click **Change**, edit a step, and **Save**. Or **Delete** to remove it (past runs stay).

### 6.8 Needs attention

1. Click **Needs attention**.

You should see: failed runs grouped by kind (for example "Item not found"), with the failed step, what was expected, what was found and a screenshot. If nothing failed it says all clear.

To clear an item, click **Dismiss** on it, or **Dismiss all shown** at the top. A dismissed failure comes back only if the test fails again on a later run.

Each failed test also says its **Likely cause**, worked out from its history:

- passed on an earlier release and fails on this one: probably the Oracle update;
- passed before on this same release: the data or the pod changed;
- never passed: the test or its data.

When the update is the likely cause, there is a **Draft SR** button. It opens the text of an Oracle service request (steps to reproduce, expected and actual result, releases, pod). Click **Copy** and paste it into My Oracle Support. Quartermaster never sends it.

To see Draft SR, a test must have passed on one release and failed on the next. Run it with the release set to, say, `26B` in Settings, then change the release and run it again.

### 6.9 Record a test

1. Click **Record a test**.
2. Fill in Test name, Module (for example `HCM`) and Product (for example `Global Human Resources`).
3. Click **Start recording**. A browser opens, already signed in.
4. In that browser, open a page and click through a short, safe flow. Only look at data. Do not save or submit anything on the pod.
5. Back in Quartermaster, try **Pause** and **Resume**, **Add note**, and **Add check** (then click a value on the pod page).
6. Click **Finish and save**.

You should see: each click appears as a step while you record. After saving, the new test is listed under **Tests** and you can run it.

### 6.10 Schedules (tests that run by themselves)

1. Click **Schedules**, then **New schedule**.
2. Name: `Nightly check`. What to test: one short test. Days: today only. Time: two minutes from now on this computer's clock. Keep **On** ticked. Click **Save**.

You should see: the schedule in the list, with "Next run" at the time you chose. Keep `qm serve` running. Within a minute of that time, a run labelled "Scheduled: Nightly check" starts under **Runs**, and "Last run" on the Schedules page links to it.

3. Click **Run now** on the schedule. It starts the same run straight away.
4. Click the schedule, then **Delete** to remove it. Its runs stay in Runs.

Schedules only start while `qm serve` is running. A time missed while it was stopped starts only if Quartermaster is back within the hour.

### 6.11 Certification pack for a release

Do this after some tests have run with the Oracle release set in Settings (6.1).

1. Click **Overview**. In the release readiness card, click **Certification pack**.
2. A zip downloads, named like `certification_26C.zip`. Unzip it.

You should see: `Certification 26C.docx` and an `evidence` folder with one Word document per test (its latest run on that release). Open the certification document. It shows the result, counts by module, every test with its result and evidence file, what failed (with a picture), tests not yet run on the release, and a sign-off table. In **Release comparison** (shown once tests ran on two releases), the download icon on each row gets the pack for that release.

### 6.12 Audit log

1. Click **Audit log** (under System).

You should see: what you changed in this test session, newest first, with who and when: the run you started, the schedule you added, settings, test data saved (only which steps, never the values), approvals. Type a word in the search box to filter. Click **Download CSV** for the whole log in a file Excel opens. "Who" is the user signed in to this computer, or the tester's name for a run by hand. Scheduled runs show "Schedule".

### 6.13 Search and shortcuts

- Press **Ctrl+K**. Type `release impact`. Press Enter. It opens Release impact.
- Press **N**. The New run panel opens. Press Esc to close it.
- Click the moon or sun icon at the top. The page switches between dark and light.

### 6.14 Cleanup after a test

This test only reads from the pod (GET), so it is safe. It shows that cleanup steps run, and that they are skipped when there is nothing to clean up. It needs a pod where Locations can be read by the REST service (the same one used by Create Location).

1. Copy the demo files into your tests folder (they only read from the pod):

   ```powershell
   Copy-Item examples\demos\*.yaml my_tests\
   ```

   Open **Tests** (press F5 if it was already open).

   You should see: three new tests whose names say "demo": Cleanup demo, Retry demo and Suggestion demo.

2. In **Tests**, click **Cleanup demo (reads only)**, then **Run this test**.

You should see: the run passes. Under the test's name a blue box says **Test data cleaned up** ("1 cleanup step done after the test"). Open the Word document. Near the end there is a **Cleanup of test data** table with the step marked Passed.

3. Open the file `my_tests\cleanup_demo.yaml` in Notepad and change `locationsV2?limit=1` to `locationsV9?limit=1`. Run it again.

You should see: the run fails at step 1, and the blue box says **Nothing to clean up**. The test never saved a location, so the cleanup step was skipped instead of calling an address with a blank in it. The Word document shows the cleanup step as "Nothing to clean".

4. Put `locationsV2?limit=1` back. Now change the cleanup line (in Notepad too) to `value: DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/300`.

You should see: the test is unreadable (see **Needs attention**, "Unreadable files") and says a cleanup DELETE must use a value the test saved, such as `${location_id}`. Put the line back as it was, or delete the demo file when you are done.

5. To see a cleanup that fails, change the cleanup line (the last `value:` line) of `my_tests\cleanup_demo.yaml` to `value: GET /hcmRestApi/resources/11.13.18.05/locationsV2/1` (a location that does not exist), and run the demo.

You should see: the test still **passes**. Click **Needs attention**. A card **Cleanup did not finish** says "Records from this test may still be on the pod" and lists "Cleanup step 1 (...)" with the pod's answer. The Overview card says "1 cleanup not finished". **Dismiss** hides it until the next run of that test has a failed cleanup again. Put the cleanup line back when you are done.

### 6.15 Backup and restore

1. Click **Settings**, then the **General** tab. Find **Backup and restore**.
2. Click **Download a backup**.

You should see: a file named `quartermaster-backup-<date>-<time>.zip` downloads. Open it (it is a normal zip). There are folders `tests` and `data`, and a file `backup.json`. There is no `vault.key` and no `evidence` folder (you did not tick the evidence box).

3. Make a change you can recognise: in the `my_tests` folder, delete one of your test files (or open **Tests** and note how many there are).
4. Back in **Backup and restore**, click **Restore from a backup**, choose the zip from step 2 and click OK in the question.

You should see: a yellow box, **A restore is waiting**. Nothing has changed yet. (Click **Cancel the restore** to try that. The box goes away.)

5. Choose the zip again to bring the box back. Close the black window, then start Quartermaster again (double-click the desktop icon).

You should see: in the black window, "Restored the backup you chose." The test you deleted is back, and the yellow box is gone. Under **Backup and restore**, "Copies made before a restore" lists a `before-restore-...zip`. That is what was here just before, in case you restored by mistake.

6. Click **Settings, Clients and environments**. Your clients and pods are there, and the test users still say the password is saved (it is kept on this computer).

To try a restore on a second computer: copy the zip there and restore it the same way. The clients and users appear, and each user says the password is not saved yet. Type it again.

In a terminal: `qm backup my-backup.zip`, and, with Quartermaster closed, `qm restore my-backup.zip`.

### 6.15b Automatic backups

1. Click **Settings**, then the **General** tab. Find **Automatic backups**.

You should see: "On: once a day after 02:00", the time, "Keep the newest 7", and "No automatic backup yet". (Quartermaster checks a minute after it starts, so a backup may already be listed.)

2. Click **Back up now**.

You should see: "Backup made." and one row under **Saved copies** with today's time, a size, and **Download** and **Restore**.

3. Type 99 in **Keep the newest** and click **Save**.

You should see: a red message "keep between 1 and 30 backups". Type 3 and **Save**: "Saved."

4. Click **Back up now** four more times (wait a second between clicks, because the file name has the time to the second).

You should see: only the newest 3 copies are listed.

5. Click **Download** on one and open the zip. It has `tests`, `data` and `backup.json`, like a normal backup.
6. Set **After** to a time a few minutes from now, untick **Make a backup every day**, **Save**, then wait past that time.

You should see: nothing new is made. Tick it again and **Save**: a new copy appears within a minute (the time has passed and none was made since).

7. Open **Audit log**.

You should see: "Made an automatic backup" and "Changed the automatic backup settings", each with the file or what changed. No secrets.

### 6.16 Retries, flaky tests and suggested fixes

**A. A step that fails is tried again**

1. Click **Settings**, then **Evidence**. Under **If a step fails**, choose **Try once more**.
2. Make sure the demo files are in `my_tests` (see 6.14, step 1).
3. Run **Retry demo (always fails, reads only)** from **Tests**.

You should see: the test fails, and its step says **Tried 2 times**. In the Word document the step has an **Attempts** line: "2 (every attempt failed)". Now choose **Stop at once** in Settings and run it again. The step says nothing about attempts (it was tried once).

In **Needs attention** this failure is listed as **Service call failed** (a service address that does not exist, so the advice says to check the address), not as a check that did not match.

A test that passes only on the retry cannot be forced on purpose. When it happens for real, the step says "Passed on attempt 2: the first try did not work (...)", the test shows **Flaky** after it happens twice in 10 runs, and the Overview has a **Stability** card with the share of runs that needed a retry.

**B. A suggested fix for a button that was renamed**

4. The test **Suggestion demo (renamed link, reads only)** looks for a link called `Location`. The link on the page is called "Locations", so it stands in for a name Oracle changed. (The suggestion may show the link's longer full name, for example "Locations Define locations...". That is how the page names it.)
5. Run the Suggestion demo. It fails at step 2 (about a minute, because the step is tried again first).
6. Click **Needs attention**. (If a failed run shows no suggestion, open the run, scroll to the bottom and open **Messages from the run**. A line "no suggested fix (...)" says why. Send me that line.)

You should see: under the failure, a blue box **Suggested fix**: "A similar name on the screen suggests the step should find the link named "Locations" instead of the link named "Location"." The test file is not changed yet.

7. Click **Use the suggestion**, then run the test again.

You should see: the run passes. Open the file: `- role: "link:Locations"` is the first way, and the old `link:Location` is still there below it. A copy of the old file is in `.qm\backups`.

8. Only if an AI is set up (Settings, AI assistant): change the file to look for `link:Document Stuff` (a name nothing on the page looks like). Run it. If the AI recognises the screen it may suggest a link. If it is not sure it says nothing, and the failure has no suggestion. That is correct: a wrong suggestion is worse than none. With **Suggest a fix when a step cannot find its item** off, the AI is never asked.

Delete the demo files from `my_tests` when you are done.

### 6.17 Notifications when a run fails

You need one place to receive a message. Use whichever you have:

- a **Slack** or **Microsoft Teams** channel where you may add a webhook (Slack: Apps, Incoming Webhooks. Teams: Workflows, "When a Teams webhook request is received", post to a channel), or
- a **mail server** you may send through (host, port, user and password), or
- for a harmless first try, a free test address from a web page such as webhook.site (it shows what was posted, and the test message holds no secrets). Choose **Other (sends JSON)** and paste the address it gives you.

1. Click **Settings**, then the **Notifications** tab. A blue box says nothing is switched on.
2. In the Slack, Teams or other service box, choose the service, paste the address, tick **Send to this webhook** and click **Save**. (For e-mail, fill in the box below it instead.)
3. Click **Send a test message**.

You should see: "Sent to the webhook." (or "Sent to 1 recipient(s)." for e-mail) in green, and the message "Test message from Quartermaster" in your channel or inbox. Under **Recent messages** the attempt is listed. If it did not work, the text says why, for example "the webhook answered HTTP 404" or "the mail server refused the user name or password". The address and password are not shown again. The box says "Saved (hidden)".

4. Make a run fail on a schedule, which is what notifications are for. Copy the demo files if you have not (see 6.14). Open **Schedules**, **New schedule**: Name `Notify check`, what to test: **Retry demo (always fails, reads only)**, today only, a time two minutes ahead, **On** ticked. Save it, then click **Run now** on it so you need not wait.

You should see: when the run ends (about a minute), a message like "Acme: 1 of 1 test failed" in your channel or inbox, naming **Retry demo**, the failed step and "service calls that failed". It does not contain the pod's own text. **Recent messages** lists it, and the **Audit log** has "Sent a notification".

5. Start the same test yourself from **Tests**.

You should see: no message, because the default is scheduled runs only. In **When to send**, choose **Every run**, **Save**, and run it again. Now a message comes.

6. Delete the schedule when you are done (open it, **Delete**), and switch notifications off if you only wanted to try them.

### 6.18 Approving a release

Do this after some tests have run with the Oracle release set in Settings (6.1).

1. Click **Overview**. In the release card (for example "26C release readiness") there is a line **APPROVAL** with **Not approved** and a button **Approve 26C**.
2. Click **Approve 26C**. A panel opens with the numbers (passed, failed, not run). Click **Approve 26C** without typing anything.

You should see: a red message "type your name". Nothing is recorded.

3. Type your name and your role (for example Test manager). If any test failed or has not run, the panel says so and shows a box. Click **Approve 26C** without ticking it.

You should see: a message that you must tick the box. Tick it and write a short comment (try 3 letters first, because it asks for at least 10), then click **Approve 26C**.

You should see: the panel closes and the card says **Approved**, with your name, the time, the numbers and your comment. Click **History**: the record is there, marked "Approved knowing tests had failed or not run".

4. Click **Certification pack** and open the Word document in the zip. After the Summary there is an **Approval** section: your name, role, time, comment, the numbers when you approved, and "Approved with open items". The zip also holds `approvals.json`.
5. Run any test again (from **Tests**), then look at the card.

You should see: **Approved, results changed**, with how many tests changed. There is an **Approve again** button. (This shows if the test was run on the same release.)

6. Click **Withdraw**, type your name and a reason, and click **Withdraw the approval**.

You should see: the card says **Approval withdrawn** with your reason, and **Approve again**. **History** now lists the approval and the withdrawal. The **Audit log** page has both too. Download the pack again: its Approval section says "Approval withdrawn".

### 6.19 Sign-in and roles

Do this on a test copy, or be ready to turn sign-in off again (step 11). Sign-in is off until you turn it on.

1. Click **Settings**, then **Users and sign-in**. A blue box says sign-in is off.
2. Type your full name, a user name (for example `sai`) and a password of at least 10 characters twice. Click **Turn on sign-in** and confirm.

You should see: the page now lists you as an Administrator ("you"), and the menu has an **Account** section with your name and **Sign out**.

3. Click **Add a user**. Name `Jo Tester`, user name `jo`, leave **Tester** ticked, click **Add the user**.

You should see: a panel with a 12-character temporary password and the words "This is the only time it is shown". Copy it. Click **Done**.

4. Open a second browser (or a private window) and go to http://127.0.0.1:8765

You should see: only a sign-in card, no menu. Try a wrong password: "wrong user name or password".

5. Sign in as `jo` with the temporary password.

You should see: "Choose your own password". Type the temporary one, a new one twice (the two must match) and save.

6. Look at Jo's menu and the Overview.

You should see: Jo can run and record, but there is **no Settings** in the menu, and no **Approve** button on the release card. In the address bar type `#/settings?tab=users`: "this needs an administrator".

7. Back as the administrator: **Settings, Users and sign-in**, click **Change** next to Jo, tick **Approver** too, **Save**. In Jo's browser, sign out and in again (or press F5). The **Approve** button is there.
8. Approve the release as Jo (see 6.18).

You should see: the approval says "Jo Tester", whatever else is typed, and the certification pack says "Signed in as jo".

9. As the administrator, open **Audit log**.

You should see: signing in, adding the user, changing the roles, the approval, each with a name. No passwords.

10. Click **Change** next to Jo, **Reset password**. A new temporary password is shown once, and Jo is signed out. Try signing in as `sai` with a wrong password 5 times. The 6th says to try again in 15 minutes (even with the right password).
11. To turn it off: **Settings, Users and sign-in**, type your password, **Turn off sign-in**. Quartermaster is open again, as before. If you ever cannot sign in, run `qm users disable-signin` in a terminal on this computer.

### 6.20 Sign in with Google

Needs sign-in on (6.19) and a Gmail address. Follow the five Google steps in the README, section "Sign in with Google". Open Quartermaster at **http://localhost:8765** (not 127.0.0.1).

1. **Settings, Users and sign-in**: under **Sign in with Google** the exact redirect address is shown. Paste the Client ID and the Client secret, tick **Turn on Continue with Google**, click **Save**.

You should see: "Saved." and the card says "On". The secret box now says "Saved".

2. **Add a user**: name `Me on Gmail`, your own Gmail address, tick **Google only**, keep Tester. Click **Add the user**.

You should see: "can now sign in with Google" and no temporary password. The row has a **Google only** badge.

3. In a private window open http://localhost:8765.

You should see: a **Continue with Google** button above the user name and password.

4. Click it, choose your Gmail. (Google may say "unverified app". Choose Advanced, then continue. It is your own app.)

You should see: you land in Quartermaster signed in as that user.

5. Sign out, and click **Continue with Google** with a different Gmail that is not on the list.

You should see: the sign-in page with "... has not been given access to Quartermaster. Ask an administrator to add that address."

6. As the administrator, open **Audit log**.

You should see: "Signed in with Google" and "Google sign-in refused", each with the address. No secrets.

7. To turn it off: untick **Turn on Continue with Google**, Save. The button disappears.

### 6.21 Shared steps

Read only, safe on your pod (same as the demos in 6.14).

1. Copy the demos again, this time with the shared folder:

   ```powershell
   Copy-Item examples\demos\* my_tests\ -Recurse
   ```

   Press F5 in Quartermaster. Click **Shared steps** in the menu.

You should see: one card, **Read some locations (reads only)**, "used by 2 tests", with its one step, "A test can hand in how_many (if not given: 1)", and the two demo tests listed.

2. Click **Tests**, open **Shared steps demo two**, then the **Steps** tab.

You should see: one step "Find 2 location(s)" with a small tag **shared: read-locations**.

3. Run **Shared steps demo one** and **demo two**.

You should see: both pass. Each shows a blue box **Test data cleaned up** (the group's cleanup was added to each test).

4. Open `my_tests\_library\read-locations.yaml` in Notepad. Change `locationsV2?limit=` to `locationsV9?limit=` and save. Run both demos.

You should see: **both** fail at step 1, from one change in one file. Put it back to `locationsV2` and both pass again.

5. In Notepad, in `my_tests\library_demo_one.yaml`, change `use: read-locations` to `use: read-locationz`. Open **Shared steps**.

You should see: a red box "A test uses shared steps that do not exist. 'read-locationz'". The test shows as unreadable in **Needs attention**, with a message naming the groups that do exist. Change it back.

6. Delete the demo files from `my_tests` when you are done (including `_library\read-locations.yaml`).

### 6.22 Reading Oracle's What's New

Uses a small sample page that comes with Quartermaster, so it needs no Oracle sign-in. Nothing is saved until you choose Save.

1. Click **Release impact**, then **Import feature list**. Choose the file `examples\releases\26D_whats_new_sample.html`.

You should see: the release box fills in with **26D**, and "4 features found for release 26D". **Columns used** says "Feature (table)" and "the text under each feature's heading". The first features are listed with their product, module, and "opt-in" on **New Personal Details Page** only. **Invoice Approval by Line** says Financials and BOTH.

2. Click **What the page says** under a feature.

You should see: the page's own words for it, for example for Redwood Worker Search: "Search for workers faster..." and the tip about the older page, nothing rewritten.

3. Click **Save feature list**. Choose **26D.json** at the top if it is not already shown.

You should see: the plan for 26D with those four features. Tests that mention search, personal details, locations or invoices are matched to them.

4. Open the import again. Instead of a file, paste the contents of `examples\releases\26D_whats_new_sample.txt` into the box **Or paste text** and click **Check file**.

You should see: "2 features found for release 26D" (the text has two features).

5. Now try your own: open a real What's New page of your update in your browser, save it as HTML only, and import it. Look at the preview. If products or opt-in marks look wrong, send a screenshot of the page and of the preview.

### 6.23 Running tests at the same time

Safe on your pod: it uses the read-only demo tests (copy them as in 6.21 if they are not in `my_tests`).

1. Click **Settings**, then the **Evidence** tab. Find **Tests at the same time**. It says **One at a time**.
2. Run all the demo tests once as they are (**Run all tests**, or **New run** with the folder `my_tests`). Note the **Duration** on the run page.
3. Click **2** under **Tests at the same time**.

You should see: "Saved. Runs started from now on use it."

4. Run the same tests again and open the run page while it runs.

You should see: while it runs, "2 tests running at the same time (0 of 5 finished)", counting up. When it ends: the same results as before, in the same order, and a fact **Tests at once: 2 at the same time**. The **Duration** is shorter. (With read-only demos the gain is small. With 20 or more tests it is large.)

5. Open the evidence of two of the tests. Each has its own folder, screenshots and Word document.
6. In one demo file add the line `tags: [serial]` (under `priority:`) and run again with 2.

You should see: that test runs by itself after the other tests have finished.

7. Set it back to **One at a time** when you are done, if you prefer. If two tests ever disturb each other, report which two and what they change.

### 6.24 Pod discovery

This one needs your real test pod and test user. It only reads, but it signs in, so use a test pod. It has not been tried on a real pod, so please send what you see if it does not work.

1. Click **Settings**, then **Clients and environments**. Find **Pod discovery** under **In use now**.

You should see: "Off", and "Nothing is read until you allow it". **Look at the pod now** is grey.

2. Tick **Allow pod discovery for this pod**.

You should see: "On for this pod", and the button turns blue. Nothing has been read yet.

3. Click **Look at the pod now**. A browser works in the background for about a minute.

You should see: the button says "Looking at the pod...", then a line like "87 pages found today 10:42 am on abcd-dev2...", followed by the names as small tags (for example Person Management, Locations, Absences).

4. Open **Audit log**.

You should see: "Switched pod discovery on", "Started pod discovery" and "Pod discovery finished" (with the number of pages found).

5. Import a feature list for your release (the What's New page, 6.22, or the Readiness sheet), open **Release impact**, and look at the **Features** tab.

You should see: a filter **On your pod** and, under some features, "On your pod: Locations" (or other page names). Click the filter: only those features remain.

6. Back in Settings, click **Remove what was found**, then open Release impact again.

You should see: the filter and the lines are gone. Untick **Allow pod discovery** to switch it off.

If step 3 says "the Navigator button was not found", finds no pages, or finds menu text that is not page names, send a screenshot of your pod's home page with the Navigator open, and of the card. The reading is made to cope with Oracle's menu without knowing its exact markup, but it has only been tried on a stand-in menu.

### 6.25 Ticket links

Needs a failing test in Needs attention (the retry demo from 6.16 fails on purpose). No tracker account is needed.

1. Click **Settings**, then **General**. Find **Ticket tracker**. Type `https://example.atlassian.net/browse/{key}` in **Ticket address** (use your real tracker's address if you have one), leave the other box empty, and click **Save**.

You should see: "Saved." A wrong address (one that does not start with https:// or has no {key}) gives a red message.

2. Click **Needs attention**. On a failure card, click **Ticket**.

You should see: a panel with a title and a description of the failure (test, step, expected and observed, release, run) and a **Copy title and description** button. There is no "Open ... with it" button, because no new-ticket address is set.

3. Type `PROJ-123` under **Link a ticket** and click **Link ticket**.

You should see: "Linked.", and `PROJ-123` under **Linked tickets**. Close the panel. The card now shows **Tickets PROJ-123** and its button says **Ticket (1)**. Click the tag: it opens `https://example.atlassian.net/browse/PROJ-123` in a new tab.

4. Open **Tests** and the same test.

You should see: **Tickets PROJ-123** under the test's name.

5. Try to link `javascript:alert(1)`.

You should see: a red message "a ticket number uses letters, digits and . _ # - only", and nothing linked.

6. If a release is set, open **Overview** and download the **Certification pack**. Open the Word document, section "What failed".

You should see: a row **Tickets** with `PROJ-123 (https://...)` under that test.

7. Click **Ticket (1)**, then **Remove** next to the ticket. Open **Audit log**.

You should see: the link gone, and "Linked a ticket" and "Removed a ticket link" in the log.

### 6.26 Nightly run against the pod (GitHub)

Needs your repository on GitHub, a test pod that GitHub's computers can reach, and a test user. Type the secrets only in GitHub's page, never into a chat or a file.

1. On GitHub open the repository, **Settings, Secrets and variables, Actions, Secrets, New repository secret**. Add `QM_FUSION_URL`, `QM_FUSION_USER` and `QM_FUSION_PASSWORD`.
2. Click **Actions**, then **Nightly pod run** on the left, **Run workflow**, **Run workflow** again.

You should see: a run starts. If a secret is missing, it ends red at the first step with "Add these repository secrets first: ...".

3. When it ends, open it.

You should see: a summary at the top, "1 of 1 tests passed", with the test "Sign in and reach the Fusion home page". The log does not show what the pod displayed.

4. If it says "The run did not finish", open the job's log. A pod that only accepts known addresses refuses GitHub. Tell your Oracle administrator, or skip this and use Schedules on your computer.
5. To see the nightly switch: before you add the variable `QM_NIGHTLY`, the schedule does nothing. Add it (Variables tab, value `on`), and the next night a run appears at about 02:17 UTC (04:17 in Germany in summer, 22:17 in New York the evening before).
6. Optional: set the variable `QM_NIGHTLY_TESTS` to `examples/tests` (if your pod has the Create Location data) or to a folder of your own tests that you committed, run it by hand again, and look at the summary. Only in a private repository, add `QM_NIGHTLY_DETAILS` = `on` and run again. Now the failed step shows what the pod displayed.

### 6.27 AI quality check

Needs an AI provider and key saved in Settings (any provider). It sends 15 short made-up questions and nothing from your pod.

1. Click **Settings**, then **AI assistant**. Choose your provider, model and key, click **Save**, then **Test the AI**. Below the AI box find **AI quality check**.

You should see: "Not run yet". The button **Check this AI** is grey until an AI is chosen and its key works.

2. Click **Check this AI**.

You should see: the button counts up ("Asking... 7 of 15"), taking about a minute.

3. When it ends:

You should see: a verdict (Good, Usable with care, Weak), a percentage, and a table of counts. Open **Every question**: each question has a coloured result, what the AI picked and what was wanted. A **Wrong pick** is the one to care about. The card says how many would have reached you.

4. Change the model in the AI box (a smaller one if you used a large one), Save, and check again.

You should see: a second score. Click **Earlier checks**: both scores are listed with the model names, so you can compare.

5. Open **Audit log**.

You should see: "Started the AI quality check" and "Finished the AI quality check" with the score. No keys.

If every question says "Could not ask", the key or the model name is wrong. Use **Test the AI** and fix it first. If a model scores Weak, report which model and send a screenshot of **Every question**, so we can see whether the questions or the model are at fault.

### 6.28 Exporting tests as plain Playwright

You can look at the files without running anything. To run them you need Python and your test pod (same login as for Quartermaster).

1. Click **Tests**. Click **Export** at the top. A zip downloads (`quartermaster-tests-playwright-<date>.zip`). Unzip it into a new folder, for example `exported`.

You should see: one `test_....py` file per test that can be read, and `fusion_runtime.py`, `conftest.py`, `pytest.ini`, `requirements.txt`, `README.md`.

2. Open one `test_....py` in Notepad.

You should see: the test's title and id at the top, a `DATA = {...}` block, then `with fusion.step(1, "..."):` blocks, one per step, each with a short call. Passwords do not appear anywhere. A test with cleanup has a `finally:` part at the end.

3. Open a test's page and click **Export** there.

You should see: a smaller zip with just that test (`<test id>-playwright.zip`).

4. Run them. In PowerShell, in the `exported` folder (use a new virtual environment if you like):

   ```powershell
   pip install -r requirements.txt
   python -m playwright install chromium
   $env:QM_FUSION_URL = "https://your-test-pod..."
   $env:QM_FUSION_USER = "your.test.user"
   $env:QM_FUSION_PASSWORD = "..."
   pytest -v -k login
   ```

   (Use a test whose name matches `-k`. The smoke login test, if you export `examples\smoke`, is `-k smoke_login`. To export just that folder: `qm export examples\smoke --out exported_smoke`.)

You should see: `1 passed`. With `$env:QM_HEADED = "1"` first, you also see the browser sign in.

5. Break it on purpose: in the test file, change the name in an `assert_text` or the link name in a `click` to something that does not exist, and run it again.

You should see: a failure that says `step 2 (...)`, the list of ways it tried (`role='link:...' matched 0`), and a screenshot in the `evidence` folder.

6. Run `qm export my_tests --out exported` again.

You should see: an error that the folder already has the files, so nothing is replaced. Add `--force` only if you want to replace them.

If a test behaves differently in the export than in Quartermaster, send the test, the step number and what the pod showed.

### 6.29 Test data

Read only, safe on your pod (same as the demos in 6.14). You need an environment set up in Settings, with its **Kind** (DEV, TEST or STAGE) chosen.

1. Copy the demos again, with the data folder:

   ```powershell
   Copy-Item examples\demos\* my_tests\ -Recurse
   ```

   Press F5 in Quartermaster. Click **Test data** in the menu.

You should see: a card **How many rows to ask for**, "used by 1 test", with a table that has one row for each of your pods (its name and kind) and the number of rows that pod gets (1 for a TEST or STAGE pod, 2 for a DEV pod). Under it, a card **Values made fresh for every run** with a name, `pick`, how it is made ("a whole number from 1 to 3") and a sample.

2. Click **Tests**, open **Test data demo (reads only)**, and open its **Test data** tab.

You should see: `rows = 2`, "Data sets used: pod-sizes", "Different on some pods" (TEST and STAGE) and "Made fresh for every run: pick".

3. Run **Test data demo (reads only)** twice. Open each run and read the two steps.

You should see: both pass. Step 1 shows `.../locationsV2?limit=2` (or 1, if your pod is a TEST or STAGE pod). Step 2 shows `limit=` with a number from 1 to 3. The second run may show another number, because it is made again for each run.

4. Run **Test data gap demo (stops unless STAGE)**.

You should see: it fails at step 1 with "No test data for location_limit on (your pod's name) (its kind)", and the other steps are marked not run. Click **Needs attention**: the item is called **Test data not ready**, with a note that it is a gap in the data and not a broken release, and a button **Open test data**. The test never called the pod.

5. In Notepad, open `my_tests\data_gap_demo.yaml`. Under `pods:` add a line for your pod's kind, for example `  DEV: {location_limit: "1"}` (use TEST if your pod is a TEST pod), save, and run it again.

You should see: it passes now.

6. In Notepad, open `my_tests\_data\pod-sizes.yaml` and change `rows: "2"` to `rows: "3"`. Press F5 on **Test data**.

You should see: the table shows 3 for a pod that is not TEST or STAGE. Run the demo again: step 1 asks for 3. Put it back.

7. In Notepad, in `my_tests\data_demo.yaml`, change `data_sets: [pod-sizes]` to `data_sets: [pod-sizez]`. Open **Test data**.

You should see: a red box "A test uses a data set that does not exist. 'pod-sizez'". The test shows as unreadable in **Needs attention**, with a message naming the sets that do exist. Change it back.

8. Download the export: **Tests**, then **Export**. Unzip it and open `test_demo_test_data.py`.

You should see: a `DATA` and a `PODS` dictionary, a `GENERATE` dictionary, and `fusion.use_data(DATA, pods=PODS, generate=GENERATE)` at the top of the test. Running it with pytest on your pod gives the same values as above.

9. Delete the demo files from `my_tests` when you are done (including the `_data` folder).

### 6.30 Setup steps

Read only, safe on your pod (same as the demos in 6.14).

1. Copy the demos again:

   ```powershell
   Copy-Item examples\demos\* my_tests\ -Recurse
   ```

   Press F5 in Quartermaster. Click **Tests**, open **Setup demo (reads only)** and look at its **Steps** tab.

You should see: a blue box **Checked or made on the pod first** listing "The pod has a location to work with" and its address, then the one step under it.

2. Run **Setup demo (reads only)**. Open the run and click the test.

You should see: it passes, with a blue box **The pod was ready.** naming the setup step. Open the **Evidence document**: it has a table **Setup before the test**, before the Steps.

3. Run **Setup that is not met (stops at step 1)**.

You should see: it fails at step 1 with "Setup not met: The pod has a location to work with." and a note from the pod (the address `locationsV9` does not exist). The run page has an orange box **Setup not met: the pod was not ready, so the test steps did not run.** The steps after it are marked not run.

4. Click **Needs attention**.

You should see: an item with the label **Test data not ready**, a blue note "The test data was not ready, which is not a broken release", a button **Open test data**, and no advice that blames the Oracle update.

5. In Notepad, open `my_tests\setup_gap_demo.yaml`. Change `locationsV9` to `locationsV2` and save. Run it again.

You should see: it passes now, with **The pod was ready.**

6. Download the export (**Tests**, then **Export**), unzip it and open `test_demo_setup.py`.

You should see: `with fusion.setup(1, 'The pod has a location to work with'):` before `with fusion.step(1, ...)`.

7. Delete the demo files from `my_tests` when you are done.

### 6.31 Suites

Read only, safe on your pod (same as the demos in 6.14).

1. Copy the demos again, with the suites folder:

   ```powershell
   Copy-Item examples\demos\* my_tests\ -Recurse
   ```

   Press F5 in Quartermaster. Click **Suites** in the menu.

You should see: one card **Demos that pass**, "5 tests", "Includes tests: tagged demo", "Except tests: named demo.retry or demo.suggest or demo.test-data-gap or demo.setup-gap". Open "The 5 tests it has now": each title is a link to the test.

2. Click **Run** on the card. In the New run drawer, **A suite** is chosen and says "Demos that pass, 5 tests". Click **Start run**.

You should see: a run with 5 tests that all pass. In **Runs** it is labelled **Suite: Demos that pass**.

3. Click **New suite**. Name it `Locations`. Click a product, folder or tag chip (for example the tag `demo`), and watch **What it has now** list the tests as you choose. Click **Save**.

You should see: a new card with the tests it has now and a **Change** button. Click **Change**, pick one more chip, and save. The count changes.

4. Open `my_tests\_suites\` in File Explorer. Your new suite is a small text file. Open it in Notepad.

You should see: the same rule you chose, in plain words. Change `tags: [demo]` to `tags: [nothing]`, save, and press F5 on **Suites**. The card shows a yellow note "Group 1 matches no test." and **0 tests**, and **Run** is greyed out.

5. Click **Schedules**, **New schedule**. In **What to test** choose **Suite: Demos that pass (5 tests now)**. Set a day and a time a few minutes ahead, leave Quartermaster running, and save.

You should see: the schedule listed with **Suite: Demos that pass**. When the time comes it runs the 5 tests (Runs shows **Scheduled: your name**). **Run now** on the schedule does the same at once.

6. In a terminal in the Quartermaster folder:

   ```
   qm suites my_tests --list
   qm run my_tests --suite demos-that-pass
   ```

You should see: the first lists each suite with how many tests it has and their ids. The second runs only those 5 tests (it needs the pod set in the terminal as in Part 5).

7. Click **Delete** on the suite you made, and confirm.

You should see: it is gone and its file is removed. The tests themselves are untouched. Delete the demo files from `my_tests` when you are done (including the `_suites` folder).

### 6.32 Audit trail

1. Click **Audit log** in the menu.

You should see: a blue box **The audit log is whole.** with the number of lines and the dates they run from and to, then the table. Buttons **Export** and **Download CSV** are at the top.

2. Do something that is recorded: start any run, or change the release in Settings. Press F5 on **Audit log**.

You should see: the new line at the top, and the blue box still says the log is whole (with one more line).

3. Click **Export**. Choose **JSON lines**, type a word that is in some lines (for example the name of a test) in **Word**, and watch the line at the bottom say how many lines will be in the export. Click **Download the zip**, and unzip it.

You should see: three files: `audit.jsonl`, `manifest.json` and `HOW_TO_VERIFY.txt`. In the manifest: the number of lines, your filter under `filters`, `"complete": false`, and `log.chain` is `intact`. Back on the Audit log page there is a new line, **Exported the audit log**, with your name.

4. In a terminal in the Quartermaster folder:

   ```
   qm audit verify
   qm audit verify path\to\audit.jsonl
   ```

You should see: `OK: ... the chain is whole` for the first, and `OK: ... each is what its hash says` for the second.

5. Now try to cheat. Make a copy of `.qm\audit.jsonl` first. Close Quartermaster. Open `.qm\audit.jsonl` in Notepad, change a word in one line (for example the name in a **Who**), save, and run `qm audit verify` again.

You should see: `BROKEN at line N: this line was changed`, with N the line you edited, and the exit code is 1. Start Quartermaster: the Audit log page shows a red box **The audit log has been changed.** with the same line number. Change the word back: it is whole again.

6. Put your backup copy of `.qm\audit.jsonl` back, then delete a whole line in the middle of the file in Notepad and save.

You should see: `BROKEN at line N+1: the line before it was changed, removed or moved` (N is the line you deleted).

7. Make an export of the whole log (no word, no dates) and keep the `manifest.json`. Then, with Quartermaster closed, delete the **last** line of `.qm\audit.jsonl`.

You should see: `qm audit verify` still says OK (a chain cannot see the end cut off), but `qm audit verify --against path\to\manifest.json` says `NOT CONSISTENT with the manifest: the newest line of that export is not in the log any more`.

Put your copy of `.qm\audit.jsonl` back when you are done.

### 6.33 Single sign-on (company)

You need a company provider to try this for real (OpenID Connect): your company's Okta, Microsoft Entra ID or Keycloak, or a free developer account at Okta or Auth0, or Keycloak on your own computer. Register an app as described in the README (section Single sign-on) and keep its client ID, secret and groups claim ready. Have a second browser (or a private window) for the person who signs in, and sign-in turned on in Quartermaster (6.19). Type the secret only into the page.

1. **Settings, Users and sign-in.** Find **Single sign-on (company)**. Copy the redirect address it shows into the provider's app.
2. Fill in the provider address, client ID and client secret, and press **Check the provider**.

You should see: "The provider answers. N signing keys found, so the signature of every sign-in is checked." A wrong address says "Could not reach ..." or "The provider answered 404 ...".

3. Leave **Only people I add under Users**, turn on **single sign-on**, Save. Under **Users**, add yourself or a colleague by their company e-mail address (the one the provider sends), with **No password** ticked.

You should see: the card says "On: people can use Sign in with ...", and the secret box says "Saved". In the other browser, the sign-in page has a **Sign in with ...** button above the user name and password.

4. In the other browser click it, and sign in at the provider.

You should see: you land on Overview, signed in as the person you added, with their roles. **Audit log** has "Signed in with single sign-on".

5. Try someone who was not added: sign in at the provider with another account.

You should see: back on the sign-in page, in red: "... has not been given access to Quartermaster. Ask an administrator to add that address." The audit log has "Single sign-on refused" with the reason.

6. Switch to **Make a person at their first sign-in**, add a group row (a group the provider sends, for example `qm-testers`) with role Tester, Save, and sign in again with that second account (a member of the group).

You should see: the person is made at once (Users shows them with the badge **Single sign-on**) with the role Tester. In the audit log: "Created a user from single sign-on".

7. Change the group at the provider (take the person out of it), sign out and in again.

You should see: their roles no longer include Tester (the audit log says "Changed roles from single sign-on", from and to).

8. Tick **Only people in one of these groups may sign in** and sign in with an account in no mapped group.

You should see: refused with "... is not in a group that gives access to Quartermaster."

9. Type your company's e-mail domain under **E-mail domains allowed**, Save, and try an account with another domain.

You should see: refused with "... is not at an e-mail domain that may sign in here."

10. Tick **Require single sign-on**, Save. In a third window try a user name and password for a tester.

You should see: "your company requires single sign-on: use the single sign-on button instead". An administrator's password still works. Untick it again when you are done, if you like.

Common problems:

- "The provider refused the sign-in (invalid_client)": the client ID or secret is wrong.
- "did not say the person's e-mail address": the provider needs the `email` scope (or a Microsoft account with an e-mail address).
- Groups missing: check the groups claim name on the card, and that the provider adds it to the **ID token**.

### 6.34 Test library

Read only, safe on your pod: every test in the library only asks the pod questions (GET). The test user you set in Settings needs permission to read the REST services of the areas you try. Where it does not, that test fails with "the API answered HTTP 403".

1. Click **Test library** in the menu.

You should see: five cards (Financials, HCM, Procurement, Sales and Service, Supply Chain), each with the number of tests, a green line "checked on a pod: N of N passed" and what the test user needs. At the top a box says what the tests do and what they do not.

2. On **HCM services (read only)**, open **The 23 tests**, then click **Install**.

You should see: a message "HCM services (read only): 23 added." and, on the card, "Installed (version 1) in tests/library/hcm-services. Up to date." **Run these tests** appears. Click **Tests**: 23 new tests, each called like "Workers: the service answers and returns the fields integrations rely on". Click **Suites**: a suite "HCM services (read only) (library)" with 23 tests.

3. Click **Test library**, **Run these tests** on the HCM card, then **Start run**.

You should see: 23 tests run in a few minutes (they are plain service calls made through the signed-in session). Open the run. Passing tests say the service answered and the fields were there. Tests that fail are the information you wanted. Open one:

- "The Absence plans service answers" failing with "the API answered HTTP 404": this pod's release does not have that service.
- "HTTP 403": the test user may not read it.
- "the API reply has no items[0].X": the pod has no record of that kind, or the field was renamed or removed.

Look at **Needs attention**: they are listed by kind there like any other failure.

4. Open one test (for example Workers) and its **Steps** tab, then its **File** tab.

You should see: step 1 asks for one worker and checks `count`, `hasMore`, `PersonId` and `PersonNumber`. The second step (where there is one) reads the worker again by its id. The file is plain YAML you can change. In Notepad, open `my_tests\library\hcm-services\workers.yaml`, add `"items[0].LastName": "*"` under `check:` (a field you rely on), save, and run the test again.

5. Press **Install again** on the HCM card.

You should see: "... 22 unchanged, 1 kept as you have them" and, on the card, "You have changed 1 file; it is kept as it is when you update." Your edit is still in the file.

6. From a terminal in the Quartermaster folder:

   ```
   qm packs list --tests my_tests
   qm packs install financials-services --tests my_tests
   qm run my_tests --suite library-financials-services
   ```

You should see: the list says which packs are installed and when they were last checked. The second installs the Financials pack. The third runs just its 13 tests (the pod set in the terminal as in Part 5).

7. To see exactly what a pack's tests do to your pod, open any file of a pack. Every step is `GET`. The test library never creates, changes or deletes anything.

### 6.35 Copy a test and edit a client's values

This answers "each client has its own business unit and ledger, so how do I handle that?" Nothing here touches the pod.

1. Click **Test data**, then **New data set**.

You should see: a side panel with a Name box, a Title box and a table with the columns "Value name" and "Every pod", then one column for each pod you added in Settings.

2. Type the name `acme-gl` and press **Save** without filling anything in.

You should see: a red message "Could not save." and the reason (a set needs at least one value). Nothing is saved.

3. In the first row type `ledger_name` as the Value name, and `US Primary Ledger` under **Every pod**. In a pod's own column type a different ledger if that pod uses another one. Press **Save**.

You should see: "Saved". The new set appears on the page with a table of what each pod gets. In Notepad, open `my_tests\_data\acme-gl.yaml`: the values are there as plain text. If you saved over an existing set, the old file is kept in the backups folder.

4. Press **Edit values** on a set that exists, change a value, press **Save**.

You should see: the table on the page shows the new value. Every test that uses the set follows it.

5. Click **Tests**, open a test, press **Make a copy**.

You should see: a side panel with an Id (the old id with "-copy"), a Title (with "(copy)") and a Folder (the same folder).

6. Change the id to `view-worker-acme` and the title to `View a worker (Acme)`, press **Make the copy**.

You should see: the copy opens. Its Steps and File tabs match the original, comments included. Only `id:` and `title:` differ. Click **Tests**: both are listed.

7. Open the copy, press **Make a copy**, and keep the same id as the first copy.

You should see: "Could not copy." and "a test with the id 'view-worker-acme' already exists". Nothing is created.

8. Try the Folder box with `..` or `_data`.

You should see: a refusal that the folder must be inside the tests folder, or must not start with an underscore.

To use a test for a new client: add the client, copy the test (or its folder) into that client's tests folder, and fill in that client's data set. The steps stay the same.

## Part 7. Stop

Press Ctrl+C in the terminal running `qm serve`.

Where things are kept:

- `evidence\` holds screenshots, videos and Word documents of every run.
- `.qm\` holds the run history, settings, imported feature lists and imported manual scripts.

Keep `.qm\`. Deleting it also deletes your imported manual scripts and run history. The scenarios you did by hand or prepared are saved as tests in `my_tests\manual\`. `my_tests` is yours: back it up. It is not part of the Quartermaster repository, so it is never committed or overwritten by a pull.

## Reporting a problem

Send these four things:

1. Which part and step (for example "6.6 step 7").
2. What you expected and what you saw.
3. A screenshot of the page.
4. The last 30 lines of the terminal. Remove the password if it appears anywhere.
