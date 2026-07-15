"""Atomic chunk checkpoints for resumable long-form STT."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chunking import ChunkPlan
from .types import Segment, STTResult


PARTIAL_SUFFIX = ".stt.partial.json"


def partial_path(out_dir: Path, stem: str) -> Path:
    return out_dir / f"{stem}{PARTIAL_SUFFIX}"


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load_checkpoint(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def init_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    settings_hash: str,
    chunks: List[ChunkPlan],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = {
        "version": 1,
        "fingerprint": fingerprint,
        "settings_hash": settings_hash,
        "chunks": [c.to_dict() for c in chunks],
        "completed": {},  # index -> segment list
        "meta": meta or {},
    }
    _atomic_write(path, data)
    return data


def save_chunk(
    path: Path,
    chunk_index: int,
    segments: List[Segment],
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = load_checkpoint(path) or {"version": 1, "completed": {}, "chunks": [], "meta": {}}
    completed = dict(data.get("completed") or {})
    completed[str(chunk_index)] = {
        "segments": [s.to_dict() for s in segments],
        **(extra or {}),
    }
    data["completed"] = completed
    _atomic_write(path, data)
    return data


def first_missing_chunk(data: Dict[str, Any]) -> Optional[int]:
    chunks = data.get("chunks") or []
    completed = data.get("completed") or {}
    for ch in chunks:
        idx = int(ch["index"])
        if str(idx) not in completed:
            return idx
    return None


def completed_pairs(data: Dict[str, Any]) -> List[tuple]:
    from .chunking import ChunkPlan
    chunks = {int(c["index"]): ChunkPlan(**{
        "index": int(c["index"]),
        "start": float(c["start"]),
        "end": float(c["end"]),
        "overlap_prev": float(c.get("overlap_prev") or 0.0),
    }) for c in data.get("chunks") or []}
    pairs = []
    completed = data.get("completed") or {}
    for key in sorted(completed.keys(), key=lambda x: int(x)):
        idx = int(key)
        plan = chunks.get(idx)
        if not plan:
            continue
        segs = [Segment.from_dict(s) for s in (completed[key].get("segments") or [])]
        pairs.append((plan, segs))
    return pairs


def clear_checkpoint(path: Path) -> None:
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def settings_fingerprint(settings: Dict[str, Any]) -> str:
    import hashlib
    blob = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
