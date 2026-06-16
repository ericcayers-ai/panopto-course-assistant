#!/usr/bin/env bash
# =============================================================================
#  Optional add-ons for Panopto Course Assistant (macOS / Linux)
#  Installs: faster-whisper (transcription) + yt-dlp + MarkItDown[all].
#  Run start-unix.sh at least once first (it creates the environment).
# =============================================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Please run ./start-unix.sh once first to create the environment."
  exit 1
fi

echo "Installing optional add-ons (this can take several minutes)..."
.venv/bin/python -m pip install -r requirements-transcribe.txt

echo
echo "  Done. Restart the app (./start-unix.sh) to use transcription and"
echo "  full document conversion."
