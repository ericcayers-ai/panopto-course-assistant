#!/usr/bin/env bash
# =============================================================================
#  Panopto Course Assistant - one-click launcher (macOS / Linux)
#  Make it executable once:  chmod +x start-unix.sh
#  Then double-click (macOS: rename to start-unix.command) or run ./start-unix.sh
#  First run sets everything up; later runs just start the app + open the browser.
# =============================================================================
set -e
cd "$(dirname "$0")"

# --- find a Python interpreter ----------------------------------------------
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo
  echo "  Python was not found."
  echo "  Install Python 3.10+ from https://www.python.org/downloads/ and run this again."
  echo
  read -r -p "Press Enter to close..." _ || true
  exit 1
fi

# --- create the virtual environment on first run ----------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating a private Python environment (first run, ~1 min)..."
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"

# --- install / update dependencies ------------------------------------------
echo "Installing dependencies (first run only, quick after that)..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

# --- launch -----------------------------------------------------------------
echo
echo "  Starting Panopto Course Assistant..."
echo "  Your browser will open at http://127.0.0.1:8000"
echo "  Leave this window open while you use the app; press Ctrl+C to stop."
echo
export OPEN_BROWSER=1
exec "$VENV_PY" run.py
