"""routers/security.py - security endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import secrets as secret_store
from .. import context
from ..context import _audit
from ..schemas import SecretReq

router = APIRouter()


@router.get("/api/secrets")
def api_secrets_list() -> Dict[str, Any]:
    return {"backend": secret_store.backend_status(),
            "names": secret_store.list_secret_names(context.OUTPUT_DIR)}


@router.put("/api/secrets/{name}")
def api_secrets_set(name: str, req: SecretReq) -> Dict[str, Any]:
    if not req.value.strip():
        raise HTTPException(status_code=400, detail="Empty secret.")
    secret_store.set_secret(name, req.value, root=context.OUTPUT_DIR)
    _audit("secret.set", target=name, feature="")
    return {"stored": name, "backend": secret_store.backend_status()["backend"]}


@router.delete("/api/secrets/{name}")
def api_secrets_delete(name: str) -> Dict[str, Any]:
    ok = secret_store.delete_secret(name, root=context.OUTPUT_DIR)
    return {"deleted": name if ok else "", "ok": ok}


@router.post("/api/secrets/clear")
def api_secrets_clear() -> Dict[str, Any]:
    secret_store.clear_all(context.OUTPUT_DIR)
    _audit("secret.clear_all")
    return {"cleared": True}


@router.get("/api/privacy")
def api_privacy() -> Dict[str, Any]:
    return {"transparency": secret_store.transparency(),
            "secrets": secret_store.backend_status()}


@router.get("/api/audit")
def api_audit(limit: int = 200) -> Dict[str, Any]:
    rows = context.db.list_audit(limit)
    return {"events": [dict(r) for r in rows]}


@router.post("/api/audit/clear")
def api_audit_clear() -> Dict[str, Any]:
    return {"cleared": context.db.clear_audit()}
