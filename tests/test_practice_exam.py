"""Tests for practice_exam builders, layout, and API clamps.

OUTPUT is pinned to a temp dir (via PANOPTO_OUTPUT) before importing app.main,
so these never touch the real ./transcripts.
"""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Pure helpers (no app.main import)
# ---------------------------------------------------------------------------

def test_normalize_weights_sums_to_100():
    from app import practice_exam as pe
    out = pe.normalize_weights({"TCP": 40, "UDP": 60, "Routing": 0})
    assert abs(sum(out.values()) - 100.0) < 0.2
    assert out["TCP"] == 40.0
    assert out["UDP"] == 60.0
    assert "Routing" not in out or out["Routing"] == 0.0


def test_normalize_weights_empty_and_bad():
    from app import practice_exam as pe
    assert pe.normalize_weights(None) == {}
    assert pe.normalize_weights({}) == {}
    assert pe.normalize_weights({"a": -1, "b": "x"}) == {}


def test_allocate_counts_sum_equals_n():
    from app import practice_exam as pe
    for n in (10, 40, 100):
        counts = pe.allocate_counts(n, ["mcq", "short", "long"])
        assert sum(counts.values()) == n
        assert set(counts) == {"mcq", "short", "long"}
        assert counts["mcq"] >= counts["long"]


def test_batch_targets_for_n100():
    from app import practice_exam as pe
    batches = pe._batch_targets(100)
    assert sum(batches) == 100
    assert all(5 <= b <= 25 for b in batches)
    assert len(batches) >= 4


def test_part_labels_contiguous():
    from app import practice_exam as pe
    labels = pe._part_labels(["short", "long"])
    assert labels["short"][0] == "Part A"
    assert labels["long"][0] == "Part B"
    assert "Short-answer" in labels["short"][1]
    # Default order with mcq first stays A/B/C
    d = pe._part_labels(["mcq", "short", "long"])
    assert [d[t][0] for t in ("mcq", "short", "long")] == ["Part A", "Part B", "Part C"]


def _sample_questions():
    return [
        {"type": "mcq", "question": "What is TCP?",
         "options": ["Reliable", "Unreliable", "Both", "Neither"], "answer": "Reliable"},
        {"type": "mcq", "question": "UDP provides?",
         "options": ["Speed", "Reliability", "Ordering", "Congestion control"],
         "answer": "Speed"},
        {"type": "short", "question": "Define routing.", "answer": "Forwarding packets."},
        {"type": "long", "question": "Explain congestion control.",
         "answer": "TCP adjusts window size."},
    ]


def test_to_markdown_contiguous_parts_and_answer_key():
    from app import practice_exam as pe
    qs = _sample_questions()
    # short/long only → Part A/B contiguous (not C/D)
    qs2 = [q for q in qs if q["type"] in ("short", "long")]
    md = pe.to_markdown({"course": "COMPX999", "kind": "practice"}, qs2, include_key=True)
    assert "## Part A — Short-answer questions" in md
    assert "## Part B — Long-answer / essay" in md
    assert "Part C" not in md
    assert "## Answer key" in md
    assert "**1.**" in md and "**2.**" in md
    # Full default type order → A/B/C
    md3 = pe.to_markdown({"course": "COMPX999"}, qs, include_key=True)
    assert "## Part A — Multiple-choice questions" in md3
    assert "## Part B — Short-answer questions" in md3
    assert "## Part C — Long-answer / essay" in md3
    assert "Answer key" in md3


def test_render_pdf_structure(tmp_path: Path):
    from app import practice_exam as pe
    if not pe._have_fpdf():
        pytest.skip("fpdf2 not installed")
    qs = _sample_questions()
    out = tmp_path / "pack.pdf"
    info = pe.render_pdf(
        {"course": "COMPX999", "kind": "practice", "weights": {"TCP": 60.0, "UDP": 40.0}},
        qs, out, include_key=True,
    )
    assert out.exists() and out.stat().st_size > 500
    assert info["questions"] == 4
    assert info["pages"] >= 2


