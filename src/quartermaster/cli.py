"""`qm` command line: validate specs and build a risk-ranked regression plan."""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.domain.models import Environment, EnvironmentKind, RunResult, ScreenshotMode, StepStatus, TestCase
from quartermaster.dsl.library import files_of_tests
from quartermaster.dsl.loader import SpecError, load_release, load_test, load_tests
from quartermaster.dsl.suites import SUITES_DIR, SuiteError, read_suites, resolve
from quartermaster.evidence.document import write_evidence_document
from quartermaster.evidence.run_record import build_record, new_run_id, run_folder, write_record
from quartermaster.evidence.suite import build_suite_record, suite_folder, write_suite_document, write_suite_record
from quartermaster.impact.analyzer import analyze, plan
from quartermaster.runner.credentials import MissingCredentialsError
from quartermaster.runner.engine import Driver, run_test
from quartermaster.safety.guards import UnsafeEnvironmentError, assert_safe_target, confirmed_hosts


def _playwright_driver(args: argparse.Namespace, run_dir: Path) -> Driver:
    from quartermaster.runner.playwright_driver import PlaywrightDriver

    return PlaywrightDriver(
        headless=not args.headed,
        evidence_dir=str(run_dir),
        record_video=args.video != "off",
        highlight=not getattr(args, "no_highlight", False),
    )


# Replaced in tests with a fake; the real run drives a browser. Gets the run's evidence folder.
driver_factory: Callable[[argparse.Namespace, Path], Driver] = _playwright_driver

MAX_PARALLEL = 4  # tests run at the same time: each is a browser, and the pod is shared with other people


def _validate(args: argparse.Namespace) -> int:
    tests = load_tests(args.tests)
    print(f"OK: {len(tests)} test spec(s) valid in {args.tests}")
    return 0


def _plan(args: argparse.Namespace) -> int:
    release = load_release(args.release)
    tests = load_tests(args.tests)
    opt_ins = set(args.opt_in or [])
    p = plan(analyze(release, tests, opt_ins), budget_minutes=args.budget, min_risk=args.min_risk)

    budget = f"{args.budget:g} min budget" if args.budget else "no budget"
    print(f"Release {release.id}: {len(release.features)} features, {len(tests)} tests, {budget}\n")
    print(f"{'#':>2}  {'priority':>8}  {'risk':>5}  {'min':>4}  test")
    for n, i in enumerate(p.selected, 1):
        print(
            f"{n:>2}  {i.priority:>8.2f}  {i.risk:>5.2f}  {i.test.estimated_minutes:>4g}  {i.test.id} - {i.test.title}"
        )
        if args.explain:
            for r in i.reasons:
                print(f"{'':>26}- {r}")
    print(f"\nSelected {len(p.selected)} test(s), {p.total_minutes:g} min. Deferred {len(p.deferred)}.")
    uncovered = p.uncovered_features(release)
    if uncovered:
        print("\nFeatures with NO covering test (candidates for new tests):")
        for f in uncovered:
            print(f"  - {f.id} [{f.product}] {f.title}")
    return 0


def _suite_facts(target: Path, tests: list[TestCase], files: list[Path]) -> list[dict[str, Any]]:
    """What a suite looks at in each test, from the loaded tests (folders are counted from the tests folder)."""
    root = target if target.is_dir() else target.parent
    return [
        {
            "id": t.id,
            "title": t.title,
            "tags": t.tags,
            "module": t.module,
            "product": t.product,
            "priority": t.priority.value,
            "folder": f.relative_to(root).parent.as_posix().removeprefix("."),
        }
        for t, f in zip(tests, files, strict=True)
    ]


def _suite_ids(target: Path, tests: list[TestCase], files: list[Path], name: str) -> list[str]:
    root = target if target.is_dir() else target.parent
    directory = root / SUITES_DIR
    found, _ = read_suites(directory if directory.is_dir() else None)
    if name not in found:
        raise SuiteError(f"no suite called '{name}' (there is: {', '.join(sorted(found)) or 'none yet'})")
    got = resolve(found[name][1], _suite_facts(target, tests, files))
    if not got.ids:
        raise SuiteError(f"the suite '{name}' has no tests right now")
    return got.ids


def _suites(args: argparse.Namespace) -> int:
    target = Path(args.tests)
    tests = load_tests(target)
    files = files_of_tests(target)
    directory = target / SUITES_DIR
    found, problems = read_suites(directory if directory.is_dir() else None)
    if not found and not problems:
        print(f"No saved suites yet. Put a YAML file in {directory} (or use Suites in the web pages).")
        return 0
    facts = _suite_facts(target, tests, files)
    for name, (_, raw) in sorted(found.items()):
        got = resolve(raw, facts)
        print(f"{name:<24} {len(got.ids):>3} tests  {raw.get('title', '')}")
        for w in got.warnings:
            print(f"    warning: {w}")
        if args.list:
            for i in got.ids:
                print(f"    {i}")
    for path, why in problems:
        print(f"cannot use {path.name}: {why}", file=sys.stderr)
    return 1 if problems else 0


