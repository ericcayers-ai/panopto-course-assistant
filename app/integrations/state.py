"""
integrations/state.py - persisted connection config + last-sync timestamps (§5).

Sync targets (Notion, Anki) need a little durable state: credentials/endpoints,
the editable field mapping, and when each target was last synced. It all lives
under the single ``sync`` settings key so it round-trips with the rest of the
preferences and is wiped by the §10 "clear sync history" control.

Secrets are stored here only as a stop-gap; §10 moves tokens into the OS keyring.
Tokens are never echoed back through the API (see ``public_status``).
"""
from __future__ import annotations

import os
from typing import Any, Dict

from .. import settings_store
from ..database import Database
from .notion import DEFAULT_FIELD_MAP
from .anki import DEFAULT_URL as ANKI_DEFAULT_URL

_KEY = "sync"

_DEFAULT = {
    "notion": {"token": "", "database_id": "", "parent_page_id": "",
               "field_map": dict(DEFAULT_FIELD_MAP), "last_sync": ""},
    "anki": {"url": ANKI_DEFAULT_URL, "last_sync": ""},
}


def get(db: Database) -> Dict[str, Any]:
    """Merge stored sync config over the defaults (deep on the two known targets)."""
    cfg = {k: dict(v) for k, v in _DEFAULT.items()}
    stored = settings_store.get(db, _KEY) or {}
    if isinstance(stored, dict):
        for target in ("notion", "anki"):
            if isinstance(stored.get(target), dict):
                cfg[target].update(stored[target])
    # field_map always a full mapping
    fm = cfg["notion"].get("field_map") or {}
    cfg["notion"]["field_map"] = {**DEFAULT_FIELD_MAP, **fm}
    return cfg


def set_target(db: Database, target: str, values: Dict[str, Any]) -> Dict[str, Any]:
    stored = settings_store.get(db, _KEY) or {}
    if not isinstance(stored, dict):
        stored = {}
    cur = stored.get(target) if isinstance(stored.get(target), dict) else {}
    cur.update(values)
    stored[target] = cur
    settings_store.set(db, _KEY, stored)
    return get(db)


def notion_token(db: Database, override: str = "") -> str:
    if override:
        return override
    # Prefer the §10 secret store (keyring), then legacy settings, then env.
    try:
        from .. import secrets as secret_store
        kr = secret_store.get_secret("notion_token", root=db.root)
        if kr:
            return kr
    except Exception:
        pass
    stored = get(db)["notion"].get("token", "")
    return stored or os.environ.get("NOTION_TOKEN", "")


def public_status(db: Database) -> Dict[str, Any]:
    """Connection status for the UI - never leaks the token, only its presence."""
    cfg = get(db)
    return {
        "notion": {
            "connected": bool(notion_token(db)),
            "has_token": bool(notion_token(db)),
            "database_id": cfg["notion"].get("database_id", ""),
            "field_map": cfg["notion"].get("field_map", {}),
            "last_sync": cfg["notion"].get("last_sync", ""),
        },
        "anki": {
            "url": cfg["anki"].get("url", ANKI_DEFAULT_URL),
            "last_sync": cfg["anki"].get("last_sync", ""),
        },
    }
