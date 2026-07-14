from pathlib import Path
import re
p = Path("app/task_schedule.py")
text = p.read_text(encoding="utf-8")
old = """    merged: Dict[str, Dict[str, Any]] = {}
    codes = {c.upper() for c in (paper_codes or [])}

    for t in schedule_tasks:
        subject = (t.get("subject") or "").upper()
        if codes and subject and subject not in codes:
            continue"""
new = """    merged: Dict[str, Dict[str, Any]] = {}
    codes = {c.upper() for c in (paper_codes or [])}
    bases = {c.split("-")[0] for c in codes if c}

    for t in schedule_tasks:
        subject = (t.get("subject") or "").upper()
        sub_base = subject.split("-")[0] if subject else ""
        if codes and subject and subject not in codes and sub_base not in bases:
            continue"""
if old not in text:
    raise SystemExit("anchor missing")
p.write_text(text.replace(old, new), encoding="utf-8")
print("patched merge_tasks")
