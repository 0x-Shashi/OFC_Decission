#!/bin/sh
# Mac and Linux: start Quartermaster (installs what it needs the first time). Keep the window open;
# press Ctrl+C to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First start: installing Quartermaster. This takes a few minutes, only this once."
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet -e ".[browser]" \
    && .venv/bin/python -m playwright install chromium || { echo "Installing did not work: see above."; exit 1; }
fi
exec .venv/bin/python -m quartermaster.cli serve
