"""REST API steps: call a Fusion REST service with the browser's signed-in session, check the reply
and keep values from it for later steps.

    - action: api_call
      intent: The new location is in the REST API
      value: GET /hcmRestApi/resources/11.13.18.05/locationsV2?q=LocationName='${name}'
      options:
        expect_status: 200          # optional; any 2xx when not given
        check:                      # optional; each path in the JSON reply and the value it must have
          count: "1"
          items[0].ActiveStatus: A
          items[0].LocationId: "*"  # "*" means: there, and not empty
        save:                       # optional; ${location_id} can then be used by later steps
          location_id: items[0].LocationId
        body: {...}                 # for POST, PATCH and PUT

Only the pod's own address is called: a full URL to another host is refused.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse_request(value: str) -> tuple[str, str]:
    """ "GET /path" -> ("GET", "/path"). A value without a method is a GET."""
    text = " ".join(value.split())
    method, _, rest = text.partition(" ")
    if method.upper() in METHODS and rest:
        return method.upper(), rest.strip()
    if text.startswith(("/", "http")):
        return "GET", text
    raise ValueError(f"write the request as METHOD /path, for example GET /hcmRestApi/..., not {value!r}")


def json_get(data: Any, path: str) -> Any:
    """A value in a JSON reply by its path, e.g. items[0].PersonNumber. Raises KeyError when absent."""
    here = data
    for name, index in _PART.findall(path.strip()):
        if name:
            if not isinstance(here, dict) or name not in here:
                raise KeyError(path)
            here = here[name]
        else:
            i = int(index)
            if not isinstance(here, list) or i >= len(here):
                raise KeyError(path)
            here = here[i]
    return here


def render_body(body: Any, render: Callable[[str], str]) -> Any:
    """The request body with ${name} filled in, in every text inside it."""
    if isinstance(body, str):
        return render(body)
    if isinstance(body, dict):
        return {k: render_body(v, render) for k, v in body.items()}
    if isinstance(body, list):
        return [render_body(v, render) for v in body]
    return body


def as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def check_reply(
    status: int, data: Any, options: dict[str, Any], render: Callable[[str], str]
) -> tuple[list[str], dict[str, str]]:
    """What is wrong with a reply (empty when it is right), and the values to keep for later steps."""
    wrong: list[str] = []
    expected = options.get("expect_status")
    if expected is not None and status != int(expected):
        wrong.append(f"the API answered HTTP {status}, expected {int(expected)}")
    elif expected is None and not 200 <= status < 300:
        wrong.append(f"the API answered HTTP {status}")
    if wrong:
        return wrong, {}
    for path, want in (options.get("check") or {}).items():
        want_text = render(as_text(want))
        try:
            got = json_get(data, str(path))
        except KeyError:
            wrong.append(f'the API reply has no "{path}"')
            continue
        if want_text == "*":
            if as_text(got) == "" or got in ([], {}):
                wrong.append(f'"{path}" is empty in the API reply')
        elif as_text(got) != want_text:
            wrong.append(f'"{path}" is "{as_text(got)}" in the API reply, expected "{want_text}"')
    kept: dict[str, str] = {}
    for name, path in (options.get("save") or {}).items():
        try:
            kept[str(name)] = as_text(json_get(data, str(path)))
        except KeyError:
            wrong.append(f'the API reply has no "{path}" to keep as {name}')
    return wrong, kept
