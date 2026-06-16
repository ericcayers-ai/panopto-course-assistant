#!/usr/bin/env python3
"""Convenience launcher: `python run.py` -> http://127.0.0.1:8000

Works regardless of the current working directory (chdirs to its own folder so
the `app` package imports cleanly).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402  (after sys.path setup)


def _maybe_open_browser(url: str) -> None:
    """Open the default browser shortly after the server starts (opt-in via
    OPEN_BROWSER=1, used by the one-click launchers)."""
    if os.environ.get("OPEN_BROWSER", "").lower() not in ("1", "true", "yes"):
        return
    import threading
    import webbrowser

    threading.Timer(1.5, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    print(f"Panopto Course Assistant -> {url}")
    _maybe_open_browser(url)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
