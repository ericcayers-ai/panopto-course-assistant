"""Panopto caption-first ingestion: SRT/VTT → canonical segments."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .types import STTResult, Segment, TimingSource

_TS = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
)
_SRT_BLOCK = re.compile(
    r"(?m)^\s*(\d+)\s*\n"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}).*\n"
    r"([\s\S]*?)(?=\n\s*\n|\Z)"
)
_VTT_BLOCK = re.compile(
    r"(?m)^(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}).*\n"
    r"([\s\S]*?)(?=\n\s*\n|\Z)"
)


def parse_timestamp(ts: str) -> float:
    m = _TS.search(ts.strip())
    if not m:
        return 0.0
    hours = int(m.group(1) or 0)
    mins = int(m.group(2))
    secs = int(m.group(3))
    frac = m.group(4).ljust(3, "0")[:3]
    return hours * 3600 + mins * 60 + secs + int(frac) / 1000.0


def _clean_caption_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)  # strip VTT tags
    text = text.replace("\u2028", "\n").replace("\r", "")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return " ".join(lines).strip()


def parse_srt(content: str) -> List[Segment]:
    segs: List[Segment] = []
    for i, m in enumerate(_SRT_BLOCK.finditer(content), start=1):
        text = _clean_caption_text(m.group(4))
        if not text:
            continue
        segs.append(Segment(
            id=i,
            start=parse_timestamp(m.group(2)),
            end=parse_timestamp(m.group(3)),
            text=text,
        ))
    return segs


def parse_vtt(content: str) -> List[Segment]:
    # Drop WEBVTT header / NOTE / STYLE blocks roughly by searching cue patterns.
    body = content
    if body.lstrip().upper().startswith("WEBVTT"):
        body = re.sub(r"(?is)^WEBVTT.*?\n\n", "", body, count=1)
    segs: List[Segment] = []
    for i, m in enumerate(_VTT_BLOCK.finditer(body), start=1):
        text = _clean_caption_text(m.group(3))
        if not text:
            continue
        segs.append(Segment(
            id=i,
            start=parse_timestamp(m.group(1)),
            end=parse_timestamp(m.group(2)),
            text=text,
        ))
    return segs


def parse_captions(content: str, *, hint: str = "") -> List[Segment]:
    hint = (hint or "").lower()
    sample = content.lstrip()[:64].upper()
    if hint.endswith(".vtt") or sample.startswith("WEBVTT"):
        return parse_vtt(content)
    if hint.endswith(".srt") or _SRT_BLOCK.search(content):
        return parse_srt(content)
    # Prefer VTT if labeled, else SRT-like numbered cues, else VTT cues.
    if _SRT_BLOCK.search(content):
        return parse_srt(content)
    return parse_vtt(content)


def captions_usable(segments: List[Segment], *, min_chars: int = 40, min_cues: int = 2) -> bool:
    if len(segments) < min_cues:
        return False
    total = sum(len(s.text or "") for s in segments)
    return total >= min_chars


def result_from_captions(segments: List[Segment], *, source: str = "panopto") -> STTResult:
    text = " ".join((s.text or "").strip() for s in segments if (s.text or "").strip()).strip()
    return STTResult(
        segments=segments,
        text=text,
        language="",
        engine="captions",
        model=source,
        schema_version=2,
        route_reason="Reused downloaded captions.",
        timing_source=TimingSource.CAPTION.value,
        raw_provenance={"caption_source": source},
    )


def download_caption_url(url: str, dest: Path, cookies: str = "") -> Path:
    """Fetch a CaptionDownloadUrl with optional cookie jar path."""
    import requests
    from ..core import ensure_dir

    ensure_dir(dest.parent)
    headers = {}
    jar = None
    if cookies:
        try:
            from http.cookiejar import MozillaCookieJar
            jar = MozillaCookieJar(cookies)
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            jar = None
    with requests.get(url, stream=True, timeout=60, cookies=jar, headers=headers) as r:
        r.raise_for_status()
        dest.write_bytes(r.content if hasattr(r, "content") else b"".join(r.iter_content(1 << 16)))
    # Prefer writing response text properly
    try:
        text = dest.read_bytes().decode("utf-8-sig", errors="replace")
        dest.write_text(text, encoding="utf-8")
    except Exception:
        pass
    return dest


def try_caption_first(
    caption_url: str = "",
    caption_path: str = "",
    cookies: str = "",
    work_dir: Optional[Path] = None,
) -> Tuple[Optional[STTResult], Dict[str, Any]]:
    """Return (result, meta) when usable captions exist; else (None, meta)."""
    meta: Dict[str, Any] = {"caption_tried": bool(caption_url or caption_path)}
    content = ""
    hint = caption_path or caption_url
    try:
        if caption_path and Path(caption_path).is_file():
            content = Path(caption_path).read_text(encoding="utf-8", errors="replace")
            meta["caption_path"] = caption_path
        elif caption_url:
            dest = (work_dir or Path(".")) / "_captions_download.vtt"
            download_caption_url(caption_url, dest, cookies=cookies)
            content = dest.read_text(encoding="utf-8", errors="replace")
            hint = caption_url
            meta["caption_url"] = caption_url
            meta["caption_path"] = str(dest)
        else:
            return None, meta
    except Exception as e:
        meta["caption_error"] = str(e)
        return None, meta

    segs = parse_captions(content, hint=hint)
    meta["caption_cues"] = len(segs)
    if not captions_usable(segs):
        meta["caption_usable"] = False
        return None, meta
    meta["caption_usable"] = True
    return result_from_captions(segs), meta
