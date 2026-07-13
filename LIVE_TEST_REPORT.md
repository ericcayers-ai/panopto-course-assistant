# Live test report — 2026-07-13

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

Schedule zip: `C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip` → 43 tasks, subjects COMPX202, COMPX225, CSMAX275, JAPAN332.

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

## Fixes applied during live testing

1. **Nested Notion export zip** — outer archive containing only `*-Part-1.zip` now unwraps automatically (`app/schedule_parser.py`).
2. **Plan merge with full paper codes** — `COMPX202-26B` now matches schedule subject `COMPX202` (`app/task_schedule.py`).

## Verdict

**Ready for v3.4.0 release** after committing semester planner + fixes above.
