"""
practice_exam.py - build style-matched practice / exam PDFs from the library.

Matches COMPX234 Practice 100Q layout: titled pack, format blurb + optional
topic weights, parted sections (MCQ / short / long / …), numbered items with
a) b) c) d) choices, answer key appendix. Also writes companion Markdown for
suite Sample Questions folders.
"""
from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ai, core, llm

EXPORTS_DIRNAME = "_exports"

TYPE_SEMANTIC = {
    "mcq": "Multiple-choice questions",
    "truefalse": "True / false",
    "short": "Short-answer questions",
    "long": "Long-answer / essay",
    "cloze": "Fill-in-the-blank",
}

# Legacy fixed labels (tests may reference); prefer _part_labels(types).
TYPE_LABELS = {
    "mcq": ("Part A", TYPE_SEMANTIC["mcq"]),
    "truefalse": ("Part B", TYPE_SEMANTIC["truefalse"]),
    "short": ("Part C", TYPE_SEMANTIC["short"]),
    "long": ("Part D", TYPE_SEMANTIC["long"]),
    "cloze": ("Part E", TYPE_SEMANTIC["cloze"]),
}

DEFAULT_TYPES = ["mcq", "short", "long"]
DEFAULT_TYPE_SHARE = {"mcq": 0.55, "short": 0.30, "long": 0.15, "truefalse": 0.15, "cloze": 0.15}


def _have_fpdf() -> bool:
    import importlib.util
    return importlib.util.find_spec("fpdf") is not None


def _latin1(s: str) -> str:
    from .cheatsheet import _latin1 as _cs
    return _cs(s)


