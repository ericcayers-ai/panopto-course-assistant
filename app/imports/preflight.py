"""
imports/preflight.py - validate an import before it runs (§7).

Before kicking off a (possibly long) import, surface: missing engines/deps,
oversized files, and the *expected output* (counts + target) so the user can
confirm. Pure inspection - never writes, never transcribes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .. import core, transcribe
from . import folder as folder_import

# Warn on individual files past this size (slow transcription / huge conversions).
_BIG_FILE_BYTES = 500 * 1024 * 1024          # 500 MB


def _has(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def preflight_folder(path: Path) -> Dict[str, Any]:
    """Inspect a folder import: counts, expected targets, and dependency/size
    warnings - everything the UI needs to confirm before starting."""
    manifest = folder_import.scan(path)
    warnings: List[str] = []
    deps: Dict[str, Any] = {}

    counts = manifest["counts"]
    if counts.get("media"):
        eng = transcribe.engine_status()
        ready = bool(eng.get("engines"))
        deps["transcription"] = {"required": True, "ready": ready}
        if not ready:
            warnings.append(f"{counts['media']} media file(s) need a transcription "
                            "engine (whisper/faster-whisper) - none detected.")
    if counts.get("document"):
        ready = _has("markitdown")
        deps["markitdown"] = {"required": True, "ready": ready}
        if not ready:
            warnings.append(f"{counts['document']} document(s) convert best with "
                            "markitdown - not installed (plain text still works).")

    big = [it for it in manifest["items"] if it["size"] > _BIG_FILE_BYTES]
    for it in big:
        warnings.append(f"Large file ({it['size'] // (1024*1024)} MB): {it['rel']}")

    return {
        "source": str(path), "ok": True,
        "expected_output": {
            "documents_indexed": counts.get("document", 0) + counts.get("subtitle", 0),
            "media_to_transcribe": counts.get("media", 0),
            "target_dir": core.DOCS_DIRNAME,
        },
        "counts": counts,
        "total_size_mb": round(manifest["total_size"] / (1024 * 1024), 1),
        "dependencies": deps,
        "warnings": warnings,
    }
