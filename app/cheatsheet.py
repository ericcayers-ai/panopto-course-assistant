"""
cheatsheet.py - generate a dense exam cheat sheet PDF from the course library.

The model condenses the course material into the most exam-relevant facts,
formulas and definitions, and the result is laid out in a compact two-column A4
PDF bounded to a caller-specified page limit. Content that does not fit within
the limit is dropped (most-important-first), so the page budget is always
respected.

Requires the optional ``fpdf2`` dependency for PDF output; without it the
condensed notes are written as Markdown instead, and the caller is told.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ai, core, llm

EXPORTS_DIRNAME = "_exports"

_SYSTEM = (
    "You are an expert tutor compiling a single-sheet exam cheat sheet. From the "
    "provided course material, extract ONLY the highest-value, exam-relevant "
    "content: key definitions, formulas, rules, classifications, and the kind of "
    "facts most likely to be tested. Be extremely concise - telegraphic phrasing, "
    "no filler, no full sentences where a fragment will do. Organise under short "
    "topic headings using Markdown: '## Heading' for topics and '- ' for points. "
    "Do not invent content; ground everything in the material."
)


def _have_fpdf() -> bool:
    import importlib.util
    return importlib.util.find_spec("fpdf") is not None


def _latin1(s: str) -> str:
    """fpdf2 core fonts are Latin-1; map common Unicode punctuation to ASCII and
    drop anything else so rendering never errors."""
    repl = {
        "—": "-", "–": "-", "−": "-",
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "…": "...", "•": "-", " ": " ",
        "→": "->", "⇒": "=>", "≤": "<=", "≥": ">=",
        "×": "x", "≈": "~", "≠": "!=",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _gather(output_dir: Path, course: str) -> str:
    """All transcript + document text for the course, as one block."""
    return ai.collect_text(output_dir, "course", "", max_chars=60000)


def condense(output_dir: Path, course: str, max_pages: int,
             config: Dict[str, Any]) -> str:
    """Ask the model for cheat-sheet Markdown sized to roughly ``max_pages`` pages."""
    text = _gather(output_dir, course)
    if not text.strip():
        return ""
    # ~700 dense words per compact two-column A4 page is a realistic target.
    budget_words = max(250, int(max_pages) * 700)
    prompt = (
        f"Course: {course or 'this course'}.\n"
        f"Produce a condensed exam cheat sheet of about {budget_words} words "
        f"(it must fit on {max_pages} A4 page(s) in a small two-column layout). "
        f"Prioritise the most testable material; omit anything low-value.\n\n"
        f"Material:\n{text}"
    )
    out = llm.complete(prompt, system=_SYSTEM, config=config)
    return (out or "").strip()


# ---------------------------------------------------------------------------
# Markdown -> blocks -> compact two-column PDF
# ---------------------------------------------------------------------------

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
    """Render cheat-sheet Markdown into a compact two-column A4 PDF, capped at
    ``max_pages``. Returns ``{path, pages, truncated}``."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(left=8, top=8, right=8)

    page_w, page_h = 210.0, 297.0
    margin = 8.0
    gutter = 6.0
    usable_h = page_h - 2 * margin
    col_w = (page_w - 2 * margin - gutter) / 2.0
    col_x = [margin, margin + col_w + gutter]

    state = {"page": 0, "col": 0, "y": margin, "truncated": False}

    def new_page() -> bool:
        if state["page"] >= max_pages:
            state["truncated"] = True
            return False
        pdf.add_page()
        state["page"] += 1
        state["col"] = 0
        state["y"] = margin
        if state["page"] == 1 and title:
            pdf.set_xy(margin, state["y"])
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(page_w - 2 * margin, 5, _latin1(title), align="C")
            state["y"] = pdf.get_y() + 1.5
        return True

    def fits_or_advance(h: float) -> bool:
        # Move to the next column / page when the current column is full.
        if state["y"] + h <= margin + usable_h:
            return True
        if state["col"] == 0:
            state["col"] = 1
            state["y"] = margin
            if title and state["page"] == 1:
                state["y"] = margin  # second column starts at the top margin
            return state["y"] + h <= margin + usable_h
        return new_page()

    styles = {
        "h1": ("Helvetica", "B", 8.5, 4.0),
        "h2": ("Helvetica", "B", 7.5, 3.6),
        "h3": ("Helvetica", "BI", 7.0, 3.4),
        "bullet": ("Helvetica", "", 6.5, 3.1),
        "para": ("Helvetica", "", 6.5, 3.1),
    }

    new_page()
    for kind, text in _parse_blocks(md):
        font, style, size, lh = styles.get(kind, styles["para"])
        body = _latin1(("- " + text) if kind == "bullet" else text)
        pdf.set_font(font, style, size)
        # Measure wrapped height in the column width.
        h = pdf.multi_cell(col_w, lh, body, dry_run=True, output="HEIGHT")
        if kind in ("h1", "h2", "h3"):
            h += 0.8
        if not fits_or_advance(h):
            break
        pdf.set_xy(col_x[state["col"]], state["y"])
        pdf.set_font(font, style, size)
        pdf.multi_cell(col_w, lh, body, align="L")
        state["y"] = pdf.get_y() + (0.8 if kind.startswith("h") else 0.3)

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(save_path))
    return {"path": str(save_path), "pages": state["page"], "truncated": state["truncated"]}


def build(output_dir: Path, *, course: str, max_pages: int,
          config: Dict[str, Any], save_path: Optional[str] = None) -> Dict[str, Any]:
    """End-to-end: condense the course, render the capped PDF (or Markdown
    fallback). Returns a result dict with the written path and metadata."""
    max_pages = max(1, min(int(max_pages or 1), 10))
    md = condense(output_dir, course, max_pages, config)
    if not md.strip():
        raise ValueError("No course material found to build a cheat sheet from. "
                         "Import or transcribe some lectures first.")

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
                "truncated": info["truncated"], "generated": "ai",
                "provider": config.get("provider")}

    # No PDF engine: write the condensed notes as Markdown so the work isn't lost.
    target = Path(save_path).expanduser() if save_path else \
        core.ensure_dir(output_dir / EXPORTS_DIRNAME) / f"{stem}.md"
    if target.suffix.lower() == ".pdf":
        target = target.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {course or 'Course'} - Exam Cheat Sheet\n\n{md}\n", encoding="utf-8")
    return {"format": "markdown", "path": str(target), "rel": None,
            "max_pages": max_pages, "generated": "ai",
            "provider": config.get("provider"),
            "note": "Install the 'fpdf2' package for PDF output; wrote Markdown instead."}
