#!/usr/bin/env python3
"""Convenience launcher: `python run.py` -> http://127.0.0.1:8000"""
import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Panopto Course Assistant -> http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
