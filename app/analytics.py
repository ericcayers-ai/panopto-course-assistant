"""
analytics.py - local-only usage insight (§13). **No cloud, no network.**

Everything here is derived from rows the app already stores (`jobs`, `exports`,
`study_sessions`). There is deliberately no telemetry SDK and no outbound call -
the only egress path is a *user-initiated* diagnostics export (a local JSON file
they choose to share), which is scrubbed of secrets/PII.

Surfaces:
* feature usage counts (imports, transcriptions, exports, AI calls)
* a simple import→transcribe→export completion funnel
* failed-job counts by §3 failure category (recurring pain points)
* throughput (job-duration percentiles)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import Database


def _parse(ts: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _percentiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "max": 0.0}
    s = sorted(values)
    def pct(p: float) -> float:
        idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return round(s[idx], 2)
    return {"p50": pct(0.5), "p90": pct(0.9), "max": round(s[-1], 2)}


def compute(db: Database, course_id: Optional[int] = None) -> Dict[str, Any]:
    """Usage stats + funnel + failure insights + throughput, from local rows."""
    jobs = [dict(r) for r in db.list_jobs()]
    if course_id is not None:
        jobs = [j for j in jobs if j.get("course_id") == course_id]
    exports = [dict(r) for r in db.list_exports(course_id)]
    sessions = [dict(r) for r in db.list_study_sessions(course_id)]

    # -- feature usage counts (by job type + status) ----------------------
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for j in jobs:
        by_type[j["type"]] = by_type.get(j["type"], 0) + 1
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1

    transcribe_jobs = [j for j in jobs if "transcrib" in j["type"].lower()]
    completed_transcribe = sum(1 for j in transcribe_jobs if j["status"] == "done")

    usage = {
        "jobs_total": len(jobs),
        "by_type": by_type,
        "by_status": by_status,
        "transcriptions": len(transcribe_jobs),
        "exports": len(exports),
        "study_sessions": len(sessions),
        "documents_indexed": db.count_documents(course_id),
        "transcripts_indexed": db.count_transcripts(course_id),
    }

    # -- completion funnel: import → transcribe → export ------------------
    imported = usage["documents_indexed"] + usage["transcripts_indexed"]
    funnel = {
        "imported": imported,
        "transcribed": completed_transcribe,
        "exported": len(exports),
        "transcribe_rate": round(completed_transcribe / imported, 3) if imported else 0.0,
        "export_rate": round(len(exports) / completed_transcribe, 3) if completed_transcribe else 0.0,
    }

    # -- failure insights: failed jobs by §3 category ---------------------
    failures: Dict[str, int] = {}
    for j in jobs:
        if j["status"] in ("error", "dead_letter"):
            cat = j.get("failure_category") or "unknown"
            failures[cat] = failures.get(cat, 0) + 1

    # -- throughput: completed-job durations (seconds) --------------------
    durations: List[float] = []
    for j in jobs:
        if j["status"] != "done":
            continue
        a, b = _parse(j.get("created_at", "")), _parse(j.get("updated_at", ""))
        if a and b and b >= a:
            durations.append((b - a).total_seconds())

    return {
        "course_id": course_id,
        "usage": usage,
        "funnel": funnel,
        "failures_by_category": failures,
        "throughput_seconds": _percentiles(durations),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def feedback_prompt(stats: Dict[str, Any]) -> Optional[str]:
    """Purely-local nudge after repeated failures of the same kind (no submission)."""
    fails = stats.get("failures_by_category", {})
    if not fails:
        return None
    cat, n = max(fails.items(), key=lambda kv: kv[1])
    if n >= 3:
        hints = {
            "network": "Several jobs failed on the network - check your connection or cookies.",
            "authentication": "Repeated auth failures - your session cookies/API key may be stale.",
            "dependency": "A missing engine keeps failing jobs - see /api/status for what to install.",
            "filesystem": "Filesystem errors recurring - check the output folder's space/permissions.",
        }
        return hints.get(cat, f"{n} jobs failed with '{cat}' - worth a look.")
    return None


def diagnostics_export(db: Database, output_dir: Path,
                      course_id: Optional[int] = None) -> Dict[str, Any]:
    """Write an anonymised diagnostics JSON the user can *choose* to share.

    Contains only aggregate counts - never paths, titles, tokens or content - so
    it carries no secrets/PII (asserted by test).
    """
    stats = compute(db, course_id)
    safe = {
        "schema_version": db.schema_version(),
        "usage": stats["usage"],
        "funnel": stats["funnel"],
        "failures_by_category": stats["failures_by_category"],
        "throughput_seconds": stats["throughput_seconds"],
        "generated_at": stats["generated_at"],
    }
    dest = output_dir / "_exports"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "diagnostics.json"
    path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
    return {"path": path.relative_to(output_dir).as_posix(), "anonymised": True,
            "contents": safe}