def _seed_int(seed: Any) -> int:
    if seed is None or seed == "":
        return random.randrange(1, 2**31 - 1)
    if isinstance(seed, int):
        return seed
    h = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def normalize_weights(weights: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Normalize topic weight map to percentages that sum to ~100."""
    if not weights:
        return {}
    cleaned: Dict[str, float] = {}
    for k, v in weights.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            cleaned[key] = float(v)
        except (TypeError, ValueError):
            continue
    total = sum(max(0.0, v) for v in cleaned.values())
    if total <= 0:
        return {}
    return {k: round(100.0 * max(0.0, v) / total, 1) for k, v in cleaned.items()}


def allocate_counts(n: int, types: Sequence[str],
                    shares: Optional[Dict[str, float]] = None) -> Dict[str, int]:
    """Split ``n`` across question types using default or custom shares."""
    types = [t for t in types if t in TYPE_LABELS] or list(DEFAULT_TYPES)
    shares = shares or DEFAULT_TYPE_SHARE
    raw = {t: max(0.0, float(shares.get(t, 1.0 / len(types)))) for t in types}
    total = sum(raw.values()) or 1.0
    counts = {t: int(n * (raw[t] / total)) for t in types}
    # Fix rounding so counts sum to n (largest remainder)
    while sum(counts.values()) < n:
        t = max(types, key=lambda x: (raw[x] / total) - counts[x] / max(n, 1))
        counts[t] += 1
    while sum(counts.values()) > n:
        t = max(types, key=lambda x: counts[x])
        if counts[t] > 0:
            counts[t] -= 1
        else:
            break
    return counts


def _batch_targets(n: int, batch: int = 20) -> List[int]:
    """Split large n into batch sizes that fit small-model context."""
    batch = max(5, min(25, batch))
    out: List[int] = []
    left = n
    while left > 0:
        take = min(batch, left)
        out.append(take)
        left -= take
    return out


def _filter_by_types(qs: List[Dict[str, Any]], allowed: Sequence[str]) -> List[Dict[str, Any]]:
    allow = set(allowed)
    out = []
    for q in qs:
        t = (q.get("type") or "mcq").lower()
        if t == "true_false":
            t = "truefalse"
        if t not in allow and allow:
            # remapped cloze→short when only short/long requested
            if t == "cloze" and "short" in allow:
                q = {**q, "type": "short"}
                t = "short"
            elif t == "mcq" and "truefalse" in allow and not q.get("options"):
                q = {**q, "type": "truefalse", "options": ["True", "False"]}
                t = "truefalse"
            else:
                continue
        out.append({**q, "type": t})
    return out


def _topic_hint(weights: Dict[str, float]) -> str:
    if not weights:
        return ""
    parts = [f"{k} ({v:.0f}%)" for k, v in weights.items()]
    return " Topic weighting: " + "; ".join(parts) + "."


def _part_labels(types: Sequence[str]) -> Dict[str, Tuple[str, str]]:
    """Contiguous Part A/B/C… from selected type order (keep semantic titles)."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out: Dict[str, Tuple[str, str]] = {}
    for i, t in enumerate(types):
        letter = letters[i] if i < len(letters) else str(i + 1)
        out[t] = (f"Part {letter}", TYPE_SEMANTIC.get(t, t))
    return out


def generate_questions(output_dir: Path, *, n: int = 100,
                       types: Optional[List[str]] = None,
                       difficulty: str = "medium",
                       scope: str = "course", target: str = "",
                       course: str = "",
                       weights: Optional[Dict[str, Any]] = None,
                       seed: Any = None,
                       config: Optional[Dict[str, Any]] = None,
                       db=None, course_id: Optional[int] = None,
                       progress=None) -> Dict[str, Any]:
    """Assemble up to ``n`` questions via batched ``generate_quiz`` calls."""
    n = max(1, min(int(n or 40), 150))
    types = [t.lower() for t in (types or DEFAULT_TYPES) if t]
    if not types:
        types = list(DEFAULT_TYPES)
    weights_n = normalize_weights(weights)
    hint = _topic_hint(weights_n)
    seed_i = _seed_int(seed)
    rng = random.Random(seed_i)
    cfg = config or llm.get_config(db, course_id)

    # Prefer active course as topic target when scoping whole course
    quiz_target = target
    if scope == "course" and course and not target:
        quiz_target = course

    counts = allocate_counts(n, types)
    collected: List[Dict[str, Any]] = []
    generated = "extractive"
    provider = None
    seen: set = set()

    def _add(qs: List[Dict[str, Any]]) -> None:
        nonlocal generated, provider
        for q in qs:
            key = (q.get("question") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            collected.append(q)

    # Request by type group so parts stay balanced
    for qtype, need in counts.items():
        if need <= 0:
            continue
        have_type = 0
        for batch_n in _batch_targets(need):
            if have_type >= need:
                break
            if progress:
                progress(f"Generating {qtype} questions ({len(collected)}/{n})…",
                         min(0.9, 0.1 + 0.8 * len(collected) / max(n, 1)))
            diff = difficulty
            if difficulty == "mixed":
                diff = rng.choice(["easy", "medium", "hard"])
            prompt_types = ["short"] if qtype == "long" else [qtype]
            out = ai.generate_quiz(
                output_dir, scope=scope, target=quiz_target or target,
                types=prompt_types, difficulty=diff, n=batch_n,
                config=cfg, db=db, course_id=course_id,
                topic_hint=hint,
            )
            if out.get("generated") == "ai":
                generated = "ai"
                provider = out.get("provider")
            qs = _filter_by_types(out.get("questions") or [], [qtype] if qtype != "long" else ["short", "long"])
            if qtype == "long":
                qs = [{**q, "type": "long"} for q in qs]
            _add(qs)
            have_type = sum(1 for c in collected if c.get("type") == qtype)

    # Fill shortfall from a general extractive/AI pass
    if len(collected) < n:
        if progress:
            progress(f"Filling remaining questions ({len(collected)}/{n})…", 0.85)
        need = n - len(collected)
        out = ai.generate_quiz(
            output_dir, scope=scope, target=quiz_target or target,
            types=types, difficulty=("medium" if difficulty == "mixed" else difficulty),
            n=min(need + 5, 40), config=cfg, db=db, course_id=course_id,
            topic_hint=hint,
        )
        if out.get("generated") == "ai":
            generated = "ai"
            provider = out.get("provider")
        _add(_filter_by_types(out.get("questions") or [], types))

    # Trim / pad to type quotas then total n
    by_type: Dict[str, List[Dict[str, Any]]] = {t: [] for t in types}
    extras: List[Dict[str, Any]] = []
    for q in collected:
        t = q.get("type") or "mcq"
        if t in by_type and len(by_type[t]) < counts.get(t, n):
            by_type[t].append(q)
        else:
            extras.append(q)
    ordered: List[Dict[str, Any]] = []
    for t in types:
        bucket = by_type.get(t) or []
        while len(bucket) < counts.get(t, 0) and extras:
            q = extras.pop(0)
            q = {**q, "type": t}
            bucket.append(q)
        ordered.extend(bucket[: counts.get(t, 0)])
    if len(ordered) < n:
        ordered.extend(extras[: n - len(ordered)])
    ordered = ordered[:n]
    rng.shuffle(ordered)
    # Re-group by type for parted layout (stable part order)
    final: List[Dict[str, Any]] = []
    for t in types:
        final.extend([q for q in ordered if (q.get("type") or "") == t])
    # any leftovers
    used = set(id(q) for q in final)
    final.extend([q for q in ordered if id(q) not in used])

    return {
        "questions": final[:n],
        "generated": generated,
        "provider": provider,
        "counts": {t: sum(1 for q in final if q.get("type") == t) for t in types},
        "weights": weights_n,
        "seed": seed_i,
        "n": len(final[:n]),
        "types": list(types),
    }


def _choice_letters(options: List[str]) -> List[Tuple[str, str]]:
    letters = "abcdefghijklmnopqrstuvwxyz"
    return [(letters[i], opt) for i, opt in enumerate(options) if i < len(letters)]


def _answer_display(q: Dict[str, Any]) -> str:
    ans = str(q.get("answer") or "").strip()
    opts = q.get("options") or []
    if opts and ans:
        for i, opt in enumerate(opts):
            if str(opt).strip().lower() == ans.lower():
                return f"{chr(ord('a') + i)}) {opt}"
            if ans.lower() in ("a", "b", "c", "d") and i == ord(ans.lower()) - ord("a"):
                return f"{chr(ord('a') + i)}) {opt}"
    return ans or "(see marking guide)"


def to_markdown(meta: Dict[str, Any], questions: List[Dict[str, Any]], *,
                include_key: bool = True) -> str:
    course = meta.get("course") or "Course"
    kind = meta.get("kind") or "practice"  # practice | exam
    title = meta.get("title") or (
        f"{course} — Practice Exam ({len(questions)} questions)"
        if kind == "practice" else f"{course} — Exam ({len(questions)} questions)")
    lines = [f"# {title}", ""]
    blurb = meta.get("blurb") or (
        "Closed-book style practice pack. Work through each part in order; "
        "answers are in the key at the end." if kind == "practice"
        else "Examination paper. Attempt all questions unless told otherwise.")
    lines.append(blurb)
    if meta.get("time_minutes") or meta.get("total_marks"):
        footer = []
        if meta.get("time_minutes"):
            footer.append(f"Time allowed: {meta['time_minutes']} minutes")
        if meta.get("total_marks"):
            footer.append(f"Total marks: {meta['total_marks']}")
        lines.append(" · ".join(footer))
    weights = meta.get("weights") or {}
    if weights:
        lines.append("Topic weighting: " + "; ".join(f"{k} {v:.0f}%" for k, v in weights.items()))
    lines.append("")

    # Parts — contiguous Part A/B/C from type order
    type_order = []
    for q in questions:
        t = q.get("type") or "mcq"
        if t not in type_order:
            type_order.append(t)
    labels = _part_labels(type_order)
    num = 0
    for t in type_order:
        label, desc = labels.get(t, (f"Part · {t}", t))
        part_qs = [q for q in questions if (q.get("type") or "mcq") == t]
        if not part_qs:
            continue
        lines.append(f"## {label} — {desc}")
        lines.append("")
        for q in part_qs:
            num += 1
            lines.append(f"**{num}.** {q.get('question', '').strip()}")
            opts = q.get("options") or []
            if opts:
                for letter, opt in _choice_letters([str(o) for o in opts]):
                    lines.append(f"   {letter}) {opt}")
            lines.append("")
    if include_key:
        lines.append("## Answer key")
        lines.append("")
        num = 0
        for t in type_order:
            for q in [x for x in questions if (x.get("type") or "mcq") == t]:
                num += 1
                lines.append(f"{num}. {_answer_display(q)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_pdf(meta: Dict[str, Any], questions: List[Dict[str, Any]],
               save_path: Path, *, include_key: bool = True) -> Dict[str, Any]:
    """Render practice/exam pack as multi-page A4 PDF."""
    from fpdf import FPDF

    course = meta.get("course") or "Course"
    kind = meta.get("kind") or "practice"
    title = meta.get("title") or (
        f"{course} — Practice Exam" if kind == "practice" else f"{course} — Exam")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    margin = 14.0
    pdf.set_margins(left=margin, top=margin, right=margin)
    usable_w = 210.0 - 2 * margin

    def write_title_page() -> None:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.multi_cell(usable_w, 8, _latin1(title), align="C")
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 10)
        blurb = meta.get("blurb") or (
            "Practice pack with multiple parts. Answer all questions. "
            "An answer key is provided at the end." if kind == "practice"
            else "Examination paper. Read all instructions carefully.")
        pdf.multi_cell(usable_w, 5, _latin1(blurb))
        pdf.ln(2)
        bits = [f"{len(questions)} questions"]
        if meta.get("time_minutes"):
            bits.append(f"Time: {meta['time_minutes']} min")
        if meta.get("total_marks"):
            bits.append(f"Marks: {meta['total_marks']}")
        if meta.get("difficulty"):
            bits.append(f"Difficulty: {meta['difficulty']}")
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(usable_w, 4.5, _latin1(" · ".join(bits)))
        weights = meta.get("weights") or {}
        if weights:
            pdf.ln(2)
            pdf.set_font("Helvetica", "", 9)
            wline = "Topic weighting: " + "; ".join(
                f"{k} {v:.0f}%" for k, v in weights.items())
            pdf.multi_cell(usable_w, 4.5, _latin1(wline))
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(usable_w, 4.5, _latin1(
            "Format: Part A multiple choice; later parts short and long answer. "
            "For MCQ circle the best option."))

    write_title_page()

    type_order: List[str] = []
    for q in questions:
        t = q.get("type") or "mcq"
        if t not in type_order:
            type_order.append(t)
    labels = _part_labels(type_order)

    num = 0
    for t in type_order:
        part_qs = [q for q in questions if (q.get("type") or "mcq") == t]
        if not part_qs:
            continue
        label, desc = labels.get(t, (f"Part · {t}", t))
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(usable_w, 6, _latin1(f"{label} — {desc}"))
        pdf.ln(2)
        for q in part_qs:
            num += 1
            pdf.set_font("Helvetica", "B", 9)
            qtext = f"{num}. {str(q.get('question') or '').strip()}"
            pdf.multi_cell(usable_w, 4.5, _latin1(qtext))
            opts = q.get("options") or []
            if opts:
                pdf.set_font("Helvetica", "", 9)
                for letter, opt in _choice_letters([str(o) for o in opts]):
                    pdf.multi_cell(usable_w, 4.2, _latin1(f"   {letter}) {opt}"))
            else:
                # answer lines for short/long
                pdf.set_font("Helvetica", "", 8)
                lines_n = 3 if t == "short" else (8 if t == "long" else 2)
                for _ in range(lines_n):
                    y = pdf.get_y()
                    if y > 280:
                        pdf.add_page()
                        y = pdf.get_y()
                    pdf.line(margin + 2, y + 4, 210 - margin, y + 4)
                    pdf.set_y(y + 6)
            pdf.ln(2)

    if include_key:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(usable_w, 6, _latin1("Answer key"))
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        num = 0
        for t in type_order:
            for q in [x for x in questions if (x.get("type") or "mcq") == t]:
                num += 1
                pdf.multi_cell(usable_w, 4.5, _latin1(f"{num}. {_answer_display(q)}"))

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(save_path))
    return {"path": str(save_path), "pages": pdf.page_no(), "questions": len(questions)}


