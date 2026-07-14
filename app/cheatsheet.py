"""
cheatsheet.py - generate a dense exam cheat sheet PDF from the course library.

Style target: numbered topic blocks ("1 · TITLE"), telegraphic fragments, dense
two-column A4 packing (COMPX234 sample voice). Content that does not fit the
page budget is truncated in document order (later sections/points are dropped).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ai, core, llm

EXPORTS_DIRNAME = "_exports"

_SYSTEM = (
    "You compile a dense exam cheat sheet in the style of a printed revision "
    "card. Extract ONLY the highest-value exam content: definitions, formulas, "
    "rules, classifications, state transitions, and testable facts. "
    "Use TELEGRAPHIC fragments - no filler sentences. "
    "Organise as Markdown numbered sections exactly like:\n"
    "## 1 · TOPIC NAME\n"
    "- telegraphic point\n"
    "- formula or rule\n"
    "Do not invent content; ground everything in the material."
)


def _have_fpdf() -> bool:
    import importlib.util
    return importlib.util.find_spec("fpdf") is not None


def _latin1(s: str) -> str:
    """fpdf2 core fonts are Latin-1; map common Unicode punctuation to ASCII."""
    repl = {
        "—": "-", "–": "-", "−": "-",
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "…": "...", "•": "-", " ": " ",
        "→": "->", "⇒": "=>", "≤": "<=", "≥": ">=",
        "×": "x", "≈": "~", "≠": "!=",
        "▼": "v", "▲": "^", "◄": "<", "►": ">",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _course_selection(output_dir: Path, course: str) -> Optional[List[str]]:
    """Restrict chunks to lectures whose metadata mentions the active course."""
    course = (course or "").strip()
    if not course:
        return None
    from . import lectures
    needle = course.lower()
    base = course.split("-")[0].lower() if "-" in course else needle
    picked: List[str] = []
    for lec in lectures.iter_lectures(output_dir, with_text=False):
        blob = " ".join([
            str(lec.get("course") or ""),
            str(lec.get("title") or ""),
            str(lec.get("folder") or ""),
            str(lec.get("stem") or ""),
            str(lec.get("path") or ""),
        ]).lower()
        if needle in blob or (base and base in blob):
            picked.append(f"{lec['folder']}/{lec['stem']}".strip("/") if lec.get("folder") else lec["stem"])
    return picked or None


def _bullets_from(text: str) -> List[str]:
    """Extract terse bullets; drop headings and echoed paragraphs."""
    out: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^(?:[-*•]|\d+[.)]|N\s*·)\s*", "", s).strip()
        if 3 <= len(s) <= 220:
            out.append(s)
    return out


def _renumber_sections(md: str) -> str:
    """Force '## N · Title' numbering for sample-matching section headers."""
    parts: List[str] = []
    n = 0
    for raw in (md or "").splitlines():
        m = re.match(r"^#{1,6}\s+(?:\d+\s*[·.\-:]+\s*)?(.*)$", raw.strip())
        if m:
            n += 1
            title = m.group(1).strip() or f"Topic {n}"
            parts.append(f"## {n} · {title}")
        else:
            parts.append(raw)
    return "\n".join(parts)


def condense(output_dir: Path, course: str, max_pages: int,
             config: Dict[str, Any],
             selection: Optional[List[str]] = None) -> str:
    """Map-reduce course material into numbered telegraphic cheat-sheet Markdown."""
    sel = selection if selection is not None else _course_selection(output_dir, course)
    chunks = ai._llm_chunks(output_dir, sel, size=4500, max_chunks=16)
    if not chunks:
        return ""
    budget_words = max(250, int(max_pages) * 700)
    per = min(10, max(3, budget_words // len(chunks) // 7))

    sections: Dict[str, List[str]] = {}
    order: List[str] = []
    for title, chunk in chunks:
        prompt = (
            f"From this excerpt of '{title}', list up to {per} of the most "
            f"exam-relevant points as terse '- ' bullets "
            f"(definitions, formulas, rules, key facts). No preamble.\n\n{chunk}")
        try:
            out = llm.complete(prompt, system=_SYSTEM, config=config)
        except llm.LLMError:
            out = ""
        bullets = _bullets_from(out)
        if not bullets:
            continue
        if title not in sections:
            sections[title] = []
            order.append(title)
        sections[title].extend(bullets)

    parts: List[str] = []
    for i, title in enumerate(order, 1):
        seen: set = set()
        body: List[str] = []
        for b in sections[title]:
            k = b.lower()
            if k not in seen:
                seen.add(k)
                body.append(f"- {b}")
        if body:
            parts.append(f"## {i} · {title}")
            parts.extend(body)
    return "\n".join(parts).strip()


def _extractive_cheatsheet(output_dir: Path, course: str,
                           selection: Optional[List[str]] = None) -> str:
    """Dependency-free sheet so empty model replies never yield a blank PDF."""
    from . import keywords, lectures
    sel = selection if selection is not None else _course_selection(output_dir, course)
    wanted = set(sel or [])
    parts: List[str] = []
    idx = 0
    for lec in lectures.iter_lectures(output_dir):
        if wanted:
            key = f"{lec['folder']}/{lec['stem']}".strip("/") if lec.get("folder") else lec["stem"]
            if lec["stem"] not in wanted and key not in wanted and lec.get("path") not in wanted:
                continue
        text = (lec.get("text") or "").strip()
        if not text:
            continue
        idx += 1
        parts.append(f"## {idx} · {lec['title']}")
        for s in core.summarize_text(text, max_sentences=6):
            parts.append(f"- {s}")
        phr = keywords.key_phrases(text, limit=6)
        if phr:
            parts.append("- Key terms: " + ", ".join(p["phrase"] for p in phr))
    return "\n".join(parts).strip()


def _parse_blocks(md: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            blocks.append((f"h{min(level, 3)}", m.group(2).strip()))
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            blocks.append(("bullet", m.group(1).strip()))
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            blocks.append(("bullet", m.group(1).strip()))
            continue
        blocks.append(("para", line.strip()))
    return blocks


def render_pdf(md: str, save_path: Path, *, title: str = "", max_pages: int = 1) -> Dict[str, Any]:
    """Render cheat-sheet Markdown into a compact two-column A4 PDF."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(left=7, top=7, right=7)

    page_w, page_h = 210.0, 297.0
    margin = 7.0
    gutter = 5.0
    usable_h = page_h - 2 * margin
    col_w = (page_w - 2 * margin - gutter) / 2.0
    col_x = [margin, margin + col_w + gutter]

    state = {"page": 0, "col": 0, "y": margin, "content_top": margin, "truncated": False}

    def new_page() -> bool:
        if state["page"] >= max_pages:
            state["truncated"] = True
            return False
        pdf.add_page()
        state["page"] += 1
        state["col"] = 0
        state["y"] = margin
        state["content_top"] = margin
        if state["page"] == 1 and title:
            pdf.set_xy(margin, state["y"])
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(page_w - 2 * margin, 4.5, _latin1(title), align="C")
            state["y"] = pdf.get_y() + 1.2
            state["content_top"] = state["y"]
        return True

    def fits_or_advance(h: float) -> bool:
        if state["y"] + h <= margin + usable_h:
            return True
        if state["col"] == 0:
            state["col"] = 1
            state["y"] = state["content_top"]
            return state["y"] + h <= margin + usable_h
        return new_page()

    styles = {
        "h1": ("Helvetica", "B", 8.0, 3.6),
        "h2": ("Helvetica", "B", 7.2, 3.3),
        "h3": ("Helvetica", "BI", 6.8, 3.1),
        "bullet": ("Helvetica", "", 6.2, 2.9),
        "para": ("Helvetica", "", 6.2, 2.9),
    }

    new_page()
    for kind, text in _parse_blocks(md):
        font, style, size, lh = styles.get(kind, styles["para"])
        body = _latin1(("- " + text) if kind == "bullet" else text)
        pdf.set_font(font, style, size)
        h = pdf.multi_cell(col_w, lh, body, dry_run=True, output="HEIGHT")
        if kind in ("h1", "h2", "h3"):
            h += 0.6
        if not fits_or_advance(h):
            break
        pdf.set_xy(col_x[state["col"]], state["y"])
        pdf.set_font(font, style, size)
        pdf.multi_cell(col_w, lh, body, align="L")
        state["y"] = pdf.get_y() + (0.6 if kind.startswith("h") else 0.2)

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(save_path))
    return {"path": str(save_path), "pages": state["page"], "truncated": state["truncated"]}


