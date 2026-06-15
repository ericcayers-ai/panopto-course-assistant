"""
core.py — engine-independent logic for the Panopto Course Assistant.

Adapted from the original single-file CLI tool (panopto_course_assistant.py).
Everything here works with only the standard library + `requests`; the heavy
transcription engines (whisper / faster-whisper / torch) are imported lazily by
``transcribe.py`` so the rest of the app runs without a GPU stack installed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LectureItem:
    title: str
    url: str
    size: int = 0
    duration: int = 0
    pub_date: str = ""
    author: str = ""
    summary: str = ""
    guid: str = ""

    @property
    def week(self) -> Optional[int]:
        return infer_week(self.title)

    @property
    def topic(self) -> str:
        return infer_topic(self.title)

    @property
    def date_obj(self) -> Optional[dt.date]:
        return parse_pubdate(self.pub_date)

    @property
    def safe_title(self) -> str:
        return safe_name(self.title)

    def to_dict(self) -> Dict[str, Any]:
        d = self.date_obj
        return {
            "title": self.title,
            "url": self.url,
            "size": self.size,
            "size_human": human_size(self.size),
            "duration": self.duration,
            "duration_human": human_duration(self.duration),
            "pub_date": self.pub_date,
            "date": d.isoformat() if d else None,
            "author": self.author,
            "summary": self.summary,
            "guid": self.guid,
            "week": self.week,
            "topic": self.topic,
            "safe_title": self.safe_title,
        }


ORG_CHOICES = ["none", "date", "week", "topic"]
OUTPUT_CHOICES = ["txt", "srt", "vtt", "md", "json", "notebooklm"]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def safe_name(text: str, max_len: int = 120) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "")
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    text = text.strip("._ ")
    return (text[:max_len] if text else "lecture") or "lecture"


def human_duration(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "?"
    return str(dt.timedelta(seconds=int(seconds)))


def human_size(num_bytes: int) -> str:
    if not num_bytes or num_bytes <= 0:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def parse_pubdate(value: str) -> Optional[dt.date]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except Exception:
            pass
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception:
        return None


def infer_week(title: str) -> Optional[int]:
    m = re.search(r"\b(?:week|w)[_\-\s]*0*(\d{1,2})(?!\d)", title or "", flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def infer_topic(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"\b(old|draft|rev(?:ision)?|part\s*\d+)\b", "", title, flags=re.I)
    title = re.sub(r"[_\-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    m = re.match(r"(?i)(?:week|w)\s*0*\d+\s*(.*)$", title)
    if m and m.group(1).strip():
        title = m.group(1).strip()
    if not title:
        return "uncategorized"
    return safe_name(title, 60)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------

ITUNES_NS = [
    "https://www.itunes.com/dtds/podcast-1.0.dtd",
    "http://www.itunes.com/dtds/podcast-1.0.dtd",
]


def _itunes(item, tag):
    for ns in ITUNES_NS:
        el = item.find(f"{{{ns}}}{tag}")
        if el is not None:
            return el
    return None


def parse_feed_bytes(raw: bytes) -> List[LectureItem]:
    """Parse Panopto RSS podcast XML (already-fetched bytes) into lecture items."""
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        return []

    items: List[LectureItem] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        url = (enclosure.get("url") or "").strip()
        if not url:
            continue
        title = (item.findtext("title") or "Untitled").strip()
        dur_el = _itunes(item, "duration")
        summary_el = _itunes(item, "summary")
        summary = (
            item.findtext("description")
            or (summary_el.text if summary_el is not None else "")
            or ""
        )
        author_el = _itunes(item, "author")
        author = (author_el.text if author_el is not None else None) or item.findtext("author") or ""
        try:
            size = int(enclosure.get("length", "0") or 0)
        except Exception:
            size = 0
        try:
            duration = int(dur_el.text) if dur_el is not None and dur_el.text else 0
        except Exception:
            duration = 0
        items.append(
            LectureItem(
                title=title,
                url=url,
                size=size,
                duration=duration,
                pub_date=(item.findtext("pubDate") or "").strip(),
                author=(author or "").strip(),
                summary=(summary or "").strip(),
                guid=(item.findtext("guid") or "").strip(),
            )
        )
    return items


def parse_feed(source: str, cookies: str = "") -> List[LectureItem]:
    """Parse a feed from a URL or a local XML path."""
    if not source:
        raise ValueError("feed source is empty")
    p = Path(source)
    if p.exists():
        return parse_feed_bytes(p.read_bytes())

    import requests  # local import keeps requests optional for pure-XML callers

    session = requests.Session()
    if cookies:
        try:
            from http.cookiejar import MozillaCookieJar

            cj = MozillaCookieJar(cookies)
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies = cj
        except Exception:
            pass
    r = session.get(source, timeout=60)
    r.raise_for_status()
    return parse_feed_bytes(r.content)


def channel_title(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
        channel = root.find("channel")
        if channel is not None:
            return (channel.findtext("title") or "").strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


def organization_folder(item: LectureItem, mode: str) -> str:
    mode = (mode or "none").lower()
    if mode == "none":
        return ""
    if mode == "date":
        d = item.date_obj
        return d.strftime("%Y-%m-%d") if d else "unknown-date"
    if mode == "week":
        w = item.week
        return f"Week_{w:02d}" if w is not None else "unparsed-week"
    if mode == "topic":
        return item.topic
    return ""


def output_dir_for(base: Path, item: LectureItem, mode: str) -> Path:
    folder = organization_folder(item, mode)
    return base / folder if folder else base


# ---------------------------------------------------------------------------
# Transcript writers (timestamps + formats)
# ---------------------------------------------------------------------------


def ts_hhmmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ts_srt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_vtt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def render_txt(segments: List[Dict[str, Any]], interval: int) -> str:
    blocks: List[str] = []
    current_bucket = None
    current_start = 0.0
    words: List[str] = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        bucket = int(start // max(1, interval))
        if current_bucket is None:
            current_bucket, current_start = bucket, start
        if bucket != current_bucket:
            if words:
                blocks.append(f"[{ts_hhmmss(current_start)}]  {' '.join(words).strip()}")
            current_bucket, current_start, words = bucket, start, [text]
        else:
            words.append(text)
    if words:
        blocks.append(f"[{ts_hhmmss(current_start)}]  {' '.join(words).strip()}")
    return "\n\n".join(blocks).strip() + "\n"


def render_srt(segments: List[Dict[str, Any]]) -> str:
    lines, n = [], 1
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = max(float(seg.get("end", start)), start)
        lines.append(f"{n}\n{ts_srt(start)} --> {ts_srt(end)}\n{text}\n")
        n += 1
    return "\n".join(lines).strip() + "\n"


def render_vtt(segments: List[Dict[str, Any]]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = max(float(seg.get("end", start)), start)
        lines += [f"{ts_vtt(start)} --> {ts_vtt(end)}", text, ""]
    return "\n".join(lines).strip() + "\n"


def render_md(item: LectureItem, segments: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    lines = [f"# {item.title}", ""]
    lines += [
        f"- **Published:** {item.pub_date or 'unknown'}",
        f"- **Author:** {item.author or 'unknown'}",
        f"- **Duration:** {human_duration(item.duration)}",
        f"- **Engine:** {meta.get('engine', '')}",
        f"- **Model:** {meta.get('model', '')}",
        f"- **Language:** {meta.get('language') or 'unknown'}",
    ]
    if item.week is not None:
        lines.append(f"- **Week:** {item.week}")
    lines += ["", "## Transcript", ""]
    for seg in segments:
        txt = (seg.get("text") or "").strip()
        if not txt:
            continue
        lines += [f"### {ts_hhmmss(float(seg.get('start', 0.0)))}", "", txt, ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# NotebookLM-friendly rendering
#
# NotebookLM works best with clean, readable prose: a clear title, a compact
# metadata line for grounding/citations, and continuous paragraphs WITHOUT
# per-segment timestamps (timestamps fragment sentences and add noise). These
# helpers turn whisper segments — or an already-written .txt — into that shape.
# ---------------------------------------------------------------------------

# Matches a leading grouped timestamp like "[00:12:30]  " produced by render_txt.
_TS_PREFIX = re.compile(r"^\[\d{1,2}:\d{2}:\d{2}\]\s*")


def paragraphs_from_texts(texts: List[str], target_chars: int = 700) -> List[str]:
    """Merge many short fragments into readable paragraphs of ~target_chars."""
    paragraphs: List[str] = []
    buf = ""
    for raw in texts:
        t = (raw or "").strip()
        if not t:
            continue
        buf = f"{buf} {t}".strip() if buf else t
        if len(buf) >= target_chars and re.search(r"[.!?]\"?$", buf):
            paragraphs.append(buf)
            buf = ""
    if buf:
        paragraphs.append(buf)
    return paragraphs


def notebooklm_header(item: LectureItem, course: str = "") -> List[str]:
    bits = []
    if item.week is not None:
        bits.append(f"Week {item.week}")
    d = item.date_obj
    if d:
        bits.append(d.isoformat())
    if item.duration:
        bits.append(human_duration(item.duration))
    source_line = "Source: lecture transcript"
    if course:
        source_line += f" — {course}"
    lines = [f"# {item.title}", ""]
    if bits:
        lines.append("> " + "  ·  ".join(bits))
    lines.append(f"> {source_line}")
    lines.append("")
    return lines


def render_notebooklm(item: LectureItem, segments: List[Dict[str, Any]], course: str = "") -> str:
    """Clean, de-timestamped Markdown optimised for a NotebookLM source."""
    texts = [(s.get("text") or "").strip() for s in segments]
    paragraphs = paragraphs_from_texts(texts)
    header = "\n".join(notebooklm_header(item, course)).rstrip()
    return (header + "\n\n" + "\n\n".join(paragraphs)).strip() + "\n"


def clean_txt_to_notebooklm(raw_txt: str, title: str = "", course: str = "") -> str:
    """Convert an existing grouped-timestamp .txt transcript into NotebookLM prose."""
    blocks = [b.strip() for b in raw_txt.split("\n\n") if b.strip()]
    texts = [_TS_PREFIX.sub("", b).replace("\n", " ").strip() for b in blocks]
    paragraphs = paragraphs_from_texts(texts)
    header = [f"# {title}", "", "> Source: lecture transcript" + (f" — {course}" if course else ""), ""] if title else []
    return ("\n".join(header) + "\n" + "\n\n".join(paragraphs)).strip() + "\n"


def write_outputs(
    item: LectureItem,
    segments: List[Dict[str, Any]],
    text: str,
    out_dir: Path,
    outputs: List[str],
    interval: int,
    meta: Dict[str, Any],
) -> Dict[str, str]:
    ensure_dir(out_dir)
    stem = item.safe_title
    written: Dict[str, str] = {}
    if "txt" in outputs:
        p = out_dir / f"{stem}.txt"
        p.write_text(render_txt(segments, interval), encoding="utf-8")
        written["txt"] = str(p)
    if "srt" in outputs:
        p = out_dir / f"{stem}.srt"
        p.write_text(render_srt(segments), encoding="utf-8")
        written["srt"] = str(p)
    if "vtt" in outputs:
        p = out_dir / f"{stem}.vtt"
        p.write_text(render_vtt(segments), encoding="utf-8")
        written["vtt"] = str(p)
    if "md" in outputs:
        p = out_dir / f"{stem}.md"
        p.write_text(render_md(item, segments, meta), encoding="utf-8")
        written["md"] = str(p)
    if "notebooklm" in outputs:
        p = out_dir / f"{stem}.notebooklm.md"
        p.write_text(render_notebooklm(item, segments, meta.get("course", "")), encoding="utf-8")
        written["notebooklm"] = str(p)
    if "json" in outputs:
        p = out_dir / f"{stem}.json"
        payload = {
            **item.to_dict(),
            **meta,
            "segments": segments,
            "text": text,
            "created_at": now_iso(),
        }
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written["json"] = str(p)
    return written


# ---------------------------------------------------------------------------
# Transcript library (listing / reading / search)
# ---------------------------------------------------------------------------

TEXT_EXTS = {".txt", ".md", ".srt", ".vtt", ".json"}


def _is_internal(f: Path, output_dir: Path) -> bool:
    """True for manifests, error logs, and anything under an export folder (_*)."""
    rel = f.relative_to(output_dir)
    return any(part.startswith("_") for part in rel.parts)


def list_transcripts(output_dir: Path) -> List[Dict[str, Any]]:
    """Group transcript files under output_dir by their stem (one entry per lecture)."""
    if not output_dir.exists():
        return []
    groups: Dict[str, Dict[str, Any]] = {}
    for f in sorted(output_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT_EXTS:
            continue
        if _is_internal(f, output_dir):
            continue
        if f.name.endswith(".notebooklm.md"):  # export, listed separately
            continue
        rel_parent = f.parent.relative_to(output_dir).as_posix()
        key = f"{rel_parent}/{f.stem}"
        g = groups.setdefault(
            key,
            {"stem": f.stem, "folder": rel_parent if rel_parent != "." else "", "formats": {}},
        )
        g["formats"][f.suffix.lstrip(".").lower()] = f.relative_to(output_dir).as_posix()
    return sorted(groups.values(), key=lambda g: (g["folder"], g["stem"]))


def read_transcript_file(output_dir: Path, rel_path: str) -> str:
    """Safely read a file inside output_dir (prevents path traversal)."""
    target = (output_dir / rel_path).resolve()
    if not str(target).startswith(str(output_dir.resolve())):
        raise ValueError("path escapes output directory")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target.read_text(encoding="utf-8", errors="replace")


def search_transcripts(output_dir: Path, query: str, context: int = 60) -> List[Dict[str, Any]]:
    """Case-insensitive full-text search across .txt/.md files; returns snippets."""
    results: List[Dict[str, Any]] = []
    if not query or not output_dir.exists():
        return results
    needle = query.lower()
    for f in sorted(output_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in {".txt", ".md"}:
            continue
        if _is_internal(f, output_dir) or f.name.endswith(".notebooklm.md"):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lower = content.lower()
        hits = []
        start = 0
        while len(hits) < 5:
            i = lower.find(needle, start)
            if i == -1:
                break
            a = max(0, i - context)
            b = min(len(content), i + len(query) + context)
            snippet = content[a:b].replace("\n", " ").strip()
            hits.append(snippet)
            start = i + len(query)
        if hits:
            results.append(
                {
                    "file": f.relative_to(output_dir).as_posix(),
                    "count": lower.count(needle),
                    "snippets": hits,
                }
            )
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# PDF -> Markdown
# ---------------------------------------------------------------------------


def convert_pdf_tree(
    input_root: Path,
    suffix: str = "_copy",
    include_subfolders: bool = True,
    overwrite: bool = False,
) -> List[Tuple[str, str]]:
    """Mirror input_root into <name><suffix> and convert each PDF to Markdown."""
    try:
        from markitdown import MarkItDown
    except Exception as e:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "markitdown is not installed. Install with: pip install markitdown"
        ) from e

    md = MarkItDown()
    input_root = input_root.resolve()
    if not input_root.is_dir():
        raise NotADirectoryError(str(input_root))
    out_root = input_root.parent / f"{input_root.name}{suffix}"
    ensure_dir(out_root)

    pdfs = list(input_root.rglob("*.pdf")) if include_subfolders else list(input_root.glob("*.pdf"))
    converted: List[Tuple[str, str]] = []
    for pdf in pdfs:
        rel = pdf.relative_to(input_root)
        target_dir = ensure_dir(out_root / rel.parent)
        out_file = target_dir / (pdf.stem + ".md")
        if out_file.exists() and not overwrite:
            converted.append((str(pdf), str(out_file)))
            continue
        result = md.convert(str(pdf))
        text = getattr(result, "text_content", None) or str(result)
        out_file.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        converted.append((str(pdf), str(out_file)))
    return converted


# ---------------------------------------------------------------------------
# NotebookLM export (convert EXISTING transcripts into clean NotebookLM sources)
# ---------------------------------------------------------------------------

NOTEBOOKLM_DIRNAME = "_notebooklm"


def _title_from_stem(stem: str) -> str:
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip() or stem


def _notebooklm_body_for_group(output_dir: Path, group: Dict[str, Any], course: str) -> Tuple[str, str]:
    """Build (clean_markdown, display_title) for one transcript group.

    Prefers the .json output (has segments + metadata); falls back to the
    grouped-timestamp .txt; finally to stripping the .md. Returns ("", "") if
    no usable source is found.
    """
    fmts = group["formats"]

    if "json" in fmts:
        data = json.loads((output_dir / fmts["json"]).read_text(encoding="utf-8", errors="replace"))
        item = LectureItem(
            title=data.get("title") or _title_from_stem(group["stem"]),
            url=data.get("url", ""),
            duration=int(data.get("duration", 0) or 0),
            pub_date=data.get("pub_date", ""),
            author=data.get("author", ""),
            guid=data.get("guid", ""),
        )
        segments = data.get("segments") or [{"text": data.get("text", "")}]
        return render_notebooklm(item, segments, course), item.title

    if "txt" in fmts:
        title = _title_from_stem(group["stem"])
        raw = (output_dir / fmts["txt"]).read_text(encoding="utf-8", errors="replace")
        return clean_txt_to_notebooklm(raw, title=title, course=course), title

    if "md" in fmts:
        title = _title_from_stem(group["stem"])
        raw = (output_dir / fmts["md"]).read_text(encoding="utf-8", errors="replace")
        kept = [
            ln for ln in raw.splitlines()
            if not ln.startswith("### ") and not ln.startswith("- **") and ln.strip() not in ("## Transcript",)
        ]
        texts = [ln.strip() for ln in kept if ln.strip() and not ln.startswith("#")]
        return clean_txt_to_notebooklm("\n\n".join(texts), title=title, course=course), title

    return "", ""


def export_notebooklm(
    output_dir: Path,
    selection: Optional[List[str]] = None,
    combined: bool = False,
    course: str = "",
) -> Dict[str, Any]:
    """Render existing transcripts into NotebookLM-friendly Markdown.

    Writes one clean ``.md`` per lecture under ``<output_dir>/_notebooklm/``
    (mirroring the week/topic folder structure). If ``combined`` is set, also
    writes a single ``course_pack.md`` containing every lecture — handy as one
    NotebookLM upload. ``selection`` (a list of "<folder>/<stem>" or "<stem>"
    keys) limits which lectures are exported; ``None`` exports all.
    """
    groups = list_transcripts(output_dir)
    if selection:
        wanted = set(selection)
        groups = [
            g for g in groups
            if g["stem"] in wanted or f"{g['folder']}/{g['stem']}".strip("/") in wanted
        ]

    dest = ensure_dir(output_dir / NOTEBOOKLM_DIRNAME)
    written: List[str] = []
    docs: List[Tuple[str, str]] = []  # (title, body)

    for g in groups:
        body, title = _notebooklm_body_for_group(output_dir, g, course)
        if not body.strip():
            continue
        target_dir = ensure_dir(dest / g["folder"]) if g["folder"] else dest
        out_file = target_dir / f"{g['stem']}.md"
        out_file.write_text(body, encoding="utf-8")
        written.append(out_file.relative_to(output_dir).as_posix())
        docs.append((title, body))

    combined_path = None
    if combined and docs:
        header = [f"# {course or 'Course'} — Lecture Transcripts", ""]
        header.append("## Contents")
        header += [f"- {title}" for title, _ in docs]
        header.append("")
        parts = ["\n".join(header)]
        for title, body in docs:
            parts.append(body.strip())
        combined_file = dest / "course_pack.md"
        combined_file.write_text("\n\n---\n\n".join(parts).strip() + "\n", encoding="utf-8")
        combined_path = combined_file.relative_to(output_dir).as_posix()

    return {
        "count": len(written),
        "dest": str(dest),
        "files": written,
        "combined": combined_path,
    }
