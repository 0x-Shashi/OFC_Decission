# Unverified example tests

These six tests were written as examples before Quartermaster was connected to a real pod. Their
navigation paths, labels and data have **not** been checked against any pod, so they fail on step 1
or 2 (for example, "could not resolve 'link:Create Order'"). All six also **submit real
transactions** (an invoice, a journal, a purchase order, an absence request, a hire, a promotion)
once they get that far.

They are kept apart from `examples/tests` so that "Run all tests" in the web UI, and
`qm serve --tests examples/tests`, only run tests that are checked and safe to repeat.

They are still useful:

- as patterns for writing tests by hand;
- for the release impact demo: `qm plan --release examples/releases/26D_sample.json --tests examples/unverified --explain`.

To make one usable, record the same business flow on your pod (Record a test in the web UI, or
`qm record`), or fix its navigation and labels against your pod, then move it into your tests folder.
