"""
ollama_mgr.py - run a local Ollama server from inside the app.

Ollama is the simplest way to give the optional AI features a fully local model
with no API key and no data leaving the machine. Rather than asking the user to
open a terminal, this module detects an installed Ollama, starts its server when
needed, lists and pulls models, and reports status to the UI.

Network access is confined to ``127.0.0.1`` (the local Ollama server). If Ollama
is not installed the functions report that clearly so the UI can link the user to
the official installer - the binary itself is not bundled.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3"

# Handle to a server we started ourselves, so we don't spawn duplicates.
_server_proc: Optional[subprocess.Popen] = None


def binary() -> Optional[str]:
    """Path to the installed ``ollama`` executable, or ``None``."""
    return shutil.which("ollama")


def is_running(host: str = DEFAULT_HOST, timeout: float = 1.5) -> bool:
    """Whether an Ollama server answers on ``host``."""
    import requests
    try:
        r = requests.get(host.rstrip("/") + "/api/version", timeout=timeout)
        return r.ok
    except Exception:
        return False


def list_models(host: str = DEFAULT_HOST) -> List[str]:
    """Model names available on the running server (empty if unreachable)."""
    import requests
    try:
        r = requests.get(host.rstrip("/") + "/api/tags", timeout=3)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return []


def status(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """A single snapshot for the UI: installed / running / models."""
    running = is_running(host)
    return {
        "installed": binary() is not None,
        "running": running,
        "host": host,
        "models": list_models(host) if running else [],
        "default_model": DEFAULT_MODEL,
        "install_url": "https://ollama.com/download",
    }


def start_server(host: str = DEFAULT_HOST, wait_seconds: float = 20.0) -> Dict[str, Any]:
    """Start ``ollama serve`` if it is installed and not already running.

    Returns the resulting :func:`status`. Raises ``RuntimeError`` if Ollama is not
    installed so the caller can surface an actionable message.
    """
    global _server_proc
    if is_running(host):
        return status(host)
    exe = binary()
    if not exe:
        raise RuntimeError(
            "Ollama is not installed. Install it from https://ollama.com/download, "
            "then start it from here.")
    # Detach so the server outlives this request; suppress its console window on Windows.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        _server_proc = subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        raise RuntimeError(f"Could not start Ollama: {e}") from e
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_running(host):
            break
        time.sleep(0.5)
    return status(host)


def pull_model(name: str, host: str = DEFAULT_HOST,
               progress: Optional[Callable[[str, float], None]] = None) -> Dict[str, Any]:
    """Download a model into the running server, reporting progress.

    Streams Ollama's NDJSON pull events; ``progress(stage, fraction)`` is called as
    layers download so a job can show a live bar.
    """
    import requests
    name = (name or "").strip()
    if not name:
        raise ValueError("No model name given.")
    if not is_running(host):
        start_server(host)
    url = host.rstrip("/") + "/api/pull"
    with requests.post(url, json={"name": name, "stream": True}, stream=True, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("error"):
                raise RuntimeError(str(evt["error"]))
            total = evt.get("total") or 0
            completed = evt.get("completed") or 0
            frac = (completed / total) if total else 0.0
            if progress:
                progress(evt.get("status", "pulling"), min(0.99, float(frac)))
    if progress:
        progress("done", 1.0)
    return {"model": name, "models": list_models(host)}
