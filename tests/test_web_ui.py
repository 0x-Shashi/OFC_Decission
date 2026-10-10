"""Quartermaster's own web pages, driven in a real browser: every page opens without errors, and the main
things a person does on them work (read the lists, accept a change, import a feature list, turn on sign-in,
sign in with Google).

The sample data is built by `ui_site.py`. Skipped when Playwright is not installed."""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
import oidc_stub  # noqa: E402
from conftest import EXAMPLES  # noqa: E402
from playwright.sync_api import Browser, Page, expect, sync_playwright  # noqa: E402
from ui_site import CLIENT, SECRET, Site, build_site, serve  # noqa: E402

WAIT = 15_000  # ms to wait for a page to show something


def chromium() -> str | None:
    return os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    pw = sync_playwright().start()
    b = pw.chromium.launch(executable_path=chromium())
    yield b
    b.close()
    pw.stop()


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Site]:
    with serve(build_site(tmp_path_factory.mktemp("ui"))) as s:
        yield s


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """A fresh browser window. At the end it is an error if a page script failed or the console showed an error."""
    context = browser.new_context(viewport={"width": 1300, "height": 1000})
    p = context.new_page()
    p.set_default_timeout(WAIT)
    problems: list[str] = []
    p.on("pageerror", lambda e: problems.append(f"script error: {e}"))
    p.on(
        "console",
        lambda m: (
            problems.append(f"console: {m.text}")
            if m.type == "error" and "Failed to load resource" not in m.text
            else None
        ),
    )
    p.on("dialog", lambda d: d.accept())
    yield p
    context.close()
    assert problems == []


def see(page: Page, text: str | re.Pattern[str]) -> None:
    expect(page.locator("main")).to_contain_text(text, timeout=WAIT)


# ------------------------------------------------------------------ every page opens

PAGES = [
    ("#/", "Overview"),
    ("#/runs", "Runs"),
    ("#/tests", "Tests"),
    ("#/suites", "Suites"),
    ("#/library", "Shared steps"),
    ("#/data", "Test data"),
    ("#/impact", "Release impact"),
    ("#/attention", "Needs attention"),
    ("#/schedules", "Schedules"),
    ("#/record", "Record a test"),
    ("#/audit", "Audit log"),
    ("#/settings?tab=general", "Settings"),
    ("#/settings?tab=evidence", "Settings"),
    ("#/settings?tab=notifications", "Settings"),
    ("#/settings?tab=users", "Settings"),
]


@pytest.mark.parametrize(("route", "heading"), PAGES)
def test_every_page_opens_and_says_what_it_is(site: Site, page: Page, route: str, heading: str) -> None:
    page.goto(f"{site.url}/{route}")
    expect(page.locator("main h1").first).to_have_text(heading, timeout=WAIT)
    expect(page.locator("main")).not_to_contain_text("Something went wrong")


