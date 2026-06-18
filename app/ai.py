"""
ai.py — AI-assisted study features (§4), each with a dependency-free fallback.

Every function works **with or without** an LLM:
* LLM configured  -> richer output, flagged ``generated: "ai"``.
* No LLM          -> extractive / heuristic output, flagged ``generated: "extractive"``.

So the feature set never disappears when AI is off — it just gets simpler. All
LLM calls go through :mod:`app.llm`; failures fall back rather than erroring.

(Roadmap maps these to an ``app/ai/`` package; consolidated into one module here
to avoid sprawl — summarise / flashcards / quiz / rag live in clearly marked
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
# Flashcards (§4) — AI Q&A/cloze, heuristic fallback
# ---------------------------------------------------------------------------

_FLASH_SYSTEM = (
    "You generate study flashcards from lecture material. Return ONLY a JSON array "
    "of objects like {\"front\": \"...\", \"back\": \"...\", \"type\": \"qa|cloze|definition\"}. "
    "Ground every card in the text; no preamble, no markdown fences."
)


def _parse_json_array(raw: str) -> List[Dict[str, Any]]:
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [d for d in data if isinstance(d, dict) and d.get("front")]
    except Exception:
        return []


def generate_flashcards(output_dir: Path, *, selection: Optional[List[str]] = None,
                       types: Optional[List[str]] = None, course: str = "",
                       db: Optional[Database] = None, course_id: Optional[int] = None,
                       config: Optional[Dict[str, Any]] = None,
                       max_cards: int = 20) -> Dict[str, Any]:
    cfg = config or llm.get_config(db, course_id)
    if llm.is_enabled(cfg):
        text = collect_text(output_dir, "course" if not selection else "lecture",
                           selection[0] if selection else "")
        if text.strip():
            kinds = ", ".join(types or ["qa", "cloze", "definition"])
            prompt = (f"Create up to {max_cards} flashcards ({kinds}) from this material:\n\n{text}")
            try:
                cards = _parse_json_array(llm.complete(prompt, system=_FLASH_SYSTEM, config=cfg))
                if cards:
                    return {"cards": cards[:max_cards], "generated": "ai",
                            "provider": cfg.get("provider")}
            except llm.LLMError:
                pass
    # Fallback: existing keyword/definition heuristics.
    heur = flashcards.generate_from_library(output_dir, selection=selection,
                                           course=course, max_per_lecture=max_cards)
    cards = [{"front": c.get("front", ""), "back": c.get("back", ""),
              "type": "definition", "tags": c.get("tags") or []} for c in heur]
    return {"cards": cards, "generated": "extractive"}


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
    prompt = (f"Categorize these flashcards{course_hint} — add a topic tag to each:\n\n{cards_json}")
    raw = llm.complete(prompt, system=_CATEGORIZE_SYSTEM, config=cfg)
    result = _parse_json_array(raw)
    if result:
        return {"cards": result, "generated": "ai", "provider": cfg.get("provider")}
    return {"cards": cards, "generated": "extractive"}


# ---------------------------------------------------------------------------
# Quiz generation (§4) — extractive MCQ + cloze, LLM-enhanced when available
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
        prompt = (f"Write {n} {difficulty} quiz questions ({kinds}) from:\n\n{text}")
        try:
            qs = _parse_json_array(llm.complete(prompt, system=_QUIZ_SYSTEM, config=cfg))
            qs = [q for q in qs if q.get("question") and q.get("answer")]
            if qs:
                return {"questions": qs[:n], "generated": "ai", "provider": cfg.get("provider")}
        except llm.LLMError:
            pass
    return {"questions": _extractive_quiz(text, n), "generated": "extractive"}


# ---------------------------------------------------------------------------
# RAG chat — "Chat with your course" (§4)
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
