"""§10 security: secret storage never plaintext, transparency labels, audit log."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import database, secrets as secret_store


def test_set_get_delete_roundtrip(tmp_path: Path):
    secret_store.set_secret("notion_token", "secret-abc-123", root=tmp_path)
    assert secret_store.get_secret("notion_token", root=tmp_path) == "secret-abc-123"
    assert "notion_token" in secret_store.list_secret_names(tmp_path)
    assert secret_store.delete_secret("notion_token", root=tmp_path) is True
    assert secret_store.get_secret("notion_token", root=tmp_path) is None


def test_secret_never_written_in_plaintext(tmp_path: Path):
    """The raw value must not appear verbatim in any file on disk."""
    secret_store.set_secret("anthropic", "sk-ant-PLAINTEXTLEAK", root=tmp_path)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            assert b"sk-ant-PLAINTEXTLEAK" not in raw


def test_clear_all_wipes(tmp_path: Path):
    secret_store.set_secret("a", "1", root=tmp_path)
    secret_store.set_secret("b", "2", root=tmp_path)
    secret_store.clear_all(tmp_path)
    assert secret_store.list_secret_names(tmp_path) == []


def test_transparency_labels():
    t = secret_store.transparency()
    assert secret_store.label_for("ai_cloud") == secret_store.CLOUD
    assert secret_store.label_for("transcribe") == secret_store.LOCAL_ONLY
    assert secret_store.label_for("moodle_import_url") == secret_store.LOCAL_INTERNET
    assert set(t["legend"]) == {secret_store.LOCAL_ONLY, secret_store.LOCAL_INTERNET,
                                secret_store.CLOUD}


def test_audit_log_dao(tmp_path: Path):
    db = database.Database(tmp_path / "t.db")
    db.add_audit("sync.notion", target="notion", detail="created=3", label="cloud-processed")
    events = db.list_audit()
    assert len(events) == 1 and events[0]["action"] == "sync.notion"
    assert db.clear_audit() == 1
    assert db.list_audit() == []
    db.close()


def test_backend_status_reports_encryption(tmp_path: Path):
    st = secret_store.backend_status()
    assert st["backend"] in ("keyring", "encrypted_file", "plain_file")
    # if not encrypted, a warning must be present so the user is informed
    if not st["encrypted"]:
        assert st["warning"]
