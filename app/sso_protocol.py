"""Windows URL protocol handler for courseassistant:// SSO callbacks.

After Moodle's launch.php flow completes with urlscheme=courseassistant, the browser
navigates to courseassistant://token=BASE64.  On Windows, the registered handler
intercepts this, POSTs to /api/moodle/sso-callback, and the frontend polling loop
picks up the token automatically — no copy-paste required.

No admin privileges needed: the handler is written to HKCU.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional

SCHEME = "courseassistant"

_lock = threading.Lock()
_pending: Optional[dict] = None   # {"token": str, "at": float}

_APP_DIR = Path.home() / ".courseassistant"
_PORT_FILE = _APP_DIR / "server_port"
_HANDLER_SCRIPT = _APP_DIR / "sso_handler.pyw"

_HANDLER_CODE = '''\
import sys
import json
import urllib.request
from pathlib import Path

port_file = Path.home() / ".courseassistant" / "server_port"
try:
    port = int(port_file.read_text().strip())
except Exception:
    port = 8123

if len(sys.argv) < 2:
    sys.exit(0)

try:
    data = json.dumps({"raw": sys.argv[1]}).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/api/moodle/sso-callback",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
'''


def store_token(token: str) -> None:
    """Store an incoming SSO token for the next poll."""
    import time
    global _pending
    with _lock:
        _pending = {"token": token, "at": time.monotonic()}


def poll_token() -> Optional[str]:
    """Return and clear the pending token (within 5-min window), else None."""
    import time
    global _pending
    with _lock:
        p = _pending
        if p and time.monotonic() - p["at"] < 300:
            _pending = None
            return p["token"]
        return None


def register(port: int) -> bool:
    """Register courseassistant:// in Windows HKCU.  Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        _APP_DIR.mkdir(parents=True, exist_ok=True)
        _PORT_FILE.write_text(str(port), encoding="ascii")
        _HANDLER_SCRIPT.write_text(_HANDLER_CODE, encoding="utf-8")
    except Exception:
        return False
    try:
        import winreg
    except ImportError:
        return False

    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else str(py)
    cmd = f'"{exe}" "{_HANDLER_SCRIPT}" "%1"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              f"Software\\Classes\\{SCHEME}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Course Assistant Token")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              f"Software\\Classes\\{SCHEME}\\shell\\open\\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
        return True
    except Exception:
        return False
