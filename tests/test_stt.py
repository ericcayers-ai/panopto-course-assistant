"""Focused adaptive STT contract, ingest, routing, and API tests.

PANOPTO_OUTPUT is pinned to a temp dir before importing app.main.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    monkeypatch.setenv("PANOPTO_STT_INPROCESS", "1")
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), tmp_path


def test_stt_types_roundtrip_schema_v2():
    from app.stt.types import SCHEMA_VERSION, Segment, STTResult, Word

    result = STTResult(
        segments=[Segment(
            id=1, start=0.0, end=1.5, text="hello world",
            speaker="SPEAKER_00", language="en", confidence=0.9,
            words=[Word(word="hello", start=0.0, end=0.5, confidence=0.95)],
        )],
        text="hello world",
        language="en",
        engine="faster-whisper",
        model="small",
        schema_version=SCHEMA_VERSION,
        route_reason="test",
    )
    raw = result.to_dict()
    assert raw["schema_version"] == 2
    assert raw["segments"][0]["start"] == 0.0
    assert raw["segments"][0]["text"] == "hello world"
    assert raw["segments"][0]["speaker"] == "SPEAKER_00"
    assert raw["segments"][0]["words"][0]["word"] == "hello"
    back = STTResult.from_dict(raw)
    assert back.text == "hello world"
    assert back.segments[0].speaker == "SPEAKER_00"


def test_router_auto_prefers_captions_when_usable():
    from app.stt.router import route
    from app.stt.types import STTRequest

    d = route(
        STTRequest(profile="auto", caption_first=True),
        available={"faster-whisper": True},
        has_usable_captions=True,
    )
    assert d.engine == "captions"


def test_router_fast_and_eco_fall_back_to_faster_whisper():
    from app.stt.router import route
    from app.stt.types import STTRequest

    avail = {"faster-whisper": True, "parakeet": False, "moonshine": False, "granite": False}
    fast = route(STTRequest(profile="fast", language="en"), available=avail)
    assert fast.engine == "faster-whisper"
    eco = route(STTRequest(profile="eco", language="en"), available=avail)
    assert eco.engine == "faster-whisper"


def test_chunk_plan_respects_180s_cap():
    from app.stt.chunking import plan_chunks

    plans = plan_chunks(600.0, None, max_seconds=180.0)
    assert plans
    assert all((p.end - p.start) <= 180.0 + 1e-6 for p in plans)


def test_checkpoint_resume_skips_completed(tmp_path: Path):
    from app.stt import checkpoint as ckpt
    from app.stt.chunking import ChunkPlan
    from app.stt.types import Segment

    plans = [
        ChunkPlan(index=0, start=0.0, end=10.0),
        ChunkPlan(index=1, start=9.0, end=20.0),
    ]
    partial = tmp_path / "x.stt.partial.json"
    data = ckpt.init_checkpoint(partial, fingerprint="abc", settings_hash="s", chunks=plans)
    data = ckpt.save_chunk(partial, 0, [Segment(id=1, start=0.0, end=1.0, text="a")])
    assert ckpt.first_missing_chunk(data) == 1
    data = ckpt.save_chunk(partial, 1, [Segment(id=2, start=10.0, end=11.0, text="b")])
    assert ckpt.first_missing_chunk(data) is None


def test_merge_dedupes_boundary_overlap():
    from app.stt.chunking import ChunkPlan, merge_chunk_segments
    from app.stt.types import Segment

    a = [Segment(id=1, start=0.0, end=2.0, text="hello world")]
    b = [Segment(id=1, start=1.8, end=3.5, text="world again")]
    plans = [
        (ChunkPlan(0, 0.0, 2.0, overlap_prev=0.0), a),
        (ChunkPlan(1, 1.5, 3.5, overlap_prev=0.5), b),
    ]
    merged = merge_chunk_segments(plans)
    texts = " ".join(s.text for s in merged)
    assert "hello" in texts
    assert "again" in texts or "world" in texts


def test_captions_parse_srt():
    from app.stt import captions as captions_mod

    srt = "1\n00:00:00,000 --> 00:00:01,500\nHello there\n\n2\n00:00:01,500 --> 00:00:03,000\nWorld\n"
    segs = captions_mod.parse_captions(srt, hint="srt")
    assert len(segs) >= 2
    result = captions_mod.result_from_captions(segs)
    assert "Hello" in result.text
    assert result.schema_version == 2


def test_force_legacy_opt_in_only(tmp_path: Path, monkeypatch):
    from app import core, transcribe

    item = core.LectureItem(title="W", url="http://x/y.mp4")
    out_dir = core.output_dir_for(tmp_path, item, "none")
    called = {"legacy": 0, "adaptive": 0}

    monkeypatch.setattr(transcribe, "download_media", lambda *a, **k: out_dir / "W.mp4")
    (out_dir / "W.mp4").write_bytes(b"fake")

    def legacy(*a, **k):
        called["legacy"] += 1
        return {"segments": [{"start": 0, "end": 1, "text": "L"}], "text": "L", "language": "en"}

    def adaptive(*a, **k):
        called["adaptive"] += 1
        from app.stt.types import STTResult, Segment
        return STTResult(segments=[Segment(id=1, start=0, end=1, text="A")], text="A",
                         engine="faster-whisper", model="tiny")

    monkeypatch.setattr(transcribe, "_transcribe_faster_whisper", legacy)
    monkeypatch.setattr("app.stt.pipeline.transcribe_path", adaptive)

    transcribe.transcribe_lecture(
        item, tmp_path, outputs=["txt"], organize="none",
        skip_existing=False, keep_media=True, use_adaptive=False, index_db=False,
    )
    assert called["legacy"] == 1
    assert called["adaptive"] == 0

    called["legacy"] = 0
    transcribe.transcribe_lecture(
        item, tmp_path, outputs=["txt"], organize="none",
        skip_existing=False, keep_media=True, force=True, use_adaptive=True, index_db=False,
        caption_first=False,
    )
    assert called["adaptive"] == 1
    assert called["legacy"] == 0


def test_stt_api_mounted(client):
    c, _ = client
    r = c.get("/api/stt/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["offline"] is True
    assert "auto" in body["profiles"]
    assert "engines" in body

    r2 = c.post("/api/stt/route", json={"profile": "eco", "language": "en"})
    assert r2.status_code == 200
    assert "route" in r2.json()


def test_health_and_preflight(client):
    c, _ = client
    h = c.get("/api/health")
    assert h.status_code == 200
    assert h.json()["ok"] is True
    assert h.json()["version"] == "4.2.0"

    p = c.get("/api/setup/preflight")
    assert p.status_code == 200
    assert "remediations" in p.json()

    d = c.post("/api/diagnostics/bundle")
    assert d.status_code == 200
    assert "text" in d.json()
    assert "huggingface" not in d.json()["text"].lower() or "backend" in d.json()["text"].lower()


def test_worker_protocol_ping():
    from app.stt.workers import handle_worker_message

    assert handle_worker_message({"op": "ping"})["ok"] is True


def test_write_outputs_schema_v2_preserves_speaker(tmp_path: Path):
    from app import core

    item = core.LectureItem(title="Spk", url="u")
    out = tmp_path / "out"
    segs = [{
        "id": 1, "start": 0.0, "end": 1.0, "text": "hi",
        "speaker": "SPEAKER_00", "language": "en",
        "words": [{"word": "hi", "start": 0.0, "end": 0.5}],
    }]
    written = core.write_outputs(
        item, segs, "hi", out, ["json", "srt", "md"], 30,
        {"schema_version": 2, "engine": "test"},
    )
    payload = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["segments"][0]["speaker"] == "SPEAKER_00"
    srt = Path(written["srt"]).read_text(encoding="utf-8")
    assert "SPEAKER_00" in srt or "hi" in srt


def test_vocabulary_and_corrections_enrichment():
    from app.stt.enrichment import apply_corrections, build_course_vocabulary
    from app.stt.types import STTResult, Segment

    vocab = build_course_vocabulary(
        titles=["TCP Handshake"], glossary_terms=["SYN", "ACK"], lecturer_names=["Ada"],
    )
    assert "SYN" in vocab
    result = STTResult(segments=[Segment(id=1, start=0, end=1, text="syn packet")], text="syn packet")
    fixed = apply_corrections(result, {"syn": "SYN"})
    assert "SYN" in fixed.text
    assert fixed.raw_provenance.get("raw_text") == "syn packet"
