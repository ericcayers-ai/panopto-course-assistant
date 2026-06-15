"""
jobs.py — a tiny in-memory background job manager.

Transcription is long-running, so the API kicks jobs off on a background thread
and the frontend polls ``/api/jobs``. State lives in memory only (fine for a
single-user local tool); restarting the server clears the job list.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from .core import now_iso


@dataclass
class Job:
    id: str
    title: str
    status: str = "queued"  # queued | running | done | error
    stage: str = ""
    progress: float = 0.0
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, title: str, fn: Callable[[Callable[[str, float], None]], Dict[str, Any]]) -> Job:
        """Run ``fn(progress_cb)`` on a background thread; return the Job handle."""
        job = Job(id=uuid.uuid4().hex[:12], title=title)
        with self._lock:
            self._jobs[job.id] = job

        def progress_cb(stage: str, frac: float) -> None:
            with self._lock:
                job.stage = stage
                job.progress = float(frac)
                job.updated_at = now_iso()

        def runner() -> None:
            with self._lock:
                job.status = "running"
                job.updated_at = now_iso()
            try:
                result = fn(progress_cb)
                with self._lock:
                    job.status = "done"
                    job.progress = 1.0
                    job.stage = "done"
                    job.result = result or {}
                    job.updated_at = now_iso()
            except Exception as e:
                with self._lock:
                    job.status = "error"
                    job.error = f"{e}\n{traceback.format_exc()}"
                    job.updated_at = now_iso()

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )]


manager = JobManager()
