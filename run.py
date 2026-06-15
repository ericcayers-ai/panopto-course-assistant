#!/usr/bin/env python3
"""Convenience launcher: `python run.py` -> http://127.0.0.1:8000

Works regardless of the current working directory (chdirs to its own folder so
the `app` package imports cleanly).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402  (after sys.path setup)

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Panopto Course Assistant -> http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
