import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8123"
OUTER_ZIP = Path(r"C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip")
results = []

def record(name, ok, detail=""):
    results.append({"test": name, "ok": ok, "detail": detail[:500]})
    print(f"{'PASS' if ok else 'FAIL'}: {name} - {detail[:200]}")

r = requests.post(BASE + "/api/courses", json={"name": "Waikato 26B", "code": "26B-LIVE", "semester": "B", "year": 2026}, timeout=30)
cid = r.json().get("id")
requests.post(BASE + f"/api/courses/{cid}/activate", timeout=30)

for q in ["COMPX202", "CSMAX270", "COMPX225", "JAPAN332"]:
    r = requests.post(BASE + "/api/semester/papers/search", json={"query": q, "year": 2026}, timeout=60)
    record(f"search {q}", r.status_code == 200 and len(r.json().get("results", [])) > 0, str(len(r.json().get("results", []))))

codes = []
for q in ["COMPX202-26B", "CSMAX270-26B", "COMPX225-26B"]:
    r = requests.post(BASE + "/api/semester/papers/fetch", json={"paper_code": q}, timeout=120)
    if r.status_code == 200:
        codes.append(r.json().get("paper_code", q))
    record(f"fetch {q}", r.status_code == 200, r.json().get("title", r.text[:120]) if r.status_code==200 else r.text[:120])

with OUTER_ZIP.open("rb") as f:
    r = requests.post(BASE + "/api/semester/schedule/import", files={"file": (OUTER_ZIP.name, f, "application/zip")}, timeout=120)
sched_id = r.json().get("id") if r.status_code == 200 else None
record("import outer notion zip", r.status_code == 200, f"tasks={r.json().get('task_count')} subjects={r.json().get('subjects')}" if r.status_code==200 else r.text[:120])

r = requests.post(BASE + "/api/semester/plan/build", json={"paper_codes": codes, "class_schedule_id": sched_id, "name": "26B merged"}, timeout=180)
plan_id = r.json().get("id") if r.status_code == 200 else None
record("plan build", r.status_code == 200, f"tasks={r.json().get('task_count')}" if r.status_code==200 else r.text[:120])

if plan_id:
    r = requests.get(BASE + f"/api/semester/plans/{plan_id}/export/calendar.ics", timeout=120)
    text = r.text
    vevents = text.count("BEGIN:VEVENT")
    record("ics events", r.status_code == 200 and vevents > 0, f"vevents={vevents} CAT={'CATEGORIES:' in text} COLOR={('COLOR:' in text or 'X-APPLE-CALENDAR-COLOR' in text)} DESC={'DESCRIPTION:' in text}")
    for exp in ["notion.csv", "obsidian.zip", "google-calendar.csv"]:
        r = requests.get(BASE + f"/api/semester/plans/{plan_id}/export/{exp}", timeout=120)
        record(f"export {exp}", r.status_code == 200 and len(r.content) > 50, f"bytes={len(r.content)}")

for ep in ["/api/status", "/api/library", "/api/export/presets", "/api/jobs", "/api/llm/providers", "/api/streak"]:
    r = requests.get(BASE + ep, timeout=30)
    record(ep, r.status_code == 200, f"status={r.status_code}")

r = requests.post(BASE + "/api/llm/summarize", json={"text": "sample", "course_id": cid}, timeout=60)
record("llm summarize fallback", r.status_code == 200, r.text[:80])

r = requests.post(BASE + "/api/semester/moodle/announcements", json={"url": "https://elearn.waikato.ac.nz/course/view.php?id=1", "cookies": ""}, timeout=30)
record("moodle announcements", r.status_code == 400, "needs browser cookies (expected)")

failed = [x for x in results if not x["ok"]]
print("FAILED", failed)
Path("_live_test_results_final.json").write_text(json.dumps(results, indent=2))
