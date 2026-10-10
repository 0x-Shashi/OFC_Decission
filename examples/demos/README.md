# Demo tests

Nine small tests and a suite to try the newer features on your own pod. They only read from the pod (they never
save, submit or delete), and they are kept out of `examples/tests` so "Run all tests" and new clients do
not pick them up.

To use them, copy them into your tests folder, then open **Tests** (press F5 if it is already open):

    Windows PowerShell:   Copy-Item examples\demos\* my_tests\ -Recurse
    Mac or Linux:         cp -r examples/demos/* my_tests/

(`-Recurse` / `-r` also copies the `_library`, `_data` and `_suites` folders the shared-steps, test-data and suite demos need.)

Delete them from `my_tests` when you are done. Steps for each are in TESTING.md (6.14 and 6.16).

| File | Shows | What you should see |
|---|---|---|
| `retry_demo.yaml` | Retries | It fails, and its step says "Tried 2 times" |
| `suggest_demo.yaml` | Suggested fixes | It fails at step 2; Needs attention suggests the link "Locations" |
| `cleanup_demo.yaml` | Cleanup after a test | It passes, and a box says "Test data cleaned up" |
| `library_demo_one.yaml`, `library_demo_two.yaml` | Shared steps | Both pass. Shared steps lists `read-locations` used by 2 tests |
| `data_demo.yaml` | Test data | It passes. Test data lists the data set `pod-sizes`, and the run page shows how many rows each step asked for |
| `data_gap_demo.yaml` | A gap in the test data | It stops at step 1 with "No test data for location_limit" (unless your pod is a STAGE pod) |
| `setup_demo.yaml` | Setup steps | It passes. The run page says "The pod was ready" and lists the setup before the steps |
| `setup_gap_demo.yaml` | A setup that is not met | It stops at step 1 with "Setup not met" (the steps never run). Change `locationsV9` to `locationsV2` and it passes |
| `_suites/demos-that-pass.yaml` | A saved suite | Suites lists **Demos that pass** with 5 tests. Run it from there: all 5 pass |
