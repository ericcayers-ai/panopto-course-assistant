"""
main.py - FastAPI application assembly for the Panopto Course Assistant.

This module used to hold all 129 routes. It now does only what an app module
should: bind app-wide state (:mod:`app.context`), create the FastAPI app,
install the one error handler (:mod:`app.errors`), and mount the routers.

The HTTP surface lives in :mod:`app.routers`, one module per resource group::

    pages      /  and  /docs
    system     status, environment, backup/restore, settings, native pickers
    library    transcripts, search, index, saved views, materials
    courses    course CRUD, duplicate/archive/export
    jobs       job queue + transcription
    exports    export presets/targets, flashcards
    study      streak, glossary, keywords, study guide, notes, tags, quiz
    planner    assessments, plan, calendar, spaced repetition, progress
    moodle     web-service import, SSO, Panopto feed discovery
    ingest     feeds, folders, documents, Notion/PDF conversion
    llm        provider settings, summarize/flashcards/quiz/chat
    ollama     local model management
    sync       Notion + Anki live sync
    security   secrets, privacy labels, audit log
    analytics  local usage stats (no cloud)
    tts        text-to-speech

Request bodies live in :mod:`app.schemas`; shared helpers in :mod:`app.context`.
Browse the live route list at ``/docs`` (rendered offline, no CDN).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import context
from .errors import install_error_handler
from .routers import (analytics, courses, exports, ingest, jobs, library, llm,
                      moodle, ollama, pages, planner, security, study, sync,
                      system, tts)

# Re-exported so `main.moodle_api` keeps resolving: the endpoint tests monkeypatch
# its transport on the module object, which routers/moodle.py imports too.
from .imports import moodle_api  # noqa: F401

# Bind OUTPUT_DIR / db / the job queue from the environment. Re-runs on
# importlib.reload(app.main), which is how the tests point at a temp directory.
context.init()

APP_VERSION = context.APP_VERSION

# Default Swagger/ReDoc pull their JS+CSS from a CDN, so /docs is blank when the
# machine is offline. We disable them and serve a self-contained docs page.
app = FastAPI(title="Course Assistant", version=APP_VERSION,
              docs_url=None, redoc_url=None)

# §17: every integration failure (Moodle, Notion, Anki, LLM, …) renders through
# one JSON envelope, so the frontend has a single error path instead of six.
install_error_handler(app)

for _router in (system, tts, ollama, courses, llm, sync, planner, study,
                security, analytics, library, exports, jobs, ingest, moodle,
                pages):
    app.include_router(_router.router)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that tells browsers to always revalidate.

    Without this, assets are served with only an ETag/Last-Modified and browsers
    apply *heuristic caching* - happily serving a stale app.js/style.css after an
    update without checking. ``no-cache`` keeps the ETag fast-path (304 when
    unchanged) but forces a revalidation every load, so updates show immediately.
    """

    def file_response(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStaticFiles(directory=str(context.STATIC_DIR)), name="static")


def __getattr__(name: str) -> Any:
    """Keep ``main.db`` / ``main.OUTPUT_DIR`` working after the §17 split.

    The state moved to :mod:`app.context`, but tests (and any caller that grew
    up with the old layout) still reach for it here. Forwarding on attribute
    lookup - rather than copying at import - means they always see the value
    :func:`context.init` most recently bound.
    """
    if name in ("db", "OUTPUT_DIR", "STATIC_DIR", "BASE_DIR"):
        return getattr(context, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
