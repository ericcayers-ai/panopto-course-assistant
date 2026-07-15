#!/usr/bin/env bash
# =============================================================================
#  Optional add-ons for Panopto Course Assistant (macOS / Linux)
#  Installs: faster-whisper + yt-dlp + MarkItDown[all], Kokoro TTS,
#  and Playwright + Chromium for Moodle browser scrape.
#  Run start-unix.sh at least once first (it creates the environment).
# =============================================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Please run ./start-unix.sh once first to create the environment."
  exit 1
fi

echo "Installing transcription + document conversion..."
.venv/bin/python -m pip install -r requirements-transcribe.txt

echo
echo "Installing Kokoro text-to-speech..."
if ! .venv/bin/python -m pip install -r requirements-tts.txt; then
  echo "Kokoro install failed - TTS will not be available."
fi

echo
echo "Installing Playwright browser scrape support..."
if .venv/bin/python -m pip install -r requirements-browser.txt; then
  echo "Installing Chromium for Playwright (best-effort)..."
  if ! .venv/bin/python -m playwright install chromium; then
    echo "WARNING: playwright install chromium failed."
    echo "Retry: .venv/bin/python -m playwright install chromium"
  else
    echo "Chromium installed."
  fi
else
  echo "Playwright pip install failed - browser Moodle scrape unavailable."
fi

echo
echo "  Done. Restart the app (./start-unix.sh) to use transcription,"
echo "  document conversion, Speech TTS, and Moodle browser scrape."
