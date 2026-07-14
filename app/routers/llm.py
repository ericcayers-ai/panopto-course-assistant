"""routers/llm.py - llm endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import ai
from .. import llm
from .. import settings_store
from .. import context
from ..context import _audit, _safe_ai_config
from ..schemas import ChatReq, FlashcardsAIReq, LLMSettings, QuizReq, SummarizeReq

router = APIRouter()


@router.get("/api/llm/providers")
def api_llm_providers() -> Dict[str, Any]:
    return llm.detect()


@router.get("/api/llm/settings")
def api_llm_settings_get() -> Dict[str, Any]:
    return _safe_ai_config(llm.get_config(context.db, settings_store.get_active_course(context.db)))


@router.patch("/api/llm/settings")
def api_llm_settings_update(req: LLMSettings) -> Dict[str, Any]:
    cfg = llm.set_config(context.db, settings_store.get_active_course(context.db), req.values)
    return _safe_ai_config(cfg)


@router.post("/api/llm/summarize")
def api_llm_summarize(req: SummarizeReq) -> Dict[str, Any]:
    return ai.summarize(context.OUTPUT_DIR, req.scope, req.target, db=context.db,
                       course_id=settings_store.get_active_course(context.db))


@router.post("/api/llm/flashcards")
def api_llm_flashcards(req: FlashcardsAIReq) -> Dict[str, Any]:
    return ai.generate_flashcards(context.OUTPUT_DIR, selection=req.selection, types=req.types,
                                 course=req.course, max_cards=req.max_cards, db=context.db,
                                 course_id=settings_store.get_active_course(context.db))


@router.post("/api/llm/quiz")
def api_llm_quiz(req: QuizReq) -> Dict[str, Any]:
    """Return quiz JSON, or queue a PDF/Markdown exam job when ``formats``/``save_path`` set."""
    cid = settings_store.get_active_course(context.db)
    target = req.target
    if req.scope == "course" and req.course and not target:
        target = req.course
    wants_export = bool(req.save_path) or (
        req.formats and any(f.lower() in ("pdf", "md") for f in req.formats))
    if wants_export or req.kind in ("practice", "exam"):
        from .. import practice_exam as practice_exam_mod
        from ..jobs import manager
        cfg = llm.get_config(context.db, cid)
        try:
            n = int(req.n or 40)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="n must be an integer")
        if n < 10 or n > 150:
            raise HTTPException(status_code=400, detail="n must be between 10 and 150")
        kind = "exam" if req.kind == "exam" else "practice"
        formats = req.formats or ["pdf", "md"]
        captured_cfg = dict(cfg)

        def work(_progress):
            def prog(msg, frac=None):
                if frac is None:
                    _progress(msg)
                else:
                    _progress(msg, frac)
            return practice_exam_mod.build(
                context.OUTPUT_DIR,
                course=req.course or "", n=n, types=req.types,
                difficulty=req.difficulty, scope=req.scope, target=target,
                weights=req.weights, seed=req.seed,
                include_answer_key=bool(req.include_answer_key),
                time_minutes=req.time_minutes, total_marks=req.total_marks,
                kind=kind, formats=formats, save_path=req.save_path,
                config=captured_cfg, db=context.db, course_id=cid, progress=prog,
            )

        label = f"{'Exam' if kind == 'exam' else 'Practice'} ({n}Q)"
        job = manager.submit(label, work, type="practice_exam",
                             payload=req.model_dump(), course_id=cid)
        return job.to_dict()
    return ai.generate_quiz(context.OUTPUT_DIR, req.scope, target, types=req.types,
                           difficulty=req.difficulty, n=req.n, db=context.db,
                           course_id=cid)


@router.post("/api/llm/chat")
def api_llm_chat(req: ChatReq) -> Dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Ask a question first.")
    cid = settings_store.get_active_course(context.db)
    out = ai.chat(context.OUTPUT_DIR, req.query, history=req.history, db=context.db, course_id=cid)
    if out.get("generated") == "ai":      # only a cloud/local provider call leaves a trace
        cfg = llm.get_config(context.db, cid)
        if cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.chat", target=cfg.get("provider", ""),
                   detail="RAG chat", feature="ai_cloud")
    return out
