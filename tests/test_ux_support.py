"""Backend support for the Simple/Advanced UX: notion folder-of-zips,
transcription recommendation, and 30s-throttled transcribe progress."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app import notion, transcribe


def _make_notion_zip(path: Path, page_name: str, body: str):
    html = f"<html><head><title>{page_name}</title></head><body><h1>{page_name}</h1><p>{body}</p></body></html>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{page_name}.html", html)


def test_notion_import_folder_of_multiple_zips(tmp_path: Path):
    src = tmp_path / "notion_exports"
    src.mkdir()
    _make_notion_zip(src / "course_a.zip", "COMPX234", "networks notes")
    _make_notion_zip(src / "course_b.zip", "COMPX201", "data structures notes")
    out = tmp_path / "out"
    res = notion.convert_notion_export(src, out, combined=True)
    assert res["count"] == 2                       # both zipped pages converted
    titles = " ".join(res["files"])
    assert "COMPX234" in titles and "COMPX201" in titles


def test_recommend_settings_shape():
    rec = transcribe.recommend_settings()
    assert "ready" in rec
    if rec["ready"]:
        assert rec["device"] in ("cpu", "cuda")
        assert rec["model"] and rec["engine"]
        assert rec["interval"] == 30
    else:
        assert rec["reason"]


def test_transcribe_progress_throttled_to_period(monkeypatch):
    """The faster-whisper segment loop emits progress at most once per period."""
    times = iter([0.0, 5.0, 40.0, 41.0, 80.0])   # wall-clock readings
    monkeypatch.setattr(transcribe.time, "time", lambda: next(times))

    class Seg:
        def __init__(self, end, text): self.start, self.end, self.text = end - 1, end, text

    class Info:  # 100s of audio
        duration = 100.0

    class Model:
        def transcribe(self, *a, **k):
            return iter([Seg(10, "a"), Seg(20, "b"), Seg(50, "c"), Seg(90, "d")]), Info()

    monkeypatch.setattr(transcribe, "_faster_whisper_model", lambda m, d: Model())
    seen = []
    res = transcribe._transcribe_faster_whisper(
        Path("x.mp4"), "small", "en", "cpu", 1, False,
        progress=lambda f: seen.append(round(f, 2)), progress_period=30.0)
    assert res["text"] == "a b c d"
    # last_emit starts at t=0. seg@t=5 (<30 -> skip); seg@t=40 emits (20/100=0.2,
    # last_emit=40); seg@t=41 (<30 -> skip); seg@t=80 emits (90/100=0.9).
    assert seen == [0.2, 0.9]
