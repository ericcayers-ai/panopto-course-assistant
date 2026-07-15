"""Model cache lifecycle: download progress, checksums, attribution (no weights bundled)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .registry import MODELS, get_model


def default_cache_dir() -> Path:
    env = os.environ.get("PANOPTO_STT_CACHE") or os.environ.get("HF_HOME")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "panopto-stt"


def cache_size_bytes(root: Optional[Path] = None) -> int:
    root = root or default_cache_dir()
    if not root.exists():
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def list_cached_models(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = root or default_cache_dir()
    manifest = root / "manifest.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return list(data.get("models") or [])
    except Exception:
        return []


def _update_manifest(root: Path, entry: Dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    data = {"models": []}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"models": []}
    models = [m for m in (data.get("models") or [])
              if not (m.get("engine") == entry.get("engine") and m.get("model_id") == entry.get("model_id"))]
    models.append(entry)
    data["models"] = models
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def model_info(engine: str, model_id: str) -> Dict[str, Any]:
    spec = get_model(engine, model_id)
    if not spec:
        return {"engine": engine, "model_id": model_id, "known": False}
    d = spec.to_dict()
    d["known"] = True
    d["cached"] = any(
        m.get("engine") == engine and m.get("model_id") == model_id
        for m in list_cached_models()
    )
    return d


def verify_file_checksum(path: Path, sha256: str) -> bool:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower() == sha256.lower()


def record_download(
    engine: str,
    model_id: str,
    *,
    path: str = "",
    sha256: str = "",
    bytes_size: int = 0,
    license_accepted: bool = False,
) -> Dict[str, Any]:
    root = default_cache_dir()
    entry = {
        "engine": engine,
        "model_id": model_id,
        "path": path,
        "sha256": sha256,
        "bytes": bytes_size,
        "license_accepted": license_accepted,
    }
    _update_manifest(root, entry)
    return entry


def delete_model(engine: str, model_id: str) -> Dict[str, Any]:
    root = default_cache_dir()
    removed = []
    # HuggingFace hubs layout heuristic
    for candidate in (
        root / "hub" / f"models--{model_id.replace('/', '--')}",
        root / engine / model_id.replace("/", "__"),
    ):
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(str(candidate))
    remaining = [
        m for m in list_cached_models(root)
        if not (m.get("engine") == engine and m.get("model_id") == model_id)
    ]
    (root / "manifest.json").write_text(
        json.dumps({"models": remaining}, indent=2), encoding="utf-8"
    )
    return {"removed": removed, "engine": engine, "model_id": model_id}


def estimate_download(engine: str, model_id: str) -> Dict[str, Any]:
    spec = get_model(engine, model_id)
    if not spec:
        return {"engine": engine, "model_id": model_id, "disk_mb": 0, "known": False}
    return {
        "engine": engine,
        "model_id": model_id,
        "disk_mb": spec.disk_mb,
        "vram_mb": spec.vram_mb,
        "license": spec.license,
        "hf_repo": spec.hf_repo,
        "known": True,
        "attribution": f"{spec.display_name} ({spec.license})",
    }


def preflight_install(required_disk_mb: int = 2000) -> Dict[str, Any]:
    from .hardware import probe_hardware
    hw = probe_hardware()
    ok = hw.free_disk_mb == 0 or hw.free_disk_mb >= required_disk_mb
    return {
        "ok": ok,
        "ffmpeg": hw.ffmpeg,
        "cuda": hw.cuda,
        "vram_mb": hw.vram_mb,
        "ram_mb": hw.ram_mb,
        "free_disk_mb": hw.free_disk_mb,
        "gpu_name": hw.gpu_name,
        "warnings": [] if ok else [f"Need ~{required_disk_mb} MB free disk"],
    }
