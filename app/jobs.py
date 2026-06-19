"""
jobs.py - background job manager (§1: now DB-backed for restart durability).

Transcription is long-running and heavy (it downloads media and loads a Whisper
model), so the API queues jobs and a small pool of **worker threads** drains the
queue while the frontend polls ``/api/jobs``. The pool runs a few jobs at once so
downloads, document conversion and exports overlap instead of waiting in line.
The one genuinely memory-hungry step, running a Whisper model, is serialised by a
semaphore in ``transcribe.py`` so several lectures can download in parallel while
only one is transcribed at a time, which keeps GPU and RAM use safe.

Persistence (§1)
----------------
When a ``Database`` is bound via :meth:`JobManager.bind` (done by ``app.main``
after ``database.init``), every state change is written to the ``jobs`` table, so
the job list and history **survive a restart**. On bind, any job left
``running``/``queued`` by a crash is marked ``interrupted`` (a known state the
user can resume - §3 hardens auto-resume).

With no database bound (the direct ``JobManager()`` used in unit tests) the
manager behaves exactly as before: pure in-memory, lost on restart.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import now_iso
from .database import Database


class JobCancelled(Exception):
    """Raised inside a job's progress callback when a cancel was requested, so the
    work unwinds cooperatively rather than being killed mid-write."""


def classify_failure(exc: BaseException) -> str:
    """Bucket an exception into a §3 failure category (drives UI hints + retry).

    Heuristic: exception *type* first, then keywords in the message - good enough
    to tell a flaky network apart from a missing dependency or a bad input."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if isinstance(exc, (ImportError, ModuleNotFoundError)) or "not installed" in msg \
            or "ffmpeg" in msg:
        return "dependency"
    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError)) \
            or "no space" in msg or "disk full" in msg or "permission denied" in msg:
        return "filesystem"
    if any(k in msg for k in ("unauthorized", "forbidden", "401", "403",
                              "authentication", "cookie")):
        return "authentication"
    if any(k in name.lower() for k in ("timeout", "connection")) \
            or any(k in msg for k in ("timed out", "connection", "network",
                                      "temporarily unavailable", "max retries")):
        return "network"
    if isinstance(exc, ValueError) or "could not parse" in msg or "no media url" in msg \
            or "invalid" in msg:
        return "invalid_source"
    return "unknown"


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
    type: str = "job"
    status: str = "queued"  # queued | running | done | error | interrupted | canceled
    stage: str = ""
    progress: float = 0.0
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    attempts: int = 0
    course_id: Optional[int] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    failure_category: str = ""
    logs: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    started_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "course_id": self.course_id,
            "failure_category": self.failure_category,
            "retryable": self.status in ("error", "interrupted", "canceled"),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
        }

    @classmethod
    def from_row(cls, row) -> "Job":
        def _load(raw: str) -> Dict[str, Any]:
            try:
                return json.loads(raw) if raw else {}
            except Exception:
                return {}

        keys = row.keys()
        return cls(
            id=row["id"], title=row["title"], type=row["type"], status=row["status"],
            stage=row["stage"] or "", progress=float(row["progress"] or 0.0),
            result=_load(row["result_json"]), error=row["error"] or "",
            attempts=int(row["attempts"] or 0), course_id=row["course_id"],
            payload=_load(row["payload_json"]),
            failure_category=(row["failure_category"] if "failure_category" in keys else "") or "",
            logs=(row["logs"] if "logs" in keys else "") or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
            started_at=(row["started_at"] if "started_at" in keys else None) or None,
        )


