"""
jobs.py — a tiny in-memory background job manager.

Transcription is long-running and heavy (it downloads media and loads a Whisper
model), so the API queues jobs and a small pool of **worker threads** drains the
queue while the frontend polls ``/api/jobs``. State lives in memory only (fine
for a single-user local tool); restarting the server clears the job list.

Jobs run **serially by default** (one worker). Submitting a whole feed used to
spawn a thread per lecture — a dozen simultaneous downloads and Whisper model
loads that exhausted RAM/VRAM, froze the desktop, and could crash the process.
A single worker keeps exactly one transcription in flight; the rest wait their
turn as ``queued``. Override with ``PANOPTO_WORKERS`` if you know you can spare
the resources.
"""
from __future__ import annotations

import os
import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from .core import now_iso


def _lower_process_priority() -> None:
    """Best-effort: drop to below-normal priority so the GUI stays responsive
    while a job pegs the CPU/GPU. No-op off Windows or if it isn't permitted."""
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)  # POSIX
    except Exception:
        pass


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


def _worker_count() -> int:
    try:
        return max(1, int(os.environ.get("PANOPTO_WORKERS", "1")))
    except Exception:
        return 1


class JobManager:
    def __init__(self, workers: int | None = None) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[Tuple[Job, Callable]]" = queue.Queue()
        self._workers = workers if workers is not None else _worker_count()
        self._nice = os.environ.get("PANOPTO_NICE", "1").lower() not in ("0", "false", "no")
        self._started = False

    def _ensure_workers(self) -> None:
        """Spin up the worker pool on first submit (lazy, so importing the app
        never starts threads — keeps the test suite and tooling clean)."""
        with self._lock:
            if self._started:
                return
            self._started = True
        if self._nice:
            _lower_process_priority()
        for i in range(self._workers):
            threading.Thread(target=self._worker_loop, name=f"job-worker-{i}", daemon=True).start()

    def submit(self, title: str, fn: Callable[[Callable[[str, float], None]], Dict[str, Any]]) -> Job:
        """Enqueue ``fn(progress_cb)``; a worker runs it when one is free.

        Returns immediately with a ``queued`` Job handle. With the default single
        worker, jobs run one at a time in submission order.
        """
        job = Job(id=uuid.uuid4().hex[:12], title=title)
        with self._lock:
            self._jobs[job.id] = job
        self._ensure_workers()
        self._queue.put((job, fn))
        return job

    def _worker_loop(self) -> None:
        while True:
            job, fn = self._queue.get()
            try:
                self._run(job, fn)
            except Exception:  # never let a bad job kill the worker
                pass
            finally:
                self._queue.task_done()

    def _run(self, job: Job, fn: Callable) -> None:
        def progress_cb(stage: str, frac: float) -> None:
            with self._lock:
                job.stage = stage
                job.progress = float(frac)
                job.updated_at = now_iso()

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

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )]


manager = JobManager()
