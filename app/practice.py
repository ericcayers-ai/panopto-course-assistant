"""
practice.py - offline multiple-choice quiz from the review deck (v3).

Builds a practice exam straight from the user's spaced-repetition cards
(``review_items``): each card's *front* becomes a question, its *back* the correct
answer, and distractors are sampled from other cards' answers. No model required,
so it works fully offline and complements the LLM quiz. Deterministic given a seed
so the same deck + seed yields the same exam (and tests stay stable).
"""
from __future__ import annotations

import random
from typing import Any, Dict, Iterable, List, Optional

from .database import Database


def _as_dicts(items: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        front = (it["front"] if not isinstance(it, dict) else it.get("front")) or ""
        back = (it["back"] if not isinstance(it, dict) else it.get("back")) or ""
        try:
            ref = it["ref"] if not isinstance(it, dict) else it.get("ref", "")
        except (KeyError, IndexError):
            ref = ""
        front, back = str(front).strip(), str(back).strip()
        if front and back:
            out.append({"front": front, "back": back, "ref": ref or ""})
    return out


def build_quiz(items: Iterable[Any], *, count: int = 10, choices: int = 4,
               seed: Optional[int] = None) -> Dict[str, Any]:
    """A multiple-choice exam from review cards.

    Needs at least 2 distinct answers to form choices; returns an empty quiz with a
    ``reason`` otherwise rather than raising.
    """
    cards = _as_dicts(items)
    distinct_answers = list(dict.fromkeys(c["back"] for c in cards))
    if len(cards) < 1 or len(distinct_answers) < 2:
        return {"count": 0, "questions": [],
                "reason": "Need at least two cards with distinct answers."}

    rng = random.Random(seed)
    count = max(1, count)
    choices = max(2, choices)
    selected = cards if len(cards) <= count else rng.sample(cards, count)

    questions: List[Dict[str, Any]] = []
    for card in selected:
        pool = [a for a in distinct_answers if a != card["back"]]
        k = min(choices - 1, len(pool))
        options = rng.sample(pool, k) + [card["back"]]
        rng.shuffle(options)
        questions.append({
            "question": card["front"],
            "options": options,
            "answer": card["back"],
            "answer_index": options.index(card["back"]),
            "ref": card["ref"],
        })
    return {"count": len(questions), "questions": questions}


def from_db(db: Database, *, course_id: Optional[int] = None, count: int = 10,
            choices: int = 4, seed: Optional[int] = None) -> Dict[str, Any]:
    return build_quiz(db.list_review_items(course_id), count=count,
                      choices=choices, seed=seed)


def grade(questions: List[Dict[str, Any]], answers: List[Any]) -> Dict[str, Any]:
    """Score answers (each an option string or index) against the question key."""
    correct = 0
    detail: List[Dict[str, Any]] = []
    for i, q in enumerate(questions):
        given = answers[i] if i < len(answers) else None
        if isinstance(given, int):
            picked = q["options"][given] if 0 <= given < len(q["options"]) else None
        else:
            picked = given
        ok = picked is not None and picked == q["answer"]
        correct += 1 if ok else 0
        detail.append({"question": q["question"], "correct": ok,
                       "picked": picked, "answer": q["answer"]})
    total = len(questions)
    return {"score": correct, "total": total,
            "pct": round(correct / total * 100) if total else 0, "detail": detail}
