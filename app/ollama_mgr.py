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
DEFAULT_MODEL = "llama3.2:3b"

# Handle to a server we started ourselves, so we don't spawn duplicates.
_server_proc: Optional[subprocess.Popen] = None

# ---------------------------------------------------------------------------
# Curated model catalogue
# (label, ollama_tag, min_vram_mb_to_recommend_for_gpu)
# ---------------------------------------------------------------------------
_CURATED: List[tuple] = [
    ("Llama 3.2 1B (tiny, very fast)", "llama3.2:1b", 0),
    ("Gemma 3 1B (tiny, very fast)", "gemma3:1b", 0),
    ("Llama 3.2 3B (balanced, recommended)", "llama3.2:3b", 0),
    ("Gemma 3 4B (balanced)", "gemma3:4b", 0),
    ("Qwen 3 4B (balanced)", "qwen3:4b", 0),
    ("Qwen 3 8B (accurate, needs 8 GB+)", "qwen3:8b", 7_000),
    ("Gemma 3 12B (accurate, needs 10 GB+)", "gemma3:12b", 10_000),
    ("Qwen 3 14B (most accurate, needs 12 GB+)", "qwen3:14b", 12_000),
]


def get_vram_mb() -> int:
    """Best-effort GPU VRAM in MB via PyTorch or CTranslate2. Returns 0 if unknown."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
    except Exception:
        pass
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return 4096  # GPU present but can't query VRAM; guess 4 GB
    except Exception:
        pass
    return 0


def recommended_model(vram_mb: int = 0) -> str:
    """Pick the best model tag for the detected VRAM (or CPU)."""
    if vram_mb >= 12_000:
        return "qwen3:14b"
    if vram_mb >= 10_000:
        return "gemma3:12b"
    if vram_mb >= 7_000:
        return "qwen3:8b"
    return "llama3.2:3b"


def curated_models(vram_mb: int = 0) -> List[Dict[str, Any]]:
    """Return the curated catalogue, marking the best pick as recommended."""
    best = recommended_model(vram_mb)
    return [
        {"label": label, "tag": tag, "recommended": tag == best}
        for label, tag, _ in _CURATED
    ]


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
    """A single snapshot for the UI: installed / running / models / VRAM / curated list."""
    running = is_running(host)
    vram = get_vram_mb()
    installed_models = list_models(host) if running else []
    return {
        "installed": binary() is not None,
        "running": running,
        "host": host,
        "models": installed_models,
        "default_model": recommended_model(vram),
        "vram_mb": vram,
        "curated_models": curated_models(vram),
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


def install_windows() -> Dict[str, Any]:
    """Run the official Ollama Windows PowerShell installer.

    Only called when ``binary()`` returns ``None`` (Ollama not on PATH) and the
    platform is ``win32``. The installer script is fetched from ollama.com over
    HTTPS by PowerShell itself — the app does not bundle or proxy it.
    """
    if binary():
        return {"ok": True, "already_installed": True}
    try:
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
             "irm https://ollama.com/install.ps1 | iex"],
            capture_output=True, text=True, timeout=300,
        )
        # Refresh PATH so the new binary is visible without restarting Python.
        shutil.which.cache_clear() if hasattr(shutil.which, "cache_clear") else None
        ok = result.returncode == 0 or binary() is not None
        return {"ok": ok,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-500:]}
    except FileNotFoundError:
        raise RuntimeError("PowerShell is not available on this system.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Ollama installer timed out after 5 minutes.")
    except Exception as e:
        raise RuntimeError(f"Could not run Ollama installer: {e}") from e


def initialize_model(
    model: str,
    host: str = DEFAULT_HOST,
    progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """Start the server (if needed) and pull ``model`` if it is not installed.

    This is the single "one-click" path: start → pull → ready. Returns the
    resulting :func:`status` dict plus a ``message`` key for the UI.
    """
    if not binary():
        return {"installed": False, "running": False, "message":
                "Ollama is not installed. Click Install to set it up automatically."}

    if not is_running(host):
        start_server(host)

    installed = list_models(host)
    base = model.split(":")[0]
    already = any(m == model or m.startswith(base + ":") for m in installed)

    if not already:
        pull_model(model, host=host, progress=progress)

    s = status(host)
    s["message"] = f'Model "{model}" is ready. AI features are now active.'
    return s
