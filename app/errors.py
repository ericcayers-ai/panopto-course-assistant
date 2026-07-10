"""
errors.py - one error contract for every integration (§17).

Before this module each integration raised its own bare ``Exception`` subclass
(``MoodleApiError``, ``NotionError``, ``AnkiError``, …), so every caller had to
special-case every integration's failure. They now share :class:`AppError`,
which carries the same failure taxonomy the job queue already uses (§3) plus the
HTTP status the API should answer with. ``install_error_handler(app)`` renders
all of them through a single JSON shape, so the frontend needs one error path.

Wire shape (also see ``static/app.js``)::

    {"detail": "<message>",                     # back-compat with HTTPException
     "error": {"message": ..., "category": ..., "detail": {...}}}

``detail`` is kept because FastAPI's own ``HTTPException`` uses it and the
frontend already reads it; ``error`` is the structured envelope.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# The §3 failure taxonomy, shared with jobs.classify_failure().
CATEGORIES = (
    "network",
    "authentication",
    "dependency",
    "filesystem",
    "invalid_source",
    "unknown",
)


class AppError(Exception):
    """Base for every integration failure.

    Subclasses set ``category``/``status_code`` as class attributes so a raise
    site stays as short as ``raise NotionError("token rejected")`` while the API
    still answers with the right status and the UI still gets a category to
    render a hint from.
    """

    category: str = "unknown"
    status_code: int = 502

    def __init__(self, message: str = "", *, category: str = "",
                 status_code: int = 0, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message or self.__class__.__name__
        if category:
            self.category = category
        if status_code:
            self.status_code = status_code
        self.detail: Dict[str, Any] = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "category": self.category, "detail": self.detail}

    def payload(self) -> Dict[str, Any]:
        """The exact JSON body the API returns for this error."""
        return {"detail": self.message, "error": self.to_dict()}


def install_error_handler(app: Any) -> None:
    """Register the single handler that renders every :class:`AppError`."""
    from fastapi.responses import JSONResponse

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Any, exc: AppError) -> Any:  # noqa: ANN401
        return JSONResponse(status_code=exc.status_code, content=exc.payload())
