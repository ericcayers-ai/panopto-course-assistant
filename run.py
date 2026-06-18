#!/usr/bin/env python3
"""Convenience launcher: ``python run.py`` -> http://127.0.0.1:8000

Works regardless of the current working directory (chdirs to its own folder so
the ``app`` package imports cleanly).
"""
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(str(HERE))
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402  (after sys.path setup)


def _free_port(host: str, preferred: int, attempts: int = 20) -> int:
    """Return the first bindable port at/after ``preferred``.

    The default port is often still held by a previous run (closing the browser
    tab doesn't stop the server). Rather than crash with a cryptic WinError
    10048, fall back to the next free port so the app just starts.
    """
    bind_host = "127.0.0.1" if host == "0.0.0.0" else host
    for port in range(preferred, preferred + attempts):
        # Plain exclusive bind (no SO_REUSEADDR) to mirror uvicorn — on Windows
        # SO_REUSEADDR lets two sockets share a port, which would hide a clash.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind_host, port))
                return port
            except OSError:
                continue
    return preferred  # give up; let uvicorn surface the original error


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
    requested = int(os.environ.get("PORT", "8000"))
    port = _free_port(host, requested)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    if port != requested:
        print(f"Port {requested} is in use (another copy may already be running) "
              f"-> using {port} instead.")
    print(f"Course Assistant -> {url}")
    _maybe_open_browser(url)
    # Register courseassistant:// protocol handler so browser SSO callbacks reach
    # this server (Windows only; no-op elsewhere).
    os.environ["CA_PORT"] = str(port)
    try:
        from app import sso_protocol
        sso_protocol.register(port)
    except Exception:
        pass
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
