import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8123"
ZIP = Path(r"C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip")
results = []

def record(name, ok, detail=""):
    results.append({"test": name, "ok": ok, "detail": detail[:500]})
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" - {detail[:160]}" if detail else ""))

# Create + activate course
r = requests.post(BASE + "/api/courses", json={"name": "Live Test Semester", "code": "LIVE-TEST", "semester": "B", "year": 2026}, timeout=30)
ok = r.status_code == 200
cid = r.json().get("id") if ok else None
record("POST /api/courses", ok, f"id={cid}")
if cid:
    r2 = requests.post(BASE + f"/api/courses/{cid}/activate", timeout=30)
    record("POST courses/activate", r2.status_code == 200, f"status={r2.status_code}")

for path in ["/api/status", "/api/environment", "/api/settings"]:
    r = requests.get(BASE + path, timeout=30)
    record(f"GET {path}", r.status_code == 200, f"status={r.status_code}")

for q in ["COMPX202", "CSMAX270", "COMPX225", "JAPAN332"]:
    r = requests.post(BASE + "/api/semester/papers/search", json={"query": q, "year": 2026}, timeout=60)
    ok = r.status_code == 200
    n = len(r.json().get("results", [])) if ok else 0
    record(f"papers/search {q}", ok and n > 0, f"results={n}")

codes = []
for q in ["COMPX202-26B", "CSMAX270-26B", "COMPX225-26B"]:
    r = requests.post(BASE + "/api/semester/papers/fetch", json={"paper_code": q}, timeout=120)
    ok = r.status_code == 200
    if ok:
        d = r.json()
        codes.append(d.get("paper_code", q))
        detail = f"assessments={len(d.get('assessments',[]))} weeks={len(d.get('weeks',[]))}"
    else:
        detail = r.text[:200]
    record(f"papers/fetch {q}", ok, detail)

sched_id = None
with ZIP.open("rb") as f:
    r = requests.post(BASE + "/api/semester/schedule/import", files={"file": (ZIP.name, f, "application/zip")}, timeout=120)
ok = r.status_code == 200
if ok:
    d = r.json()
    sched_id = d.get("id")
    detail = f"tasks={d.get('task_count')} subjects={len(d.get('subjects',[]))}"
else:
    detail = r.text[:200]
record("schedule/import zip", ok, detail)

body = {"paper_codes": codes, "name": "Waikato 26B live plan", "class_schedule_id": sched_id}
r = requests.post(BASE + "/api/semester/plan/build", json=body, timeout=180)
ok = r.status_code == 200
plan_id = r.json().get("id") if ok else None
record("plan/build", ok, f"id={plan_id} tasks={r.json().get('task_count') if ok else r.text[:120]}")

if plan_id:
    r = requests.get(BASE + f"/api/semester/plans/{plan_id}", timeout=60)
    record("plan/get", r.status_code == 200, f"timeline={len(r.json().get('timeline',[])) if r.status_code==200 else 0}")
    for exp in ["notion.csv", "obsidian.zip", "calendar.ics", "google-calendar.csv"]:
        r = requests.get(BASE + f"/api/semester/plans/{plan_id}/export/{exp}", timeout=120)
        ok = r.status_code == 200 and len(r.content) > 50
        extra = f"bytes={len(r.content)}"
        if exp == "calendar.ics" and ok:
            text = r.text
            checks = {
                "VEVENT": "BEGIN:VEVENT" in text,
                "CATEGORIES": "CATEGORIES:" in text,
                "COLOR": ("COLOR:" in text or "X-APPLE-CALENDAR-COLOR" in text),
                "DESCRIPTION": "DESCRIPTION:" in text,
            }
            extra += " " + str(checks)
            ok = ok and checks["VEVENT"]
        record(f"export/{exp}", ok, extra)

r = requests.get(BASE + "/api/library", timeout=60)
record("GET /api/library", r.status_code == 200, f"groups={len(r.json().get('groups', r.json().get('items',[]))) if r.status_code==200 else 0}")

r = requests.get(BASE + "/api/export/presets", timeout=30)
record("GET /api/export/presets", r.status_code == 200, f"presets={len(r.json().get('presets',[])) if r.status_code==200 else 0}")

r = requests.get(BASE + "/api/llm/providers", timeout=30)
record("GET /api/llm/providers", r.status_code == 200, r.text[:100])

r = requests.get(BASE + "/api/streak", timeout=30)
record("GET /api/streak", r.status_code == 200, f"status={r.status_code}")

r = requests.get(BASE + "/api/jobs", timeout=30)
record("GET /api/jobs", r.status_code == 200, f"count={len(r.json().get('jobs',[]))}")

# LLM summarize graceful without key
r = requests.post(BASE + "/api/llm/summarize", json={"text": "Hello world test.", "course_id": cid}, timeout=60)
record("POST llm/summarize", r.status_code in (200, 400, 503), f"status={r.status_code} {r.text[:80]}")

r = requests.post(BASE + "/api/semester/moodle/announcements", json={"url": "https://elearn.waikato.ac.nz/course/view.php?id=1", "cookies": ""}, timeout=30)
record("moodle/announcements no cookies", r.status_code in (200, 400), f"status={r.status_code} {r.text[:100]}")

passed = sum(1 for x in results if x["ok"])
print("\nSUMMARY", passed, "/", len(results), "failed", [x["test"] for x in results if not x["ok"]])
Path("_live_test_results.json").write_text(json.dumps(results, indent=2))
