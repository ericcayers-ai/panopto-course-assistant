"""
notion.py — convert a Notion page exported as HTML into clean Markdown.

Notion's "Export → HTML" produces, per page, an `<article>` with an
`<h1 class="page-title">` and a `<div class="page-body">` of well-structured
blocks (headings, paragraphs, lists, tables, callouts, code, quotes). The export
download is a `.zip` (sometimes wrapping nested `ExportBlock-*.zip` parts); this
module accepts that `.zip` directly, a single `.html` page, or an unzipped folder.

It turns the export into Markdown — usable as a NotebookLM / AI source or as
study notes — using only the standard library (html.parser + zipfile), so it
needs no extra dependencies and behaves predictably on Notion's clean output.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import core

# NB: do NOT skip <header> — Notion nests the page <h1 class="page-title"> in it.
_SKIP_TAGS = {"style", "script", "head"}
_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _NotionToMarkdown(HTMLParser):
    """Streaming HTML→Markdown converter tuned for Notion exports."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []     # finished markdown blocks
        self.title: str = ""
        self.images: List[str] = []     # image srcs encountered (for asset copy)
        self._inline: str = ""          # current inline text buffer
        self._wrap_stack: List[Tuple[int, str]] = []  # (start_index, kind)
        self._href: Optional[str] = None

        self._skip_depth = 0
        self._capture_title = False
        self._got_page_title = False

        self._list_stack: List[Dict[str, Any]] = []  # {"type": ul|ol, "n": int}
        self._heading: Optional[int] = None
        self._in_pre = False
        self._pre: List[str] = []
        self._quote_depth = 0

        # table state
        self._in_table = False
        self._rows: List[List[str]] = []
        self._row: List[str] = []
        self._in_cell = False
        self._cell = ""
        self._header_row = False

    # -- helpers ----------------------------------------------------------
    def _flush_inline(self, prefix: str = "") -> None:
        text = re.sub(r"[ \t]+", " ", self._inline).strip()
        self._inline = ""
        if text:
            self.blocks.append(prefix + text)

    def _emit(self, block: str) -> None:
        self.blocks.append(block)

    # -- tag handling -----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""

        if tag in _SKIP_TAGS:
            # capture <title> text even though <head> is skipped
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = True
            return

        if self._in_cell and tag not in ("td", "th", "tr", "table", "thead", "tbody"):
            return  # inside a table cell we only collect plain text

        if tag in _HEADING_LEVEL:
            if "page-title" in cls and not self._got_page_title:
                self._capture_title = True  # the main page title
                self._heading = None
            else:
                self._flush_inline()
                self._heading = _HEADING_LEVEL[tag]
        elif tag == "p":
            self._flush_inline()
        elif tag == "br":
            self._inline += "\n"
        elif tag == "img":
            # Preserve figures/diagrams instead of dropping them. Keep the alt as a
            # caption and record the src so the asset can travel with the markdown.
            src = a.get("src", "")
            if src:
                self.images.append(src)
                self._flush_inline()
                alt = a.get("alt") or "image"
                self._emit(f"![{alt}]({src})")
        elif tag in ("ul", "ol"):
            self._flush_inline()
            self._list_stack.append({"type": tag, "n": 0})
        elif tag == "li":
            self._flush_inline()
        elif tag == "a":
            self._href = a.get("href")
            self._wrap_stack.append((len(self._inline), "a"))
        elif tag in ("strong", "b"):
            self._wrap_stack.append((len(self._inline), "**"))
        elif tag in ("em", "i"):
            self._wrap_stack.append((len(self._inline), "*"))
        elif tag == "code" and not self._in_pre:
            self._wrap_stack.append((len(self._inline), "`"))
        elif tag == "pre":
            self._flush_inline()
            self._in_pre = True
            self._pre = []
        elif tag == "blockquote":
            self._flush_inline()
            self._quote_depth += 1
        elif tag == "hr":
            self._flush_inline()
            self._emit("---")
        elif tag == "table":
            self._flush_inline()
            self._in_table = True
            self._rows = []
        elif tag == "tr":
            self._row = []
            self._header_row = False
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell = ""
            if tag == "th":
                self._header_row = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = False
            return

        if tag in _HEADING_LEVEL:
            if self._capture_title and not self._got_page_title:
                self.title = re.sub(r"\s+", " ", self._inline).strip()
                self._inline = ""
                self._capture_title = False
                self._got_page_title = True
            else:
                self._flush_inline("#" * (self._heading or 1) + " ")
                self._heading = None
        elif tag == "p":
            if self._quote_depth:
                self._flush_inline("> ")
            else:
                self._flush_inline()
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "li":
            self._close_inline_wraps()
            text = re.sub(r"[ \t]+", " ", self._inline).strip()
            self._inline = ""
            if text:
                depth = max(0, len(self._list_stack) - 1)
                indent = "  " * depth
                top = self._list_stack[-1] if self._list_stack else {"type": "ul", "n": 0}
                if top["type"] == "ol":
                    top["n"] += 1
                    self._emit(f"{indent}{top['n']}. {text}")
                else:
                    self._emit(f"{indent}- {text}")
        elif tag == "a":
            self._wrap("a")
        elif tag in ("strong", "b"):
            self._wrap("**")
        elif tag in ("em", "i"):
            self._wrap("*")
        elif tag == "code" and not self._in_pre:
            self._wrap("`")
        elif tag == "pre":
            self._in_pre = False
            code = "".join(self._pre).rstrip("\n")
            self._emit(f"```\n{code}\n```")
            self._pre = []
        elif tag == "blockquote":
            self._flush_inline("> ")
            if self._quote_depth:
                self._quote_depth -= 1
        elif tag in ("td", "th"):
            self._in_cell = False
            self._row.append(re.sub(r"\s+", " ", self._cell).strip())
            self._cell = ""
        elif tag == "tr":
            if self._row:
                self._rows.append(self._row)
            self._row = []
        elif tag == "table":
            self._emit_table()
            self._in_table = False
            self._rows = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._capture_title or (self._heading is not None) or (not self._in_cell and not self._in_pre):
            self._inline += data
        if self._in_pre:
            self._pre.append(data)
        elif self._in_cell:
            self._cell += data

    # -- inline wrap helpers ---------------------------------------------
    def _wrap(self, kind: str) -> None:
        if not self._wrap_stack:
            return
        start, k = self._wrap_stack.pop()
        if k != kind:
            return
        inner = self._inline[start:]
        if kind == "a":
            href = self._href or ""
            self._href = None
            if inner.strip():
                self._inline = self._inline[:start] + f"[{inner}]({href})"
        else:
            if inner.strip():
                self._inline = self._inline[:start] + f"{kind}{inner}{kind}"

    def _close_inline_wraps(self) -> None:
        while self._wrap_stack:
            start, kind = self._wrap_stack[-1]
            self._wrap(kind if kind != "a" else "a")
            if self._wrap_stack and self._wrap_stack[-1][0] == start:
                self._wrap_stack.pop()  # safety: avoid infinite loop

    def _emit_table(self) -> None:
        rows = [r for r in self._rows if any(c.strip() for c in r)]
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join("---" for _ in range(width)) + " |"]
        for r in rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        self._emit("\n".join(lines))

    # -- result -----------------------------------------------------------
    _LIST_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s")

    def markdown(self) -> str:
        self._flush_inline()
        blocks = [b for b in self.blocks if b.strip()]
        # Join blocks with a blank line, except keep consecutive list items tight.
        body = ""
        for i, b in enumerate(blocks):
            if i == 0:
                body = b
            elif self._LIST_RE.match(b) and self._LIST_RE.match(blocks[i - 1]):
                body += "\n" + b
            else:
                body += "\n\n" + b
        title = self.title.strip()
        if title:
            return f"# {title}\n\n{body}".strip() + "\n"
        return body.strip() + "\n"


