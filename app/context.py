"""context.py - app-wide state and shared route helpers (§17).

`main.py` used to own the database handle, the output directory and every
cross-cutting helper, which is why 129 routes could not live anywhere else.
They live here now. Routers read the state through this module
(``context.db``, not ``from .context import db``) because :func:`init` rebinds
it on ``importlib.reload(app.main)`` - the pattern the test-suite relies on to
point the app at a temp directory.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import json
import shutil

from . import ai
from . import core
from . import courses
from . import database
from . import llm
from . import secrets as secret_store
from . import settings_store
from . import study
from . import transcribe
from .imports import folder as folder_import
from .integrations import state as sync_state
from .jobs import manager
from .schemas import AnkiSyncReq, NotionSyncReq

def _detect_base_dir() -> Path:
    """Resolve the application root for source and portable/frozen layouts."""
    env_root = os.environ.get("CA_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    # Portable ZIP: runtime\\python.exe runs run.py with cwd = ZIP root.
    here = Path(__file__).resolve().parent.parent
    if (here / "app").is_dir() and (here / "static").is_dir():
        return here
    return here


def _probe_writable(candidate: Path) -> bool:
    """True when we can create the directory and write a probe file."""
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _default_output_dir(base: Path) -> Path:
    """Writable library directory: beside the app, else LOCALAPPDATA.

    ``PANOPTO_OUTPUT`` is preferred when set *and writable*. Portable installs
    on read-only media still fall back to ``%LOCALAPPDATA%\\CourseAssistant``.
    """
    candidates: list[Path] = []
    if os.environ.get("PANOPTO_OUTPUT"):
        candidates.append(Path(os.environ["PANOPTO_OUTPUT"]).expanduser().resolve())
    candidates.append((base / "transcripts").resolve())
    for candidate in candidates:
        if _probe_writable(candidate):
            return candidate
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or str(base)
    fallback = Path(local) / "CourseAssistant" / "transcripts"
    return fallback.resolve()


BASE_DIR = _detect_base_dir()

# Single version source — keep in sync with app/__init__.py only via import.
from . import __version__ as APP_VERSION  # noqa: E402

# Rebound by init(); routers must reference these as ``context.<name>``.
STATIC_DIR: Path = BASE_DIR / "static"
OUTPUT_DIR: Path = BASE_DIR / "transcripts"
db: Any = None


def init() -> None:
    """(Re)bind app-wide state from the environment. Idempotent."""
    global BASE_DIR, STATIC_DIR, OUTPUT_DIR, db
    BASE_DIR = _detect_base_dir()
    STATIC_DIR = Path(os.environ.get("CA_STATIC_DIR", BASE_DIR / "static")).resolve()
    OUTPUT_DIR = _default_output_dir(BASE_DIR)
    core.ensure_dir(OUTPUT_DIR)
    # Durable SQLite store lives alongside the library. Re-initialised on reload
    # so the tests can rebind everything to a fresh output directory.
    db = database.init(OUTPUT_DIR / "course_assistant.db")
    manager.bind(db)      # jobs now survive restarts; crashed jobs -> 'interrupted'
    _backfill_library(db, OUTPUT_DIR)


def _backfill_library(database_, output_dir: Path) -> None:
    """One-time import of an existing ``transcripts/`` folder into the DB index.

    Runs only when the DB has no courses yet, so we never orphan files a user
    already produced before persistence existed (roadmap §Conventions). Idempotent
    via ``INSERT OR IGNORE`` on the file path.
    """
    if database_.count_courses() > 0:
        return
    groups = core.list_transcripts(output_dir)
    library = core.list_library(output_dir)
    documents = library["categories"]["documents"]
    if not groups and not documents:
        return
    course = courses.create_course(database_, name="My Course")
    cid = course["id"]
    for g in groups:
        fmts = g["formats"]
        primary = (fmts.get("txt") or fmts.get("md") or fmts.get("json")
                   or next(iter(fmts.values()), ""))
        if not primary:
            continue
        title = g["stem"]
        database_.insert_transcript(
            cid, title=title, path=primary,
            week=core.infer_week(title), topic=core.infer_topic(title),
        )
    for d in documents:
        database_.insert_document(
            cid, title=d["name"], path=d["path"], type="document",
            import_source="backfill",
        )


def _safe_ai_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Never echo a stored API key back to the client - report only its presence."""
    out = {k: v for k, v in cfg.items() if k != "api_key"}
    out["has_api_key"] = bool(cfg.get("api_key"))
    return out