def test_the_menu_takes_you_to_each_page(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/")
    for label, heading in (
        ("Runs", "Runs"),
        ("Tests", "Tests"),
        ("Shared steps", "Shared steps"),
        ("Needs attention", "Needs attention"),
    ):
        page.locator("#nav").get_by_role("link", name=re.compile(label)).first.click()
        expect(page.locator("main h1").first).to_have_text(heading, timeout=WAIT)


# ------------------------------------------------------------------ the lists


def test_the_tests_page_lists_the_tests_and_a_test_shows_its_steps(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/tests")
    see(page, "A test with shared steps")
    see(page, "View a worker")
    page.goto(f"{site.url}/#/tests/hcm%2Fshared_user.yaml")
    see(page, "Open Locations")
    expect(page.locator("main")).to_contain_text("shared: open-locations")


def test_tests_can_be_exported_as_plain_playwright_files(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/tests")
    link = page.get_by_role("link", name="Export", exact=True)
    expect(link).to_be_visible(timeout=WAIT)
    with page.expect_download() as everything:
        link.click()
    name = everything.value.suggested_filename
    assert "-playwright-" in name and name.endswith(".zip")
    page.goto(f"{site.url}/#/tests/hcm%2Fshared_user.yaml")
    with page.expect_download() as one:
        page.get_by_role("link", name="Export", exact=True).click()
    assert one.value.suggested_filename == "hcm.shared-user-playwright.zip"
    with zipfile.ZipFile(one.value.path()) as z:
        assert {"test_hcm_shared_user.py", "fusion_runtime.py", "conftest.py"} <= set(z.namelist())
        assert "fusion.navigate('Workforce Structures > Locations')" in z.read("test_hcm_shared_user.py").decode()


def test_the_shared_steps_page_shows_the_group_and_who_uses_it(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/library")
    see(page, "Open the Locations page")
    see(page, "used by 1 test")
    see(page, "A test with shared steps")


def test_the_test_data_page_shows_sets_gaps_and_generated_values(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/data")
    see(page, "Names on the pods")
    see(page, "used by 1 test")
    see(page, "A test with test data")
    see(page, "Some pods have no value.")
    see(page, "special on ")
    see(page, "Values made fresh for every run")
    sample = page.locator("table[aria-label='Values made for A test with test data'] tbody td code").last
    expect(sample).to_have_text(re.compile(r"^REF-[A-Z0-9]{6}$"), timeout=WAIT)
    page.goto(f"{site.url}/#/tests/hcm%2Fuses_data.yaml")
    page.get_by_role("tab", name="Test data").click()
    see(page, "Data sets used:")
    see(page, "Different on some pods")
    see(page, "6 letters and digits, new each run after 'REF-'")


def test_a_test_with_setup_shows_it_and_its_run_says_the_pod_was_ready(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/tests/hcm%2Fwith_setup.yaml")
    see(page, "Checked or made on the pod first.")
    see(page, "The pod has a location")
    page.goto(f"{site.url}/#/runs")
    page.locator("main a[href^='#/runs/']").first.click()
    page.locator("main").get_by_text("Title of hcm.view-worker").first.click()
    see(page, "The pod was ready.")
    see(page, "The accounting period is open")


def test_suites_are_listed_made_in_the_page_and_offered_when_starting_a_run(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/suites")
    see(page, "Everything in HCM")
    see(page, "in the folder hcm")
    see(page, "tagged skip-me")
    page.get_by_role("button", name="New suite").click()
    drawer = page.locator(".drawer")
    drawer.get_by_label("Name").fill("HR tests")
    drawer.get_by_role("button", name="HR", exact=True).click()
    expect(drawer).to_contain_text("fit now", timeout=WAIT)  # the tests the rule finds are listed as it is chosen
    drawer.get_by_role("button", name="Save").click()
    see(page, "HR tests")
    see(page, "in the product HR")
    page.locator("main").get_by_role("button", name="Run", exact=True).last.click()
    run = page.locator(".drawer")
    expect(run).to_contain_text("A suite", timeout=WAIT)
    expect(run.get_by_label("Suite")).to_contain_text("HR tests")


def test_a_schedule_can_run_a_suite(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/schedules")
    page.get_by_role("button", name="New schedule").click()
    drawer = page.locator(".drawer")
    drawer.get_by_label("Name", exact=True).fill("Nightly HCM")
    drawer.get_by_label("What to test").select_option(label="Suite: Everything in HCM (6 tests now)")
    drawer.get_by_role("button", name="Save").click()
    see(page, "Nightly HCM")
    see(page, "Suite: Everything in HCM")


def test_the_audit_log_says_it_is_whole_and_exports_a_checkable_zip(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/audit")
    see(page, "The audit log is whole.")
    page.get_by_role("button", name="Export", exact=True).click()
    drawer = page.locator(".drawer")
    drawer.get_by_label("Format").select_option("jsonl")
    drawer.get_by_label("Word").fill("sample site")
    expect(drawer).to_contain_text("1 line will be in the export.", timeout=WAIT)
    with page.expect_download() as got:
        drawer.get_by_role("link", name="Download the zip").click()
    with zipfile.ZipFile(got.value.path()) as z:
        assert sorted(z.namelist()) == ["HOW_TO_VERIFY.txt", "audit.jsonl", "manifest.json"]
        manifest = json.loads(z.read("manifest.json"))
        assert (
            manifest["entries"] == 1
            and manifest["filters"] == {"text": "sample site"}
            and manifest["log"]["chain"] == "intact"
        )
        assert "Opened the sample site" in z.read("audit.jsonl").decode()


def test_company_single_sign_on_is_set_up_in_the_pages_and_a_person_signs_in_with_it(
    secure: Site, page: Page, browser: Browser
) -> None:
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{secure.url}/#/settings?tab=users")
    page.fill("#on-name", "Sai Ram")
    page.fill("#on-user", "sai")
    page.fill("#on-pw", "correct-horse-battery")
    page.fill("#on-again", "correct-horse-battery")
    page.get_by_role("button", name="Turn on sign-in").click()
    expect(page.locator("#nav")).to_contain_text("Sign out", timeout=WAIT)
    page.goto(f"{secure.url}/#/settings?tab=users")
    card = page.locator("section", has=page.get_by_text("Single sign-on (company)", exact=True))
    expect(card).to_be_visible(timeout=WAIT)
    page.fill("#sso-issuer", oidc_stub.ISSUER)
    page.fill("#sso-id", oidc_stub.CLIENT_ID)
    page.fill("#sso-secret", oidc_stub.SECRET)
    page.fill("#sso-label", "Acme login")
    card.get_by_role("button", name="Add a group").click()
    card.get_by_label("Group", exact=True).fill("qm-testers")  # the role beside it is Tester
    card.get_by_label("Who gets in").select_option("auto")
    card.get_by_label("Turn on single sign-on").check()
    card.get_by_role("button", name="Check the provider").click()
    expect(card).to_contain_text("The provider answers. 1 signing key found", timeout=WAIT)
    card.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("main")).to_contain_text("On: people can use Sign in with Acme login", timeout=WAIT)
    expect(page.locator("main")).not_to_contain_text(oidc_stub.SECRET)  # the secret is never shown again

    # Pat, in their own window, from the company's provider: made at the first sign-in, a tester through the group
    secure.idp.identity.update(email="pat@acme.com", name="Pat Tester", groups=["qm-testers"])
    pat_window = browser.new_context()
    pat = pat_window.new_page()
    pat.set_default_timeout(WAIT)
    pat.goto(f"{secure.url}/")
    expect(pat.locator("#login-sso")).to_have_text("Sign in with Acme login")
    pat.locator("#login-sso").click()
    expect(pat.locator("main h1").first).to_have_text("Overview", timeout=WAIT)
    me = pat.evaluate(
        "fetch('/api/auth/status').then(r => r.json()).then(s => s.user.username + ' ' + s.user.roles.join(','))"
    )
    assert me == "pat tester"
    pat_window.close()

    page.reload()
    expect(page.locator("main")).to_contain_text("2 people can sign in", timeout=WAIT)
    expect(page.locator("main")).to_contain_text("Pat Tester")
    expect(page.locator("main")).to_contain_text("Single sign-on")  # the badge on Pat's line


def test_the_test_library_installs_a_pack_and_offers_its_suite(secure: Site, page: Page) -> None:
    page.goto(f"{secure.url}/#/packs")
    card = page.locator("section", has=page.get_by_text("HCM services (read only)", exact=True))
    expect(card).to_contain_text("checked on a pod: 23 of 23 passed", timeout=WAIT)
    expect(page.locator("main")).to_contain_text("only ask the pod questions (GET)")
    card.get_by_role("button", name="Install", exact=True).click()
    expect(card).to_contain_text("Installed (version 1)", timeout=WAIT)
    expect(card).to_contain_text("Up to date.")
    page.goto(f"{secure.url}/#/tests")
    expect(page.locator("main")).to_contain_text("Workers: the service answers", timeout=WAIT)  # tests like any other
    page.goto(f"{secure.url}/#/packs")
    card = page.locator("section", has=page.get_by_text("HCM services (read only)", exact=True))
    card.get_by_role("button", name="Run these tests").click()
    expect(page.locator(".drawer").get_by_label("Suite")).to_have_value("library-hcm-services", timeout=WAIT)


def test_a_finished_run_shows_its_results(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/runs")
    page.locator("main a[href^='#/runs/']").first.click()
    see(page, "2 of 3 tests passed")
    see(page, "Title of hcm.view-worker")
    see(page, "Title of hcm.create-location")
    see(page, "Run again")


# ------------------------------------------------------------------ Needs attention


def test_needs_attention_shows_each_kind_and_dismissing_hides_only_that_one(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/attention")
    see(page, "Checks that did not match")
    see(page, "Cleanup that did not finish")
    see(page, "Oracle screen changes")
    see(page, "Records from this test may still be on the pod")
    see(page, "Redwood Shores")  # what the check expected
    cleanup = page.locator("article[aria-label^='Cleanup did not finish']")
    expect(cleanup).to_contain_text("The API answered HTTP 403.")
    cleanup.get_by_role("button", name="Dismiss").click()
    expect(page.locator("article[aria-label^='Cleanup did not finish']")).to_have_count(0, timeout=WAIT)
    expect(page.locator("article[aria-label^='Check did not match']")).to_have_count(1)
    # put it back for the other tests: a new run is the only way, so this test leaves it dismissed


def test_accepting_an_oracle_screen_change_updates_the_test_file(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/attention")
    card = page.locator("article[aria-label^='Oracle screen changed']")
    expect(card).to_have_count(1, timeout=WAIT)
    card.get_by_role("button", name="Accept update").click()
    expect(page.locator("article[aria-label^='Oracle screen changed']")).to_have_count(0, timeout=WAIT)
    text = (site.app.tests_root / "hcm" / "personal.yaml").read_text(encoding="utf-8")
    assert text.index("role:") < text.index("label:")  # the role is tried first now


# ------------------------------------------------------------------ Settings


def test_settings_remember_how_many_tests_run_at_once(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/settings?tab=evidence")
    group = page.get_by_role("radiogroup", name="Tests at the same time")
    group.get_by_role("radio", name="3").click()
    expect(page.get_by_text("Saved. Runs started from now on use it.")).to_be_visible(timeout=WAIT)
    page.reload()
    expect(
        page.get_by_role("radiogroup", name="Tests at the same time").get_by_role("radio", name="3")
    ).to_have_attribute("aria-checked", "true", timeout=WAIT)


def test_automatic_backups_can_be_made_and_their_settings_are_checked(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/settings?tab=general")
    card = page.locator("section", has_text="Automatic backups")
    expect(card).to_contain_text("once a day after 02:00", timeout=WAIT)
    card.get_by_role("button", name="Back up now").click()
    # The page redraws itself when the backup is done; the clicked button stays disabled until then. Typing before the
    # redraw would lose the message below, so wait for the fresh card with an enabled button.
    expect(
        page.locator("section", has_text="Automatic backups").get_by_role("button", name="Back up now")
    ).to_be_enabled(timeout=WAIT)
    expect(page.locator("section", has_text="Automatic backups")).to_contain_text(
        re.compile(r"Saved copies \(\d+\)"), timeout=WAIT
    )
    page.fill("#ab-keep", "99")
    page.locator("section", has_text="Automatic backups").get_by_role("button", name="Save").click()
    expect(page.locator("section", has_text="Automatic backups")).to_contain_text(
        "keep between 1 and 30 backups", timeout=WAIT
    )


def test_pod_discovery_is_off_until_it_is_allowed_and_can_be_switched_off_again(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/settings?tab=environments")
    card = page.locator("section", has_text="Pod discovery")
    expect(card).to_contain_text("Nothing is read until you allow it", timeout=WAIT)
    expect(card.get_by_role("button", name="Look at the pod now")).to_be_disabled()
    page.get_by_label("Allow pod discovery for this pod").check()
    expect(page.locator("section", has_text="Pod discovery")).to_contain_text("On for this pod", timeout=WAIT)
    expect(page.get_by_role("button", name="Look at the pod now")).to_be_enabled()
    page.get_by_label("Allow pod discovery for this pod").uncheck()
    expect(page.locator("section", has_text="Pod discovery")).to_contain_text(
        "Nothing is read until you allow it", timeout=WAIT
    )


def test_the_ai_quality_check_is_there_and_needs_an_ai_to_be_chosen_first(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/settings?tab=ai")
    card = page.locator("section", has_text="AI quality check")
    expect(card).to_contain_text("Not run yet", timeout=WAIT)
    expect(card).to_contain_text("made-up questions")
    expect(card.get_by_role("button", name="Check this AI")).to_be_disabled()


# ------------------------------------------------------------------ ticket links


def test_a_ticket_can_be_written_linked_and_removed_from_a_failure(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/settings?tab=general")
    page.fill("#tt-link", "https://example.atlassian.net/browse/{key}")
    page.fill(
        "#tt-create",
        "https://example.atlassian.net/secure/CreateIssueDetails!init.jspa?summary={title}&description={description}",
    )
    page.locator("section", has_text="Ticket tracker").get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved.", exact=True)).to_be_visible(timeout=WAIT)

    page.goto(f"{site.url}/#/attention")
    card = page.locator("article[aria-label^='Check did not match']")
    card.get_by_role("button", name="Ticket", exact=True).click()
    drawer = page.locator(".drawer")
    expect(drawer.locator("textarea")).to_have_value(re.compile("Redwood Shores"), timeout=WAIT)
    expect(drawer.get_by_role("link", name=re.compile("Open .* with it|Open the tracker with it"))).to_be_visible()
    page.fill("#tk-ref", "javascript:alert(1)")
    drawer.get_by_role("button", name="Link ticket").click()
    expect(drawer).to_contain_text("a ticket number uses letters", timeout=WAIT)
    page.fill("#tk-ref", "PROJ-5")
    drawer.get_by_role("button", name="Link ticket").click()
    expect(drawer.locator("a[href='https://example.atlassian.net/browse/PROJ-5']")).to_be_visible(timeout=WAIT)
    drawer.get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator("article[aria-label^='Check did not match'] a[href$='/browse/PROJ-5']")).to_be_visible(
        timeout=WAIT
    )

    page.goto(f"{site.url}/#/tests/hcm%2Flocation.yaml")  # the failing test shows its ticket too
    expect(page.locator("main [data-tickets] a")).to_have_text("PROJ-5", timeout=WAIT)

    page.goto(f"{site.url}/#/attention")
    page.locator("article[aria-label^='Check did not match']").get_by_role(
        "button", name=re.compile(r"Ticket \(1\)")
    ).click()
    page.locator(".drawer").get_by_role("button", name="Remove").click()
    expect(page.locator(".drawer")).to_contain_text("No ticket linked yet", timeout=WAIT)


# ------------------------------------------------------------------ Release impact: What's New


def test_a_saved_whats_new_page_is_read_and_saved_as_a_feature_list(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/impact")
    page.get_by_role("button", name="Import feature list").first.click()
    page.set_input_files("input[type=file]", str(EXAMPLES / "releases" / "26D_whats_new_sample.html"))
    drawer = page.locator(".drawer")
    expect(drawer).to_contain_text("4 features found for release 26D", timeout=WAIT)
    expect(drawer).to_contain_text("Invoice Approval by Line")
    drawer.get_by_role("button", name="Save feature list").click()
    expect(page).to_have_url(re.compile(r"#/impact\?release=26D\.json"), timeout=WAIT)


def test_pasted_whats_new_text_can_be_checked(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/impact")
    page.get_by_role("button", name="Import feature list").first.click()
    page.fill("#imp-paste", (EXAMPLES / "releases" / "26D_whats_new_sample.txt").read_text(encoding="utf-8"))
    page.locator(".drawer").get_by_role("button", name="Check file").click()
    expect(page.locator(".drawer")).to_contain_text("2 features found for release 26D", timeout=WAIT)


# ------------------------------------------------------------------ sign-in


@pytest.fixture
def secure(tmp_path: Path) -> Iterator[Site]:
    """Its own Quartermaster: turning sign-in on must not change the pages the other tests use."""
    with serve(build_site(tmp_path)) as s:
        yield s


def test_turning_on_sign_in_then_adding_and_signing_in_a_user(secure: Site, page: Page, browser: Browser) -> None:
    page.goto(f"{secure.url}/#/settings?tab=users")
    page.fill("#on-name", "Sai Ram")
    page.fill("#on-user", "sai")
    page.fill("#on-pw", "correct-horse-battery")
    page.fill("#on-again", "correct-horse-battery")
    page.get_by_role("button", name="Turn on sign-in").click()
    expect(page.locator("#nav")).to_contain_text("Sign out", timeout=WAIT)  # you are the first administrator, signed in
    page.goto(f"{secure.url}/#/settings?tab=users")
    expect(page.locator("main")).to_contain_text("1 person can sign in", timeout=WAIT)

    page.get_by_role("button", name="Add a user").click()
    page.fill("#u-name", "Jo Tester")
    page.fill("#u-user", "jo")
    page.locator(".drawer").get_by_role("button", name="Add the user").click()
    expect(page.locator(".drawer")).to_contain_text("This is the only time it is shown", timeout=WAIT)
    temporary = page.locator(".drawer div[style*='monospace']").inner_text().strip()
    assert len(temporary) >= 10
    page.locator(".drawer").get_by_role("button", name="Done").click()

    # a second browser window is a stranger: it sees only the sign-in
    other = browser.new_context()
    stranger = other.new_page()
    stranger.set_default_timeout(WAIT)
    stranger.goto(f"{secure.url}/")
    expect(stranger.locator("main")).to_contain_text("Sign in to Quartermaster", timeout=WAIT)
    stranger.fill("input[aria-label='User name']", "jo")
    stranger.fill("input[aria-label='Password']", "not-the-password")
    stranger.get_by_role("button", name="Sign in", exact=True).click()
    expect(stranger.locator("main")).to_contain_text("wrong user name or password", timeout=WAIT)
    stranger.fill("input[aria-label='Password']", temporary)
    stranger.get_by_role("button", name="Sign in", exact=True).click()
    expect(stranger.locator("main")).to_contain_text("Choose your own password", timeout=WAIT)
    other.close()


def test_continue_with_google_signs_in_only_people_the_administrator_added(
    secure: Site, page: Page, browser: Browser
) -> None:
    # sign-in on, Google set up and one Google-only user, made through the pages
    page.goto(f"{secure.url}/#/settings?tab=users")
    page.fill("#on-name", "Sai Ram")
    page.fill("#on-user", "sai")
    page.fill("#on-pw", "correct-horse-battery")
    page.fill("#on-again", "correct-horse-battery")
    page.get_by_role("button", name="Turn on sign-in").click()
    expect(page.locator("#nav")).to_contain_text("Sign out", timeout=WAIT)
    page.goto(f"{secure.url}/#/settings?tab=users")
    expect(page.locator("main")).to_contain_text("Sign in with Google", timeout=WAIT)
    page.fill("#g-id", CLIENT)
    page.fill("#g-secret", SECRET)
    page.get_by_label("Turn on Continue with Google").check()
    page.locator("section", has_text="Sign in with Google").get_by_role("button", name="Save").click()
    expect(page.locator("main")).to_contain_text("On: people can use Continue with Google", timeout=WAIT)
    expect(page.locator("main")).not_to_contain_text(SECRET)  # the secret is never shown again

    page.get_by_role("button", name="Add a user").click()
    page.fill("#u-name", "Jane Doe")
    page.fill("#u-user", "jane")
    page.fill("#u-mail", "Jane.Doe@gmail.com")
    page.locator(".drawer").get_by_role("button", name="Add the user").click()
    expect(page.locator("main")).to_contain_text("jane.doe@gmail.com", timeout=WAIT)

    # Jane, in her own window: Gmail ignores the dots, so janedoe@gmail.com is the same person
    secure.google.email = "janedoe@gmail.com"
    jane_window = browser.new_context()
    jane = jane_window.new_page()
    jane.set_default_timeout(WAIT)
    jane.goto(f"{secure.url}/")
    jane.locator("#login-google").click()
    expect(jane.locator("main h1").first).to_have_text("Overview", timeout=WAIT)
    me = jane.evaluate("fetch('/api/auth/status').then(r => r.json()).then(s => s.user.username)")
    assert me == "jane"
    jane_window.close()

    # someone nobody added is turned away with a plain sentence
    secure.google.email = "stranger@gmail.com"
    stranger_window = browser.new_context()
    stranger = stranger_window.new_page()
    stranger.set_default_timeout(WAIT)
    stranger.goto(f"{secure.url}/")
    stranger.locator("#login-google").click()
    expect(stranger.locator("[role=alert]").first).to_contain_text("has not been given access", timeout=WAIT)
    stranger_window.close()


# This one changes the shared site (a new data set, a copy of a test), so it stays last in the file.
def test_values_are_edited_in_a_table_and_a_test_is_copied(site: Site, page: Page) -> None:
    page.goto(f"{site.url}/#/data")
    page.get_by_role("button", name="New data set").click()
    page.get_by_label("Name of the data set").fill("acme-gl")
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Could not save.")).to_be_visible(
        timeout=WAIT
    )  # (in the side panel, not in main) a set with no values is refused, and the page says why
    page.get_by_label("Name of a value").first.fill("ledger_name")
    page.get_by_label("ledger_name on every pod").fill("US Primary Ledger")
    page.get_by_role("button", name="Save").click()
    see(page, "acme-gl")
    see(page, "US Primary Ledger")
    page.goto(f"{site.url}/#/tests/hcm%2Fuses_data.yaml")
    page.get_by_role("button", name="Make a copy").click()
    page.get_by_role("button", name="Make the copy").click()
    expect(page.locator("h1")).to_have_text("A test with test data (copy)", timeout=WAIT)
