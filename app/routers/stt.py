"""routers/stt.py - adaptive STT management + live WebSocket."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import context
from .. import secrets as secret_store
from ..schemas import STTModelActionRequest, STTRouteRequest
from ..stt import models as model_mgmt
from ..stt import registry
from ..stt.engines import availability_map, get_engine, list_engines
from ..stt.hardware import probe_hardware
from ..stt.router import route
from ..stt.types import STTRequest

router = APIRouter()


@router.get("/api/stt/capabilities")
def api_stt_capabilities() -> Dict[str, Any]:
    hw = probe_hardware()
    engines = []
    for eng in list_engines():
        try:
            probe = eng.probe()
            caps = eng.capabilities().to_dict()
        except Exception as e:
            probe = {"installed": False, "error": str(e)}
            caps = {}
        engines.append({
            "name": eng.name,
            "display_name": eng.display_name,
            "probe": probe,
            "capabilities": caps,
        })
    return {
        "offline": True,
        "privacy": "Local/offline only — no cloud STT APIs.",
        "profiles": ["auto", "quality", "fast", "live", "eco"],
        "hardware": hw.to_dict(),
        "engines": engines,
        "models": [m.to_dict() for m in registry.list_models()],
        "cache": {
            "dir": str(model_mgmt.default_cache_dir()),
            "bytes": model_mgmt.cache_size_bytes(),
            "models": model_mgmt.list_cached_models(),
        },
        "preflight": model_mgmt.preflight_install(),
    }


@router.post("/api/stt/route")
def api_stt_route(req: STTRouteRequest) -> Dict[str, Any]:
    decision = route(
        STTRequest(
            profile=req.profile,
            language=req.language,
            device=req.device,
            code_switch=req.code_switch,
            engine=req.engine or None,
            model=req.model or None,
            caption_first=req.caption_first,
        ),
        hardware=probe_hardware(),
        available=availability_map(),
        has_usable_captions=req.has_usable_captions,
    )
    estimate = model_mgmt.estimate_download(decision.engine, decision.model)
    return {"route": decision.to_dict(), "estimate": estimate, "offline": True}


@router.get("/api/stt/models")
def api_stt_models() -> Dict[str, Any]:
    return registry.registry_summary()


@router.post("/api/stt/models/estimate")
def api_stt_model_estimate(req: STTModelActionRequest) -> Dict[str, Any]:
    return model_mgmt.estimate_download(req.engine, req.model_id)


@router.post("/api/stt/models/delete")
def api_stt_model_delete(req: STTModelActionRequest) -> Dict[str, Any]:
    if not req.model_id:
        raise HTTPException(status_code=400, detail="model_id required")
    return model_mgmt.delete_model(req.engine, req.model_id)


@router.post("/api/stt/models/record")
def api_stt_model_record(req: STTModelActionRequest) -> Dict[str, Any]:
    """Record that a model was downloaded (weights stay in cache — never in the repo)."""
    if not req.model_id:
        raise HTTPException(status_code=400, detail="model_id required")
    info = model_mgmt.estimate_download(req.engine, req.model_id)
    return model_mgmt.record_download(
        req.engine, req.model_id,
        bytes_size=int(info.get("disk_mb") or 0) * 1024 * 1024,
        license_accepted=True,
    )


@router.get("/api/stt/cache")
def api_stt_cache() -> Dict[str, Any]:
    return {
        "dir": str(model_mgmt.default_cache_dir()),
        "bytes": model_mgmt.cache_size_bytes(),
        "models": model_mgmt.list_cached_models(),
    }


@router.post("/api/stt/hf-token")
def api_stt_hf_token(body: Dict[str, Any]) -> Dict[str, Any]:
    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    secret_store.set_secret("huggingface_token", token, root=context.OUTPUT_DIR)
    return {"saved": True, "offline_after_download": True}


@router.websocket("/ws/stt/live")
async def ws_stt_live(websocket: WebSocket) -> None:
    """Live microphone session with provisional/final segments and backpressure."""
    await websocket.accept()
    session = None
    seq = 0
    paused = False
    parts: List[str] = []
    try:
        raw = await websocket.receive()
        if "text" in raw and raw["text"] is not None:
            msg = json.loads(raw["text"])
        else:
            await websocket.send_json({"event": "error", "error": "expected start JSON"})
            await websocket.close()
            return
        if msg.get("op") != "start":
            await websocket.send_json({"event": "error", "error": "send op=start first"})
            await websocket.close()
            return

        req = STTRequest(
            profile="live",
            language=msg.get("language") or "en",
            model=msg.get("model") or None,
            extras={"live": True},
        )
        decision = route(req, available=availability_map())
        engine_name = decision.engine
        try:
            eng = get_engine(engine_name)
            session = eng.start_stream(req)
        except Exception:
            session = None
            engine_name = "faster-whisper"
        await websocket.send_json({
            "event": "ready",
            "engine": engine_name,
            "model": decision.model,
            "reason": decision.reason,
            "seq": 0,
        })

        pcm_buf = bytearray()
        while True:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            if "text" in raw and raw["text"] is not None:
                ctrl = json.loads(raw["text"])
                op = ctrl.get("op")
                if op == "pause":
                    paused = True
                    await websocket.send_json({"event": "paused", "seq": seq})
                elif op == "resume":
                    paused = False
                    await websocket.send_json({"event": "resumed", "seq": seq})
                elif op == "stop":
                    text = " ".join(parts).strip()
                    if session is not None:
                        try:
                            result = session.finalize()
                            payload = result.to_legacy_dict()
                        except Exception as e:
                            payload = {
                                "text": text,
                                "segments": [{"start": 0, "end": 0, "text": text}],
                                "error": str(e),
                            }
                    else:
                        payload = {
                            "text": text,
                            "segments": [{"start": 0.0, "end": 0.0, "text": text}] if text else [],
                            "engine": engine_name,
                            "model": decision.model,
                            "schema_version": 2,
                        }
                    await websocket.send_json({"event": "done", "seq": seq, "result": payload})
                    break
                else:
                    await websocket.send_json({"event": "error", "error": f"unknown op {op}"})
            elif "bytes" in raw and raw["bytes"] is not None:
                if paused:
                    continue
                chunk = raw["bytes"]
                if len(pcm_buf) > 2_000_000:
                    await websocket.send_json({"event": "backpressure", "seq": seq})
                    pcm_buf.clear()
                pcm_buf.extend(chunk)
                seq += 1
                if session is not None:
                    try:
                        for ev in session.feed_audio(bytes(chunk)):
                            ev["seq"] = seq
                            if ev.get("text"):
                                parts.append(ev["text"])
                            await websocket.send_json(ev)
                    except Exception as e:
                        await websocket.send_json({"event": "error", "seq": seq, "error": str(e)})
                elif seq % 20 == 0:
                    await websocket.send_json({
                        "event": "provisional", "seq": seq,
                        "text": "", "final": False,
                        "buffered_bytes": len(pcm_buf),
                    })
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_json({"event": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
