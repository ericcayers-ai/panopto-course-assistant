"""
ai.py - AI-assisted study features (§4), each with a dependency-free fallback.

Every function works **with or without** an LLM:
* LLM configured  -> richer output, flagged ``generated: "ai"``.
* No LLM          -> extractive / heuristic output, flagged ``generated: "extractive"``.

So the feature set never disappears when AI is off - it just gets simpler. All
LLM calls go through :mod:`app.llm`; failures fall back rather than erroring.

(Roadmap maps these to an ``app/ai/`` package; consolidated into one module here
to avoid sprawl - summarise / flashcards / quiz / rag live in clearly marked
sections.)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core, flashcards, llm, search
from .database import Database

_STOP = core._STOPWORDS


# ---------------------------------------------------------------------------
# Source gathering
# ---------------------------------------------------------------------------


def _read(output_dir: Path, rel: str) -> str:
    try:
        return core.read_transcript_file(output_dir, rel)
    except Exception:
        return ""


def collect_text(output_dir: Path, scope: str, target: str = "",
                max_chars: int = 24000) -> str:
    """Concatenate the source text for a scope: ``lecture`` (target=path),
    ``week`` (target=N), ``topic`` (target=topic) or ``course`` (everything)."""
    if scope == "lecture" and target:
        return _read(output_dir, target)[:max_chars]
    items = search.build_index(output_dir)
    items = [it for it in items if it["type"] == "transcript"]
    if scope == "week" and target:
        try:
            wk = int(target)
            items = [it for it in items if it["week"] == wk]
        except ValueError:
            items = []
    elif scope == "topic" and target:
        items = [it for it in items if it["topic"] == target]
    # scope == course -> all transcripts
    parts: List[str] = []
    total = 0
    for it in items:
        txt = _read(output_dir, it["path"])
        if not txt:
            continue
        block = f"## {it['title']}\n{txt}"
        parts.append(block)
        total += len(block)
        if total >= max_chars:
            break
    return "\n\n".join(parts)[:max_chars]


# ---------------------------------------------------------------------------
# Summarisation (§4)
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = (
    "You are a concise study assistant. Summarise the lecture material into clear, "
    "accurate study notes grounded strictly in the provided text. Use short bullet "
    "points for key ideas, definitions and caveats. Do not invent facts."
)


def summarize(output_dir: Path, scope: str, target: str = "", *,
             db: Optional[Database] = None, course_id: Optional[int] = None,
             config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = collect_text(output_dir, scope, target)
    if not text.strip():
        return {"scope": scope, "summary": "", "generated": "extractive",
                "note": "No source text found for that scope."}
    cfg = config or llm.get_config(db, course_id)
    if llm.is_enabled(cfg):
        prompt = (f"Summarise the following {scope} material as study notes.\n\n{text}")
        try:
            out = llm.complete(prompt, system=_SUMMARY_SYSTEM, config=cfg)
            if out.strip():
                return {"scope": scope, "summary": out.strip(), "generated": "ai",
                        "provider": cfg.get("provider")}
        except llm.LLMError:
            pass  # fall through to extractive
    bullets = core.summarize_text(text, max_sentences=10)
    md = "\n".join(f"- {b}" for b in bullets) or "- (Source too short to summarise.)"
    return {"scope": scope, "summary": md, "generated": "extractive"}


# ---------------------------------------------------------------------------
# Flashcards (§4) - AI Q&A/cloze, heuristic fallback
# ---------------------------------------------------------------------------

_FLASH_SYSTEM = (
    "You generate study flashcards from lecture material. Return ONLY a JSON array "
    "of objects like {\"front\": \"...\", \"back\": \"...\", \"type\": \"qa|cloze|definition\"}. "
    "Ground every card in the text; no preamble, no markdown fences."
)


# Small models ignore the exact field names we ask for, so accept the common
# synonyms and normalise them to front/back (cards) or question/answer (quiz).
_FRONT_KEYS = ("front", "question", "term", "q", "prompt", "name", "title", "word")
_BACK_KEYS = ("back", "answer", "definition", "a", "response", "text", "description", "meaning")
_QUESTION_KEYS = ("question", "prompt", "q", "front", "text")
_ANSWER_KEYS = ("answer", "a", "correct", "correct_answer", "solution", "back")


def _coalesce(d: Dict[str, Any], names) -> str:
    for n in names:
        v = d.get(n)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return ""


def _normalize_obj(d: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """Map a loosely-shaped model object onto our canonical card/quiz fields."""
    if not isinstance(d, dict):
        return None
    out = dict(d)
    if key == "question":
        q = _coalesce(d, _QUESTION_KEYS)
        if not q:
            return None
        out["question"] = q
        out["answer"] = out.get("answer") or _coalesce(d, _ANSWER_KEYS)
        return out
    front = _coalesce(d, _FRONT_KEYS)
    if not front:
        return None
    out["front"] = front
    out["back"] = _coalesce(d, _BACK_KEYS)
    return out


def _slice(s: str, open_ch: str, close_ch: str) -> str:
    i, j = s.find(open_ch), s.rfind(close_ch)
    return s[i:j + 1] if 0 <= i < j else ""


def _parse_json_array(raw: str, *, key: str = "front") -> List[Dict[str, Any]]:
    """Parse a list of card/quiz objects out of a model response, tolerantly.

    Handles everything small local models throw at us: ``` fences, a prose
    preamble, the array wrapped in an object (``{"flashcards":[...]}``), a single
    object, output truncated mid-array, JSONL, and - crucially - alternative field
    names (``question``/``answer``, ``term``/``definition``), which are normalised
    to ``front``/``back`` (or ``question``/``answer`` when ``key='question'``).
    """
    if not raw:
        return []
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)      # opening fence
    s = re.sub(r"\s*```\s*$", "", s).strip()    # closing fence

    raw_objs: List[Dict[str, Any]] = []
    # (a) whole value, the outermost [...] slice, or the outermost {...} value
    for cand in (s, _slice(s, "[", "]"), _slice(s, "{", "}")):
        if not cand:
            continue
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, list):
            raw_objs = [d for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            nested = next((v for v in data.values()
                           if isinstance(v, list) and any(isinstance(x, dict) for x in v)),
                          None)
            raw_objs = ([d for d in nested if isinstance(d, dict)] if nested else [data])
        if raw_objs:
            break

    # (b) tolerant recovery of individual flat {...} objects (truncation/JSONL)
    if not raw_objs:
        for om in re.finditer(r"\{[^{}]*\}", s, re.S):
            chunk = om.group(0)
            try:
                d = json.loads(chunk)
            except Exception:
                try:
                    d = json.loads(re.sub(r",\s*\}$", "}", chunk))  # trailing comma
                except Exception:
                    continue
            if isinstance(d, dict):
                raw_objs.append(d)

    out: List[Dict[str, Any]] = []
    for d in raw_objs:
        norm = _normalize_obj(d, key)
        if norm:
            out.append(norm)
    return out


def _llm_json_array(prompt: str, *, system: str, cfg: Dict[str, Any],
                    min_items: int = 1, key: str = "front") -> List[Dict[str, Any]]:
    """Ask the model for a JSON array and validate parseability before returning."""
    ccfg = {**cfg, "format": "json"}

    def _validate(raw: str) -> List[Dict[str, Any]]:
        items = _parse_json_array(raw, key=key)
        if len(items) < min_items:
            raise ValueError(f"expected at least {min_items} items, got {len(items)}")
        return items

    return llm.complete_validated(prompt, system=system, config=ccfg, validate=_validate)


def _llm_chunks(output_dir: Path, selection: Optional[List[str]], *,
                size: int = 4000, max_chunks: int = 20) -> List[tuple]:
    """Clean per-lecture text split into small chunks, sampled evenly across the
    whole course. Small local models echo a huge transcript back instead of
    making cards, so we keep each prompt small and aggregate the results."""
    from . import lectures
    lecs = lectures.iter_lectures(output_dir)
    if selection:
        wanted = set(selection)
        picked = [le for le in lecs
                  if le["stem"] in wanted or le.get("path") in wanted
                  or f"{le['folder']}/{le['stem']}".strip("/") in wanted]
        lecs = picked or lecs
    chunks: List[tuple] = []
    for le in lecs:
        t = (le.get("text") or "").strip()
        for i in range(0, len(t), size):
            chunk = t[i:i + size].strip()
            if len(chunk) > 200:
                chunks.append((le["title"], chunk))
    if len(chunks) > max_chunks:                       # sample across the course
        step = len(chunks) / max_chunks
        chunks = [chunks[int(i * step)] for i in range(max_chunks)]
    return chunks


def generate_flashcards(output_dir: Path, *, selection: Optional[List[str]] = None,
                       types: Optional[List[str]] = None, course: str = "",
                       db: Optional[Database] = None, course_id: Optional[int] = None,
                       config: Optional[Dict[str, Any]] = None,
                       max_cards: int = 20) -> Dict[str, Any]:
    cfg = config or llm.get_config(db, course_id)
    # Record *why* we fell back so the UI can be specific instead of guessing.
    reason = "No AI model is configured - enable one under Local AI model."
    if llm.is_enabled(cfg):
        chunks = _llm_chunks(output_dir, selection)
        if not chunks:
            reason = "No transcript text was found to build cards from."
        else:
            kinds = ", ".join(types or ["qa", "cloze", "definition"])
            per = max(2, -(-max_cards // len(chunks)))   # ceil division
            cards: List[Dict[str, Any]] = []
            seen: set = set()
            llm_replied = False
            for title, chunk in chunks:
                if len(cards) >= max_cards:
                    break
                want = min(per + 1, max_cards - len(cards) + 1)
                ccfg = {**cfg, "format": "json", "max_tokens": want * 90 + 256}
                prompt = (
                    f"Create up to {want} exam-style flashcards ({kinds}) from this "
                    f"lecture excerpt. Focus on concepts, definitions and facts; skip "
                    f"greetings and administrative chatter.\n\nLecture: {title}\n\n{chunk}")
                try:
                    got = _llm_json_array(
                        prompt, system=_FLASH_SYSTEM, cfg=ccfg, min_items=1)
                    llm_replied = True
                except llm.LLMError as e:
                    if "expected at least" in str(e).lower():
                        llm_replied = True
                        continue
                    reason = (f"The {cfg.get('provider', 'AI')} model call failed ({e}) - "
                              "is the local server running?")
                    break
                for c in got:
                    front = (c.get("front") or "").strip()
                    if len(front) > 3 and front.lower() not in seen:
                        seen.add(front.lower())
                        c.setdefault("type", "qa")
                        cards.append(c)
            if cards:
                return {"cards": cards[:max_cards], "generated": "ai",
                        "provider": cfg.get("provider")}
            if llm_replied:
                reason = (f"The {cfg.get('provider', 'AI')} model replied but produced no "
                          "usable cards - try a larger model.")
    # Fallback: existing keyword/definition heuristics.
    heur = flashcards.generate_from_library(output_dir, selection=selection,
                                           course=course, max_per_lecture=max_cards)
    cards = [{"front": c.get("front", ""), "back": c.get("back", ""),
              "type": "definition", "tags": c.get("tags") or []} for c in heur]
    return {"cards": cards, "generated": "extractive", "reason": reason}


_CATEGORIZE_SYSTEM = (
    "You categorize study flashcards into topics. Given a list of cards, assign each a "
    "descriptive topic tag based on its subject matter. Return ONLY a JSON array of "
    "objects like {\"front\": \"...\", \"back\": \"...\", \"tags\": [\"topic\", ...]}. "
    "Keep any existing tags and prepend the topic tag. No preamble, no markdown fences."
)


def llm_categorize_cards(cards: List[Dict[str, Any]], course: str = "",
                         db=None, course_id=None,
                         config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tag flashcards by topic using LLM. Raises LLMError if no provider configured."""
    cfg = config or llm.get_config(db, course_id)
    if not llm.is_enabled(cfg):
        raise llm.LLMError("No LLM provider configured")
    course_hint = f" for a '{course}' course" if course else ""
    cards_json = json.dumps(
        [{"front": c.get("front", ""), "back": c.get("back", ""), "tags": c.get("tags") or []}
         for c in cards[:150]],
        indent=2)
    prompt = (f"Categorize these flashcards{course_hint} - add a topic tag to each:\n\n{cards_json}")
    raw = llm.complete(prompt, system=_CATEGORIZE_SYSTEM, config=cfg)
    result = _parse_json_array(raw)
    if result:
        return {"cards": result, "generated": "ai", "provider": cfg.get("provider")}
    return {"cards": cards, "generated": "extractive"}


# ---------------------------------------------------------------------------
# Quiz generation (§4) - extractive MCQ + cloze, LLM-enhanced when available
# ---------------------------------------------------------------------------

_DEF_RE = re.compile(
    r"\b([A-Z][\w\-]{2,40}(?:\s[\w\-]{2,20}){0,3})\s+(?:is|are|refers to|means)\s+([^.]{10,160})\.")


def _extractive_quiz(text: str, n: int = 8) -> List[Dict[str, Any]]:
    questions: List[Dict[str, Any]] = []
    defs = []
    for m in _DEF_RE.finditer(text):
        term, definition = m.group(1).strip(), m.group(2).strip()
        if 2 < len(term) < 45:
            defs.append((term, definition))
    # MCQ from definitions (distractors = other definitions' answers)
    for i, (term, definition) in enumerate(defs):
        distractors = [d for j, (_, d) in enumerate(defs) if j != i][:3]
        if len(distractors) < 2:
            continue
        options = distractors + [definition]
        questions.append({
            "type": "mcq", "question": f"What best describes {term}?",
            "options": options, "answer": definition,
        })
        if len(questions) >= n:
            break
    # Cloze from salient sentences for the remainder
    for sent in core.summarize_text(text, max_sentences=n):
        if len(questions) >= n:
            break
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", sent)
                 if w.lower() not in _STOP]
        if not words:
            continue
        blank = max(words, key=len)
        questions.append({
            "type": "cloze", "question": re.sub(rf"\b{re.escape(blank)}\b", "_____", sent, count=1),
            "answer": blank,
        })
    return questions[:n]


_QUIZ_SYSTEM = (
    "You write quiz questions from lecture material. Return ONLY a JSON array of "
    "{\"type\": \"mcq|cloze|short|truefalse\", \"question\": \"...\", "
    "\"options\": [...], \"answer\": \"...\"}. Ground everything in the text.")


def generate_quiz(output_dir: Path, scope: str = "course", target: str = "", *,
                 types: Optional[List[str]] = None, difficulty: str = "medium",
                 n: int = 8, db: Optional[Database] = None,
                 course_id: Optional[int] = None,
                 config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = collect_text(output_dir, scope, target)
    if not text.strip():
        return {"questions": [], "generated": "extractive",
                "note": "No source text found for that scope."}
    cfg = config or llm.get_config(db, course_id)
    if llm.is_enabled(cfg):
        kinds = ", ".join(types or ["mcq", "cloze"])
        cfg = {**cfg, "format": "json",
               "max_tokens": max(int(cfg.get("max_tokens", 1024) or 1024), n * 80 + 256)}
        prompt = (f"Write {n} {difficulty} quiz questions ({kinds}) from:\n\n{text}")
        try:
            qs = _llm_json_array(prompt, system=_QUIZ_SYSTEM, cfg=cfg,
                                 min_items=1, key="question")
            qs = [q for q in qs if q.get("question") and q.get("answer")]
            if qs:
                return {"questions": qs[:n], "generated": "ai", "provider": cfg.get("provider")}
        except llm.LLMError:
            pass
    return {"questions": _extractive_quiz(text, n), "generated": "extractive"}


# ---------------------------------------------------------------------------
# RAG chat - "Chat with your course" (§4)
# ---------------------------------------------------------------------------

_RAG_SYSTEM = (
    "You answer questions about a student's course using ONLY the provided sources. "
    "Cite sources inline as [1], [2] matching the numbered context. If the sources "
    "don't contain the answer, say so plainly. Be concise and accurate.")


def _retrieve(output_dir: Path, query: str, depth: int) -> List[Dict[str, Any]]:
    hits = search.search(output_dir, query)[:depth]
    sources = []
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.get("snippets", [])[:2])
        sources.append({"n": i, "lecture": h["lecture"], "file": h["file"],
                        "snippet": snippet})
    return sources


def chat(output_dir: Path, query: str, *, history: Optional[List[Dict[str, str]]] = None,
        db: Optional[Database] = None, course_id: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"answer": "", "citations": [], "generated": "extractive", "confidence": "low"}
    cfg = config or llm.get_config(db, course_id)
    depth = int(cfg.get("retrieval_depth", 5) or 5)
    sources = _retrieve(output_dir, query, depth)
    confidence = "high" if len(sources) >= 3 else ("medium" if sources else "low")

    if llm.is_enabled(cfg) and sources:
        context = "\n\n".join(f"[{s['n']}] {s['lecture']}: {s['snippet']}" for s in sources)
        prompt = f"Sources:\n{context}\n\nQuestion: {query}\n\nAnswer with inline [n] citations."
        try:
            answer = llm.complete(prompt, system=_RAG_SYSTEM, config=cfg)
            if answer.strip():
                return {"answer": answer.strip(), "citations": sources,
                        "generated": "ai", "provider": cfg.get("provider"),
                        "confidence": confidence}
        except llm.LLMError:
            pass

    # Offline fallback: surface the most relevant snippets as an extractive answer.
    if not sources:
        return {"answer": "I couldn't find anything in your library about that.",
                "citations": [], "generated": "extractive", "confidence": "low"}
    lines = [f"Here's what your library says about “{query}”:"]
    lines += [f"[{s['n']}] {s['lecture']}: {s['snippet']}" for s in sources]
    lines.append("\n(Enable an AI provider in settings for a synthesised answer.)")
    return {"answer": "\n".join(lines), "citations": sources,
            "generated": "extractive", "confidence": confidence}
