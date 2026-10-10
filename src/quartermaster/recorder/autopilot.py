"""Prepare a manual scenario automatically: an AI follows the written steps in the signed-in pod.

For each written step the AI is shown the step, what should happen, and what is on the screen:
the headings and a numbered list of the buttons, links and fields that can be used. It answers
with one action (click, fill, select), or says the step is done or that it is stuck. Quartermaster
does the action itself, so only things that are really on the screen can be used, and records it
the same way a person's click is recorded. When a step is done a picture is taken (see Guide).

The AI never decides that a test passed on its own: the result is a draft that a person reviews
(its pictures) and approves before it is run every quarter. It stops at the first step it cannot
do, never types values the script does not give, and does not press buttons that change data
(Save, Submit, Delete, Approve...) unless the step says so.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from quartermaster.ai.providers import AIError, parse_json

MAX_ACTIONS = 5  # per written step
_ASKS_CLICK = re.compile(r"^(?:select|click(?: on)?|choose|open|tap|press)\s+(?:the\s+)?(.+)$", re.I)
_RISKY = re.compile(
    r"\b(submit|save|delete|remove|approve|reject|withdraw|terminate|post|send|confirm|cancel|apply|sign out)\b", re.I
)
_ROLES = {
    "link",
    "button",
    "menuitem",
    "tab",
    "option",
    "treeitem",
    "checkbox",
    "radio",
    "textbox",
    "combobox",
    "searchbox",
}

SYSTEM = """You help test Oracle Fusion Cloud. A tester's written test script is being carried out in a \
browser that is already signed in. You get ONE step at a time: the step, what should happen, what was \
already done for this step (including actions that did not work), and what is on the screen (headings, and \
a numbered list of things that can be clicked or filled).

Answer with one JSON object and nothing else:
{"do": "click" | "fill" | "select" | "navigate" | "wait" | "done" | "stuck", "element": <number from the list>, \
"value": "<text to type, option to choose, or Navigator path>", "why": "<a few words>"}