def build(output_dir: Path, *, course: str = "", n: int = 100,
          types: Optional[List[str]] = None, difficulty: str = "medium",
          scope: str = "course", target: str = "",
          weights: Optional[Dict[str, Any]] = None,
          seed: Any = None, include_answer_key: bool = True,
          time_minutes: Optional[int] = None, total_marks: Optional[int] = None,
          kind: str = "practice",  # practice | exam
          formats: Optional[List[str]] = None,  # pdf, md
          save_path: Optional[str] = None,
          config: Optional[Dict[str, Any]] = None,
          db=None, course_id: Optional[int] = None,
          progress=None) -> Dict[str, Any]:
    """End-to-end practice/exam pack: questions → PDF and/or Markdown."""
    formats = [f.lower() for f in (formats or ["pdf", "md"])]
    n = max(1, min(int(n or 40), 150))
    bundled = generate_questions(
        output_dir, n=n, types=types, difficulty=difficulty,
        scope=scope, target=target, course=course, weights=weights,
        seed=seed, config=config, db=db, course_id=course_id, progress=progress,
    )
    questions = bundled["questions"]
    if not questions:
        raise ValueError("No questions generated — import or transcribe lectures first.")

    weights_n = bundled.get("weights") or normalize_weights(weights)
    meta = {
        "course": course or "Course",
        "kind": kind if kind in ("practice", "exam") else "practice",
        "difficulty": difficulty,
        "weights": weights_n,
        "time_minutes": time_minutes,
        "total_marks": total_marks,
        "seed": bundled.get("seed"),
    }
    if meta["kind"] == "practice":
        meta["title"] = f"{meta['course']} — Practice Exam ({len(questions)} questions)"
    else:
        meta["title"] = f"{meta['course']} — Exam ({len(questions)} questions)"

    stem = core.safe_name(
        f"{course or 'course'}_{'practice' if meta['kind'] == 'practice' else 'exam'}_{len(questions)}q"
    ) or "practice_exam"
    out_dir = core.ensure_dir(output_dir / EXPORTS_DIRNAME)
    result: Dict[str, Any] = {
        "n": len(questions),
        "counts": bundled.get("counts") or {},
        "generated": bundled.get("generated"),
        "provider": bundled.get("provider"),
        "weights": weights_n,
        "seed": bundled.get("seed"),
        "kind": meta["kind"],
        "path": None,
        "md_path": None,
        "pdf_path": None,
        "format": None,
    }

    md_text = to_markdown(meta, questions, include_key=include_answer_key)
    if "md" in formats or not _have_fpdf():
        md_target = out_dir / f"{stem}.md"
        if save_path and (str(save_path).lower().endswith(".md") or "pdf" not in formats):
            md_target = Path(save_path).expanduser()
            if md_target.suffix.lower() == ".pdf":
                md_target = md_target.with_suffix(".md")
        md_target.parent.mkdir(parents=True, exist_ok=True)
        md_target.write_text(md_text, encoding="utf-8")
        result["md_path"] = str(md_target)
        result["path"] = str(md_target)
        result["format"] = "markdown"

    if "pdf" in formats:
        if not _have_fpdf():
            result["note"] = "Install fpdf2 for PDF output; wrote Markdown instead."
        else:
            if save_path and str(save_path).lower().endswith(".pdf"):
                pdf_target = Path(save_path).expanduser()
            elif save_path and "md" not in formats:
                pdf_target = Path(save_path).expanduser()
                if pdf_target.suffix.lower() != ".pdf":
                    pdf_target = pdf_target.with_suffix(".pdf")
            else:
                pdf_target = out_dir / f"{stem}.pdf"
            info = render_pdf(meta, questions, pdf_target, include_key=include_answer_key)
            result["pdf_path"] = info["path"]
            result["path"] = info["path"]
            result["pages"] = info["pages"]
            result["format"] = "pdf"

    try:
        if result.get("path"):
            result["rel"] = Path(result["path"]).relative_to(output_dir).as_posix()
    except ValueError:
        result["rel"] = None
    return result
