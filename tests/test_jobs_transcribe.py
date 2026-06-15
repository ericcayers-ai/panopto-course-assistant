"""Tests for the background job manager and the transcribe control flow
(skip-existing / force / output filtering) — without the heavy ASR deps."""
from __future__ import annotations

import time
from pathlib import Path

from app import core
from app.jobs import JobManager
from app import transcribe


# ---------------------------------------------------------------------------
# JobManager lifecycle
# ---------------------------------------------------------------------------

def _wait(job, mgr, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        j = mgr.get(job.id)
        if j.status in ("done", "error"):
            return j
        time.sleep(0.02)
    return mgr.get(job.id)


def test_job_runs_to_completion_with_progress():
    mgr = JobManager()
    seen = []

    def work(progress):
        progress("step", 0.5)
        seen.append(0.5)
        return {"status": "done", "value": 42}

    job = mgr.submit("t", work)
    done = _wait(job, mgr)
    assert done.status == "done"
    assert done.progress == 1.0
    assert done.result["value"] == 42
    assert seen == [0.5]


def test_job_captures_errors():
    mgr = JobManager()

    def work(progress):
        raise RuntimeError("boom")

    job = mgr.submit("t", work)
    done = _wait(job, mgr)
    assert done.status == "error"
    assert "boom" in done.error


def test_job_list_orders_newest_first():
    mgr = JobManager()
    a = mgr.submit("a", lambda p: {})
    _wait(a, mgr)
    b = mgr.submit("b", lambda p: {})
    _wait(b, mgr)
    titles = [j["title"] for j in mgr.list()]
    assert set(titles) == {"a", "b"}


def test_job_get_unknown_returns_none():
    assert JobManager().get("nope") is None


# ---------------------------------------------------------------------------
# transcribe_lecture control flow (skip / force / output filtering)
# ---------------------------------------------------------------------------

def test_transcribe_skips_when_outputs_exist(tmp_path: Path):
    item = core.LectureItem(title="Week2_CPU", url="http://x/y.mp4",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    out_dir = core.output_dir_for(tmp_path, item, "week")
    # pre-create the outputs the request will ask for
    core.write_outputs(item, [{"start": 0, "end": 1, "text": "hi"}], "hi",
                       out_dir, ["txt", "json"], 30, {})

    stages = []
    res = transcribe.transcribe_lecture(
        item, tmp_path, outputs=["txt", "json"], organize="week",
        skip_existing=True, force=False,
        progress=lambda s, f: stages.append(s),
    )
    assert res["status"] == "skipped"
    # it must NOT have attempted a download/transcribe
    assert "downloading" not in stages


def test_transcribe_force_does_not_skip(tmp_path: Path, monkeypatch):
    item = core.LectureItem(title="Week2_CPU", url="http://x/y.mp4")
    out_dir = core.output_dir_for(tmp_path, item, "week")
    core.write_outputs(item, [{"start": 0, "end": 1, "text": "hi"}], "hi",
                       out_dir, ["txt"], 30, {})

    # stub out download + engine so no network / model is needed
    monkeypatch.setattr(transcribe, "download_media",
                        lambda *a, **k: out_dir / f"{item.safe_title}.mp4")
    monkeypatch.setattr(transcribe, "_transcribe_faster_whisper",
                        lambda *a, **k: {"segments": [{"start": 0, "end": 2, "text": "forced"}],
                                         "text": "forced", "language": "en"})

    res = transcribe.transcribe_lecture(
        item, tmp_path, outputs=["txt"], organize="week",
        skip_existing=True, force=True, keep_media=True,
    )
    assert res["status"] == "done"
    txt = (out_dir / f"{item.safe_title}.txt").read_text(encoding="utf-8")
    assert "forced" in txt


def test_transcribe_filters_invalid_outputs(tmp_path: Path, monkeypatch):
    item = core.LectureItem(title="W1", url="http://x/y.mp4")
    out_dir = core.output_dir_for(tmp_path, item, "none")
    monkeypatch.setattr(transcribe, "download_media",
                        lambda *a, **k: out_dir / "W1.mp4")
    monkeypatch.setattr(transcribe, "_transcribe_faster_whisper",
                        lambda *a, **k: {"segments": [{"start": 0, "end": 1, "text": "x"}],
                                         "text": "x", "language": "en"})
    res = transcribe.transcribe_lecture(
        item, tmp_path, outputs=["txt", "bogus"], organize="none",
        skip_existing=False, keep_media=True,
    )
    assert "txt" in res["outputs"]
    assert "bogus" not in res["outputs"]


def test_engine_status_shape():
    s = transcribe.engine_status()
    assert "engines" in s and "any_engine" in s
    assert set(s["engines"]) == {"faster-whisper", "whisper"}
    assert isinstance(s["cuda"], bool)