def _copy_export_files(rel_paths: List[str], src_root: Path, dest_dir: Path) -> None:
    """Copy exported files (given as rel paths from src_root) into dest_dir,
    flattening the hierarchy so all files land directly in dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        if not rel:
            continue
        src = src_root / rel
        if src.exists():
            shutil.copy2(src, dest_dir / src.name)


def _find_media(folder: Path, stem: str) -> Optional[Path]:
    """Return the lecture recording for ``stem`` inside ``folder`` if one exists."""
    if not folder.is_dir():
        return None
    for ext in folder_import.MEDIA_EXTS:
        cand = folder / f"{stem}{ext}"
        if cand.exists():
            return cand
    return None


def _export_recordings(dest_dir: Path, cookies: str = "") -> Dict[str, Any]:
    """Ensure each transcribed lecture's recording sits next to its exported SRT.

    For every transcript we copy the recording from the library if it was kept
    there, otherwise we try to (re)download it from the source URL stored in the
    lecture's JSON. Subtitle players auto-load a ``.srt`` when the video shares
    its folder and stem, so this makes the SRT export self-contained.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    downloaded: List[str] = []
    missing: List[str] = []
    for g in core.list_transcripts(OUTPUT_DIR):
        fmts = g.get("formats", {})
        if "json" not in fmts:
            continue
        stem = g["stem"]
        if _find_media(dest_dir, stem):
            copied.append(stem)            # already present in the destination
            continue
        json_path = OUTPUT_DIR / fmts["json"]
        lib_media = _find_media(json_path.parent, stem)
        if lib_media:
            try:
                shutil.copy2(lib_media, dest_dir / lib_media.name)
                copied.append(stem)
                continue
            except OSError:
                pass
        # Not kept in the library - re-download from the stored source URL.
        # Prefer the mp4 video URL (we transcribe from audio, so `url` may be mp3).
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            url = (data.get("video_url") or data.get("url") or "").strip()
        except (json.JSONDecodeError, OSError):
            url = ""
        if url:
            try:
                transcribe.download_media(url, dest_dir / f"{stem}.mp4", cookies=cookies)
                downloaded.append(stem)
                continue
            except Exception:
                pass
        missing.append(stem)
    return {"copied": copied, "downloaded": downloaded, "missing": missing,
            "have": len(copied) + len(downloaded)}


def _notion_args(req: NotionSyncReq) -> Dict[str, Any]:
    cfg = sync_state.get(db)["notion"]
    token = sync_state.notion_token(db, req.token)
    database_id = req.database_id or cfg.get("database_id", "")
    if not token:
        raise HTTPException(status_code=400, detail="No Notion token configured (set it in Sync settings or NOTION_TOKEN).")
    if not database_id:
        raise HTTPException(status_code=400, detail="No Notion database_id configured.")
    return {"token": token, "database_id": database_id,
            "course": req.course, "field_map": cfg.get("field_map")}


def _anki_cards(req: AnkiSyncReq) -> List[Dict[str, Any]]:
    out = ai.generate_flashcards(OUTPUT_DIR, selection=req.selection, course=req.course,
                                db=db, course_id=settings_store.get_active_course(db))
    return out.get("cards", [])


def _course_or_active(course_id: Optional[int]) -> int:
    cid = course_id if course_id is not None else settings_store.get_active_course(db)
    if cid is None:
        raise HTTPException(status_code=400, detail="No active course; create one first.")
    return cid


def _active_course_name() -> str:
    cid = settings_store.get_active_course(db)
    if cid is None:
        return ""
    row = db.get_course(cid)
    return (row["code"] or row["name"]) if row else ""


