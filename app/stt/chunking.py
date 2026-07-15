"""Speech-boundary chunking with overlap and merge for long lectures."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .types import Segment


@dataclass
class ChunkPlan:
    index: int
    start: float
    end: float
    overlap_prev: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "overlap_prev": self.overlap_prev,
        }


def plan_chunks(
    duration_s: float,
    vad_regions: Optional[Sequence[Dict[str, float]]] = None,
    *,
    max_seconds: float = 180.0,
    overlap: float = 1.5,
) -> List[ChunkPlan]:
    """Split into ≤max_seconds chunks at speech boundaries when possible."""
    max_seconds = max(30.0, float(max_seconds or 180.0))
    overlap = max(0.0, float(overlap))
    if duration_s <= 0:
        return [ChunkPlan(0, 0.0, 0.0)]
    if duration_s <= max_seconds:
        return [ChunkPlan(0, 0.0, duration_s)]

    boundaries = _candidate_cuts(duration_s, vad_regions, max_seconds)
    chunks: List[ChunkPlan] = []
    start = 0.0
    idx = 0
    for cut in boundaries:
        if cut <= start:
            continue
        ov = overlap if idx > 0 else 0.0
        c_start = max(0.0, start - ov)
        c_end = min(duration_s, cut)
        # Cap inclusive span at max_seconds even when overlap pulls start backward.
        if c_end - c_start > max_seconds:
            c_start = max(0.0, c_end - max_seconds)
            ov = max(0.0, start - c_start)
        chunks.append(ChunkPlan(idx, c_start, c_end, ov))
        start = cut
        idx += 1
    if start < duration_s - 0.05:
        ov = overlap if idx > 0 else 0.0
        c_start = max(0.0, start - ov)
        c_end = duration_s
        if c_end - c_start > max_seconds:
            c_start = max(0.0, c_end - max_seconds)
            ov = max(0.0, start - c_start)
        chunks.append(ChunkPlan(idx, c_start, c_end, ov))
    if not chunks:
        chunks = [ChunkPlan(0, 0.0, duration_s)]
    return chunks


def _candidate_cuts(
    duration_s: float,
    vad_regions: Optional[Sequence[Dict[str, float]]],
    max_seconds: float,
) -> List[float]:
    cuts: List[float] = []
    target = max_seconds
    regions = list(vad_regions or [])
    # Prefer gaps between VAD regions near each target.
    gaps = []
    for a, b in zip(regions, regions[1:]):
        gap_mid = (float(a["end"]) + float(b["start"])) / 2.0
        gaps.append(gap_mid)
    pos = 0.0
    while pos + max_seconds < duration_s:
        target = pos + max_seconds
        # nearest gap within ±25s of target
        best = target
        best_dist = abs(max_seconds)
        for g in gaps:
            if g <= pos + 20:
                continue
            dist = abs(g - target)
            if dist < best_dist and g - pos >= max_seconds * 0.5:
                best, best_dist = g, dist
        cuts.append(min(duration_s, best))
        pos = cuts[-1]
    return cuts


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def merge_chunk_segments(
    chunk_results: Sequence[Tuple[ChunkPlan, List[Segment]]],
    *,
    overlap_sim: float = 0.55,
) -> List[Segment]:
    """Merge per-chunk segments, deduping overlap windows by timestamp/text similarity."""
    if not chunk_results:
        return []
    merged: List[Segment] = []
    for plan, segs in chunk_results:
        offset_segs = [
            Segment(
                start=s.start,  # already absolute if engine used sliced audio with shifted times
                end=s.end,
                text=s.text,
                speaker=s.speaker,
                language=s.language,
                confidence=s.confidence,
                words=list(s.words),
            )
            for s in segs
            if (s.text or "").strip()
        ]
        if not merged:
            merged.extend(offset_segs)
            continue
        if plan.overlap_prev <= 0:
            merged.extend(offset_segs)
            continue
        # Drop leading new segments that duplicate the overlap tail.
        overlap_end = plan.start + plan.overlap_prev
        keep_from = 0
        for i, seg in enumerate(offset_segs):
            if seg.start >= overlap_end - 0.05:
                keep_from = i
                break
            # If fully inside overlap and similar to an existing segment, skip.
            if _is_duplicate(seg, merged, overlap_sim):
                keep_from = i + 1
                continue
            keep_from = i
            break
        # Also trim trailing merged segs that sit entirely in the overlap of this chunk
        # and match incoming text (prevents duplicated boundary words).
        while merged and offset_segs:
            last = merged[-1]
            if last.start < plan.start:
                break
            if _is_duplicate(last, offset_segs[: keep_from + 1] or offset_segs[:1], overlap_sim):
                merged.pop()
            else:
                break
        merged.extend(offset_segs[keep_from:])
    # Re-number ids
    for i, seg in enumerate(merged, start=1):
        seg.id = i
    return _dedupe_adjacent(merged)


def _is_duplicate(seg: Segment, pool: Sequence[Segment], sim: float) -> bool:
    tok = _tokens(seg.text)
    if not tok:
        return True
    for other in pool:
        if abs(other.start - seg.start) > 2.5 and abs(other.end - seg.end) > 2.5:
            continue
        ot = _tokens(other.text)
        if not ot:
            continue
        inter = len(set(tok) & set(ot))
        score = inter / max(1, min(len(tok), len(ot)))
        if score >= sim:
            return True
    return False


def _dedupe_adjacent(segs: List[Segment]) -> List[Segment]:
    if not segs:
        return segs
    out = [segs[0]]
    for seg in segs[1:]:
        prev = out[-1]
        if _tokens(seg.text) and _tokens(seg.text) == _tokens(prev.text) and abs(seg.start - prev.start) < 1.0:
            prev.end = max(prev.end, seg.end)
            continue
        # Strip repeated leading words duplicated across chunk boundary.
        pt, st = _tokens(prev.text), _tokens(seg.text)
        k = 0
        while k < min(6, len(pt), len(st)) and pt[-(k + 1)] == st[k]:
            # grow matching suffix/prefix
            k += 1
            # re-check from start of match
            if pt[-k:] != st[:k]:
                k -= 1
                break
        if k >= 2:
            # remove first k tokens from new segment text approximately
            words = (seg.text or "").split()
            if len(words) > k:
                seg = Segment(
                    start=seg.start, end=seg.end,
                    text=" ".join(words[k:]),
                    speaker=seg.speaker, language=seg.language,
                    confidence=seg.confidence, words=list(seg.words),
                )
        out.append(seg)
    for i, s in enumerate(out, start=1):
        s.id = i
    return out


def shift_segments(segments: Sequence[Segment], offset: float) -> List[Segment]:
    return [
        Segment(
            start=s.start + offset,
            end=s.end + offset,
            text=s.text,
            id=s.id,
            speaker=s.speaker,
            language=s.language,
            confidence=s.confidence,
            words=[
                type(w)(word=w.word, start=w.start + offset, end=w.end + offset,
                        confidence=w.confidence, speaker=w.speaker)
                for w in s.words
            ],
        )
        for s in segments
    ]