def _worker_count() -> int:
    """How many jobs run at once. Defaults to a small parallel pool so light work
    (exports, document conversion, CSV) and downloads overlap instead of queueing
    behind one another. Heavy transcription is kept safe separately by a semaphore
    in ``transcribe.py``. Override with PANOPTO_WORKERS."""
    env = os.environ.get("PANOPTO_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    cpu = os.cpu_count() or 4
    return min(4, max(2, cpu // 2))


class JobManager:
    def __init__(self, workers: int | None = None, db: Optional[Database] = None) -> None:
        self._jobs: Dict[str, Job] = {}
        # Reentrant: progress_cb holds the lock and calls _log(), which re-acquires it.
        self._lock = threading.RLock()
        self._queue: "queue.Queue[Tuple[Job, Callable]]" = queue.Queue()
        self._workers = workers if workers is not None else _worker_count()
        self._nice = os.environ.get("PANOPTO_NICE", "1").lower() not in ("0", "false", "no")
        self._started = False
        self._db = db

    # -- persistence wiring -------------------------------------------------

    def bind(self, db: Optional[Database]) -> None:
        """Attach (or replace) the durable store and recover crashed jobs.

        Called by ``app.main`` after ``database.init``. Re-callable (the test
        suite reloads the app against a fresh temp DB)."""
        self._db = db
        if db is not None:
            try:
                db.recover_running_jobs()
            except Exception:
                pass

    def _persist_insert(self, job: Job) -> None:
        if self._db is None:
            return
        try:
            self._db.insert_job(
                id=job.id, type=job.type, title=job.title, status=job.status,
                stage=job.stage, progress=job.progress,
                payload_json=json.dumps(job.payload or {}), course_id=job.course_id,
                created_at=job.created_at, updated_at=job.updated_at,
            )
        except Exception:
            pass

    def _persist_update(self, job: Job) -> None:
        if self._db is None:
            return
        try:
            self._db.update_job(
                job.id, status=job.status, stage=job.stage, progress=job.progress,
                result_json=json.dumps(job.result or {}), error=job.error,
                attempts=job.attempts, failure_category=job.failure_category,
                updated_at=job.updated_at, started_at=job.started_at,
            )
        except Exception:
            pass

    def _log(self, job: Job, line: str) -> None:
        """Append a human-readable log line (persisted per job for §3)."""
        stamped = f"[{now_iso()}] {line}"
        with self._lock:
            job.logs = (job.logs + "\n" + stamped) if job.logs else stamped
        if self._db is not None:
            try:
                self._db.append_job_log(job.id, line)
            except Exception:
                pass

    def _is_cancelled(self, job: Job) -> bool:
        if self._db is not None:
            try:
                return self._db.cancel_requested(job.id)
            except Exception:
                pass
        return getattr(job, "_cancel", False)

    # -- worker pool --------------------------------------------------------

    def _ensure_workers(self) -> None:
        """Spin up the worker pool on first submit (lazy, so importing the app
        never starts threads - keeps the test suite and tooling clean)."""
        with self._lock:
            if self._started:
                return
            self._started = True
        if self._nice:
            _lower_process_priority()
        for i in range(self._workers):
            threading.Thread(target=self._worker_loop, name=f"job-worker-{i}", daemon=True).start()

    def submit(self, title: str, fn: Callable[[Callable[[str, float], None]], Dict[str, Any]],
               *, type: str = "job", payload: Optional[Dict[str, Any]] = None,
               course_id: Optional[int] = None) -> Job:
        """Enqueue ``fn(progress_cb)``; a worker runs it when one is free.

        Returns immediately with a ``queued`` Job handle. With the default single
        worker, jobs run one at a time in submission order.
        """
        job = Job(id=uuid.uuid4().hex[:12], title=title, type=type,
                  payload=payload or {}, course_id=course_id)
        with self._lock:
            self._jobs[job.id] = job
        self._persist_insert(job)
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
            # Cooperative cancellation: each progress tick is a checkpoint where we
            # can bail out cleanly instead of being killed mid-write.
            if self._is_cancelled(job):
                raise JobCancelled()
            with self._lock:
                if stage and stage != job.stage:
                    self._log(job, f"stage: {stage}")
                job.stage = stage
                job.progress = float(frac)
                job.updated_at = now_iso()
            self._persist_update(job)

        # A queued job that was cancelled before a worker picked it up never runs.
        if self._is_cancelled(job):
            with self._lock:
                job.status = "canceled"
                job.updated_at = now_iso()
            self._log(job, "canceled before start")
            self._persist_update(job)
            return

        with self._lock:
            job.status = "running"
            job.attempts += 1
            job.failure_category = ""
            job.error = ""
            job.started_at = now_iso()
            job.updated_at = job.started_at
        self._log(job, f"started (attempt {job.attempts})")
        self._persist_update(job)
        try:
            result = fn(progress_cb)
            with self._lock:
                job.status = "done"
                job.progress = 1.0
                job.stage = "done"
                job.result = result or {}
                job.updated_at = now_iso()
            self._log(job, "done")
        except JobCancelled:
            with self._lock:
                job.status = "canceled"
                job.updated_at = now_iso()
            self._log(job, "canceled")
        except Exception as e:
            category = classify_failure(e)
            with self._lock:
                job.status = "error"
                job.failure_category = category
                job.error = f"{e}\n{traceback.format_exc()}"
                job.updated_at = now_iso()
            self._log(job, f"error [{category}]: {e}")
        self._persist_update(job)

    # -- reads --------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            live = self._jobs.get(job_id)
        if live is not None:
            return live
        if self._db is not None:
            row = self._db.get_job(job_id)
            if row is not None:
                return Job.from_row(row)
        return None

    def list(self) -> List[Dict[str, Any]]:
        if self._db is not None:
            # DB is kept current on every state change, so it reflects live jobs
            # *and* history that survived a restart.
            return [Job.from_row(r).to_dict() for r in self._db.list_jobs()]
        with self._lock:
            return [j.to_dict() for j in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )]

    def logs(self, job_id: str) -> Optional[str]:
        with self._lock:
            live = self._jobs.get(job_id)
        if live is not None:
            return live.logs
        if self._db is not None:
            row = self._db.get_job(job_id)
            if row is not None and "logs" in row.keys():
                return row["logs"] or ""
        return None

    # -- controls (§3) ------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        """Request cooperative cancellation. A running job stops at its next
        progress checkpoint; a queued job is skipped when its worker reaches it."""
        job = self.get(job_id)
        if job is None or job.status in ("done", "error", "canceled"):
            return False
        with self._lock:
            live = self._jobs.get(job_id)
            if live is not None:
                live._cancel = True  # type: ignore[attr-defined]
        if self._db is not None:
            try:
                self._db.request_cancel(job_id)
            except Exception:
                pass
        return True

    def retry(self, job_id: str, fn: Callable) -> Optional[Job]:
        """Re-run a finished/failed/canceled job under its existing id, keeping its
        history and logs. ``fn`` is rebuilt by the caller from the job's payload."""
        job = self.get(job_id)
        if job is None or job.status not in ("error", "interrupted", "canceled", "done"):
            return None
        with self._lock:
            job.status = "queued"
            job.error = ""
            job.failure_category = ""
            job.stage = ""
            job.progress = 0.0
            if hasattr(job, "_cancel"):
                job._cancel = False  # type: ignore[attr-defined]
            job.updated_at = now_iso()
            self._jobs[job.id] = job
        if self._db is not None:
            try:
                self._db.update_job(job.id, status="queued", error="", failure_category="",
                                    stage="", progress=0.0, updated_at=now_iso())
                self._db.execute("UPDATE jobs SET cancel_requested=0 WHERE id=?", (job.id,))
            except Exception:
                pass
        self._log(job, "retry requested")
        self._ensure_workers()
        self._queue.put((job, fn))
        return job


manager = JobManager()
