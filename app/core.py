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


ORG_CHOICES = ["auto", "none", "date", "week", "lecture", "module", "topic"]
OUTPUT_CHOICES = ["txt", "srt", "vtt", "md", "json", "notebooklm", "summary"]


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


# Recognised "sequence" keywords in lecture titles, longest/safest alternations
# first. Single-letter abbreviations are deliberately conservative to avoid false
# positives (e.g. bare "l"/"m" matching ordinary words).
_SEQUENCE_PATTERNS = {
    "week": r"(?:week|wk|w)",
    "lecture": r"(?:lecture|lect|lec)",
    "module": r"(?:module|mod)",
    "unit": r"(?:unit)",
    "session": r"(?:session|sess)",
    "topic": r"(?:topic)",
    "lab": r"(?:lab|practical|prac)",
}

_SEQUENCE_LABELS = {
    "week": "Week", "lecture": "Lecture", "module": "Module",
    "unit": "Unit", "session": "Session", "topic": "Topic", "lab": "Lab",
}

# Order tried by the "auto" organiser: most-specific course structure first.
_AUTO_ORDER = ["week", "lecture", "module", "unit", "session", "lab"]


def infer_number(title: str, kind: str) -> Optional[int]:
    """Extract the N from e.g. 'Week 3', 'Lecture_03', 'Mod-4' for the given kind."""
    pat = _SEQUENCE_PATTERNS.get(kind)
    if not pat:
        return None
    m = re.search(rf"\b{pat}[_\-\s]*0*(\d{{1,2}})(?!\d)", title or "", flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def infer_week(title: str) -> Optional[int]:
    return infer_number(title, "week")


def infer_sequence(title: str) -> Optional[tuple]:
    """Return (kind, number) for the first recognised sequence keyword, else None.
    Used by the 'auto' organiser to handle courses that don't say 'Week N'."""
    for kind in _AUTO_ORDER:
        n = infer_number(title, kind)
        if n is not None:
            return kind, n
    return None


def infer_topic(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"\b(old|draft|rev(?:ision)?|part\s*\d+)\b", "", title, flags=re.I)
    title = re.sub(r"[_\-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    # strip a leading sequence prefix (Week 3, Lecture 2, Module 1, …) if present
    seq_alt = "|".join(p for p in _SEQUENCE_PATTERNS.values())
    m = re.match(rf"(?i)(?:{seq_alt})\s*0*\d+\s*[:.\-]?\s*(.*)$", title)
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
    if mode == "topic":
        return item.topic
    if mode in _SEQUENCE_LABELS:  # week | lecture | module | unit | session | lab
        n = infer_number(item.title, mode)
        label = _SEQUENCE_LABELS[mode]
        return f"{label}_{n:02d}" if n is not None else f"unparsed-{mode}"
    if mode == "auto":
        seq = infer_sequence(item.title)
        if seq:
            kind, n = seq
            return f"{_SEQUENCE_LABELS[kind]}_{n:02d}"
        d = item.date_obj
        return d.strftime("%Y-%m-%d") if d else "uncategorized"
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
    if "summary" in outputs:
        p = out_dir / f"{stem}.summary.md"
        p.write_text(render_summary(item, segments, text), encoding="utf-8")
        written["summary"] = str(p)
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


def read_any_text(path: Path) -> str:
    """Read an arbitrary local text file (user-supplied path, e.g. a deck CSV)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8", errors="replace")


def _is_internal(f: Path, output_dir: Path) -> bool:
    """True for manifests, error logs, and anything under an export folder (_*)."""
    rel = f.relative_to(output_dir)
    return any(part.startswith("_") for part in rel.parts)


def _split_stem_format(name: str) -> Tuple[str, str]:
    """Return (stem, format_key), folding compound suffixes like
    ``Lecture.notebooklm.md`` -> ("Lecture", "notebooklm")."""
    lower = name.lower()
    for compound, key in ((".notebooklm.md", "notebooklm"), (".summary.md", "summary")):
        if lower.endswith(compound):
            return name[: -len(compound)], key
    dot = name.rfind(".")
    return (name[:dot], name[dot + 1:].lower()) if dot > 0 else (name, "")


def list_transcripts(output_dir: Path) -> List[Dict[str, Any]]:
    """Group transcript files under output_dir by their stem (one entry per lecture).

    Compound outputs (``*.summary.md``, ``*.notebooklm.md``) are folded into the
    parent lecture as extra formats rather than shown as separate entries.
    """
    if not output_dir.exists():
        return []
    groups: Dict[str, Dict[str, Any]] = {}
    for f in sorted(output_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT_EXTS:
            continue
        if _is_internal(f, output_dir):  # skips _notebooklm/, manifests, logs
            continue
        stem, fmt = _split_stem_format(f.name)
        if not fmt:
            continue
        rel_parent = f.parent.relative_to(output_dir).as_posix()
        key = f"{rel_parent}/{stem}"
        g = groups.setdefault(
            key,
            {"stem": stem, "folder": rel_parent if rel_parent != "." else "", "formats": {}},
        )
        g["formats"][fmt] = f.relative_to(output_dir).as_posix()
    return sorted(groups.values(), key=lambda g: (g["folder"], g["stem"]))


def read_transcript_file(output_dir: Path, rel_path: str) -> str:
    """Safely read a file inside output_dir (prevents path traversal)."""
    target = (output_dir / rel_path).resolve()
    if not str(target).startswith(str(output_dir.resolve())):
        raise ValueError("path escapes output directory")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target.read_text(encoding="utf-8", errors="replace")


def _text_for_search(output_dir: Path, group: Dict[str, Any]) -> Tuple[str, str]:
    """Pick the best readable text for a lecture group: txt -> md -> json text.
    Returns (rel_path_used, content)."""
    fmts = group["formats"]
    for key in ("txt", "md"):
        if key in fmts:
            rel = fmts[key]
            try:
                return rel, (output_dir / rel).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
    if "json" in fmts:
        rel = fmts["json"]
        try:
            data = json.loads((output_dir / rel).read_text(encoding="utf-8", errors="replace"))
            return rel, data.get("text", "") or ""
        except Exception:
            pass
    return "", ""


def search_transcripts(output_dir: Path, query: str, context: int = 60) -> List[Dict[str, Any]]:
    """Case-insensitive full-text search, one result per lecture, with snippets."""
    results: List[Dict[str, Any]] = []
    query = (query or "").strip()
    if not query or not output_dir.exists():
        return results
    needle = query.lower()
    for group in list_transcripts(output_dir):
        rel, content = _text_for_search(output_dir, group)
        if not content:
            continue
        lower = content.lower()
        hits: List[str] = []
        start = 0
        while len(hits) < 5:
            i = lower.find(needle, start)
            if i == -1:
                break
            a = max(0, i - context)
            b = min(len(content), i + len(query) + context)
            snippet = " ".join(content[a:b].split())
            if a > 0:
                snippet = "… " + snippet
            if b < len(content):
                snippet = snippet + " …"
            hits.append(snippet)
            start = i + len(query)
        if hits:
            results.append(
                {
                    "file": rel,
                    "lecture": group["stem"],
                    "folder": group["folder"],
                    "count": lower.count(needle),
                    "snippets": hits,
                }
            )
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Documents -> Markdown (via MarkItDown) — versatile, for NotebookLM / other AI
# ---------------------------------------------------------------------------

# Extensions MarkItDown can convert. Slides/docs/sheets/pages all become text
# suitable for feeding to NotebookLM or any other AI alongside the transcripts.
DOC_EXTS = [
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".epub", ".txt", ".md", ".rtf",
]

DOCS_DIRNAME = "_docs"


def _markitdown_converter():
    """Return a function path->markdown using MarkItDown, or raise RuntimeError."""
    try:
        from markitdown import MarkItDown
    except Exception as e:  # pragma: no cover - optional dep
        raise RuntimeError(
            "markitdown is not installed. Install with: pip install markitdown"
        ) from e
    md = MarkItDown()

    def convert(path: str) -> str:
        result = md.convert(path)
        text = getattr(result, "text_content", None) or str(result)
        return text if text.endswith("\n") else text + "\n"

    return convert


def convert_documents(
    input_path: Path,
    output_dir: Path,
    *,
    exts: Optional[List[str]] = None,
    include_subfolders: bool = True,
    overwrite: bool = False,
    target: str = "ai",          # "ai" -> output_dir/_docs ; "copy" -> sibling *_copy
    suffix: str = "_copy",
    combined: bool = False,
    converter=None,              # injectable for testing; defaults to MarkItDown
) -> Dict[str, Any]:
    """Convert a document (or a folder of documents) to Markdown.

    target="ai":  write into ``output_dir/_docs`` (an AI/NotebookLM source area,
                  excluded from the transcript library), optionally with a single
                  combined ``documents_pack.md``.
    target="copy": mirror a folder into a sibling ``<name><suffix>`` folder
                  (the classic "PDF → Markdown" behaviour).
    """
    input_path = Path(input_path).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))
    wanted = {e.lower() if e.startswith(".") else "." + e.lower() for e in (exts or DOC_EXTS)}
    convert = converter or _markitdown_converter()

    # Gather (file, relative-path) pairs.
    if input_path.is_file():
        if input_path.suffix.lower() not in wanted:
            raise ValueError(f"Unsupported file type: {input_path.suffix}")
        items = [(input_path, Path(input_path.name))]
        base = input_path.parent
    else:
        globber = input_path.rglob("*") if include_subfolders else input_path.glob("*")
        files = sorted(p for p in globber if p.is_file() and p.suffix.lower() in wanted)
        if not files:
            raise ValueError("No supported documents found to convert.")
        items = [(p, p.relative_to(input_path)) for p in files]
        base = input_path

    if target == "copy":
        if not input_path.is_dir():
            raise ValueError("'copy' target requires a folder.")
        out_root = ensure_dir(input_path.parent / f"{input_path.name}{suffix}")
    else:
        out_root = ensure_dir(output_dir / DOCS_DIRNAME)

    converted: List[Dict[str, str]] = []
    docs: List[Tuple[str, str]] = []
    for src, rel in items:
        target_dir = ensure_dir(out_root / rel.parent) if len(rel.parts) > 1 else out_root
        out_file = target_dir / f"{safe_name(rel.stem)}.md"
        if out_file.exists() and not overwrite:
            converted.append({"src": str(src), "md": _relto(out_file, output_dir, out_root, target)})
            continue
        try:
            text = convert(str(src))
        except Exception as e:
            converted.append({"src": str(src), "md": "", "error": str(e)})
            continue
        out_file.write_text(text, encoding="utf-8")
        converted.append({"src": str(src), "md": _relto(out_file, output_dir, out_root, target)})
        docs.append((rel.stem, text))

    combined_path = None
    if combined and docs and target == "ai":
        parts = ["# Documents", ""] + [f"- {t}" for t, _ in docs]
        body = "\n\n---\n\n".join(d.strip() for _, d in docs)
        cf = out_root / "documents_pack.md"
        cf.write_text("\n".join(parts) + "\n\n---\n\n" + body + "\n", encoding="utf-8")
        combined_path = cf.relative_to(output_dir).as_posix()

    return {
        "count": sum(1 for c in converted if not c.get("error")),
        "output_root": str(out_root),
        "files": converted,
        "combined": combined_path,
    }


def _relto(path: Path, output_dir: Path, out_root: Path, target: str) -> str:
    """Path relative to output_dir for AI target (so it's viewable via the API),
    else an absolute string for the sibling-copy target."""
    if target == "ai":
        try:
            return path.relative_to(output_dir).as_posix()
        except ValueError:
            return str(path)
    return str(path)


def convert_pdf_tree(
    input_root: Path,
    suffix: str = "_copy",
    include_subfolders: bool = True,
    overwrite: bool = False,
) -> List[Tuple[str, str]]:
    """Backwards-compatible PDF-only mirror into <name><suffix> (uses convert_documents)."""
    if not Path(input_root).expanduser().is_dir():
        raise NotADirectoryError(str(input_root))
    res = convert_documents(
        input_root, Path(input_root), exts=[".pdf"], include_subfolders=include_subfolders,
        overwrite=overwrite, target="copy", suffix=suffix,
    )
    return [(c["src"], c["md"]) for c in res["files"]]


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


NOTION_DIRNAME = "_notion"


def _collect_source_markdown(folder: Path, exclude: set) -> List[Tuple[str, str]]:
    """Read every ``.md`` under ``folder`` (recursively) into (title, body),
    skipping combined-pack files we generated ourselves."""
    out: List[Tuple[str, str]] = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.rglob("*.md")):
        if f.name in exclude:
            continue
        out.append((_title_from_stem(f.stem), f.read_text(encoding="utf-8", errors="replace")))
    return out


def export_all_sources(output_dir: Path, combined: bool = True, course: str = "") -> Dict[str, Any]:
    """Bring **everything imported** together into one NotebookLM / AI export.

    Gathers cleaned lecture transcripts *and* the converted documents (``_docs``)
    and Notion pages (``_notion``) already in the library, writes the per-lecture
    transcript Markdown into ``_notebooklm/`` (as the normal export does), and —
    when ``combined`` is set — concatenates all three into a single
    ``everything_pack.md`` with a grouped table of contents. The combined pack is
    plain Markdown, so it works as a NotebookLM source *or* for any other AI.
    """
    # Per-lecture transcript Markdown (reuses the standard exporter).
    nb = export_notebooklm(output_dir, combined=False, course=course)

    transcripts: List[Tuple[str, str]] = []
    for g in list_transcripts(output_dir):
        body, title = _notebooklm_body_for_group(output_dir, g, course)
        if body.strip():
            transcripts.append((title, body))
    documents = _collect_source_markdown(output_dir / DOCS_DIRNAME, {"documents_pack.md"})
    notion_pages = _collect_source_markdown(output_dir / NOTION_DIRNAME, {"notion_pack.md"})

    sections = [
        ("Lecture transcripts", transcripts),
        ("Documents", documents),
        ("Notion pages", notion_pages),
    ]
    total = sum(len(items) for _, items in sections)

    dest = ensure_dir(output_dir / NOTEBOOKLM_DIRNAME)
    combined_path = None
    if combined and total:
        toc = [f"# {course or 'Course'} — All sources", ""]
        for name, items in sections:
            if not items:
                continue
            toc.append(f"## {name}")
            toc += [f"- {title}" for title, _ in items]
            toc.append("")
        parts = ["\n".join(toc).strip()]
        for name, items in sections:
            for title, body in items:
                parts.append(body.strip())
        cf = dest / "everything_pack.md"
        cf.write_text("\n\n---\n\n".join(parts).strip() + "\n", encoding="utf-8")
        combined_path = cf.relative_to(output_dir).as_posix()

    return {
        "count": total,
        "transcripts": len(transcripts),
        "documents": len(documents),
        "notion": len(notion_pages),
        "notebooklm_files": nb["count"],
        "dest": str(dest),
        "combined": combined_path,
    }


# ---------------------------------------------------------------------------
# Extractive summary (no LLM required)
# ---------------------------------------------------------------------------

_STOPWORDS = set(
    """a an the and or but if then else for to of in on at by with from as is are was were be been
    being this that these those it its it's we you they i he she them his her our your their not no
    do does did so than too very can will just into out up down over under again about above below
    one two also each which who whom what when where why how all any both few more most other some
    such only own same here there because while during before after between against""".split()
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def summarize_text(text: str, max_sentences: int = 8) -> List[str]:
    """Frequency-based extractive summary: returns the most salient sentences
    in their original order. Deterministic and dependency-free."""
    text = (text or "").strip()
    if not text:
        return []
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if len(sentences) <= max_sentences:
        return sentences

    freq: Dict[str, int] = {}
    for word in re.findall(r"[a-zA-Z][a-zA-Z'-]+", text.lower()):
        if word in _STOPWORDS or len(word) < 3:
            continue
        freq[word] = freq.get(word, 0) + 1
    if not freq:
        return sentences[:max_sentences]
    peak = max(freq.values())

    scored = []
    for idx, sent in enumerate(sentences):
        words = re.findall(r"[a-zA-Z][a-zA-Z'-]+", sent.lower())
        if not words:
            continue
        score = sum(freq.get(w, 0) for w in words) / (len(words) ** 0.5)
        # gentle bonus for early sentences (intros tend to frame the lecture)
        if idx < 3:
            score *= 1.1
        scored.append((idx, score, sent))

    top = sorted(scored, key=lambda x: x[1], reverse=True)[:max_sentences]
    return [sent for idx, _, sent in sorted(top, key=lambda x: x[0])]


def render_summary(item: LectureItem, segments: List[Dict[str, Any]], text: str = "") -> str:
    """A short Markdown study summary for a lecture."""
    if not text:
        text = " ".join((s.get("text") or "").strip() for s in segments).strip()
    points = summarize_text(text, max_sentences=8)
    lines = [f"# Summary — {item.title}", ""]
    if item.week is not None:
        lines.append(f"*Week {item.week}*")
        lines.append("")
    lines.append("## Key points")
    lines.append("")
    if points:
        lines += [f"- {p}" for p in points]
    else:
        lines.append("- (Transcript too short to summarise.)")
    words = len(re.findall(r"\w+", text))
    lines += ["", f"*Generated from a {words:,}-word transcript.*"]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Reorganize existing outputs into Week/Date/Topic folders
# ---------------------------------------------------------------------------


def _item_from_path(output_dir: Path, stem: str, folder: str) -> LectureItem:
    """Recover a LectureItem from a sibling .json if present, else from the stem."""
    base = output_dir / folder if folder else output_dir
    jpath = base / f"{stem}.json"
    if jpath.exists():
        try:
            data = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
            return LectureItem(
                title=data.get("title") or _title_from_stem(stem),
                url=data.get("url", ""),
                duration=int(data.get("duration", 0) or 0),
                pub_date=data.get("pub_date", ""),
                author=data.get("author", ""),
                guid=data.get("guid", ""),
            )
        except Exception:
            pass
    return LectureItem(title=_title_from_stem(stem), url="")


def reorganize_outputs(output_dir: Path, organize: str) -> List[str]:
    """Move existing transcript files into <organize> folders. Returns moved paths."""
    import shutil

    moved: List[str] = []
    for group in list_transcripts(output_dir):
        item = _item_from_path(output_dir, group["stem"], group["folder"])
        target_dir = output_dir_for(output_dir, item, organize)
        for rel in list(group["formats"].values()):
            src = output_dir / rel
            if not src.exists():
                continue
            ensure_dir(target_dir)
            dest = target_dir / src.name
            if dest.resolve() == src.resolve():
                continue
            if dest.exists():
                dest = target_dir / f"{src.stem}_dup{src.suffix}"
            shutil.move(str(src), str(dest))
            moved.append(dest.relative_to(output_dir).as_posix())
    return moved
