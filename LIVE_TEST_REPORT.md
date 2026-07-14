# Live test report — v3.6.0 (2026-07-14)

v3.6.0 adds **study suites** (Obsidian / Notion / OneNote), **suite Sync**, dual Moodle import (API vs Browser + capability matrix), Panopto RSS discovery, Playwright fallback (`requirements-browser.txt`), and a release ZIP with root `install.bat` + `CourseAssistant/`.

## Automated suite (v3.6.0)

| Run | Result |
|-----|--------|
| `.venv/Scripts/python.exe -m pytest -q` | **528 passed**, 1 skipped |

---

# Live test report — v3.5.0 (2026-07-14)

Server: `.venv/Scripts/python.exe -m uvicorn app.main:app --port 8123` with isolated `PANOPTO_OUTPUT` (`%TEMP%\panopto_live_release_v35`).

## Automated suite

| Run | Result |
|-----|--------|
| `.venv/Scripts/python.exe -m pytest -q` | **515 passed**, 1 skipped |

Note: one jobs reliability test (`test_failed_job_records_category_and_logs`) can flake on timing; re-run passes. Use the project venv for releases.

## Semester planner (live Waikato)

| Test | Result | Notes |
|------|--------|-------|
| POST `/api/courses` + activate | PASS | Fresh output dir requires an active course |
| POST `/api/semester/papers/search` COMPX202, CSMAX270, COMPX225, JAPAN332 | PASS | Live paperoutlines.waikato.ac.nz |
| POST `/api/semester/papers/fetch` COMPX202-26B, CSMAX270-26B | PASS | Titles resolved from live HTML |
| POST `/api/semester/schedule/import` (Notion export zip) | PASS | **43** schedule tasks |
| POST `/api/semester/plan/build` (4 papers + schedule) | PASS | **27** merged tasks |
| GET `/api/semester/plans/{id}/export/notion.csv` | PASS | Non-trivial CSV |
| GET `obsidian.zip` | PASS | **Gantt.md** present (semester + per-paper Compx234 layout) |
| GET `calendar.ics` | PASS | **26 VEVENTs** |
| GET `google-calendar.csv` | PASS | Non-trivial CSV |
| POST `/api/semester/sync-all` | PASS | Outlines refreshed; plan built (no stored calendar URL in test env) |

Schedule zip: `C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip`.

## Moodle calendar

| Test | Result | Notes |
|------|--------|-------|
| GET/PUT `/api/semester/moodle/calendar-url` | PASS via pytest | URL stored in secrets; responses use masked URL only |
| Calendar merge in sync-all | SKIP live | No authtoken URL configured in test output dir |

## Core app (live smoke)

| Endpoint | Result |
|----------|--------|
| GET `/api/status` | PASS |
| GET `/api/library` | PASS |
| GET `/api/export/presets` | PASS |
| GET `/api/jobs` | PASS |
| GET `/api/llm/providers` | PASS |

## Verdict

**Ready for v3.5.0 release.**

---

# Live test report — v3.4.0 (2026-07-13)

Server: `uvicorn app.main:app --port 8123` with project `.venv` and isolated `PANOPTO_OUTPUT`.

## Automated suite

| Run | Result |
|-----|--------|
| `.venv/Scripts/python.exe -m pytest -q` | **506 passed**, 1 skipped |

Note: system Python (no venv) fails 3 optional-dep tests (`markitdown`, `fpdf2`); use the project venv for releases.

## Semester planner (live Waikato)

| Test | Result | Notes |
|------|--------|-------|
| POST `/api/semester/papers/search` COMPX202, CSMAX270, COMPX225, JAPAN332 | PASS | Live paperoutlines.waikato.ac.nz |
| POST `/api/semester/papers/fetch` COMPX202-26B, CSMAX270-26B, COMPX225-26B | PASS | Titles resolved; assessments sparse on live HTML (parser still stores outline) |
| POST `/api/semester/schedule/import` (Downloads outer Notion zip) | PASS | After fix for nested Part-1 zip wrapper |
| POST `/api/semester/plan/build` (3 papers + schedule) | PASS | **17 merged tasks** (schedule subjects vs full paper codes fix) |
| GET exports notion.csv / obsidian.zip / google-calendar.csv | PASS | Non-trivial byte sizes |
| GET `calendar.ics` | PASS | **17 VEVENTs**, CATEGORIES, COLOR, DESCRIPTION present |

Schedule zip: same Downloads path — 43 tasks, subjects COMPX202, COMPX225, CSMAX275, JAPAN332.

## Moodle integration

| Test | Result | Notes |
|------|--------|-------|
| POST `/api/semester/moodle/announcements` without cookies | Expected 400 | Message: paste browser session cookies |
| Moodle API structure | PASS via pytest | `tests/test_moodle_api*.py` in full suite |

**Manual:** announcements + course import with Waikato SSO/token in browser.

## Core app (live smoke)

| Endpoint | Result |
|----------|--------|
| GET `/api/status`, `/api/environment`, `/api/settings` | PASS |
| GET `/api/library` | PASS |
| GET `/api/export/presets` | PASS (6 presets) |
| GET `/api/jobs` | PASS |
| GET `/api/llm/providers` | PASS (ollama/llamacpp local ready) |
| POST `/api/llm/summarize` | PASS (extractive fallback, no keys required) |
| GET `/api/streak` | PASS |

`/api/health` → 404 (not implemented; use `/api/status`).

## Transcription

Job queue listed empty; no live Panopto transcription run (smoke only).

## Fixes applied during live testing (v3.4)

1. **Nested Notion export zip** — outer archive containing only `*-Part-1.zip` now unwraps automatically (`app/schedule_parser.py`).
2. **Plan merge with full paper codes** — `COMPX202-26B` now matches schedule subject `COMPX202` (`app/task_schedule.py`).

- Release 3.6.0: added suites and dual Moodle import support.