def build(output_dir: Path, *, course: str, max_pages: int,
          config: Dict[str, Any], save_path: Optional[str] = None,
          selection: Optional[List[str]] = None) -> Dict[str, Any]:
    """End-to-end: condense, render capped PDF (or Markdown fallback)."""
    max_pages = max(1, min(int(max_pages or 1), 10))
    sel = selection if selection is not None else _course_selection(output_dir, course)
    md = ""
    generated = "extractive"
    if llm.is_enabled(config):
        md = condense(output_dir, course, max_pages, config, selection=sel)
        generated = "ai"
    if not md.strip():
        md = _extractive_cheatsheet(output_dir, course, selection=sel)
        generated = "extractive"
    if not md.strip():
        raise ValueError("No course material found to build a cheat sheet from. "
                         "Import or transcribe some lectures first.")
    md = _renumber_sections(md)

    stem = core.safe_name(f"{course or 'course'}_cheatsheet") or "cheatsheet"
    if _have_fpdf():
        target = Path(save_path).expanduser() if save_path else \
            core.ensure_dir(output_dir / EXPORTS_DIRNAME) / f"{stem}.pdf"
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")
        info = render_pdf(md, target, title=f"{course or 'Course'} - Exam Cheat Sheet",
                          max_pages=max_pages)
        rel = None
        try:
            rel = Path(info["path"]).relative_to(output_dir).as_posix()
        except ValueError:
            pass
        return {"format": "pdf", "path": info["path"], "rel": rel,
                "pages": info["pages"], "max_pages": max_pages,
                "truncated": info["truncated"], "generated": generated,
                "provider": config.get("provider"),
                "note": ("Page budget full — remaining content was truncated in document order."
                         if info["truncated"] else "")}
    target = Path(save_path).expanduser() if save_path else \
        core.ensure_dir(output_dir / EXPORTS_DIRNAME) / f"{stem}.md"
    if target.suffix.lower() == ".pdf":
        target = target.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {course or 'Course'} - Exam Cheat Sheet\n\n{md}\n", encoding="utf-8")
    return {"format": "markdown", "path": str(target), "rel": None,
            "max_pages": max_pages, "generated": generated,
            "provider": config.get("provider"),
            "note": "Install the 'fpdf2' package for PDF output; wrote Markdown instead."}
