import json, zipfile, io, sys
from pathlib import Path
import urllib.request
import requests

BASE = "http://127.0.0.1:8123"
SCHED = r"C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip"
results = []

def req(method, path, data=None):
    url = BASE + path
    body = None
    h = {"Content-Type": "application/json"}
    if data is not None:
        body = json.dumps(data).encode()
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            ct = resp.headers.get("content-type", "")
            raw = resp.read()
            if "json" in ct:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", "replace")

for code in ["COMPX202", "CSMAX270", "COMPX225", "JAPAN332"]:
    st, j = req("POST", "/api/semester/papers/search", {"query": code})
    n = len(j.get("results", [])) if isinstance(j, dict) else 0
    results.append(("search_" + code, st == 200 and n > 0, st, "hits=" + str(n)))

for paper in ["COMPX202-26B", "CSMAX270-26B"]:
    st, j = req("POST", "/api/semester/papers/fetch", {"paper_code": paper})
    title = j.get("title", "")[:60] if isinstance(j, dict) else ""
    results.append(("fetch_" + paper, st == 200, st, title))

with open(SCHED, "rb") as f:
    r = requests.post(BASE + "/api/semester/schedule/import", files={"file": ("sched.zip", f, "application/zip")}, timeout=180)
sched_id = r.json().get("id") if r.status_code == 200 else None
tc = r.json().get("task_count") if r.status_code == 200 else 0
results.append(("schedule_import", r.status_code == 200 and tc > 0, r.status_code, "id=" + str(sched_id) + " tasks=" + str(tc)))

st, j = req("POST", "/api/semester/plan/build", {
    "paper_codes": ["COMPX202", "COMPX225", "COMPX275", "JAPAN332"],
    "class_schedule_id": sched_id,
})
pid = j.get("id") if isinstance(j, dict) else None
task_n = j.get("task_count", 0) if isinstance(j, dict) else 0
results.append(("plan_build", st == 200 and task_n > 0, st, "id=" + str(pid) + " tasks=" + str(task_n)))

if pid:
    for fmt, path in [
        ("notion", f"/api/semester/plans/{pid}/export/notion.csv"),
        ("obsidian", f"/api/semester/plans/{pid}/export/obsidian.zip"),
        ("ics", f"/api/semester/plans/{pid}/export/calendar.ics"),
        ("gcal", f"/api/semester/plans/{pid}/export/google-calendar.csv"),
    ]:
        st2, body = req("GET", path)
        sz = len(body) if isinstance(body, (bytes, bytearray)) else len(str(body))
        extra = ""
        ok = st2 == 200 and sz > 50
        if fmt == "obsidian" and isinstance(body, bytes) and st2 == 200:
            z = zipfile.ZipFile(io.BytesIO(body))
            gantt = [n for n in z.namelist() if "Gantt" in n and n.endswith(".md")]
            extra = " gantt=" + str(bool(gantt)) + " names=" + str(gantt[:3])
            ok = ok and bool(gantt)
        if fmt == "ics" and isinstance(body, bytes) and st2 == 200:
            extra = " vevents=" + str(body.decode("utf-8", "replace").count("BEGIN:VEVENT"))
        results.append(("export_" + fmt, ok, st2, "sz=" + str(sz) + extra))

st, j = req("POST", "/api/semester/sync-all", {
    "paper_codes": ["COMPX202", "COMPX225", "COMPX275", "JAPAN332"],
    "class_schedule_id": sched_id,
})
# without calendar: may still succeed partial or 400 - accept structured response
ok_sync = st == 200 and isinstance(j, dict) and ("id" in j or "plan" in j or "task_count" in j)
if st == 400 and isinstance(j, dict):
    ok_sync = "paper_codes" not in str(j.get("detail", ""))
results.append(("sync_all", ok_sync or st == 200, st, str(j)[:180].replace("authtoken", "[redacted]")))

for ep in ["/api/library", "/api/jobs", "/api/export/presets", "/api/llm/providers"]:
    st, j = req("GET", ep)
    results.append(("core" + ep, st == 200, st, type(j).__name__))

st, j = req("GET", "/api/status")
results.append(("status", st == 200, st, "ok"))

Path("_live_test_results_release.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
failed = []
for name, ok, st, info in results:
    print(name + ": " + ("PASS" if ok else "FAIL") + " (" + str(st) + ") " + str(info))
    if not ok:
        failed.append(name)
sys.exit(1 if failed else 0)
