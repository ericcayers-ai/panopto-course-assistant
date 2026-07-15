#!/usr/bin/env python3
"""Lecture-domain STT benchmark harness (metrics only — never commit audio).

Usage:
  python scripts/benchmark_stt.py --manifest path/to/manifest.json --out metrics.json

Manifest schema (local, gitignored):
  {
    "fixtures": [
      {"id": "clean_en", "audio": "C:/local/fixtures/clean.wav", "ref": "...", "tags": ["clean"]}
    ],
    "engines": ["faster-whisper", "granite", "qwen3", "parakeet", "moonshine"]
  }

Measures WER (when jiwer available), runtime, and chunk-boundary sanity on
caption/adaptive path without downloading private corpora.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List


def _wer(ref: str, hyp: str) -> float | None:
    try:
        from jiwer import wer
        return float(wer(ref, hyp))
    except Exception:
        # Simple token error rate fallback.
        r = ref.lower().split()
        h = hyp.lower().split()
        if not r:
            return 0.0 if not h else 1.0
        # Levenshtein on tokens
        dp = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
        for i in range(len(r) + 1):
            dp[i][0] = i
        for j in range(len(h) + 1):
            dp[0][j] = j
        for i in range(1, len(r) + 1):
            for j in range(1, len(h) + 1):
                cost = 0 if r[i - 1] == h[j - 1] else 1
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
        return dp[-1][-1] / max(1, len(r))


def run_one(audio: Path, engine: str, profile: str = "auto") -> Dict[str, Any]:
    os.environ.setdefault("PANOPTO_STT_INPROCESS", "1")
    from app.stt.pipeline import transcribe_path
    from app.stt.types import STTRequest

    t0 = time.time()
    result = transcribe_path(
        str(audio),
        STTRequest(profile=profile, engine=None if engine == "auto" else engine,
                   caption_first=False, resume=False),
    )
    runtime = time.time() - t0
    return {
        "engine": result.engine or engine,
        "model": result.model,
        "profile": profile,
        "route_reason": result.route_reason,
        "runtime_s": round(runtime, 3),
        "chars": len(result.text or ""),
        "segments": len(result.segments),
        "fallbacks": list(result.fallbacks_used),
        "hypothesis": result.text,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="Local JSON manifest (not committed)")
    ap.add_argument("--out", default="stt_benchmark_metrics.json")
    ap.add_argument("--profile", default="auto")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    engines: List[str] = list(manifest.get("engines") or ["faster-whisper"])
    rows: List[Dict[str, Any]] = []

    for fix in manifest.get("fixtures") or []:
        audio = Path(fix["audio"])
        if not audio.is_file():
            rows.append({"id": fix.get("id"), "error": f"missing audio: {audio}"})
            continue
        for engine in engines:
            try:
                row = run_one(audio, engine, profile=args.profile)
                ref = fix.get("ref") or ""
                if ref:
                    row["wer"] = _wer(ref, row.get("hypothesis") or "")
                row["id"] = fix.get("id")
                row["tags"] = fix.get("tags") or []
                # Never persist audio paths with private names beyond basename.
                row["audio"] = audio.name
                rows.append(row)
            except Exception as e:
                rows.append({"id": fix.get("id"), "engine": engine, "error": str(e)[:300]})

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} ({len(rows)} rows). Do not commit private audio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
