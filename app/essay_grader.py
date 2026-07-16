"""essay_grader.py - rubric-aware essay feedback.

Grades a draft against a pasted rubric. Uses an LLM when configured; otherwise
an extractive heuristic that still returns a score, originality proxy, and
criterion-level feedback so the feature never disappears offline.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from . import llm
from .database import Database

SESSION_RUBRIC_SPLIT = re.compile(r"(?m)^\s*(?:\d+[\).\]]\s+|[-*•]\s+|[A-Z][\w\s]{2,40}:\s*)")

_GRADE_SYSTEM = (
    "You are a rigorous university essay marker. Grade the draft against the "
    "rubric only. Return ONLY JSON with keys: score (0-100 number), originality "
    "(0-100 number estimating how distinctive the prose is, not a plagiarism "
    "detector), strengths (array of short strings), improvements (array of short "
    "strings), rubric (array of {criterion, score, max, comment}), summary "
    "(one sentence)."
)


def parse_rubric_criteria(rubric_text: str) -> List[str]:
    """Split a free-text rubric into criterion labels."""
    text = (rubric_text or "").strip()
    if not text:
        return ["Overall quality"]
    parts = [p.strip(" \t:-–—") for p in SESSION_RUBRIC_SPLIT.split(text) if p.strip()]
    # Prefer lines that look like criteria; fall back to sentences.
    criteria = [p.split("\n")[0].strip() for p in parts if len(p.split("\n")[0].strip()) >= 3]
    criteria = [c for c in criteria if not c.lower().startswith("rubric")]
    if len(criteria) >= 2:
        return criteria[:12]
    lines = [ln.strip(" -*•\t") for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return lines[:12]
    return [text[:80] if len(text) > 80 else text]


def _token_set(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", text or "")}


def _extractive_grade(essay: str, rubric: str) -> Dict[str, Any]:
    criteria = parse_rubric_criteria(rubric)
    essay_tokens = _token_set(essay)
    words = re.findall(r"\b\w+\b", essay or "")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", essay or "") if s.strip()]
    word_count = len(words)

    rubric_hits = []
    per_crit = []
    max_each = 100 // max(len(criteria), 1)
    remainder = 100 - max_each * len(criteria)
    for i, crit in enumerate(criteria):
        crit_tokens = _token_set(crit)
        overlap = len(crit_tokens & essay_tokens) / max(len(crit_tokens), 1)
        # Length + coverage heuristic: short drafts and missing keywords lose marks.
        length_factor = min(1.0, word_count / 600)
        score = round(max_each * (0.35 + 0.45 * overlap + 0.2 * length_factor))
        if i == 0:
            score = min(max_each + remainder, score + (remainder if overlap > 0.2 else 0))
        score = max(0, min(max_each + (remainder if i == 0 else 0), score))
        comment = (
            f"Draft covers language related to “{crit[:60]}”."
            if overlap >= 0.25 else
            f"Little explicit coverage of “{crit[:60]}” — address it directly."
        )
        per_crit.append({
            "criterion": crit[:120],
            "score": score,
            "max": max_each + (remainder if i == 0 else 0),
            "comment": comment,
        })
        rubric_hits.append(overlap)

    total = sum(c["score"] for c in per_crit)
    # Originality proxy: type-token ratio + low repeated 4-grams.
    unique_ratio = len(set(w.lower() for w in words)) / max(len(words), 1)
    grams = [" ".join(words[i:i + 4]).lower() for i in range(max(0, len(words) - 3))]
    repeat_penalty = 0.0
    if grams:
        from collections import Counter
        counts = Counter(grams)
        repeat_penalty = min(0.35, sum(1 for n in counts.values() if n > 1) / max(len(counts), 1))
    originality = round(100 * max(0.0, min(1.0, unique_ratio * 1.15 - repeat_penalty)))

    strengths = []
    improvements = []
    if word_count >= 400:
        strengths.append("Draft length is in a workable range for a university essay.")
    else:
        improvements.append("Expand the draft — aim for fuller development of each criterion.")
    if sentences and len(sentences[0].split()) >= 8:
        strengths.append("Clear thesis signal in the opening — keep the question in view.")
    for c in per_crit:
        if c["score"] / max(c["max"], 1) < 0.55:
            improvements.append(c["comment"])
        elif c["score"] / max(c["max"], 1) >= 0.75:
            strengths.append(c["criterion"][:80])
    if not strengths:
        strengths.append("Structure is present; tighten links back to the question.")
    if not improvements:
        improvements.append("Polish transitions and evidence before you submit.")

    return {
        "score": float(total),
        "originality": float(originality),
        "strengths": strengths[:6],
        "improvements": improvements[:6],
        "rubric": per_crit,
        "summary": (
            f"Heuristic mark {total}% against {len(criteria)} rubric criteria "
            f"({word_count} words). Enable an AI provider for richer feedback."
        ),
        "generated": "extractive",
        "word_count": word_count,
    }


def _coerce_result(raw: Dict[str, Any], essay: str, rubric: str) -> Dict[str, Any]:
    base = _extractive_grade(essay, rubric)
    score = raw.get("score", base["score"])
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = base["score"]
    score = max(0.0, min(100.0, score))
    try:
        originality = float(raw.get("originality", base["originality"]))
    except (TypeError, ValueError):
        originality = base["originality"]
    originality = max(0.0, min(100.0, originality))
    strengths = raw.get("strengths") if isinstance(raw.get("strengths"), list) else base["strengths"]
    improvements = raw.get("improvements") if isinstance(raw.get("improvements"), list) \
        else base["improvements"]
    rubric_rows = raw.get("rubric") if isinstance(raw.get("rubric"), list) else base["rubric"]
    cleaned_rubric = []
    for row in rubric_rows[:12]:
        if not isinstance(row, dict):
            continue
        cleaned_rubric.append({
            "criterion": str(row.get("criterion") or "")[:160],
            "score": float(row.get("score") or 0),
            "max": float(row.get("max") or 0) or None,
            "comment": str(row.get("comment") or "")[:400],
        })
    if not cleaned_rubric:
        cleaned_rubric = base["rubric"]
    return {
        "score": score,
        "originality": originality,
        "strengths": [str(s)[:240] for s in strengths[:8]],
        "improvements": [str(s)[:240] for s in improvements[:8]],
        "rubric": cleaned_rubric,
        "summary": str(raw.get("summary") or base["summary"])[:500],
        "generated": "ai",
        "word_count": len(re.findall(r"\b\w+\b", essay or "")),
    }


def grade_essay(essay: str, rubric: str, *, title: str = "",
                db: Optional[Database] = None,
                course_id: Optional[int] = None,
                config: Optional[Dict[str, Any]] = None,
                save: bool = True) -> Dict[str, Any]:
    """Grade ``essay`` against ``rubric``. Always returns a usable result."""
    essay = (essay or "").strip()
    rubric = (rubric or "").strip()
    if not essay:
        raise ValueError("Paste an essay draft first.")
    if not rubric:
        raise ValueError("Paste a rubric first.")

    cfg = config or llm.get_config(db, course_id)
    result: Dict[str, Any]
    if llm.is_enabled(cfg):
        prompt = (
            f"Title: {title or 'Untitled draft'}\n\n"
            f"RUBRIC:\n{rubric[:6000]}\n\n"
            f"ESSAY DRAFT:\n{essay[:14000]}\n\n"
            "Return the grading JSON now."
        )
        try:
            raw_text = llm.complete(prompt, system=_GRADE_SYSTEM,
                                    config={**cfg, "format": "json",
                                            "max_tokens": max(int(cfg.get("max_tokens", 1024) or 1024), 1200)})
            # Strip fences if the model wraps JSON.
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            raw = json.loads(raw_text)
            if isinstance(raw, dict):
                result = _coerce_result(raw, essay, rubric)
                result["provider"] = cfg.get("provider")
            else:
                result = _extractive_grade(essay, rubric)
        except (llm.LLMError, json.JSONDecodeError, TypeError, ValueError):
            result = _extractive_grade(essay, rubric)
    else:
        result = _extractive_grade(essay, rubric)

    result["title"] = title or "Essay draft"
    if save and db is not None:
        eid = db.add_essay_grade(
            course_id, title or "Essay draft", essay, rubric,
            json.dumps({k: v for k, v in result.items() if k not in ("essay_text",)}),
            score=result.get("score"), originality=result.get("originality"),
        )
        result["id"] = eid
    return result
