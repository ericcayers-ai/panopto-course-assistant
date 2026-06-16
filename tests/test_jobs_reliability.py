"""§3 job reliability: failure classification, cooperative cancel, retry, and
persisted logs."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.database import Database
from app.jobs import JobManager, classify_failure


def _wait(job, mgr, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        j = mgr.get(job.id)
        if j and j.status in ("done", "error", "canceled"):
            return j
        time.sleep(0.02)
    return mgr.get(job.id)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def test_classify_failure_buckets():
    assert classify_failure(ConnectionError("connection reset by peer")) == "network"
    assert classify_failure(TimeoutError("timed out")) == "network"
    assert classify_failure(ImportError("yt-dlp is not installed")) == "dependency"
    assert classify_failure(FileNotFoundError("nope")) == "filesystem"
    assert classify_failure(ValueError("could not parse feed")) == "invalid_source"
    assert classify_failure(Exception("server said 401 unauthorized")) == "authentication"
    assert classify_failure(Exception("something odd")) == "unknown"


def test_failed_job_records_category_and_logs(db: Database):
    mgr = JobManager(db=db)

    def boom(p):
        p("connecting", 0.1)
        raise ConnectionError("connection reset by peer")

    j = mgr.submit("t", boom, type="transcribe")
    done = _wait(j, mgr)
    assert done.status == "error"
    assert done.failure_category == "network"
    assert done.to_dict()["retryable"] is True
    logs = mgr.logs(j.id)
    assert "started" in logs and "error [network]" in logs


def test_cancel_queued_job_before_it_starts(db: Database):
    mgr = JobManager(workers=1, db=db)
    started, release = threading.Event(), threading.Event()

    def slow(p):
        started.set()
        release.wait(2)
        return {}

    j1 = mgr.submit("slow", slow)
    j2 = mgr.submit("queued", lambda p: {})
    assert started.wait(2)                 # j1 holds the single worker
    assert mgr.cancel(j2.id) is True       # cancel j2 while it waits in the queue
    release.set()
    assert _wait(j2, mgr).status == "canceled"


def test_cancel_running_job_at_a_checkpoint(db: Database):
    mgr = JobManager(workers=1, db=db)
    started = threading.Event()

    def work(p):
        p("step", 0.1)
        started.set()
        for _ in range(200):               # keeps hitting progress checkpoints
            time.sleep(0.01)
            p("step", 0.2)
        return {}

    j = mgr.submit("c", work)
    assert started.wait(2)
    assert mgr.cancel(j.id) is True
    assert _wait(j, mgr).status == "canceled"


def test_retry_reruns_under_same_id(db: Database):
    mgr = JobManager(db=db)
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("network blip")
        return {"ok": True}

    j = mgr.submit("r", flaky, type="transcribe")
    assert _wait(j, mgr).status == "error"
    retried = mgr.retry(j.id, flaky)
    assert retried is not None and retried.id == j.id
    done = _wait(j, mgr)
    assert done.status == "done"
    assert done.attempts == 2
    assert done.result["ok"] is True


def test_cannot_cancel_a_finished_job(db: Database):
    mgr = JobManager(db=db)
    j = mgr.submit("x", lambda p: {})
    _wait(j, mgr)
    assert mgr.cancel(j.id) is False       # already done -> nothing to cancel


def test_dead_letter_query_lists_failed_jobs(db: Database):
    mgr = JobManager(db=db)
    j = mgr.submit("x", lambda p: (_ for _ in ()).throw(RuntimeError("boom")),
                   type="transcribe")
    _wait(j, mgr)
    failed = db.list_jobs_by_status("error")
    assert any(r["id"] == j.id for r in failed)