def _run(args: argparse.Namespace) -> int:
    url = os.environ.get("QM_FUSION_URL")
    if not url:
        print("error: set QM_FUSION_URL to the non-prod pod URL", file=sys.stderr)
        return 2
    env = Environment(name=args.env_name, url=url, kind=EnvironmentKind(args.kind), release=args.release)
    target = Path(args.tests)
    if target.is_dir():
        tests: list[TestCase] = load_tests(target)  # also rejects duplicate ids
        files: list[Path] = files_of_tests(target)  # same order load_tests uses
    else:
        tests, files = [load_test(target)], [target]
    if args.suite:
        if args.only:
            print("error: use --suite or --only, not both", file=sys.stderr)
            return 2
        try:
            args.only = ",".join(_suite_ids(target, tests, files, args.suite))
        except SuiteError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if args.only:
        wanted = [t for t in args.only.split(",") if t.strip()]
        unknown = sorted(set(wanted) - {t.id for t in tests})
        if unknown:
            print(f"error: no test with id {', '.join(unknown)} in {target}", file=sys.stderr)
            return 2
        pairs = [(t, f) for t, f in zip(tests, files, strict=True) if t.id in wanted]
        tests, files = [t for t, _ in pairs], [f for _, f in pairs]
    evidence_root = Path(args.evidence)
    suite_started = datetime.now().astimezone().isoformat(timespec="seconds")
    emit = _event_writer(args.events)
    emit({"type": "suite_start", "at": suite_started, "tests": [t.id for t in tests], "parallel": _workers(args)})

    healer = _suggester(args)
    print_lock = threading.Lock()
    abort = threading.Event()  # set when a run cannot go on at all (unsafe pod, missing login)

    def run_one(t: TestCase, spec_file: Path) -> tuple[RunResult, dict[str, Any], Path, Path | None]:
        run_id = new_run_id()
        run_dir = run_folder(evidence_root, t.id, run_id)
        driver = driver_factory(args, run_dir)
        result = run_test(
            t,
            env,
            driver,
            run_id=run_id,
            screenshots=ScreenshotMode(args.screenshots),
            on_event=emit,
            healer=healer,
            retries=args.retries,
        )
        lines = [f"{result.status.value.upper():<7} {t.id}"]
        for s in result.steps:
            if s.status in (StepStatus.FAILED, StepStatus.HEALED):
                lines.append(
                    f"        step {s.index + 1} [{s.status.value}] {s.intent}: {s.error or 'used fallback locator'}"
                )
        for c in result.cleanup:
            if c.status is StepStatus.FAILED:
                lines.append(f"        cleanup {c.index + 1} [failed] {c.intent}: {c.error}")
        if result.cleanup:
            lines.append(f"        cleanup: {result.cleanup_status}")
        videos: list[str] = list(getattr(driver, "videos", []))
        if args.video == "on-failure" and result.status is not StepStatus.FAILED:
            for v in videos:
                Path(v).unlink(missing_ok=True)
            videos = []
        record = build_record(
            result, run_dir=run_dir, test_file=spec_file, video_mode=args.video, videos=videos, executed_by=args.tester
        )
        write_record(record, run_dir)
        lines.append(f"        evidence: {run_dir}")
        doc: Path | None = None
        if args.evidence_doc:
            doc = write_evidence_document(record, run_dir, run_dir / f"{t.id}_{run_id}_evidence.docx")
            lines.append(f"        document: {doc}")
        emit(
            {
                "type": "test_saved",
                "test_id": t.id,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "document": str(doc) if doc else None,
                "status": result.status.value,
            }
        )
        with print_lock:  # the lines of one test stay together even when several finish at once
            print("\n".join(lines))
        return result, record, run_dir, doc

    pairs = list(zip(tests, files, strict=True))
    done: dict[int, tuple[RunResult, dict[str, Any], Path, Path | None]] = {}
    failure: list[str] = []

    def guarded(n: int) -> None:
        if abort.is_set():
            return
        try:
            done[n] = run_one(*pairs[n])
        except (UnsafeEnvironmentError, MissingCredentialsError) as e:
            abort.set()
            failure.append(str(e))

    workers = _workers(args)
    alone = [n for n, (t, _) in enumerate(pairs) if "serial" in t.tags]  # tests that must not share the pod
    together = [n for n in range(len(pairs)) if n not in alone]
    if workers == 1 or len(together) < 2:
        for n in range(len(pairs)):
            guarded(n)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="qm-test") as pool:
            list(pool.map(guarded, together))
        for n in alone:
            guarded(n)
    if failure:
        print(f"error: {failure[0]}", file=sys.stderr)
        return 2
    results = [done[n][0] for n in sorted(done)]
    suite_runs = [(done[n][1], done[n][2], done[n][3]) for n in sorted(done)]

    suite_id = new_run_id()
    suite = build_suite_record(
        suite_runs,
        suite_id=suite_id,
        evidence_root=evidence_root,
        target=str(target),
        started_at=suite_started,
        finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    suite_dir = suite_folder(evidence_root, suite_id)
    write_suite_record(suite, suite_dir)
    print(f"\nSuite record: {suite_dir / 'suite.json'}")
    summary: Path | None = None
    if args.evidence_doc:
        summary = write_suite_document(suite, evidence_root, suite_dir / f"suite_{suite_id}_summary.docx")
        print(f"Summary document: {summary}")
    emit(
        {
            "type": "suite_end",
            "at": suite["finished_at"],
            "status": suite["status"],
            "suite_dir": str(suite_dir),
            "summary": str(summary) if summary else None,
        }
    )

    if args.report:
        Path(args.report).write_text(
            json.dumps([r.model_dump(mode="json") for r in results], indent=2), encoding="utf-8"
        )
    failed = sum(r.status is StepStatus.FAILED for r in results)
    print(f"{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


def _workers(args: argparse.Namespace) -> int:
    return max(1, min(int(getattr(args, "parallel", 1) or 1), MAX_PARALLEL))


def _event_writer(path: str | None) -> Callable[[dict[str, Any]], None]:
    """Progress events as JSON lines (for the web service); a no-op when no file is given."""
    if not path:
        return lambda _event: None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    lock = threading.Lock()  # tests running at the same time write here together

    def write(event: dict[str, Any]) -> None:
        with lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    return write


def _document(args: argparse.Namespace) -> int:
    """Rebuild the evidence document from a saved run folder, or the summary from a suite folder."""
    run_dir = Path(args.run_dir)
    if (run_dir / "suite.json").is_file():
        suite = json.loads((run_dir / "suite.json").read_text(encoding="utf-8"))
        # Suite folders live at <evidence root>/_suites/<suite id>/.
        out = Path(args.out) if args.out else run_dir / f"suite_{suite['suite_id']}_summary.docx"
        print(f"Wrote {write_suite_document(suite, run_dir.parent.parent, out)}")
        return 0
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else run_dir / f"{record['test_id']}_{record['run_id']}_evidence.docx"
    print(f"Wrote {write_evidence_document(record, run_dir, out)}")
    return 0


def _signin(args: argparse.Namespace) -> int:
    """Sign in to the pod by hand (single sign-on, MFA) and print the session for `qm serve`."""
    from quartermaster.runner.playwright_driver import PlaywrightDriver
    from quartermaster.runner.session import PREFIX

    url = os.environ.get("QM_FUSION_URL")
    if not url:
        print("error: set QM_FUSION_URL to the non-prod pod URL", file=sys.stderr)
        return 2
    env = Environment(name="pod", url=url, kind=EnvironmentKind(args.kind))
    assert_safe_target(env, confirmed_hosts())
    driver = PlaywrightDriver(headless=False)
    print("Sign in to the pod in the browser that opened. It closes by itself when the pod's home page shows.")
    try:
        session = driver.sign_in_by_hand(env, timeout_s=args.timeout)
    finally:
        driver.close()
    print(PREFIX + session, flush=True)
    print("Signed in.")
    return 0


def _discover(args: argparse.Namespace) -> int:
    """Read the names of the pages in the pod's Navigator and write them to --out. Read only (runner/discovery.py)."""
    import tempfile
    from urllib.parse import urlsplit

    from quartermaster.runner.discovery import DiscoveryError

    url = os.environ.get("QM_FUSION_URL")
    if not url:
        print("error: set QM_FUSION_URL to the non-prod pod URL", file=sys.stderr)
        return 2
    env = Environment(name="pod", url=url, kind=EnvironmentKind(args.kind))
    try:
        assert_safe_target(env, confirmed_hosts())
        with tempfile.TemporaryDirectory(prefix="qm-discover-") as scratch:
            driver = driver_factory(argparse.Namespace(headed=False, video="off", no_highlight=True), Path(scratch))
            try:
                driver.open(env, "")
                pages = driver.navigator_pages()  # type: ignore[attr-defined]
            finally:
                driver.close()
    except (UnsafeEnvironmentError, MissingCredentialsError, DiscoveryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "pod_host": urlsplit(url).hostname,
                "pages": pages,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Found {len(pages)} page(s) in the Navigator.")
    return 0


def _export(args: argparse.Namespace) -> int:
    """Write tests as plain Playwright for Python files that need nothing from Quartermaster (export/)."""
    from quartermaster.export.playwright_py import ExportError, export

    target = Path(args.tests)
    try:
        if target.is_dir():
            tests: list[TestCase] = load_tests(target)
            files = files_of_tests(target)
        else:
            tests, files = [load_test(target)], [target]
    except SpecError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.only:
        wanted = [t for t in args.only.split(",") if t.strip()]
        unknown = sorted(set(wanted) - {t.id for t in tests})
        if unknown:
            print(f"error: no test with id {', '.join(unknown)} in {target}", file=sys.stderr)
            return 2
        pairs = [(t, f) for t, f in zip(tests, files, strict=True) if t.id in wanted]
        tests, files = [t for t, _ in pairs], [f for _, f in pairs]
    base = target if target.is_dir() else target.parent
    pairs2 = [
        (t, f.relative_to(base).as_posix() if base in f.parents else f.name) for t, f in zip(tests, files, strict=True)
    ]
    try:
        written = export(pairs2, Path(args.out), overwrite=args.force)
    except ExportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    count = sum(1 for p in written if p.name.startswith("test_"))
    print(f"Wrote {count} test(s) and the files they need to {args.out}")
    print(
        f"Run them: cd {args.out} && pip install -r requirements.txt && python -m playwright install chromium "
        "&& pytest -v"
    )
    return 0


def _eval_ai(args: argparse.Namespace) -> int:
    """Ask the AI the golden questions (ai/evals/suggest.py) and print how it did. Exit 1 when it falls short."""
    from quartermaster.ai.evals import suggest as evals
    from quartermaster.ai.providers import chat, config_from

    config = config_from(  # what a provider's preset knows (its address, its key variable) need not be typed
        {
            "ai_provider": args.ai_provider,
            "ai_model": args.ai_model,
            "ai_base_url": args.ai_base_url,
            "ai_key_env": args.ai_key_env,
            "ai_workspace": args.ai_workspace,
        }
    )
    problem = config.problem() if args.ai_provider else "choose an AI with --ai-provider (and --ai-model, --ai-key-env)"
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    try:
        cases = evals.load_cases(Path(args.cases)) if args.cases else evals.load_cases()
    except evals.CaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report = evals.run(
        cases, lambda system, prompt: chat(config, system, prompt, max_tokens=300, timeout=45), label=config.label
    )
    for r in report["cases"]:
        print(f"{r['outcome'].upper():<10} {r['id']}" + (f"  (picked {r['picked']!r})" if r.get("picked") else ""))
    c = report["counts"]
    print(
        f"\n{config.label}: {c['correct'] + c['right_none']} of {report['asked']} right "
        f"({report['score']:.0%}); wrong {c['wrong']} (reached the person: {report['wrong_reached']}), "
        f"missed {c['missed']}, unreadable {c['unreadable']}, errors {c['error']}"
    )
    print(report["advice"])
    return 0 if report["verdict"] in ("good", "usable") and report["score"] >= args.min_score else 1


def _nightly_summary(args: argparse.Namespace) -> int:
    """Print the Markdown summary of a nightly run from the report `qm run --report` wrote (see nightly.py)."""
    from quartermaster.nightly import read_report, summarize

    print(summarize(read_report(args.report), details=args.details, release=args.release or ""), end="")
    return 0


def _record(args: argparse.Namespace) -> int:
    from quartermaster.recorder.recorder import Recorder, events_to_test, to_yaml
    from quartermaster.runner.playwright_driver import PlaywrightDriver

    url = os.environ.get("QM_FUSION_URL")
    if not url:
        print("error: set QM_FUSION_URL to the non-prod pod URL", file=sys.stderr)
        return 2
    env = Environment(name=args.env_name, url=url, kind=EnvironmentKind(args.kind))
    assert_safe_target(env, confirmed_hosts())

    driver = PlaywrightDriver(headless=False, evidence_dir=args.evidence)
    recorder = Recorder(feed=Path(args.events) if args.events else None, test_id=args.id)
    run_id = new_run_id()
    if args.guide:  # a manual scenario done by hand: Pass or Fail per step, with a picture each
        from quartermaster.recorder.guided import Guide

        recorder.guide = Guide.load(Path(args.guide), run_folder(Path(args.evidence), args.id, run_id))
        recorder.write_feed()
    driver.open(env, args.persona)  # sign-in is done for you and never recorded
    if args.prepare:  # an AI follows the written steps instead of a person
        return _prepare(args, driver, recorder, env, run_id)
    try:
        recorder.attach(driver.page)
        print("Recording. Do the business flow in the browser window.")
        print("Use 'Add check' in the page toolbar to record what must be true, e.g. a value on screen.")
        print("Finish with 'Stop recording' in the browser, or press Enter here.")
        print("Commands here: pause, resume, check, undo, note <text>, mask (then Enter).")
        _wait_for_stop(recorder, driver.page)
    finally:
        driver.close()

    if recorder.guide is not None:
        return _finish_by_hand(args, recorder, env, run_id)
    test = events_to_test(
        recorder.events,
        test_id=args.id,
        title=args.title,
        module=args.module,
        product=args.product,
        persona=args.persona,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_yaml(test), encoding="utf-8")
    load_test(out)  # the saved file must pass the same validation as hand-written specs
    print(f"Saved {len(test.steps)} step(s) to {out}. Replay with: qm run {out}")
    secrets = [v[6:-1] for v in test.data.values() if v.startswith("${env:")]
    if secrets:
        print("Masked values were not saved. Before running it, set: " + ", ".join(secrets))
    return 0


def _suggester(args: argparse.Namespace) -> Any:
    """What suggests a fix for a step whose item is not found: names that look like the old one, and the
    AI chosen in Settings when `--ai-suggest` is given and the AI is ready."""
    from quartermaster.ai.providers import AIConfig, chat
    from quartermaster.runner.suggest import make_healer

    ask = None
    if args.ai_suggest and args.ai_provider:
        config = AIConfig(
            args.ai_provider, args.ai_model, args.ai_base_url.rstrip("/"), args.ai_key_env, args.ai_workspace
        )
        if config.problem() is None:
            ask = lambda system, prompt: chat(config, system, prompt, max_tokens=300, timeout=30)  # noqa: E731
    return make_healer(ask)


def _prepare(args: argparse.Namespace, driver: Any, recorder: Any, env: Environment, run_id: str) -> int:
    """Let the AI chosen in Settings do the scenario's steps; save the result as a draft to review."""
    from quartermaster.ai.providers import AIConfig, chat
    from quartermaster.recorder.autopilot import Autopilot

    if recorder.guide is None:
        print("error: --prepare needs --guide (the scenario to prepare)", file=sys.stderr)
        driver.close()
        return 2
    config = AIConfig(args.ai_provider, args.ai_model, args.ai_base_url.rstrip("/"), args.ai_key_env, args.ai_workspace)
    problem = config.problem()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        driver.close()
        return 2
    lines: queue.Queue[str] = queue.Queue()

    def read_lines() -> None:  # "stop" from the web page ends the preparation
        for line in sys.stdin:
            lines.put(line.strip().lower())

    threading.Thread(target=read_lines, daemon=True).start()

    def should_stop() -> bool:
        while not lines.empty():
            if lines.get() in ("", "stop"):
                return True
        return False

    pilot = Autopilot(
        driver.page,
        recorder.guide,
        recorder,
        ask=lambda system, prompt: chat(config, system, prompt),
        settle=getattr(driver, "_settle", lambda: None),
        should_stop=should_stop,
        navigate=(lambda path: driver.navigate(path, timeout_ms=10_000)) if hasattr(driver, "navigate") else None,
        wait_process=(lambda: driver.wait_job("last", 1800)) if hasattr(driver, "wait_job") else None,
    )
    recorder.message = f"Preparing with {config.label}…"
    recorder.write_feed()
    try:
        finished = pilot.run()
    finally:
        driver.close()
    if not finished:
        print(f"The AI stopped: {pilot.reason}")
    args.tester = f"AI ({config.label})"
    return _finish_by_hand(args, recorder, env, run_id, mode="ai", save_test=finished)


def _finish_by_hand(
    args: argparse.Namespace, recorder: Any, env: Environment, run_id: str, mode: str = "manual", save_test: bool = True
) -> int:
    """Save a manual scenario done by hand: the evidence of what the tester marked (run record, Word
    document, suite record and summary, as for `qm run`), and the clicks as a test that can play by
    itself next time."""
    from quartermaster.recorder.recorder import events_to_test, to_yaml

    guide = recorder.guide
    if not guide.marked:
        print("error: no step was marked Pass or Fail, so nothing was saved", file=sys.stderr)
        return 2
    out: Path | None = Path(args.out)
    if not save_test:
        print("The AI did not finish every step, so no automatic version was saved.")
        out = None
    try:
        if out is None:
            raise ValueError("not saved")
        test = events_to_test(
            recorder.events,
            test_id=args.id,
            title=args.title,
            module=args.module,
            product=args.product,
            persona=args.persona,
            process=args.process or "",
            written_steps=[{"action": s["action"], "expected": s["expected"]} for s in guide.steps],
        )
    except ValueError:
        if out is not None:
            print("No clicks were recorded, so the automatic version was not saved.")
        out = None
    else:
        assert out is not None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_yaml(test), encoding="utf-8")
        load_test(out)
        print(f"Saved {len(test.steps)} step(s) to {out}. Replay with: qm run {out}")

    evidence_root = Path(args.evidence)
    run_dir = guide.run_dir
    result = guide.result(
        test_id=args.id,
        title=args.title,
        environment=env.name,
        environment_url=env.url,
        release=args.release,
        run_id=run_id,
    )
    record = build_record(result, run_dir=run_dir, test_file=out, video_mode="off", videos=[], executed_by=args.tester)
    record["mode"] = mode  # "manual": done by a person, "ai": prepared by an AI; the document says which
    write_record(record, run_dir)
    doc = write_evidence_document(record, run_dir, run_dir / f"{args.id}_{run_id}_evidence.docx")
    suite_id = new_run_id()
    suite = build_suite_record(
        [(record, run_dir, doc)],
        suite_id=suite_id,
        evidence_root=evidence_root,
        target=str(out or args.out),
        started_at=result.started_at or "",
        finished_at=result.finished_at or "",
    )
    suite_dir = suite_folder(evidence_root, suite_id)
    write_suite_record(suite, suite_dir)
    summary = write_suite_document(suite, evidence_root, suite_dir / f"suite_{suite_id}_summary.docx")
    print(f"Evidence document: {doc}")
    print(f"Manual run: {suite_dir}")
    print(f"Summary document: {summary}")
    print(f"Result: {result.status.value}")
    return 0


def _wait_for_stop(recorder: Any, page: Any) -> None:
    """Run the recorder's commands until it stops: Stop recording in the browser, an empty line or
    "stop" here, or the window closing.

    Playwright's sync API may only be used from this thread, so commands are read in a helper
    thread and carried out here, between short waits that keep the browser responsive.
    """
    lines: queue.Queue[str] = queue.Queue()

    def read_lines() -> None:
        while True:
            line = sys.stdin.readline()
            lines.put(line)
            if not line.strip() or line.strip().lower() in ("stop", "finish"):
                return  # an empty line (Enter) or the end of input finishes the recording

    threading.Thread(target=read_lines, daemon=True).start()
    while not recorder.stopped:
        try:
            page.wait_for_timeout(200)  # also delivers the page's events to the recorder
        except Exception:  # the browser window was closed
            return
        while not lines.empty():
            recorder.command(lines.get())


DEFAULT_TESTS = "my_tests"


def _tests_folder(name: str) -> Path | None:
    """The folder `qm serve` keeps tests in. The default one is created on first start with copies of
    the example tests, so what you record, do by hand or prepare never lands among the examples."""
    tests = Path(name)
    examples = Path("examples") / "tests"
    if not tests.is_dir() and name == DEFAULT_TESTS:
        import shutil

        if examples.is_dir():
            shutil.copytree(examples, tests)
        else:
            tests.mkdir(parents=True)
        print(f"Created {tests}: your tests are kept here (it starts with copies of the example tests).")
    if not tests.is_dir():
        print(f"error: no tests folder at {tests}", file=sys.stderr)
        return None
    if examples.is_dir() and tests.resolve() == examples.resolve():
        print(
            "Note: tests you record, do by hand or prepare are saved in this folder, next to the examples. "
            f"Start with `qm serve` (folder {DEFAULT_TESTS}) to keep your own tests apart."
        )
    return tests


def _answers(address: str) -> bool:
    """A Quartermaster answers at this address."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{address}/api/status", timeout=3) as res:  # noqa: S310 - this computer
            return str(res.headers.get("Server", "")).startswith("Quartermaster")
    except OSError:
        return False


def _ps(path: Path) -> str:
    """A path inside single quotes in PowerShell."""
    return str(path).replace("'", "''")


def _shortcut(args: argparse.Namespace) -> int:
    """Put a Quartermaster icon on the desktop that runs Start Quartermaster.bat (Windows)."""
    start = Path(__file__).resolve().parents[2] / "Start Quartermaster.bat"
    if sys.platform != "win32":
        print("Desktop icons are made on Windows only. Here, start with ./start-quartermaster.sh")
        return 0
    if not start.is_file():
        print(f"error: {start} is missing", file=sys.stderr)
        return 2
    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Quartermaster.lnk')); "
        f"$link.TargetPath = '{_ps(start)}'; $link.WorkingDirectory = '{_ps(start.parent)}'; "
        "$link.Description = 'Start Quartermaster'; $link.Save()"
    )
    import subprocess

    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False)
    if done.returncode:
        print("The desktop icon could not be made. Start with 'Start Quartermaster' in the Quartermaster folder.")
        return 0
    print("Added a Quartermaster icon to the desktop. Double-click it to start Quartermaster.")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import webbrowser

    from quartermaster.service.api import make_server, port_of
    from quartermaster.service.hub import Hub

    address = f"http://127.0.0.1:{args.port}"
    if _answers(address):  # started a second time, e.g. the desktop icon double-clicked again
        print(f"Quartermaster is already running at {address}. Opening it.")
        if not args.no_browser:
            webbrowser.open(address)
        return 0
    # a restore chosen on the Settings page waits for this start: nothing is open yet, so it can be applied
    from quartermaster.service import backup

    waiting = backup.apply_pending(backup.Folders(Path(args.tests), Path(args.evidence), Path(args.data)))
    if waiting:
        print(waiting)
    tests = _tests_folder(args.tests)
    if tests is None:
        return 2
    examples = Path("examples") / "tests"
    # one workspace per client (Settings, Clients and environments); the first client keeps these folders
    app = Hub(
        tests_root=tests,
        evidence_root=Path(args.evidence),
        data_dir=Path(args.data),
        seed_tests=examples if examples.is_dir() else None,
        releases_root=Path(args.releases),
    )
    try:
        server = make_server(app, port=args.port)
    except OSError:
        print(f"error: port {args.port} is used by another program; start with --port 8766", file=sys.stderr)
        app.stop()
        return 2
    address = f"http://127.0.0.1:{port_of(server)}"
    app.address = address
    app.start()
    print(f"Quartermaster is running at {address}  (tests: {tests}, evidence: {args.evidence})")
    print("Keep this window open while you use it. Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        server.server_close()
        app.stop()
    return 0


def _backup(args: argparse.Namespace) -> int:
    from quartermaster.service import backup

    folders = backup.Folders(Path(args.tests), Path(args.evidence), Path(args.data))
    out = Path(args.file)
    try:
        content = backup.create(folders, include_evidence=args.with_evidence)
    except backup.BackupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    print(f"Backup saved: {out} ({len(content) // 1024} KB). Test users' passwords are not in it.")
    return 0


def _restore(args: argparse.Namespace) -> int:
    from quartermaster.service import backup

    address = f"http://127.0.0.1:{args.port}"
    if _answers(address):
        print("error: Quartermaster is running. Close it first (or restore from the Settings page).", file=sys.stderr)
        return 2
    folders = backup.Folders(Path(args.tests), Path(args.evidence), Path(args.data))
    try:
        info = backup.restore(folders, Path(args.file).read_bytes())
    except (OSError, backup.BackupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(
        f"Restored the backup made {info.get('created_at')}. "
        f"The copy of what was here before is in {folders.data / backup.BACKUPS}."
    )
    return 0


def _audit(args: argparse.Namespace) -> int:
    """Check or export the audit log from a terminal: a scheduled copy, or checking an export an auditor was sent."""
    import zipfile

    from quartermaster.service import auditexport
    from quartermaster.service.audit import AuditLog, check_export, computer_user

    log = AuditLog(Path(args.data) / "audit.jsonl")
    if args.action == "export":
        try:
            data, name, manifest = auditexport.build(
                log,
                fmt=args.format,
                exported_by=computer_user(),
                since=args.since or "",
                until=args.until or "",
                who=args.who or "",
                text=args.text or "",
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else Path(name)
        out.write_bytes(data)
        print(f"Wrote {out}: {manifest['entries']} lines, chain {manifest['log']['chain']}.")
        return 0
    if args.file:  # an exported file: each line must be what its hash says
        path = Path(args.file)
        raw = path.read_bytes()
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                member = next((n for n in z.namelist() if n.endswith(".jsonl")), None)
                if member is None:
                    print("error: this zip has no .jsonl file (only the .jsonl export can be checked)", file=sys.stderr)
                    return 2
                raw = z.read(member)
        got = check_export(raw)
        if not got["ok"]:
            print(f"BROKEN at line {got['problem']['line']}: {got['problem']['why']}")
            return 1
        gaps = (
            f", {got['gaps']} gap(s) where lines are missing between two (normal for a filtered export)"
            if got["gaps"]
            else ""
        )
        print(f"OK: {got['entries']} lines, each is what its hash says{gaps}. Newest hash: {got['head']}")
        return 0
    got = log.verify()
    if not got["ok"]:
        print(f"BROKEN at line {got['problem']['line']}: {got['problem']['why']}")
        return 1
    note = f" ({got['legacy']} older lines from before hashing are covered by the first hash)" if got["legacy"] else ""
    print(f"OK: {got['entries']} lines, the chain is whole{note}. Newest hash: {got['head']}")
    if args.against:
        import json

        manifest = (
            json.loads(Path(args.against).read_text(encoding="utf-8")) if not zipfile.is_zipfile(args.against) else {}
        )
        if not manifest:
            with zipfile.ZipFile(args.against) as z:
                manifest = json.loads(z.read("manifest.json"))
        kept = manifest.get("log") or {}
        if not kept.get("head"):
            print("That manifest has no newest hash to check against (the log was empty or not hashed then).")
            return 0
        why = log.holds(str(kept["head"]), int(kept.get("entries") or 0))
        if why:
            print(f"NOT CONSISTENT with the manifest: {why}.")
            return 1
        print(
            f"Consistent with the manifest of {manifest.get('exported_at', 'that export')}: "
            "nothing before it was cut off."
        )
    return 0


def _packs(args: argparse.Namespace) -> int:
    """The test library: list the packs that come with Quartermaster, or copy some into a tests folder."""
    from quartermaster import packs as library

    root = Path(args.tests)
    try:
        if args.action == "list":
            for pack in library.list_packs():
                got = library.pack_status(root, pack) if root.is_dir() else None
                state = (
                    "not installed"
                    if got is None
                    else f"installed v{got['version']}" + (", update available" if got["update_available"] else "")
                )
                checked = (
                    f", last checked {pack.checked['date']}: {pack.checked['passed']} of {pack.checked['of']} passed"
                    if pack.checked
                    else ", not checked on a pod"
                )
                print(f"{pack.id:<24} {len(pack.tests):>3} tests  {state}{checked}")
            return 0
        names = [p.id for p in library.list_packs()] if args.all else args.names
        if not names:
            print("error: name a pack (qm packs list shows them) or use --all", file=sys.stderr)
            return 2
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            done = library.install(root, library.catalog.get_pack(name))
            said = ", ".join(f"{len(v)} {k}" for k, v in done.items() if isinstance(v, list) and v) or "nothing to do"
            print(f"{name}: {said}. Suite: {done['suite']}")
            for kept in done["kept"]:
                print(f"    kept your version of {kept}")
        return 0
    except library.catalog.PackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _users(args: argparse.Namespace) -> int:
    """Manage sign-in from a terminal: for getting back in when nobody can sign in. Whoever has the computer's
    files could always do this, so it adds no new way in."""
    from quartermaster.service.auth import ROLES, Auth, AuthError

    path = Path(args.data) / "users.db"
    if args.action == "list" and not path.is_file():
        print("Sign-in is off: no users yet.")
        return 0
    auth = Auth(path)
    try:
        if args.action == "list":
            print(f"Sign-in is {'ON' if auth.enabled else 'off'}.")
            for u in auth.users():
                flags = ", ".join(
                    [
                        *(u["roles"] or ["viewer"]),
                        *(["disabled"] if not u["active"] else []),
                        *(["must choose a new password"] if u["must_change"] else []),
                    ]
                )
                print(f"  {u['username']:<20} {u['full_name']:<28} {flags}")
            return 0
        if args.action == "disable-signin":
            auth.disable_from_terminal()
            print("Sign-in is off. Restart Quartermaster, then turn it on again in Settings, Users & sign-in.")
            return 0
        if not args.username:
            print(f"error: give the user name: qm users {args.action} <user name>", file=sys.stderr)
            return 2
        if args.action == "passwd":
            password = auth.set_password_from_terminal(args.username)
            print(f"New temporary password for {args.username}: {password}")
            print("They must choose their own at the next sign-in. Restart Quartermaster to sign out anyone who is in.")
            return 0
        roles = [r.strip() for r in args.roles.split(",") if r.strip()]
        bad = [r for r in roles if r not in ROLES]
        if bad:
            print(f"error: unknown role {bad[0]}. Roles: {', '.join(ROLES)}", file=sys.stderr)
            return 2
        password = auth.add_from_terminal(args.username, args.name or args.username, roles)
        print(f"Added {args.username}. Temporary password: {password}")
        print("They must choose their own at the first sign-in. If sign-in is off, turn it on in Settings.")
        return 0
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qm", description="Quartermaster: Oracle Fusion release regression")
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate YAML test specs")
    v.add_argument("tests", help="directory of test specs")
    v.set_defaults(func=_validate)

    pl = sub.add_parser("plan", help="rank tests by release impact")
    pl.add_argument("--release", required=True, help="release feature file (.json/.yaml)")
    pl.add_argument("--tests", required=True, help="directory of test specs")
    pl.add_argument("--budget", type=float, default=None, help="time budget in minutes")
    pl.add_argument("--min-risk", type=float, default=0.05)
    pl.add_argument("--opt-in", action="append", help="feature id enabled in this tenant (repeatable)")
    pl.add_argument("--explain", action="store_true", help="show why each test was selected")
    pl.set_defaults(func=_plan)

    rn = sub.add_parser("run", help="run specs against the pod in QM_FUSION_URL")
    rn.add_argument("tests", help="spec file or directory")
    rn.add_argument("--kind", default=os.environ.get("QM_FUSION_KIND", "DEV"), choices=["DEV", "TEST", "STAGE"])
    rn.add_argument("--env-name", default=os.environ.get("QM_ENV_NAME") or "fusion")
    rn.add_argument("--headed", action="store_true", help="show the browser window")
    rn.add_argument(
        "--no-highlight", action="store_true", help="no red marks on what is clicked or filled (live or in screenshots)"
    )
    rn.add_argument("--evidence", default="evidence", help="root folder for run evidence (one folder per run)")
    rn.add_argument(
        "--screenshots",
        default="on-failure",
        choices=[m.value for m in ScreenshotMode],
        help="when to take screenshots (default: on-failure)",
    )
    rn.add_argument(
        "--video",
        default="off",
        choices=["off", "on-failure", "always"],
        help="record a video of the run into the run folder; never put in the document (default: off)",
    )
    rn.add_argument(
        "--evidence-doc",
        action="store_true",
        help="write a Word evidence document for each test, plus a summary document for the whole run",
    )
    rn.add_argument("--release", help="Oracle release on the pod, e.g. 26C (shown in the evidence)")
    rn.add_argument("--tester", help="name shown as 'Executed by' (default: your login name)")
    rn.add_argument("--report", help="write JSON results for all runs to this file")
    rn.add_argument("--only", help="comma-separated test ids: run just these from the folder")
    rn.add_argument(
        "--suite", help="the name of a saved suite (a file in the _suites folder): run the tests it has now"
    )
    rn.add_argument("--events", help="append progress events to this file as JSON lines (used by the web service)")
    rn.add_argument(
        "--retries",
        type=int,
        default=1,
        choices=[0, 1, 2, 3],
        help="when a step fails, try it again this many times (safe steps only) before giving up",
    )
    rn.add_argument(
        "--parallel",
        type=int,
        default=1,
        choices=range(1, MAX_PARALLEL + 1),
        metavar=f"1-{MAX_PARALLEL}",
        help="run this many tests at the same time, each in its own browser (tests tagged 'serial' run alone, last)",
    )
    rn.add_argument(
        "--ai-suggest",
        action="store_true",
        help="when a step cannot find its item, let the AI (see --ai-provider) suggest what it became",
    )
    rn.add_argument("--ai-provider", default="", help="AI provider id, used by --ai-suggest")
    rn.add_argument("--ai-model", default="")
    rn.add_argument("--ai-base-url", default="")
    rn.add_argument("--ai-key-env", default="", help="NAME of the environment variable with the AI key")
    rn.add_argument("--ai-workspace", default="", help="Anthropic only: workspace ID, for a key not tied to one")
    rn.set_defaults(func=_run)

    dc = sub.add_parser("document", help="rebuild a Word document from a saved run or suite folder")
    dc.add_argument("run_dir", help="run folder (run.json) or suite folder (suite.json)")
    dc.add_argument("--out", help="output .docx path (default: inside the run folder)")
    dc.set_defaults(func=_document)

    rc = sub.add_parser("record", help="record a test by clicking through the pod in QM_FUSION_URL")
    rc.add_argument("out", help="YAML file to write")
    rc.add_argument("--id", required=True, help="test id, e.g. hcm.view-worker")
    rc.add_argument("--title", required=True)
    rc.add_argument("--module", required=True, help="e.g. HCM")
    rc.add_argument("--product", required=True, help="e.g. Global Human Resources")
    rc.add_argument("--persona", default="")
    rc.add_argument("--kind", default=os.environ.get("QM_FUSION_KIND", "DEV"), choices=["DEV", "TEST", "STAGE"])
    rc.add_argument("--env-name", default=os.environ.get("QM_ENV_NAME") or "fusion")
    rc.add_argument("--evidence", default="evidence")
    rc.add_argument("--events", help="keep the steps recorded so far in this file (used by the web UI)")
    rc.add_argument("--guide", help="a manual scenario (JSON) to do by hand, marking each step Pass or Fail")
    rc.add_argument("--process", help="what the test is part of, e.g. Manual scenario ESS-001")
    rc.add_argument("--release", help="Oracle release on the pod, e.g. 26C (shown in the evidence)")
    rc.add_argument("--tester", help="name shown as 'Run by' in the evidence")
    rc.add_argument("--prepare", action="store_true", help="with --guide: an AI does the steps (see Settings)")
    rc.add_argument("--ai-provider", default="", help="AI provider id, e.g. openai, anthropic, openrouter")
    rc.add_argument("--ai-model", default="")
    rc.add_argument("--ai-base-url", default="")
    rc.add_argument("--ai-key-env", default="", help="NAME of the environment variable with the AI key")
    rc.add_argument("--ai-workspace", default="", help="Anthropic only: workspace ID, for a key not tied to one")
    rc.set_defaults(func=_record)

    sc = sub.add_parser("shortcut", help="put a Quartermaster icon on the desktop (Windows)")
    sc.set_defaults(func=_shortcut)

    si = sub.add_parser("signin", help="sign in to the pod by hand (single sign-on, MFA); used by qm serve")
    si.add_argument("--kind", default=os.environ.get("QM_FUSION_KIND", "DEV"), choices=["DEV", "TEST", "STAGE"])
    si.add_argument("--timeout", type=float, default=600, help="seconds to wait for the sign-in")
    si.set_defaults(func=_signin)

    dv = sub.add_parser("discover", help="read the names of the pages in the pod's Navigator (read only)")
    dv.add_argument("--out", required=True, help="JSON file to write the page names to")
    dv.add_argument("--kind", default=os.environ.get("QM_FUSION_KIND", "DEV"), choices=["DEV", "TEST", "STAGE"])
    dv.set_defaults(func=_discover)

    pk = sub.add_parser("packs", help="the test library: list ready-made packs of tests, or install some into a folder")
    pk.add_argument("action", choices=["list", "install"])
    pk.add_argument("names", nargs="*", help="install: the packs (qm packs list shows them)")
    pk.add_argument("--all", action="store_true", help="install: every pack")
    pk.add_argument("--tests", default="tests", help="the tests folder (default: tests)")
    pk.set_defaults(func=_packs)

    su = sub.add_parser("suites", help="list the saved suites in a tests folder and how many tests each has now")
    su.add_argument("tests", nargs="?", default="tests", help="the tests folder (default: tests)")
    su.add_argument("--list", action="store_true", help="also list the test ids of each suite")
    su.set_defaults(func=_suites)

    ex = sub.add_parser(
        "export", help="write tests as plain Playwright (Python, pytest) files, no Quartermaster needed"
    )
    ex.add_argument("tests", help="a test file, or a folder of tests")
    ex.add_argument("--out", required=True, help="folder to write to (must be empty, or new)")
    ex.add_argument("--only", help="comma-separated test ids: export just these")
    ex.add_argument(
        "--force", action="store_true", help="replace files already in --out (your own edits there are lost)"
    )
    ex.set_defaults(func=_export)

    ev = sub.add_parser(
        "eval-ai", help="check how good the AI is at suggesting a missing item (sends only made-up screens)"
    )
    ev.add_argument("--ai-provider", default="", help="AI provider id, e.g. openai, anthropic, openrouter")
    ev.add_argument("--ai-model", default="")
    ev.add_argument("--ai-base-url", default="")
    ev.add_argument("--ai-key-env", default="", help="NAME of the environment variable with the AI key")
    ev.add_argument("--ai-workspace", default="", help="Anthropic only: workspace ID")
    ev.add_argument(
        "--cases", default="", help="your own golden cases file (default: the ones that come with Quartermaster)"
    )
    ev.add_argument("--min-score", type=float, default=0.0, help="exit 1 when fewer than this share (0 to 1) is right")
    ev.set_defaults(func=_eval_ai)

    ns = sub.add_parser("nightly-summary", help="the summary page of a nightly run (used by GitHub Actions)")
    ns.add_argument("report", help="the JSON file written by qm run --report")
    ns.add_argument(
        "--details", action="store_true", help="include what the pod showed (errors); off keeps it out of logs"
    )
    ns.add_argument("--release", default="", help="Oracle release, shown in the title")
    ns.set_defaults(func=_nightly_summary)

    sv = sub.add_parser("serve", help="start the web UI on this computer")
    sv.add_argument(
        "--tests", default=DEFAULT_TESTS, help=f"folder holding your tests ({DEFAULT_TESTS}, created on first start)"
    )
    sv.add_argument("--evidence", default="evidence", help="root folder for run evidence")
    sv.add_argument("--releases", default="examples/releases", help="folder holding release feature lists")
    sv.add_argument("--data", default=".qm", help="folder for the run history and run logs")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-browser", action="store_true", help="do not open the web browser")
    sv.set_defaults(func=_serve)

    au = sub.add_parser("audit", help="check the audit log's chain of hashes, or export it (a zip with a manifest)")
    au.add_argument("action", choices=["verify", "export"])
    au.add_argument("file", nargs="?", help="verify: an exported .jsonl (or the zip) instead of the live log")
    au.add_argument("--data", default=".qm", help="the data folder")
    au.add_argument(
        "--against", help="verify: a manifest.json (or the zip) from an earlier export to hold the log against"
    )
    au.add_argument("--out", help="export: where to write the zip (default: a dated name here)")
    au.add_argument(
        "--format", default="jsonl", choices=["csv", "jsonl"], help="export: csv for spreadsheets, jsonl to verify"
    )
    au.add_argument("--from", dest="since", help="export: first day, like 2026-10-01")
    au.add_argument("--to", dest="until", help="export: last day")
    au.add_argument("--who", help="export: only this person (any part of the name)")
    au.add_argument("--text", help="export: only lines with this text")
    au.set_defaults(func=_audit)

    us = sub.add_parser("users", help="sign-in: list users, add one, reset a password, or turn sign-in off")
    us.add_argument("action", choices=["list", "add", "passwd", "disable-signin"])
    us.add_argument("username", nargs="?", help="the user name (add, passwd)")
    us.add_argument("--name", default="", help="add: the person's full name")
    us.add_argument("--roles", default="tester", help="add: comma separated: admin, tester, approver (none: viewer)")
    us.add_argument("--data", default=".qm", help="the data folder")
    us.set_defaults(func=_users)

    for name, func, text in (
        ("backup", _backup, "save your tests, run history, clients and settings in one zip (no passwords)"),
        ("restore", _restore, "put a backup zip back (Quartermaster must not be running)"),
    ):
        bp = sub.add_parser(name, help=text)
        bp.add_argument("file", help="the backup zip" + (" to make" if name == "backup" else " to restore"))
        bp.add_argument("--tests", default=DEFAULT_TESTS, help="your tests folder")
        bp.add_argument("--evidence", default="evidence", help="the evidence folder")
        bp.add_argument("--data", default=".qm", help="the data folder")
        if name == "backup":
            bp.add_argument("--with-evidence", action="store_true", help="also keep screenshots, videos and documents")
        else:
            bp.add_argument("--port", type=int, default=8765)
        bp.set_defaults(func=func)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (SpecError, UnsafeEnvironmentError, MissingCredentialsError, ValueError, TimeoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