def html_to_markdown(raw_html: str) -> Tuple[str, str]:
    """Convert one Notion HTML page to (markdown, title)."""
    parser = _NotionToMarkdown()
    parser.feed(raw_html)
    return parser.markdown(), parser.title


def html_to_markdown_with_images(raw_html: str) -> Tuple[str, str, List[str]]:
    """Like :func:`html_to_markdown` but also returns the image srcs referenced,
    so the caller can copy those assets next to the output Markdown."""
    parser = _NotionToMarkdown()
    parser.feed(raw_html)
    return parser.markdown(), parser.title, parser.images


def _looks_like_asset(p: Path) -> bool:
    return p.suffix.lower() not in (".html", ".htm")


def _extract_zip_tree(zip_path: Path, dest: Path) -> None:
    """Extract a Notion export zip into ``dest``, recursively unpacking any nested
    zips (Notion wraps big exports as an outer zip containing
    ``ExportBlock-*.zip`` parts). Skips entries that would escape ``dest``."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue  # zip-slip guard
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    # recurse into any nested zips that were just written
    for nested in list(dest.rglob("*.zip")):
        sub = nested.parent / (nested.stem + "_unzipped")
        sub.mkdir(parents=True, exist_ok=True)
        try:
            _extract_zip_tree(nested, sub)
            nested.unlink()  # tidy: drop the inner archive once expanded
        except zipfile.BadZipFile:
            pass


def convert_notion_export(
    input_path: Path,
    output_dir: Path,
    dest_name: str = "_notion",
    combined: bool = False,
) -> Dict[str, Any]:
    """Convert a Notion HTML page or export folder into Markdown under
    ``output_dir/_notion/`` (mirroring folder structure). Returns written files."""
    input_path = Path(input_path).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    # A Notion "Export → HTML" download is a .zip (often wrapping nested
    # ExportBlock-*.zip parts). Unpack it to a temp dir and convert that.
    # A *folder* may hold several such .zip exports (multiple courses/pages) —
    # extract every one of them into a staging tree and convert the lot together.
    tmp_dir: Optional[Path] = None
    if input_path.is_file() and input_path.suffix.lower() == ".zip":
        tmp_dir = Path(tempfile.mkdtemp(prefix="notion_"))
        _extract_zip_tree(input_path, tmp_dir)
        input_path = tmp_dir
    elif input_path.is_dir():
        zips = sorted(input_path.glob("*.zip"))
        if zips:
            tmp_dir = Path(tempfile.mkdtemp(prefix="notion_"))
            for z in zips:
                _extract_zip_tree(z, tmp_dir / core.safe_name(z.stem))
            # also carry along any loose .html already sitting in the folder
            for f in input_path.rglob("*.htm*"):
                if _looks_like_asset(f):
                    continue
                rel = f.relative_to(input_path)
                target = tmp_dir / "_loose" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
            input_path = tmp_dir

    try:
        return _convert_notion_tree(input_path, output_dir, dest_name, combined)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _copy_notion_assets(md: str, images: List[str], src_dir: Path,
                       target_dir: Path, stem: str) -> str:
    """Copy locally-referenced images into ``<stem>_assets/`` and rewrite their
    Markdown links. Remote (http) images are left as-is. Returns the new markdown."""
    from urllib.parse import unquote
    assets_rel = f"{stem}_assets"
    assets_dir = target_dir / assets_rel
    n = 0
    for src in images:
        if src.startswith(("http://", "https://", "data:")):
            continue
        local = (src_dir / unquote(src)).resolve()
        if not local.is_file():
            continue
        n += 1
        dest_name_ = f"image{n:02d}{local.suffix.lower()}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(local, assets_dir / dest_name_)
        except OSError:
            continue
        # rewrite both the raw and URL-encoded forms of the original src
        new_rel = f"{assets_rel}/{dest_name_}"
        md = md.replace(f"]({src})", f"]({new_rel})")
    return md


def _convert_notion_tree(
    input_path: Path,
    output_dir: Path,
    dest_name: str,
    combined: bool,
) -> Dict[str, Any]:
    dest_root = core.ensure_dir(output_dir / dest_name)
    written: List[str] = []
    docs: List[Tuple[str, str]] = []  # (title, markdown)

    if input_path.is_file():
        files = [input_path]
        base = input_path.parent
    else:
        files = sorted(p for p in input_path.rglob("*.htm*") if not _looks_like_asset(p))
        base = input_path
    if not files:
        raise ValueError("No .html pages found to convert.")

    for f in files:
        try:
            md, title, images = html_to_markdown_with_images(
                f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not md.strip():
            continue
        rel = f.relative_to(base) if input_path.is_dir() else Path(f.name)
        # Notion appends a 32-char hash to filenames; strip it for readability.
        clean_stem = re.sub(r"\s+[0-9a-f]{32}$", "", rel.stem)
        # Drop temp extraction wrappers and Notion's hashed folder names so the
        # library path stays clean (e.g. "_notion/COMPX234.md", not
        # "_notion/ExportBlock-…_unzipped/…").
        clean_parts = [
            re.sub(r"\s+[0-9a-f]{32}$", "", p)
            for p in rel.parent.parts
            if not (p.endswith("_unzipped") or p.startswith("ExportBlock-"))
        ]
        sub = Path(*clean_parts) if clean_parts else Path(".")
        target_dir = core.ensure_dir(dest_root / sub) if str(sub) != "." else dest_root
        out_file = target_dir / f"{core.safe_name(clean_stem)}.md"
        # Copy any local images this page references next to the .md and rewrite
        # the links, so Notion diagrams/screenshots survive the conversion.
        if images:
            md = _copy_notion_assets(md, images, f.parent, target_dir,
                                    core.safe_name(clean_stem))
        out_file.write_text(md, encoding="utf-8")
        written.append(out_file.relative_to(output_dir).as_posix())
        docs.append((title or clean_stem, md))

    combined_path = None
    if combined and docs:
        parts = ["# Notion export", ""]
        parts += [f"- {t}" for t, _ in docs]
        body = "\n\n---\n\n".join(core.with_heading(t, md) for t, md in docs)
        combined_file = dest_root / "notion_pack.md"
        combined_file.write_text("\n".join(parts) + "\n\n---\n\n" + body + "\n", encoding="utf-8")
        combined_path = combined_file.relative_to(output_dir).as_posix()

    return {"count": len(written), "dest": str(dest_root), "files": written, "combined": combined_path}
