"""DB-backed job persistence (§1): jobs are written to the database, the list
reads back from it, and a 'restart' recovers jobs left running by a crash."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.database import Database
from app.jobs import JobManager


def _wait(job, mgr, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        j = mgr.get(job.id)
        if j and j.status in ("done", "error"):
            return j
        time.sleep(0.02)
    return mgr.get(job.id)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def test_completed_job_is_persisted(db: Database):
    mgr = JobManager(db=db)
    job = mgr.submit("t", lambda p: {"value": 42}, type="transcribe", course_id=None)
    done = _wait(job, mgr)
    assert done.status == "done"
    row = db.get_job(job.id)
    assert row["status"] == "done"
    assert row["type"] == "transcribe"
    assert '"value": 42' in row["result_json"]
    assert row["attempts"] == 1


def test_list_reads_from_db_and_survives_a_restart(db: Database):
    mgr = JobManager(db=db)
    j = mgr.submit("keep-me", lambda p: {})
    _wait(j, mgr)
    # a brand-new manager (simulating a process restart) bound to the same DB
    # still sees the job - it lives in the database, not memory.
    restarted = JobManager()
    restarted.bind(db)
    listed = restarted.list()
    assert any(item["id"] == j.id and item["title"] == "keep-me" for item in listed)
    assert restarted.get(j.id).status == "done"


def test_bind_recovers_interrupted_jobs(db: Database):
    ts = "2026-06-16T00:00:00+00:00"
    db.insert_job("crashed", "transcribe", "C", "running", "downloading", 0.4, "{}",
                  None, ts, ts)
    mgr = JobManager()
    mgr.bind(db)                                     # restart recovery on bind
    assert mgr.get("crashed").status == "interrupted"


def test_started_at_is_set_on_run_and_persisted(db: Database):
    # started_at drives the Jobs-tab ETA: it must be NULL until a worker picks the
    # job up, then stamped once it runs - in memory, in the DB, and in to_dict().
    mgr = JobManager(db=db)
    job = mgr.submit("eta", lambda p: {})
    done = _wait(job, mgr)
    assert done.status == "done"
    assert done.started_at, "started_at should be stamped once the job runs"
    assert done.to_dict()["started_at"] == done.started_at
    row = db.get_job(job.id)
    assert row["started_at"] == done.started_at


def test_queued_job_has_no_started_at(db: Database):
    # A job that hasn't run yet (inserted as queued) carries a NULL started_at,
    # so the frontend ETA correctly shows nothing for it.
    ts = "2026-06-16T00:00:00+00:00"
    db.insert_job("waiting", "transcribe", "W", "queued", "", 0.0, "{}", None, ts, ts)
    assert db.get_job("waiting")["started_at"] is None
    mgr = JobManager()
    mgr.bind(db)
    assert mgr.get("waiting").started_at is None


def test_progress_callback_accepts_stage_only(db: Database):
    # AI jobs (flashcards, categorize, study CSV) report a stage label with no
    # percentage: progress(stage) must work, not just progress(stage, frac).
    mgr = JobManager(db=db)

    def work(progress):
        progress("Generating flashcards with LLM...")   # one-arg call
        progress("Finalizing", 0.5)                      # two-arg still works
        return {"cards": 3}

    done = _wait(mgr.submit("flashcards", work), mgr)
    assert done.status == "done"
    assert done.result == {"cards": 3}


def test_manager_without_db_is_pure_memory(db: Database):
    # unchanged legacy behaviour: no DB bound -> nothing persisted
    mgr = JobManager()
    j = mgr.submit("mem", lambda p: {})
    _wait(j, mgr)
    assert db.get_job(j.id) is None
    assert mgr.get(j.id).status == "done"
