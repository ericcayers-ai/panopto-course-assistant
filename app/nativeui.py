"""
nativeui.py - native operating-system file dialogs for a locally-run app.

The application runs as a local server the user opens in their own browser, so
"choose where to save" is best served by a real OS file-explorer dialog rather
than typing a path. Browsers cannot open an arbitrary native folder picker, but
the backend (running on the same machine) can: it launches a short, isolated
subprocess that shows a Tkinter dialog and prints the chosen path.

Running the dialog in a subprocess keeps Tk off the server's worker threads
(Tk has strict thread affinity) and guarantees a clean teardown after each use.
Every call degrades gracefully: if no display/Tk is available the function
returns ``None`` and the caller falls back to a typed path.
"""
from __future__ import annotations

import subprocess
import sys
from typing import List, Optional

# Shown in the dialog when the caller gives no starting directory.
_TIMEOUT_SECONDS = 600  # the dialog stays open until the user acts; cap defensively.


_DIR_SCRIPT = r"""
import sys
import tkinter as tk
from tkinter import filedialog

title = sys.argv[1] if len(sys.argv) > 1 else "Choose a folder"
initial = sys.argv[2] if len(sys.argv) > 2 else ""

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
path = filedialog.askdirectory(title=title, initialdir=initial or None, mustexist=False)
root.destroy()
sys.stdout.write(path or "")
"""


_SAVE_SCRIPT = r"""
import sys
import tkinter as tk
from tkinter import filedialog

title = sys.argv[1] if len(sys.argv) > 1 else "Save as"
initial_file = sys.argv[2] if len(sys.argv) > 2 else ""
initial_dir = sys.argv[3] if len(sys.argv) > 3 else ""
ext = sys.argv[4] if len(sys.argv) > 4 else ""

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
kwargs = {"title": title}
if initial_file:
    kwargs["initialfile"] = initial_file
if initial_dir:
    kwargs["initialdir"] = initial_dir
if ext:
    kwargs["defaultextension"] = ext
    label = ext.lstrip(".").upper()
    kwargs["filetypes"] = [(f"{label} file", f"*{ext}"), ("All files", "*.*")]
path = filedialog.asksaveasfilename(**kwargs)
root.destroy()
sys.stdout.write(path or "")
"""


def _run(script: str, args: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, *args],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def available() -> bool:
    """Whether a native dialog can be shown on this machine (Tk importable)."""
    try:
        import importlib.util
        return importlib.util.find_spec("tkinter") is not None
    except Exception:
        return False


def pick_directory(title: str = "Choose a folder", initial_dir: str = "") -> Optional[str]:
    """Open a native folder picker. Returns the chosen path, or ``None`` if the
    user cancelled or no dialog could be shown."""
    return _run(_DIR_SCRIPT, [title, initial_dir])


def pick_save_file(title: str = "Save as", default_name: str = "",
                   initial_dir: str = "", ext: str = "") -> Optional[str]:
    """Open a native "Save As" dialog. Returns the chosen file path, or ``None``
    if the user cancelled or no dialog could be shown."""
    return _run(_SAVE_SCRIPT, [title, default_name, initial_dir, ext])