def _note_dict(row) -> Dict[str, Any]:
    keys = row.keys() if hasattr(row, "keys") else []
    return {"id": row["id"], "path": row["path"] or "", "body": row["body"],
            "title": row["title"] if "title" in keys else "",
            "folder_id": row["folder_id"] if "folder_id" in keys else None,
            "session_type": row["session_type"] if "session_type" in keys else "",
            "course_id": row["course_id"] if "course_id" in keys else None,
            "timestamp_s": row["timestamp_s"], "bookmark": bool(row["bookmark"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _audit(action: str, target: str = "", detail: str = "", feature: str = "") -> None:
    """Record an external/cloud action so the user can review what left the box."""
    try:
        db.add_audit(action, target=target, detail=detail,
                    label=secret_store.label_for(feature) if feature else "")
    except Exception:
        pass


def _json_loads(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


_STUDY_SUMMARY_SYSTEM = (
    "You are a concise study assistant. Summarise this lecture into 2–3 sentences of "
    "clean, accurate study notes, grounded strictly in the provided text. No preamble, "
    "no markdown, no bullet points - just the sentences."
)


def _study_summarizer(cfg: Dict[str, Any]):
    """Build an LLM-backed Summary-column writer for the Notion CSV, falling back to
    the extractive cell when the model is unreachable or the text is empty."""
    def summarize(output_dir, group, json_text: str) -> str:
        text = (json_text or "").strip()
        if not text:
            return study._summary_cell(output_dir, group, json_text)
        try:
            out = llm.complete(f"Summarise this lecture in 2–3 sentences:\n\n{text[:12000]}",
                               system=_STUDY_SUMMARY_SYSTEM, config=cfg)
            if out.strip():
                return " ".join(out.split())
        except llm.LLMError:
            pass
        return study._summary_cell(output_dir, group, json_text)
    return summarize


def _make_transcribe_work(payload: Dict[str, Any]):
    lecture = payload.get("lecture", {})
    item = core.LectureItem(
        title=lecture.get("title", "lecture"),
        url=lecture.get("url", ""),
        size=int(lecture.get("size", 0) or 0),
        duration=int(lecture.get("duration", 0) or 0),
        pub_date=lecture.get("pub_date", ""),
        author=lecture.get("author", ""),
        guid=lecture.get("guid", ""),
    )

    def work(progress) -> Dict[str, Any]:
        return transcribe.transcribe_lecture(
            item, OUTPUT_DIR,
            engine=payload.get("engine", "auto"),
            model=payload.get("model", "auto"),
            language=payload.get("language", "en"),
            device=payload.get("device", "auto"),
            organize=payload.get("organize", "auto"),
            outputs=payload.get("outputs", ["txt", "md", "json", "summary"]),
            interval=payload.get("interval", 30),
            keep_media=payload.get("keep_media", False),
            audio_only=payload.get("audio_only", False),
            skip_existing=payload.get("skip_existing", True),
            force=payload.get("force", False),
            cookies=payload.get("cookies", ""),
            course=payload.get("course", ""),
            video_url=lecture.get("video_url", ""),
            progress=progress,
            profile=payload.get("profile", "auto"),
            code_switch=bool(payload.get("code_switch", False)),
            word_timestamps=bool(payload.get("word_timestamps", True)),
            diarization=payload.get("diarization", "off"),
            speakers=payload.get("speakers"),
            vocabulary=payload.get("vocabulary"),
            caption_first=bool(payload.get("caption_first", True)),
            caption_url=payload.get("caption_url") or lecture.get("caption_url", ""),
            resume=bool(payload.get("resume", True)),
            chunk_seconds=int(payload.get("chunk_seconds", 180) or 180),
            compute=payload.get("compute", "auto"),
            hotwords=payload.get("hotwords", ""),
            initial_prompt=payload.get("initial_prompt", ""),
            use_adaptive=bool(payload.get("use_adaptive", True)),
        )

    return work


JOB_FACTORIES = {"transcribe": _make_transcribe_work}


def _moodle_token_name(host: str) -> str:
    return f"moodle_token:{host}"


def _sso_provider_label(host: str) -> str:
    """Friendly name for a detected SSO identity-provider host."""
    h = (host or "").lower()
    if "microsoft" in h or "live.com" in h:
        return "Microsoft"
    if "google" in h:
        return "Google"
    if "okta" in h:
        return "Okta"
    return "single sign-on (SSO)"


def _save_api_outline(model: Dict[str, Any]) -> str:
    """Write the labelled course outline as an AI/NotebookLM source. Returns rel path."""
    core.ensure_dir(OUTPUT_DIR)
    c = model["course"]
    stem = core.safe_name(c.get("code") or c.get("fullname") or "course") + "_outline"
    target = OUTPUT_DIR / f"{stem}.md"
    target.write_text(model["outline_markdown"], encoding="utf-8")
    return target.relative_to(OUTPUT_DIR).as_posix()


_MOODLE_PASSPORT = "courseassistant"
