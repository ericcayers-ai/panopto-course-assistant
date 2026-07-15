"""routers/exports.py - exports endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from typing import Dict
from .. import ai
from .. import cheatsheet as cheatsheet_mod
from .. import core
from .. import exports as export_engine
from .. import flashcards
from .. import llm
from .. import practice_exam as practice_exam_mod
from .. import settings_store
from .. import study
from .. import study_planner
from ..jobs import manager
from .. import context
from ..context import _audit, _copy_export_files, _export_recordings, _study_summarizer
from ..schemas import CheatsheetRequest, ExportAllRequest, ExportReq, FlashcardCatRequest, FlashcardGenRequest, FormatsRequest, NotebookLMRequest, PracticeExamRequest, SrtExportRequest, StudyCsvRequest

router = APIRouter()


@router.get("/api/export/presets")
def api_export_presets() -> Dict[str, Any]:
    return {"presets": export_engine.PRESET_TARGETS, "targets": export_engine.ALL_TARGETS,
            "scopes": list(export_engine.SCOPES)}


@router.post("/api/export/preview")
def api_export_preview(req: ExportReq) -> Dict[str, Any]:
    try:
        return export_engine.preview(context.OUTPUT_DIR, preset=req.preset, target=req.target,
                                     scope=req.scope, scope_target=req.scope_target,
                                     course=req.course)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/export/run")
def api_export_run(req: ExportReq) -> Dict[str, Any]:
    try:
        out = export_engine.export(context.OUTPUT_DIR, preset=req.preset, target=req.target,
                                  scope=req.scope, scope_target=req.scope_target,
                                  course=req.course, db=context.db,
                                  course_id=settings_store.get_active_course(context.db))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@router.post("/api/export/notebooklm")
def api_export_notebooklm(req: NotebookLMRequest) -> Dict[str, Any]:
    """Render existing transcripts into clean, NotebookLM-friendly Markdown."""
    result = core.export_notebooklm(
        context.OUTPUT_DIR,
        selection=req.selection,
        combined=req.combined,
        course=req.course,
    )
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="No transcripts found to export. Transcribe some lectures first.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        files = list(result.get("files", []))
        if result.get("combined"):
            files.append(result["combined"])
        _copy_export_files(files, context.OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@router.post("/api/export/all")
def api_export_all(req: ExportAllRequest) -> Dict[str, Any]:
    """Bring everything imported (transcripts + documents + Notion) together as
    one NotebookLM / AI export, with an optional combined everything_pack.md."""
    result = core.export_all_sources(context.OUTPUT_DIR, combined=req.combined, course=req.course)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to export yet. Import some lectures, documents or "
            "Notion pages first.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        files = list(result.get("files", []))
        if result.get("combined"):
            files.append(result["combined"])
        _copy_export_files(files, context.OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@router.post("/api/export/formats")
def api_export_formats(req: FormatsRequest) -> Dict[str, Any]:
    """Generate subtitles / alternate output formats from existing transcripts."""
    result = core.export_formats(context.OUTPUT_DIR, req.formats, interval=req.interval)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to generate. Transcribe some lectures first, and pick "
            "at least one format.",
        )
    return result


@router.post("/api/export/srt")
def api_export_srt(req: SrtExportRequest) -> Dict[str, Any]:
    """Generate SRT subtitle files from all transcribed lectures and optionally copy
    them to a folder alongside the video recordings so players pick them up automatically.

    SRT files share the same stem as each lecture's transcript so subtitle players can
    auto-load them when the video and .srt live in the same folder."""
    result = core.export_formats(context.OUTPUT_DIR, ["srt"])
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="No transcripts found. Transcribe some lectures first - SRT files "
            "are generated from the timing data produced during transcription.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files(result.get("files", []), context.OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
        result["dest"] = str(dest)
        if req.include_recordings:
            result["recordings"] = _export_recordings(dest)
    return result


@router.post("/api/export/notion-csv")
def api_export_notion_csv(req: StudyCsvRequest) -> Dict[str, Any]:
    """Export the transcript library as a Notion-importable study-database CSV.

    When an LLM provider is configured the Summary column is AI-written and the
    job runs in the background so the UI stays responsive. Extractive mode is
    synchronous."""
    cid = settings_store.get_active_course(context.db)
    cfg = llm.get_config(context.db, cid)
    used_ai = llm.is_enabled(cfg)
    course = req.course
    filename = req.filename
    out_dir_str = req.output_dir
    captured_cfg = dict(cfg)

    if used_ai:
        def work(_progress):
            _progress("Writing study database with AI summaries...")
            result = study.write_study_database(
                context.OUTPUT_DIR, course=course, filename=filename,
                summarizer=_study_summarizer(captured_cfg))
            if out_dir_str:
                _copy_export_files([result.get("csv", "")], context.OUTPUT_DIR,
                                   Path(out_dir_str).expanduser())
                result["output_dir"] = str(Path(out_dir_str).expanduser())
            result["generated"] = "ai"
            result["provider"] = captured_cfg.get("provider")
            if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
                _audit("ai.study_csv", target=captured_cfg.get("provider", ""),
                       detail="Notion study CSV summaries", feature="ai_cloud")
            return result
        job = manager.submit("Study database (AI summaries)", work,
                             type="study_csv", payload=req.model_dump(), course_id=cid)
        return job.to_dict()

    result = study.write_study_database(context.OUTPUT_DIR, course=course, filename=filename)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to export yet. Transcribe or convert some lectures first.",
        )
    if out_dir_str:
        _copy_export_files([result.get("csv", "")], context.OUTPUT_DIR,
                           Path(out_dir_str).expanduser())
        result["output_dir"] = str(Path(out_dir_str).expanduser())
    result["generated"] = "extractive"
    return result


@router.post("/api/flashcards/generate")
def api_flashcards_generate(req: FlashcardGenRequest) -> Dict[str, Any]:
    """Generate Anki-importable flashcards using LLM (requires Ollama/configured provider).

    Runs as a background job so the UI stays responsive. Returns job status immediately."""
    cid = settings_store.get_active_course(context.db)
    cfg = llm.get_config(context.db, cid)
    if not llm.is_enabled(cfg):
        raise HTTPException(
            status_code=503,
            detail="LLM not available. Install Ollama with llama3 to generate flashcards. "
            "See the Extra Dependencies installer to set this up.")
    selection = req.selection
    course = req.course
    deck = req.deck
    max_cards = max(1, min(req.max_cards, 200))
    out_dir_str = req.output_dir
    captured_cfg = dict(cfg)

    def work(_progress):
        _progress("Generating flashcards with LLM...")
        out = ai.generate_flashcards(context.OUTPUT_DIR, selection=selection, course=course,
                                     max_cards=max_cards, config=captured_cfg)
        cards = out.get("cards", [])
        if not cards:
            raise ValueError("No flashcards generated - try adding more transcript content.")
        if course:
            ctag = flashcards._tag(course)
            if ctag:
                for c in cards:
                    tags = list(c.get("tags") or [])
                    if ctag not in tags:
                        tags.insert(0, ctag)
                    c["tags"] = tags
        result = flashcards.write_deck(context.OUTPUT_DIR, cards, deck)
        result["course"] = course
        result["generated"] = out.get("generated")
        result["provider"] = out.get("provider")
        result["reason"] = out.get("reason", "")
        # Seed Study practice quiz deck from the same cards
        if cid and cards:
            try:
                result["review_seeded"] = study_planner.add_review_items(
                    context.db, cid, cards, ref=f"flashcards:{deck}")
            except Exception:
                result["review_seeded"] = 0
        if out_dir_str:
            dest = Path(out_dir_str).expanduser()
            _copy_export_files([result.get("anki_tsv", ""), result.get("csv", "")],
                               context.OUTPUT_DIR, dest / "_flashcards")
            result["output_dir"] = str(dest)
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.flashcards", target=captured_cfg.get("provider", ""),
                   detail="flashcard export", feature="ai_cloud")
        return result

    job = manager.submit(f"Flashcards: {deck}", work,
                        type="flashcards_generate", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@router.post("/api/flashcards/categorize")
def api_flashcards_categorize(req: FlashcardCatRequest) -> Dict[str, Any]:
    """Tag an existing flashcard deck by topic using LLM (requires configured provider).

    Runs as a background job. Accepts pasted CSV/TSV text or a file path."""
    cid = settings_store.get_active_course(context.db)
    cfg = llm.get_config(context.db, cid)
    if not llm.is_enabled(cfg):
        raise HTTPException(
            status_code=503,
            detail="LLM not available. Install Ollama with llama3 to categorize flashcards.")
    text = req.text
    if not text and req.path:
        try:
            text = core.read_any_text(Path(req.path).expanduser())
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    cards = flashcards.parse_cards_text(text)
    if not cards:
        raise HTTPException(status_code=400, detail="No flashcards found in the input "
                            "(expected CSV/TSV with front, back[, tags]).")
    course = req.course
    deck = req.deck
    captured_cfg = dict(cfg)

    def work(_progress):
        _progress("Categorizing flashcards with LLM...")
        out = ai.llm_categorize_cards(cards, course=course, config=captured_cfg)
        tagged = out.get("cards", cards)
        result = flashcards.write_deck(context.OUTPUT_DIR, tagged, deck)
        result["course"] = course
        result["generated"] = out.get("generated")
        result["provider"] = out.get("provider")
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.flashcards_cat", target=captured_cfg.get("provider", ""),
                   detail="flashcard categorize", feature="ai_cloud")
        return result

    job = manager.submit(f"Categorize deck: {deck}", work,
                        type="flashcards_categorize", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@router.post("/api/export/cheatsheet")
def api_export_cheatsheet(req: CheatsheetRequest) -> Dict[str, Any]:
    """Build a dense exam cheat sheet PDF from the course material.

    Prefers an LLM when configured; otherwise falls back to extractive
    summarisation so the button is never a hard dead end.
    """
    cid = settings_store.get_active_course(context.db)
    cfg = llm.get_config(context.db, cid)
    course = req.course
    max_pages = max(1, min(req.max_pages, 10))
    save_path = req.save_path
    captured_cfg = dict(cfg)
    llm_on = llm.is_enabled(cfg)

    def work(_progress):
        _progress("Condensing course material..." if llm_on else
                  "Building extractive cheat sheet (no AI model)…", 0.2)
        result = cheatsheet_mod.build(context.OUTPUT_DIR, course=course, max_pages=max_pages,
                                      config=captured_cfg, save_path=save_path)
        _progress("done", 1.0)
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS and result.get("generated") == "ai":
            _audit("ai.cheatsheet", target=captured_cfg.get("provider", ""),
                   detail="exam cheat sheet", feature="ai_cloud")
        return result

    label = f"Exam cheat sheet ({max_pages} page{'s' if max_pages != 1 else ''})"
    if not llm_on:
        label += " — extractive"
    job = manager.submit(label, work, type="cheatsheet", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@router.post("/api/export/practice-exam")
def api_export_practice_exam(req: PracticeExamRequest) -> Dict[str, Any]:
    """Build a parted practice/exam PDF (and Markdown) from the library.

    Defaults target a 100-question practice pack; set ``kind='exam'`` and a
    smaller ``n`` for an exam builder pass. Runs as a background job.
    """
    cid = settings_store.get_active_course(context.db)
    cfg = llm.get_config(context.db, cid)
    try:
        n = int(req.n or 100)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="n must be an integer")
    if n < 10 or n > 150:
        raise HTTPException(status_code=400, detail="n must be between 10 and 150")
    kind = (req.kind or "practice").lower()
    if kind not in ("practice", "exam"):
        kind = "practice"
    types = req.types or (["mcq", "short", "long"] if kind == "practice" else ["mcq", "short", "long"])
    formats = req.formats or ["pdf", "md"]
    difficulty = (req.difficulty or "medium").lower()
    if difficulty not in ("easy", "medium", "hard", "mixed"):
        raise HTTPException(status_code=400, detail="difficulty must be easy|medium|hard|mixed")
    scope = (req.scope or "course").lower()
    if scope not in ("lecture", "week", "topic", "course"):
        raise HTTPException(status_code=400, detail="scope must be lecture|week|topic|course")
    course = req.course or ""
    captured_cfg = dict(cfg)

    def work(_progress):
        def prog(msg, frac=None):
            if frac is None:
                _progress(msg)
            else:
                _progress(msg, frac)
        result = practice_exam_mod.build(
            context.OUTPUT_DIR,
            course=course, n=n, types=types, difficulty=difficulty,
            scope=scope, target=req.target or "",
            weights=req.weights, seed=req.seed,
            include_answer_key=bool(req.include_answer_key),
            time_minutes=req.time_minutes, total_marks=req.total_marks,
            kind=kind, formats=formats, save_path=req.save_path,
            config=captured_cfg, db=context.db, course_id=cid,
            progress=prog,
        )
        _progress("done", 1.0)
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS and result.get("generated") == "ai":
            _audit("ai.practice_exam", target=captured_cfg.get("provider", ""),
                   detail=f"{kind} pack n={n}", feature="ai_cloud")
        return result

    label = ("Practice exam" if kind == "practice" else "Exam paper") + f" ({n}Q)"
    job = manager.submit(label, work, type="practice_exam",
                         payload=req.model_dump(), course_id=cid)
    return job.to_dict()
