"""
secrets.py — secret storage + data-transparency helpers (§10).

Goals
-----
* **Never** persist API keys / tokens / cookies in the DB or plaintext config.
* Prefer the OS keyring (Windows Credential Manager / macOS Keychain / libsecret);
  fall back to a local file that is **encrypted when `cryptography` is available**,
  and otherwise clearly flagged as un-encrypted so the user can decide.
* Give the UI a single place to label where each feature's data goes
  (``local-only | local+internet | cloud-processed``) and an audit trail of
  anything that actually left the machine.

The backend in use is reported by :func:`backend_status` so the UI can warn when
storage isn't encrypted.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_SERVICE = "course-assistant"


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


def _have(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _keyring():
    if not _have("keyring"):
        return None
    try:
        import keyring
        # A null/fail backend counts as unavailable.
        from keyring.backends.fail import Keyring as FailKeyring
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring
    except Exception:
        return None


def backend_status() -> Dict[str, Any]:
    kr = _keyring()
    if kr is not None:
        return {"backend": "keyring", "encrypted": True, "warning": ""}
    if _have("cryptography"):
        return {"backend": "encrypted_file", "encrypted": True, "warning": ""}
    return {"backend": "plain_file", "encrypted": False,
            "warning": "Secrets are stored obfuscated but NOT encrypted "
                       "(install 'keyring' or 'cryptography' for secure storage)."}


# ---------------------------------------------------------------------------
# Encrypted-file fallback
# ---------------------------------------------------------------------------


def _store_path(root: Path) -> Path:
    return Path(root) / ".secrets.json"


def _fernet(root: Path):
    """Derive a stable Fernet key kept in a sibling file (0600). Best-effort:
    this protects against casual disk inspection, not a determined local attacker
    (a single-user local app — the threat model is shoulder-surfing/backups)."""
    from cryptography.fernet import Fernet
    key_path = Path(root) / ".secrets.key"
    if key_path.exists():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    return Fernet(key)


def _load_file(root: Path) -> Dict[str, str]:
    path = _store_path(root)
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if _have("cryptography"):
        try:
            raw = _fernet(root).decrypt(raw)
        except Exception:
            pass  # may have been written as plain obfuscation
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    # values are base64 when stored un-encrypted (obfuscation only)
    return {k: base64.b64decode(v).decode("utf-8") if isinstance(v, str) else ""
            for k, v in data.items()}


def _save_file(root: Path, data: Dict[str, str]) -> None:
    path = _store_path(root)
    obfuscated = {k: base64.b64encode(v.encode("utf-8")).decode("ascii")
                  for k, v in data.items()}
    blob = json.dumps(obfuscated).encode("utf-8")
    if _have("cryptography"):
        blob = _fernet(root).encrypt(blob)
    path.write_bytes(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public API — get / set / delete / list (names only)
# ---------------------------------------------------------------------------


def set_secret(name: str, value: str, *, root: Path) -> None:
    kr = _keyring()
    if kr is not None:
        kr.set_password(_SERVICE, name, value)
        _track(root, name, add=True)
        return
    data = _load_file(root)
    data[name] = value
    _save_file(root, data)


def get_secret(name: str, *, root: Path) -> Optional[str]:
    kr = _keyring()
    if kr is not None:
        try:
            return kr.get_password(_SERVICE, name)
        except Exception:
            return None
    return _load_file(root).get(name)


def delete_secret(name: str, *, root: Path) -> bool:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(_SERVICE, name)
            _track(root, name, add=False)
            return True
        except Exception:
            return False
    data = _load_file(root)
    if name in data:
        del data[name]
        _save_file(root, data)
        return True
    return False


def list_secret_names(root: Path) -> List[str]:
    """Names only — values are never enumerated through the API."""
    if _keyring() is not None:
        return _tracked(root)
    return sorted(_load_file(root).keys())


def clear_all(root: Path) -> None:
    for name in list_secret_names(root):
        delete_secret(name, root=root)
    for f in (_store_path(root), Path(root) / ".secrets.key"):
        try:
            f.unlink()
        except OSError:
            pass


# keyring can't enumerate our entries, so track the names in a sidecar index.
def _index_path(root: Path) -> Path:
    return Path(root) / ".secret_names.json"


def _tracked(root: Path) -> List[str]:
    p = _index_path(root)
    if not p.exists():
        return []
    try:
        return sorted(json.loads(p.read_text()))
    except Exception:
        return []


def _track(root: Path, name: str, *, add: bool) -> None:
    names = set(_tracked(root))
    names.add(name) if add else names.discard(name)
    _index_path(root).write_text(json.dumps(sorted(names)))


# ---------------------------------------------------------------------------
# Data-transparency labels (§10)
# ---------------------------------------------------------------------------

LOCAL_ONLY = "local-only"
LOCAL_INTERNET = "local+internet"
CLOUD = "cloud-processed"

# Where each feature's data goes — surfaced in the UI before an action runs.
FEATURE_LABELS: Dict[str, str] = {
    "transcribe": LOCAL_ONLY,
    "import_folder": LOCAL_ONLY,
    "moodle_import_file": LOCAL_ONLY,
    "moodle_import_url": LOCAL_INTERNET,   # fetches pages from the LMS
    "export": LOCAL_ONLY,
    "ai_local": LOCAL_ONLY,                # ollama / llama.cpp
    "ai_cloud": CLOUD,                     # openai / anthropic
    "sync_notion": CLOUD,
    "sync_anki": LOCAL_INTERNET,           # localhost AnkiConnect
}


def label_for(feature: str) -> str:
    return FEATURE_LABELS.get(feature, LOCAL_ONLY)


def transparency() -> Dict[str, Any]:
    return {"labels": FEATURE_LABELS,
            "legend": {LOCAL_ONLY: "Stays on this machine.",
                       LOCAL_INTERNET: "Talks to the network but isn't sent to a third-party AI.",
                       CLOUD: "Content is sent to an external provider you configured."}}
