import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8123"
ZIP = Path(r"C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip")
results = []
sched_id = None
plan_id = None

def record(name, ok, detail=""):
    results.append({"test": name, "ok": ok, "detail": detail[:500]})
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" - {detail[:120]}" if detail else ""))

for path in ["/api/health", "/api/status", "/"]:
    try:
        r = requests.get(BASE + path, timeout=30)
        record(f"GET {path}", r.status_code in (200, 307), f"status={r.status_code}")
    except Exception as e:
        record(f"GET {path}", False, str(e))

for q in ["COMPX202", "CSMAX270", "COMPX225"]:
    r = requests.post(BASE + "/api/semester/papers/search", json={"query": q, "year": 2026}, timeout=60)
    ok = r.status_code == 200
    data = r.json() if ok else {}
    n = len(data.get("results", []))
    record(f"POST papers/search {q}", ok and n > 0, f"results={n}")

codes = []
for q in ["COMPX202-26B", "CSMAX270-26B", "COMPX225-26B"]:
    r = requests.post(BASE + "/api/semester/papers/fetch", json={"paper_code": q}, timeout=120)
    ok = r.status_code == 200
    detail = ""
    if ok:
        d = r.json()
        codes.append(d.get("paper_code", q))
        detail = f"title={str(d.get('title',''))[:60]} assessments={len(d.get('assessments',[]))}"
    else:
        detail = r.text[:200]
    record(f"POST papers/fetch {q}", ok, detail)

if not ZIP.exists():
    record("POST schedule/import", False, "zip missing")
else:
    with ZIP.open("rb") as f:
        r = requests.post(BASE + "/api/semester/schedule/import", files={"file": (ZIP.name, f, "application/zip")}, timeout=120)
    ok = r.status_code == 200
    if ok:
        d = r.json()
        sched_id = d.get("id")
        detail = f"id={sched_id} tasks={d.get('task_count')} subjects={d.get('subjects',[])[:8]}"
    else:
        detail = r.text[:200]
    record("POST schedule/import zip", ok, detail)

paper_codes = codes or ["COMPX202-26B", "CSMAX270-26B"]
body = {"paper_codes": paper_codes, "name": "Live test plan"}
if sched_id:
    body["class_schedule_id"] = sched_id
r = requests.post(BASE + "/api/semester/plan/build", json=body, timeout=120)
ok = r.status_code == 200
if ok:
    d = r.json()
    plan_id = d.get("id")
    detail = f"id={plan_id} tasks={d.get('task_count')}"
else:
    detail = r.text[:200]
record("POST plan/build", ok, detail)

if plan_id:
    for exp in ["notion.csv", "obsidian.zip", "calendar.ics", "google-calendar.csv"]:
        r = requests.get(BASE + f"/api/semester/plans/{plan_id}/export/{exp}", timeout=120)
        ok = r.status_code == 200 and len(r.content) > 50
        extra = f"bytes={len(r.content)}"
        if exp == "calendar.ics" and ok:
            text = r.text
            has_vevent = "BEGIN:VEVENT" in text
            has_cat = "CATEGORIES:" in text
            has_color = "COLOR:" in text or "X-APPLE-CALENDAR-COLOR" in text
            has_desc = "DESCRIPTION:" in text
            extra += f" VEVENT={has_vevent} CAT={has_cat} COLOR={has_color} DESC={has_desc}"
            ok = ok and has_vevent
        record(f"GET export/{exp}", ok, extra)

r = requests.get(BASE + "/api/library", timeout=60)
record("GET /api/library", r.status_code == 200, f"status={r.status_code}")

r = requests.get(BASE + "/api/exports/presets", timeout=30)
record("GET /api/exports/presets", r.status_code == 200, f"status={r.status_code}")

for path in ["/api/ai/status", "/api/llm/status", "/api/settings/llm"]:
    r = requests.get(BASE + path, timeout=15)
    if r.status_code != 404:
        record(f"GET {path}", r.status_code == 200, f"status={r.status_code}")

r = requests.get(BASE + "/api/flashcards/decks", timeout=30)
record("GET flashcards/decks", r.status_code in (200, 404), f"status={r.status_code}")

r = requests.get(BASE + "/api/jobs", timeout=30)
record("GET /api/jobs", r.status_code == 200, f"status={r.status_code}")

r = requests.post(BASE + "/api/semester/moodle/announcements", json={"url": "https://elearn.waikato.ac.nz/course/view.php?id=1", "cookies": ""}, timeout=30)
record("POST moodle/announcements (no auth)", r.status_code in (200, 400), f"status={r.status_code} {r.text[:80]}")

passed = sum(1 for x in results if x["ok"])
failed = [x for x in results if not x["ok"]]
print("\nSUMMARY", passed, "/", len(results))
Path("_live_test_results.json").write_text(json.dumps(results, indent=2))
