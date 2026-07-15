# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app (auto-selects a free port starting at 8000)
python run.py

# Dev server on a fixed port (for the preview panel - uses .claude/launch.json)
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8123

# Install core deps
pip install -r requirements.txt

# Install dev deps (needed for tests)
pip install -r requirements.txt -r requirements-dev.txt

# Install optional transcription + document-conversion stack
pip install -r requirements-transcribe.txt

# Run all tests
python -m pytest -q

# Run a single test file
python -m pytest tests/test_core.py -q

# Run a single test by name
python -m pytest tests/test_moodle_api.py::test_fetch_token_sso_rejection -q
```

The test suite uses `PANOPTO_OUTPUT` pointed at a temp dir before importing `app.main`, so tests never touch `./transcripts`. Always set that env var before importing the app module in any new test file.

## Architecture

### Request flow

```
Browser (static/index.html + app.js)
  ↓  JSON API
app/main.py  (FastAPI routes, Pydantic request models)
  ↓  calls
app/{core,transcribe,flashcards,study,exports,…}.py  (domain logic)
  ↓  reads/writes
transcripts/   (file-based library - OUTPUT_DIR)
transcripts/course_assistant.db   (SQLite - all persistent state)
```

`app/main.py` is the only file with HTTP concerns. All business logic lives in the modules it imports; routes are thin.

### Key modules

| Module | Responsibility |
|---|---|
| `core.py` | Feed parsing, `LectureItem`, `write_outputs`, `list_transcripts`, `list_library`, all exporters (NotebookLM, all-sources, subtitles), `split_sentences`, `summarize_text`. No heavy deps - stdlib + `requests` only. |
| `database.py` | SQLite DAO. Single `RLock`-guarded connection. Schema migrations are append-only numbered steps in `_MIGRATIONS` - never edit a shipped migration. |
| `jobs.py` | `JobManager` - thread pool, DB-backed state (`queued → running → done/failed/interrupted`). `manager.bind(db)` wires persistence. Without a bound DB it runs in-memory (used in unit tests). |
| `transcribe.py` | Compatibility facade over adaptive STT. Lazy-imports; app starts without STT extras. |
| `stt/` | Adaptive offline STT: types, registry/router, engines, captions/chunk/checkpoint, workers, enrichment. |
| `routers/stt.py` | `/api/stt/*` capabilities, route, models, live WebSocket. |
| `exports.py` | Preset-driven export engine (intent × scope). Wraps the raw exporters in `core.py`. |
| `flashcards.py` | Anki card extraction. Uses `core.split_sentences()` (not raw regex). |
| `secrets.py` | Key/token storage: OS keyring → encrypted file → plaintext fallback. Never store secrets in the DB. |
| `imports/moodle_api.py` | Moodle web-service API client. All HTTP is injectable (`http_post`/`http_get`) for offline testing. `build_course_model()` is pure - no I/O - so it can be unit-tested exhaustively. |
| `settings_store.py` | JSON-encoded key/value preferences in the `settings` DB table. |

### Output directory layout

Everything lands under `OUTPUT_DIR` (`./transcripts` by default, overridden by `PANOPTO_OUTPUT`):

```
transcripts/
  course_assistant.db          # SQLite - all courses, jobs, settings, audit log
  .secrets.json / .secrets.key # encrypted secrets store (dotfiles - excluded from library listing)
  <week-folder>/
    <lecture-safe-title>.txt   # clean transcript text
    <lecture-safe-title>.md    # markdown with study summary
    <lecture-safe-title>.json  # rich segment data (source of truth)
    <lecture-safe-title>.summary.md
  _docs/                       # converted documents
  _notebooklm/                 # NotebookLM export outputs
  _flashcards/                 # Anki export outputs
  <course>_outline.md          # Moodle course outline
```

`_is_internal()` and `_is_transcript_group()` in `core.py` are the canonical gatekeepers for what counts as a transcript vs document vs "other" in library listings and exports. Dotfiles are excluded.

### Frontend

Single-page app: `static/index.html` + `static/app.js` + `static/style.css`. No build step, no framework, no bundler. The `api()` / `postJSON()` helpers in `app.js` are the only HTTP layer. `StaticFiles` is served with `no-cache` headers so browsers always revalidate after updates.

Mode/window state (Simple vs Advanced, Moodle vs Full) is stored in `localStorage` and `document.body.dataset.level`. `[data-adv-only]` elements are hidden in Simple mode via CSS.

### Moodle SSO / token flow

Three auth paths, in priority order:

1. **Username + password** → `login/token.php` (disabled on SSO-only institutions)
2. **Paste a token** → from `user/managetoken.php` (Moodle Mobile Web Service section)
3. **Browser SSO launch** → `admin/tool/mobile/launch.php` → user copies `moodlemobile://token=…` URL → `POST /api/moodle/decode-launch-token` decodes the base64 payload

`SSO_REJECTED:` prefix on a `MoodleApiError` signals the frontend to auto-switch to the token tab.

### DB schema notes

- `courses` → `transcripts` / `documents` / `assessments` / `review_items` / `quiz_attempts` with `ON DELETE CASCADE`
- `jobs` table persists the job queue; crashed jobs are marked `interrupted` on next startup
- `settings` table holds everything from `settings_store.py` + `schema_version`
- `audit_log` is append-only

### Testing patterns

- **Moodle API**: inject `http_post`/`http_get` callables; `build_course_model` is pure and needs no HTTP mock
- **API endpoints**: `TestClient` from `fastapi.testclient`; always set `PANOPTO_OUTPUT` to a `tmp_path` before importing `app.main`
- **Export validity**: seed with `_seed_lecture()`, call exporters directly, assert on file contents
- `_is_transcript_group()` is the canonical truth for what the library and all exporters treat as a real transcript - keep them consistent