def _seed_course(tmp_path: Path, course: str, title: str, text: str, week: str = "week01"):
    from app import core
    it = core.LectureItem(title=title, url="u", duration=600)
    core.write_outputs(
        it, [{"start": 0, "end": 6, "text": text}], text,
        core.output_dir_for(tmp_path, it, week), ["txt", "json"], 30,
        {"course": course},
    )


def test_build_writes_md_and_returns_seed(tmp_path: Path):
    from app import practice_exam as pe
    text = (
        "TCP is a reliable transport protocol that provides congestion control. "
        "UDP is a connectionless transport protocol used for speed. "
        "Routing forwards packets between networks using hop-by-hop decisions. "
        "The network layer handles addressing and path selection. "
    ) * 20
    _seed_course(tmp_path, "COMPX999", "COMPX999 Week1 Transport", text)
    res = pe.build(
        tmp_path, course="COMPX999", n=12, types=["mcq", "short"],
        formats=["md"], seed=42, include_answer_key=True,
    )
    assert res["n"] >= 1
    assert res["seed"] == 42
    assert res.get("md_path")
    md = Path(res["md_path"]).read_text(encoding="utf-8")
    assert "Part A" in md
    assert "Answer key" in md
    assert "COMPX999" in md


def test_build_course_isolation(tmp_path: Path):
    from app import practice_exam as pe
    good = ("TCP provides reliable delivery between hosts. "
            "UDP is used when low latency matters more than reliability. ") * 30
    distractor = ("Photosynthesis converts light energy into chemical energy in plants. "
                  "Chlorophyll absorbs light in the blue and red spectra. ") * 30
    _seed_course(tmp_path, "COMPX999", "COMPX999 Networks", good, "week01")
    _seed_course(tmp_path, "COMPXAIA", "COMPXAIA Biology", distractor, "week02")
    res = pe.build(
        tmp_path, course="COMPX999", n=10, types=["short"],
        formats=["md"], seed=7, include_answer_key=True,
    )
    md = Path(res["md_path"]).read_text(encoding="utf-8").lower()
    assert "photosynthesis" not in md
    assert "chlorophyll" not in md
    # Should mention network-ish terms from the COMPX999 lecture
    assert any(w in md for w in ("tcp", "udp", "reliable", "latency", "delivery"))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), tmp_path


def test_api_n_below_10_returns_400(client):
    c, _ = client
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 5, "difficulty": "medium",
    })
    assert r.status_code == 400
    assert "10" in (r.json().get("detail") or "")


def test_api_n_above_150_returns_400(client):
    c, _ = client
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 200, "difficulty": "medium",
    })
    assert r.status_code == 400


def test_api_bad_difficulty_returns_400(client):
    c, _ = client
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 20, "difficulty": "bogus",
    })
    assert r.status_code == 400


def test_api_queues_practice_exam_job(client):
    c, tmp = client
    text = "TCP is reliable. UDP is fast. Routing forwards packets. " * 40
    _seed_course(tmp, "COMPX999", "Lecture 1", text)
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 10, "types": ["mcq", "short"],
        "formats": ["md"], "seed": "abc",
    })
    assert r.status_code == 200
    job = r.json()
    assert job.get("id")
    assert job.get("type") == "practice_exam" or "practice" in (job.get("label") or "").lower()
    # Wait for completion
    job_id = job["id"]
    final = None
    for _ in range(80):
        final = c.get(f"/api/jobs/{job_id}").json()
        if final["status"] in ("done", "failed", "error", "interrupted"):
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "done", final
    result = final.get("result") or {}
    assert result.get("seed") is not None
    assert result.get("md_path") or result.get("path")


def test_llm_quiz_plain_json_allows_small_n(client):
    """Study quiz JSON path keeps default n=8 (not the practice-exam 10 floor)."""
    c, tmp = client
    text = "TCP is reliable. UDP is fast. " * 40
    _seed_course(tmp, "COMPX999", "Quiz Lec", text)
    r = c.post("/api/llm/quiz", json={
        "scope": "course", "course": "COMPX999", "n": 8, "types": ["mcq"],
    })
    assert r.status_code == 200
    body = r.json()
    # Not a job — direct quiz JSON
    assert "questions" in body
    assert body.get("id") is None or "questions" in body
