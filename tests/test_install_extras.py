"""Tests for POST /api/setup/install-extras (pack allow-list + job queue)."""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _wait_job(c: TestClient, job_id: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "failed", "interrupted"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app)


def test_install_extras_rejects_unknown_pack(client):
    r = client.post("/api/setup/install-extras", json={"pack": "../../evil.txt"})
    assert r.status_code == 400
    assert "Unknown pack" in r.text


def test_install_extras_rejects_empty_pack(client):
    r = client.post("/api/setup/install-extras", json={"pack": ""})
    assert r.status_code == 400


def test_install_extras_queues_known_pack(client, monkeypatch):
    """Do not run real pip — stub subprocess so the job succeeds hermetically."""
    import app.routers.system as system_mod

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(system_mod.subprocess, "run", lambda *a, **k: FakeProc())
    r = client.post("/api/setup/install-extras", json={"pack": "tts"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "install_extras"
    job = _wait_job(client, body["id"])
    assert job["status"] == "done", job
    assert job["result"]["pack"] == "tts"
    assert job["result"]["ok"] is True
