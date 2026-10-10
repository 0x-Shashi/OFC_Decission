# Tests exported from Quartermaster

These are ordinary Playwright for Python tests (run by pytest), one file per test. They need only Playwright and pytest:
no Quartermaster. Open them, change them, put them in your own repository and CI.

## Run them

```bash
pip install -r requirements.txt
python -m playwright install chromium

export QM_FUSION_URL=https://abcd-dev2.fa.us6.oraclecloud.com   # a TEST pod
export QM_FUSION_USER=your.test.user
export QM_FUSION_PASSWORD=...                                    # set it in your CI's secret store, not in a file

pytest -v                       # all of them
pytest -k create_location       # one
pytest -m smoke                 # by tag
pytest -n 4                     # four at a time, with pytest-xdist (pip install pytest-xdist)
QM_HEADED=1 pytest -k login     # watch the browser
```

On Windows PowerShell set the variables with `$env:QM_FUSION_URL = "https://..."`.

Other variables:

| Variable | Meaning |
|---|---|
| `QM_FUSION_USER_<PERSONA>`, `QM_FUSION_PASSWORD_<PERSONA>` | A test user for a persona the tests switch to (`HR Manager` is `HR_MANAGER`). |
| `QM_FUSION_ALLOWED_HOSTS` | Host names that are test pods although their name has no dev, test or stage in it. |
| `QM_STORAGE_STATE` | A file saved with `context.storage_state()` after signing in by hand, for single sign-on and MFA. It is used instead of a password. |
| `QM_ENV_NAME`, `QM_FUSION_KIND` | The pod's name and kind (`DEV`, `TEST`, `STAGE`), to choose the values in a test's `PODS`. When not set, the name is the first part of the pod's host name and the kind is read from it. |
| `QM_CHROMIUM_PATH` | A Chromium to use instead of Playwright's own. |
| `QM_HEADED` | `1` shows the browser. |

## What is in each test

- The test data is a `DATA` dictionary at the top; `${name}` in a step is filled in from it, from values kept by earlier
  REST steps, from `${RUN_ID}` (unique per run, so names do not clash) and from `${env:NAME}` (a secret read from the
  environment).
- A test may also have `PODS` (values that differ by pod, chosen by the pod's name or kind) and `GENERATE` (values made
  fresh for every run: a unique code, a date from today, a number, one of a list). `fusion.use_data(...)` at the top of
  the test applies them. If this pod has no value for a name that others have, the step stops saying so.
- Every step is `with fusion.step(n, "what it does"):`. A failing step says which step it was and saves a screenshot in
  `evidence/`.
- Every item is found by a list of ways, in order. The first way that finds exactly one element is used.
- The cleanup (if the test had one) is in `finally:`: it runs even when a step failed, a failure in it only prints a
  warning, and a step that needs something the test never made is skipped.

`fusion_runtime.py` is the small library behind it (the Navigator, date boxes, type-ahead lists, REST calls with the
browser's session, waiting for a scheduled process, the refusal to run on a production pod). It is plain code to read and change.

## What is not here

Quartermaster's own extras are not part of the export: trying a failed step again, suggesting what a renamed button
became, the Word evidence documents with a picture of every step, schedules, release impact and the approval. The
tests also do not heal themselves: when Oracle renames something, edit the list of ways in the test.
