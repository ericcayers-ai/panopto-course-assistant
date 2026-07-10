"""routers/analytics.py - analytics endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from typing import Any
from typing import Dict
from typing import Optional
from .. import analytics
from .. import settings_store
from .. import context

router = APIRouter()


@router.get("/api/analytics")
def api_analytics(course: Optional[int] = None) -> Dict[str, Any]:
    stats = analytics.compute(context.db, course)
    stats["feedback_prompt"] = analytics.feedback_prompt(stats)
    return stats


@router.post("/api/analytics/export")
def api_analytics_export() -> Dict[str, Any]:
    """User-initiated, anonymised diagnostics JSON (no secrets/PII, never auto-sent)."""
    return analytics.diagnostics_export(context.db, context.OUTPUT_DIR,
                                       settings_store.get_active_course(context.db))
