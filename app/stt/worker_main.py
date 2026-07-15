"""Subprocess entry: python -m app.stt.worker_main"""
from __future__ import annotations

import sys

from .workers import decode_line, encode_message, handle_worker_message


def main() -> int:
    for raw in sys.stdin.buffer:
        try:
            msg = decode_line(raw)
            if not msg:
                continue
            resp = handle_worker_message(msg)
            sys.stdout.buffer.write(encode_message(resp))
            sys.stdout.buffer.flush()
            if msg.get("op") == "shutdown":
                return 0
        except Exception as e:
            sys.stdout.buffer.write(encode_message({
                "ok": False, "event": "error", "error": str(e),
            }))
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
