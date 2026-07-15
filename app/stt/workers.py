"""JSON-lines subprocess worker protocol for heavy STT backends.

Workers keep Torch/Transformers/pyannote/Moonshine out of the FastAPI process.
Communication is newline-delimited JSON on stdin/stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


PROTOCOL_VERSION = 1


def encode_message(msg: Dict[str, Any]) -> bytes:
    payload = {"v": PROTOCOL_VERSION, **msg}
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: bytes | str) -> Dict[str, Any]:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return {}
    return json.loads(line)


class WorkerClient:
    """Spawn and talk to an STT worker process."""

    def __init__(self, python: Optional[str] = None, env: Optional[Dict[str, str]] = None,
                 timeout: float = 600.0):
        self.python = python or sys.executable
        self.env = env
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        cmd = [self.python, "-m", "app.stt.worker_main"]
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(Path(__file__).resolve().parents[2]),
        )

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.write(encode_message({"op": "shutdown"}))
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def request(self, op: str, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            self.start()
            assert self._proc and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(encode_message({"op": op, **kwargs}))
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                err = ""
                try:
                    err = (self._proc.stderr.read() or b"").decode("utf-8", errors="replace")
                except Exception:
                    pass
                raise RuntimeError(f"STT worker died during {op}: {err[:500]}")
            resp = decode_line(line)
            if resp.get("ok") is False:
                raise RuntimeError(resp.get("error") or f"worker op {op} failed")
            return resp

    def iter_events(self, op: str, **kwargs: Any) -> Iterator[Dict[str, Any]]:
        """For streaming ops: yields events until a final ``done`` event."""
        with self._lock:
            self.start()
            assert self._proc and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(encode_message({"op": op, **kwargs}))
            self._proc.stdin.flush()
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"STT worker died during streaming {op}")
                ev = decode_line(line)
                yield ev
                if ev.get("event") in {"done", "error"}:
                    return


def handle_worker_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a worker message in-process (used by worker_main and tests)."""
    op = msg.get("op") or ""
    if op == "ping":
        return {"ok": True, "event": "done", "pong": True}
    if op == "shutdown":
        return {"ok": True, "event": "done", "shutdown": True}
    if op == "probe":
        from . import engines as eng
        name = msg.get("engine") or "faster-whisper"
        engine = eng.get_engine(name)
        return {"ok": True, "event": "done", "probe": engine.probe()}
    if op == "transcribe":
        from .pipeline import transcribe_path
        from .types import STTRequest
        req = STTRequest.from_dict(msg.get("request") or {})
        path = msg.get("path") or req.media_path
        # Avoid nested worker spawn when the parent already isolated us.
        os.environ["PANOPTO_STT_INPROCESS"] = "1"
        result = transcribe_path(path, req)
        return {"ok": True, "event": "done", "result": result.to_legacy_dict()}
    if op == "transcribe_chunk":
        # Single-chunk engine call — no nested caption/chunk loop.
        os.environ["PANOPTO_STT_INPROCESS"] = "1"
        from . import engines as eng
        from .types import STTRequest, STTResult
        name = msg.get("engine") or "faster-whisper"
        req = STTRequest.from_dict(msg.get("request") or {})
        path = msg.get("path") or req.media_path
        engine = eng.get_engine(name)
        result: STTResult = engine.transcribe_file(path, req)
        return {"ok": True, "event": "done", "result": result.to_dict()}
    return {"ok": False, "event": "error", "error": f"unknown op: {op}"}
