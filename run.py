#!/usr/bin/env python3
"""Convenience launcher: `python run.py` -> http://127.0.0.1:8000

Works regardless of the current working directory (chdirs to its own folder so
the `app` package imports cleanly). Also serves as the PyInstaller entry point
for the portable .exe build.
"""
import os
import socket
import sys
from pathlib import Path

# --- frozen-exe path resolution ------------------------------------------
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    # PyInstaller onedir: sys._MEIPASS == directory containing the .exe.
    # Data files (static/) land there; output goes *next to* the exe so
    # the user's data survives app upgrades.
    HERE = Path(sys._MEIPASS)          # type: ignore[attr-defined]
    _EXE_DIR = Path(sys.executable).parent
    os.environ.setdefault("CA_STATIC_DIR", str(HERE / "static"))
    os.environ.setdefault("PANOPTO_OUTPUT", str(_EXE_DIR / "transcripts"))
    # Optional extras installed at first-run go here so they persist.
    _PACKAGES_DIR = _EXE_DIR / "_packages"
    if _PACKAGES_DIR.exists():
        sys.path.insert(0, str(_PACKAGES_DIR))
else:
    HERE = Path(__file__).resolve().parent

os.chdir(str(HERE))
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402  (after sys.path setup)


# ---------------------------------------------------------------------------
# First-run extras installer (frozen exe only)
# ---------------------------------------------------------------------------


def _ask_install_extras() -> bool:
    """Show a tkinter dialog asking whether to install the transcription extras."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(
            "Course Assistant — First Launch",
            "Would you like to install the optional transcription & document features?\n\n"
            "  • Lecture transcription  (faster-whisper, yt-dlp)\n"
            "  • Full document conversion  (PDF, PowerPoint, Word, Excel)\n\n"
            "Download size: ~300–500 MB  |  Takes a few minutes.\n\n"
            "You can always install later — select No to open the app now.",
        )
        root.destroy()
        return bool(answer)
    except Exception:
        return False


def _run_pip_install(target: Path, packages: list) -> bool:
    """pip-install packages into target/ using the bundled interpreter."""
    import subprocess

    target.mkdir(parents=True, exist_ok=True)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Installing…",
            "Installing extras — this will take a few minutes.\n"
            "The app will open automatically when done.",
        )
        root.destroy()
    except Exception:
        print("Installing optional extras, please wait…")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(target), "--no-deps"]
        + packages,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        # Try without --no-deps on failure (some packages need sub-deps)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(target)]
            + packages,
            capture_output=True,
            text=True,
            timeout=600,
        )
    return result.returncode == 0


def _first_run_extras_check() -> None:
    """On the very first launch as a frozen exe, offer to install the extras."""
    if not _FROZEN:
        return
    marker = _EXE_DIR / ".extras_asked"
    if marker.exists():
        return
    marker.write_text("1", encoding="utf-8")

    if not _ask_install_extras():
        return

    packages = [
        "faster-whisper>=1.0",
        "yt-dlp>=2024.1",
        "markitdown[all]>=0.0.1a2",
    ]
    ok = _run_pip_install(_PACKAGES_DIR, packages)
    if ok:
        sys.path.insert(0, str(_PACKAGES_DIR))
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if ok:
            messagebox.showinfo(
                "Done",
                "Extras installed!\n\n"
                "Tip: for AI-powered flashcards and summaries, install Ollama\n"
                "(ollama.com), then run:  ollama pull llama3",
            )
        else:
            messagebox.showwarning(
                "Partial install",
                "Some extras could not be installed automatically.\n"
                "You can try again by running install-extras-windows.bat\n"
                "from the app folder.",
            )
        root.destroy()
    except Exception:
        pass


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
    _first_run_extras_check()

    host = os.environ.get("HOST", "127.0.0.1")
    requested = int(os.environ.get("PORT", "8000"))
    port = _free_port(host, requested)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    if port != requested:
        print(f"Port {requested} is in use (another copy may already be running) "
              f"-> using {port} instead.")
    print(f"Course Assistant -> {url}")
    _maybe_open_browser(url)
    # Register courseassistant:// protocol handler so SSO callbacks reach this server.
    os.environ["CA_PORT"] = str(port)
    try:
        from app import sso_protocol
        sso_protocol.register(port)
    except Exception:
        pass
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
