"""Unit tests for Kokoro TTS helpers (no heavy model load)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Keep tests off the real transcripts/ library, matching other API tests.
os.environ.setdefault("PANOPTO_OUTPUT", str(Path(__file__).resolve().parent / "_tts_tmp"))

from app import tts as tts_mod


def test_strip_markdown_removes_markup():
    md = "# Title\n\n**Bold** and *italic* with a [link](https://x.test).\n\n- item\n\n```\ncode\n```\n"
    plain = tts_mod.strip_markdown(md)
    assert "Title" in plain
    assert "Bold" in plain
    assert "italic" in plain
    assert "link" in plain
    assert "#" not in plain
    assert "**" not in plain
    assert "```" not in plain
    assert "https://" not in plain


def test_chunk_text_prefers_sentence_boundaries():
    text = (
        "First sentence about photosynthesis. Second sentence expands the idea. "
        "Third wraps up the paragraph.\n\n"
        "New paragraph starts here. Another closing thought ends it."
    )
    chunks = tts_mod.chunk_text(text, max_chars=80)
    assert len(chunks) >= 2
    assert all(len(c) <= 80 or " " not in c for c in chunks)
    joined = " ".join(chunks)
    assert "photosynthesis" in joined
    assert "closing thought" in joined


def test_chunk_text_splits_overlong_sentence():
    long = "Word " * 120  # far above default budget
    chunks = tts_mod.chunk_text(long.strip(), max_chars=60)
    assert len(chunks) > 1
    assert all(len(c) <= 60 for c in chunks)


def test_chunk_text_empty():
    assert tts_mod.chunk_text("") == []
    assert tts_mod.chunk_text("   \n\n  ") == []


def test_list_voices_includes_english_defaults():
    voices = tts_mod.list_voices()
    ids = {v["id"] for v in voices}
    assert "af_heart" in ids
    assert "bm_george" in ids
    heart = next(v for v in voices if v["id"] == "af_heart")
    assert heart["group"] == "American English"
    assert heart["downloaded"] is True


def test_lang_for_voice():
    assert tts_mod._lang_for_voice("af_heart") == "a"
    assert tts_mod._lang_for_voice("bm_george") == "b"
    assert tts_mod._lang_for_voice("ff_siwis") == "f"
    assert tts_mod._lang_for_voice("zz_custom") == "z"


def test_concat_audio_inserts_pause():
    a = np.ones(10, dtype=np.float32)
    b = np.ones(5, dtype=np.float32) * 0.5
    out = tts_mod._concat_audio([a, b], sample_rate=100, pause_s=0.1)
    # 10 + 10 silence + 5
    assert out.shape[0] == 25
    assert float(out[10]) == 0.0


def test_generate_long_form_mocked(tmp_path: Path):
    md = tmp_path / "lecture.md"
    md.write_text(
        "# Lecture\n\n"
        "Alpha sentence here. Beta sentence continues the idea. "
        "Gamma finishes the first bit.\n\n"
        "Delta opens a new section. Epsilon closes it cleanly.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.wav"
    stages: list[tuple[str, float]] = []

    def progress(stage: str, frac: float) -> None:
        stages.append((stage, frac))

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, text, voice="af_heart", speed=1.0, split_pattern=None):
            # One short tone per call; amplitude encodes chunk index length.
            n = max(24, min(400, len(text) * 2))
            audio = np.linspace(0.1, 0.2, n, dtype=np.float32)
            yield text, "ph", audio

    with patch.dict("sys.modules", {"kokoro": MagicMock(KPipeline=FakePipeline)}):
        with patch("soundfile.write") as sf_write:
            # Re-import path: generate imports kokoro inside the function.
            result = tts_mod.generate(
                md_path=str(md),
                voice_id="af_heart",
                output_path=str(out),
                cache_dir=tmp_path / "voices",
                progress=progress,
                model_path="hexgrad/Kokoro-82M",
            )

    assert result["voice"] == "af_heart"
    assert result["chunks"] >= 2
    assert result["duration_s"] > 0
    assert stages[0][0] == "Reading file"
    assert stages[-1] == ("done", 1.0)
    assert any("Speaking chunk" in s for s, _ in stages)
    sf_write.assert_called_once()
    # WAV path + ndarray + sample rate
    assert sf_write.call_args.args[0] == str(out)
    assert sf_write.call_args.args[2] == tts_mod.SAMPLE_RATE


def test_generate_rejects_empty_markdown(tmp_path: Path):
    md = tmp_path / "empty.md"
    md.write_text("```\nonly code\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No readable text"):
        tts_mod.generate(
            md_path=str(md),
            voice_id="af_heart",
            output_path=str(tmp_path / "x.wav"),
            cache_dir=tmp_path,
            progress=lambda *_: None,
        )


def test_is_available_reflects_import():
    with patch.dict("sys.modules", {"kokoro": MagicMock()}):
        assert tts_mod.is_available() is True

    import builtins
    real_import = builtins.__import__

    def blocker(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "kokoro" or (isinstance(name, str) and name.startswith("kokoro.")):
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    with patch.dict("sys.modules"):
        # Ensure a cached successful import cannot satisfy is_available().
        import sys
        sys.modules.pop("kokoro", None)
        with patch("builtins.__import__", side_effect=blocker):
            assert tts_mod.is_available() is False