- "navigate": open a page from the Navigator menu by its path, for example {"do": "navigate", \
"value": "Me > Personal Information"} or "My Client Groups > Person Management". Prefer this to clicking \
through the Navigator menu yourself: menu groups are headers that expand, and their items can be hidden. \
A group name alone, for example {"do": "navigate", "value": "Me"}, opens that group and lists its pages: \
use it when a step only names a group, then answer "done" for that step.
- "click": a button, link, tab or tile from the list, by its number. Elements of kind "text" are cards or \
tiles that open something when clicked (for example "Employment Info" on Personal Info).
- "wait": the step says to wait for a scheduled process (ESS job) to finish, or to check that it \
completed, after it was submitted. Quartermaster reads the process number from the screen and waits.
- "done": the step is already complete on this screen (for example "Login" when signed in, or the page \
the step asks for is open). A later step may already be done by an earlier navigate.
- "stuck": the step needs a value the script does not give (blank, or written as <>), needs another \
person, or nothing on the screen fits even after trying another way.
- If an action did not work, try a different way (for example navigate instead of click).
- Only choose elements from the list. Prefer the exact words of the step.
- Never type a value that is not written in the step.
- One action per answer."""

CHECK_SYSTEM = """You help test Oracle Fusion Cloud. A test scenario has just been carried out. Choose up \
to two texts on the screen that prove the scenario reached the right page or result, for example a page \
heading or a field label. Answer with one JSON object and nothing else: \
{"check": [<numbers from the TEXTS list>], "why": "<a few words>"}. If nothing proves it, {"check": []}."""

# Where an element is on the page, as a CSS selector that matches only it: from the nearest
# ancestor with a stable id (one without long numbers, which pages generate anew), then by position.
CSS_PATH_JS = r"""(el) => {
  const parts = [];
  for (let node = el; node && node.nodeType === 1 && node !== document.documentElement; node = node.parentElement) {
    if (node.id && !/\d{3,}/.test(node.id) && document.querySelectorAll('#' + CSS.escape(node.id)).length === 1) {
      parts.unshift('#' + CSS.escape(node.id));
      break;
    }
    const same = [...(node.parentElement ? node.parentElement.children : [])].filter((c) => c.tagName === node.tagName);
    parts.unshift(node.tagName.toLowerCase() + (same.length > 1 ? `:nth-of-type(${same.indexOf(node) + 1})` : ''));
  }
  const css = parts.join(' > ');
  return css && document.querySelectorAll(css).length === 1 ? css : '';
}"""

# Whether the section with this heading is open: True, False, or null when the page does not say.
EXPANDED_JS = r"""(text) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const el = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, span, div, a, button, [role=heading]')]
    .find((e) => !e.children.length && clean(e.innerText) === text);
  // The nearest toggle that belongs to this heading only: once a container holds more than one
  // toggle, it holds other sections too, and their state says nothing about this one.
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    if (n.matches('[aria-expanded]')) return n.getAttribute('aria-expanded') === 'true';
    const inside = n.querySelectorAll('[aria-expanded]');
    if (inside.length === 1) return inside[0].getAttribute('aria-expanded') === 'true';
    if (inside.length > 1) return null;
  }
  return null;
}"""

# The toggle that opens the section with this heading: the heading's own clickable ancestor, or the
# one toggle (arrow or header button) in the smallest container around it. Looks inside web
# components too. It is marked data-qm-toggle="1" so Playwright can click it; returns its state,
# role and name, or null.
TOGGLE_JS = r"""(text) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const sel = '[aria-expanded], button, [role=button], summary, oj-button, oj-c-button';
  const all = [];
  const walk = (root) => root.querySelectorAll('*').forEach((e) => {
    all.push(e);
    if (e.shadowRoot) walk(e.shadowRoot);
  });
  walk(document);
  all.forEach((e) => e.removeAttribute && e.removeAttribute('data-qm-toggle'));
  const el = all.find((e) => clean(e.textContent) === text
    && ![...e.children].some((c) => clean(c.textContent) === text));
  if (!el) return null;
  const up = (n) => n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  const mark = (t) => {
    t.setAttribute('data-qm-toggle', '1');
    const role = t.getAttribute('role') || (t.tagName === 'BUTTON' ? 'button' : '');
    return {expanded: t.getAttribute('aria-expanded'), role,
      name: clean(t.getAttribute('aria-label') || t.getAttribute('title') || t.textContent).slice(0, 80)};
  };
  for (let n = el, depth = 0; n && n !== document.body && depth < 8; n = up(n), depth++) {
    if (n.matches && n.matches(sel)) return mark(n);
    const inside = n.querySelectorAll ? [...n.querySelectorAll(sel)] : [];
    const deep = n.shadowRoot ? [...n.shadowRoot.querySelectorAll(sel)] : [];
    const found = [...inside, ...deep];
    const withState = found.filter((t) => t.hasAttribute('aria-expanded'));
    if (withState.length === 1) return mark(withState[0]);
    if (withState.length > 1 || found.length > 2) return null;  // other sections are in here too
    if (found.length) return mark(found[found.length - 1]);  // the arrow comes after the heading
  }
  return null;
}"""

# How the section around a heading is built: tag, role, aria and tabindex of its ancestors and of
# their children (no text but the heading), for the diary.
SECTION_INFO_JS = r"""(text) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const all = [];
  const walk = (root) => root.querySelectorAll('*').forEach((e) => {
    all.push(e);
    if (e.shadowRoot) walk(e.shadowRoot);
  });
  walk(document);
  const el = all.find((e) => clean(e.textContent) === text
    && ![...e.children].some((c) => clean(c.textContent) === text));
  if (!el) return 'heading not found';
  const desc = (e) => {
    const a = (k) => (e.getAttribute(k) !== null ? `[${k}=${e.getAttribute(k)}]` : '');
    const names = typeof e.className === 'string' ? e.className.trim().split(/\s+/).filter(Boolean) : [];
    const cls = names.length ? '.' + names.slice(0, 3).join('.') : '';
    return e.tagName.toLowerCase() + cls + a('role') + a('aria-expanded') + a('tabindex')
      + (e.shadowRoot ? '(shadow)' : '');
  };
  const out = [];
  const up = (n) => n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  for (let n = el, depth = 0; n && n !== document.body && depth < 4; n = up(n), depth++) {
    out.push(desc(n) + ' > ' + [...(n.children || [])].slice(0, 6).map(desc).join(' , '));
  }
  return out.join(' || ');
}"""

# What the AI is shown of the page: never values typed in fields, only labels and visible names.
SNAPSHOT_JS = r"""() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const roleOf = (el) => { const r = el.getAttribute('role'); if (r) return r; const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link'; if (t === 'button') return 'button'; if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') { const ty = (el.type || 'text').toLowerCase();
      if (['button', 'submit', 'reset'].includes(ty)) return 'button'; if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio'; return 'textbox'; }
    return t; };
  const labelOf = (el) => { if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return clean(l.innerText); }
    return clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title')); };
  const nameOf = (el) => clean(el.getAttribute('aria-label') || el.innerText || el.getAttribute('title')
    || el.getAttribute('alt'));
  // The whole name (a card's link holds its title and description) and its first line (the title):
  // the list shows names cut short, but the element is found again by its whole name or its title.
  const fullOf = (el) => (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title')
    || el.getAttribute('alt') || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  const titleOf = (el) => clean((el.getAttribute('aria-label') || el.innerText || '').split('\n')
    .map((t) => t.trim()).find(Boolean));
  const inToolbar = (el) => el.closest('[data-qm-toolbar], #__qm_toolbar, .__qm');
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], ' +
    '[role=menuitem], [role=tab], [role=option], [role=treeitem], [onclick]';
  const items = [], seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (items.length >= 150) break;
    if (!vis(el) || inToolbar(el)) continue;
    const role = roleOf(el);
    const field = ['textbox', 'combobox', 'searchbox', 'checkbox', 'radio'].includes(role);
    const name = field ? labelOf(el) : nameOf(el);
    if (!name || seen.has(role + '|' + name)) continue;
    seen.add(role + '|' + name);
    items.push(field ? {role, name} : {role, name, full: fullOf(el), title: titleOf(el)});
  }
  // Cards and tiles (for example Redwood's Personal Info cards) are often plain boxes that react to
  // a click: their text shows the hand cursor, but they are not links or buttons.
  const names = new Set(items.map((it) => it.name));
  for (const el of document.querySelectorAll('h1, h2, h3, h4, h5, h6, span, div, p, li, td, label')) {
    if (items.length >= 150) break;
    if (el.children.length || !vis(el) || inToolbar(el) || el.closest(sel)) continue;
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    const name = clean(el.innerText);
    if (name.length < 2 || name.length > 60 || names.has(name)) continue;
    names.add(name);
    items.push({role: 'text', name});
  }
  const heads = [...document.querySelectorAll('h1, h2, h3, [role=heading]')].filter(vis)
    .map((e) => clean(e.innerText)).filter(Boolean);
  const texts = [], tseen = new Set();
  for (const el of document.querySelectorAll('h1, h2, h3, h4, [role=heading], label, th, dt, span, div, p, td')) {
    if (texts.length >= 120) break;
    if (el.children.length || !vis(el) || inToolbar(el)) continue;
    const t = clean(el.innerText);
    if (t.length < 2 || t.length > 60 || tseen.has(t)) continue;
    tseen.add(t); texts.push(t);
  }
  return {title: document.title, headings: [...new Set(heads)].slice(0, 20), items, texts};
}"""


Ask = Callable[[str, str], str]


class Autopilot:
    def __init__(
        self,
        page: Any,
        guide: Any,
        recorder: Any,
        ask: Ask,
        settle: Callable[[], None] = lambda: None,
        should_stop: Callable[[], bool] = lambda: False,
        navigate: Callable[[str], None] | None = None,
        wait_process: Callable[[], str] | None = None,
    ):
        self.page, self.guide, self.recorder, self.ask = page, guide, recorder, ask
        self.settle, self.should_stop, self.navigate = settle, should_stop, navigate
        self.wait_process = wait_process  # waits for the scheduled process just submitted; its final status
        self.reason = ""  # why it stopped early, for the tester
        # What the AI answered and what happened, step by step, kept with the run's evidence so a
        # person can see why it stopped. Only labels from the screen, never typed values or keys.
        run_dir = getattr(guide, "run_dir", None)
        self.diary = Path(run_dir) / "ai-diary.txt" if run_dir else None
        self.step_no = 1  # the written step being done now
        self.nav_pages: dict[str, str] = {}  # page link -> its Navigator group, once a group was opened

    # ------------------------------------------------------------------ the loop

    def run(self) -> bool:
        """Do every step. True only when all of them were done (the draft is then worth reviewing)."""
        steps = self.guide.steps
        for i, step in enumerate(steps):
            number = i + 1
            self.step_no = number
            if not self._do_step(number, step, len(steps)):
                return False
            if number == len(steps) and not self._add_checks(number, step):
                return False
            self.guide.mark(f"{number} pass", self.page)
            self._say(f"Step {number} done.")
        self._say("Every step is done. Saving.")
        return True

    def _do_step(self, number: int, step: dict[str, str], total: int) -> bool:
        done: list[str] = []
        nudged = False
        for _ in range(MAX_ACTIONS):
            if self.should_stop():
                return self._stuck(number, "stopped by the tester")
            screen = self._screen()
            if not done:
                self._note(f"\nStep {number}: {step['action']}")
            self._note(f"  Screen: {screen.get('title', '')} ({len(screen.get('items', []))} things to click or fill)")
            self._note(
                "  Shown: "
                + " | ".join(f"[{i}] {it['role']} {it['name']}" for i, it in enumerate(screen.get("items", []), 1))
            )
            self._say(f"Step {number}: asking the AI what to do" + (f" (try {len(done) + 1})" if done else ""))
            try:
                answer = self.ask(SYSTEM, self._prompt(number, total, step, done, screen))
                self._note(f"  AI answered: {' '.join(answer.split())[:400]}")
                reply = parse_json(answer)
            except AIError as e:
                return self._stuck(number, str(e))
            what = str(reply.get("do", "")).lower()
            why = " ".join(str(reply.get("why", "")).split())[:200]
            value = " ".join(str(reply.get("value") or "").split())
            if what == "done":
                unclicked = self._still_to_do(step, screen) if not done else ""
                if unclicked and not nudged:
                    # "Select My Compensation" with nothing done yet, while it is on the screen
                    nudged = True
                    done.append(
                        f'answered "done", but the step asks for "{unclicked}", which is on the screen'
                        " and was NOT clicked yet; click it"
                    )
                    self._note(f"  Not done yet: {unclicked} is still to be clicked")
                    continue
                return True
            if what == "wait" and self.wait_process is not None:
                self._say(f"Step {number}: waiting for the scheduled process to finish")
                try:
                    final = self.wait_process()
                except Exception as e:  # no process number on the screen, or the status check refused
                    done.append(f"wait for the process DID NOT WORK ({_first_line(e)})")
                    self._note(f"  Wait for the process did not work: {_first_line(e)}")
                    continue
                self._note(f"  The scheduled process ended {final}")
                if final != "SUCCEEDED":
                    return self._stuck(number, f"the scheduled process ended {final}, not SUCCEEDED")
                self._record({"kind": "wait_job", "value": "last"})
                done.append("waited for the scheduled process: it SUCCEEDED")
                continue
            if what == "navigate" and self.navigate is not None and value:
                self._open(number, value, why, done)
                continue
            if what not in ("click", "fill", "select"):
                return self._stuck(number, why or "the AI could not see how to do it")
            item = self._item(screen, reply.get("element"))
            if item is None:
                done.append(f"chose element {reply.get('element')!r}, which is NOT on the screen")
                continue
            group = self.nav_pages.get(item["name"]) if what == "click" and item["role"] == "link" else None
            if group and self.navigate is not None:
                # A page in an open Navigator group: replays open it the same way, from the Navigator
                self._open(number, f"{group} > {item['name']}", why, done)
                continue
            refusal = self._refuse(what, item, value, step)
            if refusal:
                return self._stuck(number, refusal)
            self._say(f'Step {number}: {what} {item["role"]} "{item["name"]}"{f" ({why})" if why else ""}')
            self._note(f'  Doing: {what} {item["role"]} "{item["name"]}"')
            action = f'{what} {item["role"]} "{item["name"]}"' + (f' = "{value}"' if value else "")
            try:
                self._act(what, item, value)
            except Exception as e:  # hidden, covered or gone: tell the AI and let it try another way
                done.append(f"{action} DID NOT WORK ({_first_line(e)})")
                self._note(f"  Did not work: {_first_line(e)}")
                self._say(f"Step {number}: that did not work. Trying another way.")
                continue
            self.settle()
            done.append(action)
        return self._stuck(number, f"not finished after {MAX_ACTIONS} actions")

    @staticmethod
    def _still_to_do(step: dict[str, str], screen: dict[str, Any]) -> str:
        """The name in a step like "Select My Compensation" when that is still on the screen."""
        m = _ASKS_CLICK.match(step["action"].strip())
        if not m:
            return ""
        wanted = " ".join(m.group(1).split()).strip(" .'\"").lower()
        for it in screen.get("items", []):
            names = [str(it.get("title") or ""), str(it.get("name") or "")]
            if wanted and any(n.lower() == wanted or n.lower().startswith(wanted + " ") for n in names if n):
                return str(it.get("title") or it.get("name"))
        return ""

    def _stuck(self, number: int, why: str) -> bool:
        self.reason = f"Step {number}: {why}"
        self._note(f"  STOPPED: {why}")
        self.guide.mark(f"{number} fail The AI could not do this step: {why}", self.page)
        self._say(f"Stopped at step {number}: {why}. Do this scenario by hand instead.")
        return False

    # ------------------------------------------------------------------ the screen and the AI

    def _screen(self) -> dict[str, Any]:
        for _ in range(3):
            try:
                screen = self.page.evaluate(SNAPSHOT_JS)
                if isinstance(screen, dict):
                    return screen
            except Exception:  # the page was navigating
                self.settle()
        return {"title": "", "headings": [], "items": [], "texts": []}

    @staticmethod
    def _prompt(number: int, total: int, step: dict[str, str], done: list[str], screen: dict[str, Any]) -> str:
        lines = [
            f"Step {number} of {total}: {step['action']}",
            f"What should happen: {step['expected'] or 'not written'}",
            f"Test case: {step.get('case_name') or step.get('case') or ''}",
            "Already done for this step: " + ("; ".join(done) if done else "nothing yet"),
            "",
            f"SCREEN: {screen.get('title', '')}",
            "Headings: " + " | ".join(screen.get("headings", [])),
            "Elements:",
            *(f'[{i}] {it["role"]} "{it["name"]}"' for i, it in enumerate(screen.get("items", []), 1)),
        ]
        return "\n".join(lines)

    @staticmethod
    def _item(screen: dict[str, Any], number: Any) -> dict[str, str] | None:
        items = screen.get("items", [])
        try:
            n = int(number)
        except (TypeError, ValueError):
            return None
        return items[n - 1] if 1 <= n <= len(items) else None

    @staticmethod
    def _refuse(what: str, item: dict[str, str], value: str, step: dict[str, str]) -> str:
        written = f"{step['action']} {step['expected']}".lower()
        word = _RISKY.search(item["name"])
        if what == "click" and word and word.group(1).lower() not in written:
            return f'it would have to click "{item["name"]}", which can change data, and the step does not say so'
        if what in ("fill", "select") and (not value or "<" in value or value.lower() not in written):
            return "the step does not give the value to enter"
        return ""

    def _open(self, number: int, value: str, why: str, done: list[str]) -> None:
        """Open a page from the Navigator ("Me > Personal Information"), or a group on its own ("Me")."""
        assert self.navigate is not None
        self._say(f"Step {number}: open {value} from the Navigator{f' ({why})' if why else ''}")
        if ">" not in value:
            try:
                group, pages = self._open_group(value)
            except Exception as e:  # not a group, or it would not open: say so, like a failed path
                group, pages = "", []
                self._note(f"  Could not open the group: {_first_line(e)}")
            if group:
                shown = ", ".join(pages) if pages else "none could be read"
                done.append(
                    f'opened the Navigator group "{group}". Its pages: {shown}. If the step only names this group,'
                    f' it is done. To open one of its pages, navigate "{group} > <page>".'
                )
                self._note(f"  Opened the Navigator group {group}; its pages: {shown}")
                return
        try:
            self.navigate(value)
        except Exception as e:  # the path is not in this Navigator: let the AI try another way
            groups = self._navigator_groups()
            done.append(
                f'navigate "{value}" DID NOT WORK ({_first_line(e)}).'
                + (f" The Navigator's groups are: {', '.join(groups)}." if groups else "")
                + ' Use the exact names shown, as "Group > Page", or the group name alone to see its pages.'
            )
            self._note(
                f"  Did not work: {_first_line(e)}" + (f"; Navigator groups: {', '.join(groups)}" if groups else "")
            )
            self._say(f"Step {number}: {value} is not in the Navigator. Trying another way.")
            return
        self._record({"kind": "navigate", "value": value})
        done.append(f'navigate "{value}"')

    def _links(self) -> list[str]:
        return [it["name"] for it in self._screen().get("items", []) if it.get("role") == "link"]

    def _open_group(self, name: str) -> tuple[str, list[str]]:
        """Expand one Navigator group (opening the Navigator first) and return its exact name and the
        pages it shows. ("", []) when there is no such group. Nothing is recorded: a replay opens the
        group itself when it navigates to one of its pages."""
        page = self.page
        headers = page.locator("div.navmenu-header")
        if headers.locator("visible=true").count() == 0:
            page.get_by_role("link", name="Navigator", exact=True).first.click(timeout=10_000)
            self.settle()
        group = next((g for g in self._navigator_groups() if g.lower() == name.strip().lower()), "")
        if not group:
            return "", []
        header = headers.filter(has_text=re.compile(rf"^\s*{re.escape(group)}\s*$")).locator("visible=true")
        exact = page.locator("div.navmenu-header[title=" + _css_string(group) + "]").locator("visible=true")
        if exact.count():
            header = exact
        before = self._links()
        header.first.click(timeout=10_000)
        self.settle()
        after = self._links()
        if len(after) < len(before):  # it was open already, and that click closed it: open it again
            header.first.click(timeout=10_000)
            self.settle()
            before, after = after, self._links()
        pages = [n for n in after if n not in before]
        for n in pages:
            self.nav_pages[n] = group
        return group, pages[:40]

    def _navigator_groups(self) -> list[str]:
        """The names of the Navigator's groups, so the AI can use the pod's exact words."""
        try:
            names = self.page.evaluate(
                "() => [...document.querySelectorAll('div.navmenu-header')]"
                ".map((e) => (e.getAttribute('title') || e.innerText || '').trim()).filter(Boolean)"
            )
        except Exception:
            return []
        return [str(n)[:60] for n in names][:40] if isinstance(names, list) else []

    def _record(self, event: dict[str, Any]) -> None:
        """Keep an action for the saved test, with the written step it was done for."""
        self.recorder.events.append({**event, "guide_step": self.step_no})

    def _note(self, line: str) -> None:
        if self.diary is None:
            return
        try:
            self.diary.parent.mkdir(parents=True, exist_ok=True)
            with self.diary.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # the diary only helps to explain; never stop the AI for it

    def _say(self, message: str) -> None:
        self.recorder.message = message
        self.recorder.write_feed()

    # ------------------------------------------------------------------ doing it, and remembering it

    def _locate(self, item: dict[str, str]) -> tuple[Any, list[dict[str, str]]]:
        """The visible element, and how to find it again on replay. Replay needs a way that finds
        exactly one element on the whole page (hidden copies too), so those ways come first."""
        page, role, name = self.page, item["role"], item["name"]
        full, title = item.get("full") or name, item.get("title") or ""
        ways: list[tuple[str, str, Any]] = []
        if role in ("textbox", "combobox", "searchbox"):
            ways.append(("label", name, page.get_by_label(name, exact=True)))
        if role in _ROLES:
            for n in dict.fromkeys([full, name]):  # the list may show the name cut short
                ways.append(("role", f"{role}:{n}", page.get_by_role(role, name=n, exact=True)))
        for n in dict.fromkeys([name, title] if title else [name]):
            # a card's title is text inside it: a click on the title opens the card
            ways.append(("text", n, page.get_by_text(n, exact=True)))
        shown = [(s, v, _visible(loc)) for s, v, loc in ways]
        clickable = [(s, v, loc) for s, v, loc in shown if _count(loc) >= 1]
        if not clickable:
            raise LookupError(f'"{name}" is not shown on the screen')
        everywhere_one = [(s, v) for s, v, loc in ways if _count(loc) == 1]
        shown_one = [(s, v) for s, v, loc in shown if _count(loc) == 1]
        chosen = everywhere_one or shown_one or [(s, v) for s, v, _ in clickable]
        element = clickable[0][2].first
        if not everywhere_one:
            # The same name more than once on the page (e.g. two "My Compensation" cards): a replay
            # needs a way that finds exactly one, so the place of this one on the page comes first.
            css = self._css_path(element)
            if css:
                chosen = [("css", css), *chosen]
        return element, [{"strategy": s, "value": v} for s, v in chosen]

    def _css_path(self, element: Any) -> str:
        try:
            css = str(element.evaluate(CSS_PATH_JS) or "")
            return css if css and _count(self.page.locator(css)) == 1 else ""
        except Exception:  # a stand-in page, or the element is gone
            return ""

    def _act(self, what: str, item: dict[str, str], value: str) -> None:
        element, candidates = self._locate(item)
        if what == "click":
            element.click(timeout=8000)
        elif what == "fill":
            element.fill(value, timeout=8000)
        else:
            element.select_option(label=value, timeout=8000)
        event: dict[str, Any] = {"kind": what, "intent": item.get("title") or item["name"], "candidates": candidates}
        if what in ("fill", "select"):
            event["value"] = value
        self._record(event)

    def _add_checks(self, number: int, step: dict[str, str]) -> bool:
        """Checks that prove the last page is right. When the script lists fields to check, those
        are checked (see _check_fields); otherwise the AI chooses. False when it must stop."""
        groups = _field_groups(self.guide.scenario.get("fields") or [])
        if groups:
            return self._check_fields(number, groups)
        self._choose_checks(step)
        return True

    def _check_fields(self, number: int, groups: list[tuple[str, list[str]]]) -> bool:
        """The fields the script says to check, e.g. "Current Salary (Salary, Annual Salary)": a
        collapsed section is opened, each field shown is checked, and a section that shows none of
        its fields stops the preparation (the test user may have no data there), so a scenario never
        passes on a page that does not show what the script asks for."""
        added: list[str] = []
        missing: list[str] = []
        for section, fields in groups:
            wanted = fields or [section]
            found = self._shown(wanted)
            if not found and fields:
                self._open_section(section, wanted)
                found = self._shown(wanted)
            if not found:
                missing.append(f"{section} ({', '.join(fields)})" if fields else section)
                continue
            for text in found[:3]:
                if self._checkable(text) and text not in added:
                    self._record(
                        {"kind": "assert_visible", "intent": text, "candidates": [{"strategy": "text", "value": text}]}
                    )
                    added.append(text)
        self._note(f"  Checks added from the fields to check: {', '.join(added) if added else 'none'}")
        if missing:
            return self._stuck(
                number,
                "the page does not show " + "; ".join(missing) + ", which the script says to check."
                " The test user may have no data there: use a test user who has, or do it by hand",
            )
        self._say(f"Checked {len(added)} field(s) the script lists.")
        return True

    def _shown(self, wanted: list[str]) -> list[str]:
        """The texts on the screen that are these fields (the same words, or starting with them)."""
        texts = self._screen().get("texts", [])
        out = []
        for w in wanted:
            low = w.lower()
            hit = next((t for t in texts if t.lower() == low), None) or next(
                (t for t in texts if t.lower().startswith(low + " ") or t.lower().startswith(low + ":")), None
            )
            if hit is None and _count(_visible(self.page.get_by_text(w, exact=True))) >= 1:
                hit = w  # shown, inside a part of the page the screen list does not reach
            if hit and hit not in out:
                out.append(hit)
        return out

    def _open_section(self, section: str, wanted: list[str]) -> None:
        """Open a closed section with its toggle (the arrow or header button next to its heading;
        on Redwood pages a click on the heading text itself does nothing), and record that click so
        replays open it too. A section that is open already is left alone. The diary says what
        happened, and how the section is built when it could not be opened."""
        try:
            found = self.page.evaluate(TOGGLE_JS, section)
        except Exception:
            found = None
        if not isinstance(found, dict):
            self._note(f"  Section {section}: no toggle found near its heading")
            self._note_section(section)
            return
        if found.get("expanded") == "true":
            self._note(f"  Section {section}: open already, and its fields are not in it")
            return
        toggle = self.page.locator('[data-qm-toggle="1"]')
        before = self._amount_shown()
        try:
            toggle.first.click(timeout=8000)
        except Exception as e:
            self._note(f"  Section {section}: its toggle could not be clicked ({_first_line(e)})")
            self._note_section(section)
            return
        self.settle()
        now = self._expanded(toggle)
        opened = self._shown(wanted) or now == "true" or (now is None and self._amount_shown() > before)
        if not opened:
            if now == "false" or self._amount_shown() < before:
                # that click closed it (it was open): open it again, so the page stays as it was
                with suppress(Exception):
                    toggle.first.click(timeout=8000)
                    self.settle()
                self._note(f"  Section {section}: it was open already, and its fields are not in it")
                return
            self._note(f"  Section {section}: clicking its toggle showed nothing new")
            self._note_section(section)
            return
        ways = self._toggle_ways(toggle, found)
        if ways:
            self._record({"kind": "click", "intent": f"Open {section}", "candidates": ways})
        self._note(
            f"  Section {section}: opened"
            + ("" if self._shown(wanted) else ", but its fields are not in it")
            + ("" if ways else " (a replay cannot find its toggle again, so it will not open it)")
        )
        with suppress(Exception):
            self.page.evaluate("() => document.querySelector('[data-qm-toggle]')?.removeAttribute('data-qm-toggle')")

    def _expanded(self, toggle: Any) -> str | None:
        try:
            value = toggle.first.get_attribute("aria-expanded", timeout=2000)
        except Exception:
            return None
        return str(value) if value is not None else None

    def _amount_shown(self) -> int:
        try:
            return int(self.page.evaluate("() => document.body.innerText.length"))
        except Exception:
            return 0

    def _toggle_ways(self, toggle: Any, found: dict[str, Any]) -> list[dict[str, str]]:
        """How a replay finds the toggle again: by its button name, or by its place on the page."""
        ways: list[dict[str, str]] = []
        name = str(found.get("name") or "")
        role = str(found.get("role") or "")
        if name and role and _count(self.page.get_by_role(role, name=name, exact=True)) == 1:
            ways.append({"strategy": "role", "value": f"{role}:{name}"})
        css = self._css_path(toggle.first)
        if css:
            ways.append({"strategy": "css", "value": css})
        return ways

    def _note_section(self, section: str) -> None:
        """How the section around this heading is built (tags, roles, toggles; no data), so the
        diary shows what to click when it could not be opened."""
        try:
            info = self.page.evaluate(SECTION_INFO_JS, section)
        except Exception:
            return
        if isinstance(info, str) and info:
            self._note("    How it is built: " + info[:1500])

    def _choose_checks(self, step: dict[str, str]) -> None:
        """Checks that prove the last page is the right one, so a replay cannot pass on a wrong page.
        The AI chooses up to two texts; when none of its choices can be used, the page's heading (or
        the name of what the last step opened) is checked instead, so a saved scenario always proves
        something."""
        screen = self._screen()
        texts = screen.get("texts", [])
        scenario = self.guide.scenario
        fields = scenario.get("fields") or []
        chosen: list[Any] = []
        if texts:
            prompt = "\n".join(
                [
                    f"Scenario: {scenario.get('title', '')}",
                    f"Last step: {step['action']}",
                    f"What should happen: {step['expected'] or 'not written'}",
                    "Fields the page should show: " + (", ".join(fields) if fields else "not written"),
                    "TEXTS:",
                    *(f"[{i}] {t}" for i, t in enumerate(texts, 1)),
                ]
            )
            try:
                chosen = parse_json(self.ask(CHECK_SYSTEM, prompt)).get("check") or []
            except AIError:
                chosen = []
        picked = [texts[n - 1] for n in chosen if isinstance(n, int) and 1 <= n <= len(texts)]
        added = [t for t in dict.fromkeys(picked) if self._checkable(t)][:2]
        if not added:
            fallback = [*screen.get("headings", []), step["action"].removeprefix("Select ").strip()]
            added = [t for t in dict.fromkeys(fallback) if t and self._checkable(t)][:1]
        for text in added:
            self._record(
                {"kind": "assert_visible", "intent": text, "candidates": [{"strategy": "text", "value": text}]}
            )
        self._note(f"  Checks added: {', '.join(added) if added else 'none could be found'}")
        self._say(f"Added {len(added)} check(s) that prove the page is right.")

    def _checkable(self, text: str) -> bool:
        """A replay finds a check by its text: exactly one element may have it, and it is shown now."""
        found = self.page.get_by_text(text, exact=True)
        return _count(found) == 1 and _count(_visible(found)) == 1


def _visible(locator: Any) -> Any:
    try:
        return locator.locator("visible=true")
    except Exception:  # a stand-in page without visibility filtering
        return locator


def _field_groups(lines: list[str]) -> list[tuple[str, list[str]]]:
    """ "Current Salary (Salary, Adjustment, Start Date)" -> ("Current Salary", ["Salary", ...]);
    "Legal Employer" -> ("Legal Employer", []). "(N/A)" means nothing to check there."""
    groups = []
    for line in lines:
        m = re.match(r"^(.*?)\s*\((.*)\)\s*$", line.strip())
        section = " ".join((m.group(1) if m else line).split()).strip(" -:")
        inner = m.group(2) if m else ""
        if re.fullmatch(r"\s*n\s*/?\s*a\s*", inner, re.I):
            continue
        fields = [" ".join(f.split()) for f in re.split(r"[,;/]", inner) if f.strip()]
        if section and len(section) <= 60:
            groups.append((section, [f for f in fields if len(f) <= 60]))
    return groups


def _css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first_line(e: Exception) -> str:
    return (str(e).splitlines() or [type(e).__name__])[0][:120]


def _count(locator: Any) -> int:
    try:
        return int(locator.count())
    except Exception:
        return 0
