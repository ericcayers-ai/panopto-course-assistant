"""§13 analytics: local-only stats, funnel, failure insights, safe diagnostics."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app import analytics, database


@pytest.fixture()
def db(tmp_path: Path):
    d = database.Database(tmp_path / "t.db")
    cid = d.create_course("C")
    # two transcribe jobs: one done, one failed (network)
    d.insert_job("j1", "transcribe", "L1", "completed", "", 1.0, "", cid,
                 "2026-06-01T10:00:00+00:00", "2026-06-01T10:05:00+00:00")
    d.insert_job("j2", "transcribe", "L2", "error", "", 0.0, "", cid,
                 "2026-06-01T11:00:00+00:00", "2026-06-01T11:01:00+00:00")
    d.update_job("j2", failure_category="network")
    d.insert_document(cid, "Doc", "Week1/doc.pdf", "document", "folder")
    d.execute("INSERT INTO exports(course_id, type, path, created_at) VALUES(?,?,?,?)",
              (cid, "revision:notebooklm", "_notebooklm/course_pack.md", "2026-06-02T09:00:00+00:00"))
    yield d
    d.close()


def test_no_network_imports_in_module():
    src = inspect.getsource(analytics)
    for bad in ("import requests", "urllib.request", "http.client", "socket"):
        assert bad not in src, f"analytics must not use the network ({bad})"


def test_usage_funnel_and_failures(db):
    stats = analytics.compute(db, course_id=1)
    assert stats["usage"]["transcriptions"] == 2
    assert stats["usage"]["exports"] == 1
    assert stats["failures_by_category"] == {"network": 1}
    # funnel: 1 doc imported, 1 transcribe completed, 1 export
    assert stats["funnel"]["transcribed"] == 1
    assert stats["funnel"]["exported"] == 1
    # throughput percentile computed from the 5-min completed job (300s)
    assert stats["throughput_seconds"]["max"] == 300.0


def test_feedback_prompt_only_after_repeated_failures(db):
    stats = analytics.compute(db, course_id=1)
    assert analytics.feedback_prompt(stats) is None      # only 1 failure
    # add two more network failures -> prompt fires
    for i in range(3, 5):
        db.insert_job(f"jx{i}", "transcribe", "L", "error", "", 0.0, "", 1,
                      "2026-06-03T10:00:00+00:00", "2026-06-03T10:01:00+00:00")
        db.update_job(f"jx{i}", failure_category="network")
    stats2 = analytics.compute(db, course_id=1)
    assert "network" in (analytics.feedback_prompt(stats2) or "").lower()


def test_diagnostics_export_has_no_pii(db, tmp_path):
    out = analytics.diagnostics_export(db, tmp_path, course_id=1)
    blob = (tmp_path / out["path"]).read_text()
    # aggregate-only: no file paths, titles, or course names leak
    assert "doc.pdf" not in blob and "course_pack.md" not in blob
    assert out["anonymised"] is True
    assert "usage" in out["contents"] and "funnel" in out["contents"]
