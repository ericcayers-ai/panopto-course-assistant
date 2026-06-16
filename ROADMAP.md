# Course Assistant — Engineering Roadmap

> Format optimized for AI-agent execution: each phase is `Goal · Depends · Files · API/Schema · Done-when`.
> Grounded in the current codebase (`app/`, `static/`, `tests/`). Read **§0 Baseline** and **§Conventions** before implementing any phase.

---

## Vision

Evolve from a **single-session, single-course, file + in-memory tool** into a **persistent, multi-course, offline-first learning platform** that manages an entire degree from first lecture to final exam.

**Invariants (must hold after every phase):**
- Offline-first — core flow works with no internet, no API key, no GPU.
- Privacy-first — no data leaves the machine unless the user opts into a cloud provider/integration.
- Optional heavy deps — whisper, markitdown, OCR, LLM, OCR stay optional; absence degrades gracefully (see `/api/status`).
- Course-centric — every artifact is scoped to a course.
- Local-fast — sub-second library/search on a typical course; no mandatory background services.
- Single-user local app — no auth/multi-tenant assumptions.
- Test coverage tracks features (currently 160+ tests; never let a phase ship untested).

---

## §0 Baseline (current state — what exists today)

| Module | LoC | Responsibility | Persistence |
| --- | --- | --- | --- |
| `app/core.py` | 1236 | feed parse, organise, writers, full-text search, extractive summary, NotebookLM render, docs→MD | files under `OUTPUT_DIR` |
| `app/sources.py` | 294 | Moodle HTML export parser → outline | — |
| `app/notion.py` | 392 | Notion HTML/zip export → Markdown (stdlib only, **import side only**) | files |
| `app/flashcards.py` | 289 | Anki cards via **heuristics** (definitions/acronyms) + categorise | files (`_flashcards/`) |
| `app/study.py` | 121 | Notion study-DB **CSV export** only (no scheduling) | files (`_exports/`) |
| `app/transcribe.py` | 351 | optional yt-dlp download + whisper engines (lazy import) | files |
| `app/jobs.py` | 157 | **in-memory** job manager, single worker, lost on restart | **none** |
| `app/main.py` | 631 | FastAPI app + ~25 routes | — |
| `static/` | — | vanilla JS SPA (no build step), sidebar, light/dark | browser `localStorage` |
| `tests/` | — | pytest suite (core, sources, notion, docs, flashcards, study, jobs, API) | — |

**Key gaps the roadmap closes:**
1. No database. `jobs.manager` is in-memory; "course" is a free-text string passed per request; user prefs live in `localStorage`. → **§1**
2. No durable index — search re-walks the filesystem. → **§2**
3. Jobs are fire-and-forget — no retry/resume/cancel/persistent logs. → **§3**
4. Flashcards/quizzes are heuristic only; no LLM, no summaries-on-demand, no chat. → **§4**
5. Notion/Anki are export-file only — no live API sync. → **§5**
6. No assessments, calendar, spaced-repetition, or progress tracking. → **§6**
7. Imports limited to Panopto RSS + Moodle + Notion + markitdown docs. → **§7**
8. No local usage signals — can't see which workflows succeed/stall without cloud tracking. → **§13**

---

## Architecture target (module map after roadmap)

```
app/
  database.py        §1  SQLite layer: schema, migrations, connection, DAOs        [NEW]
  models.py          §1  dataclasses / pydantic row models                         [NEW]
  courses.py         §1  course CRUD service                                        [NEW]
  settings_store.py  §1  persistent key/value settings (replaces localStorage-only) [NEW]
  index.py           §2  unified content index + metadata extraction                [NEW]
  search.py          §2  fuzzy + metadata (+ semantic hook) ranking                 [NEW]
  jobs.py            §1/§3  DB-backed queue, retry/resume/cancel, persisted logs    [REWRITE]
  llm.py             §4  provider abstraction (local + cloud), optional             [NEW]
  ai/                §4  summarize.py, flashcards_ai.py, quiz.py, rag.py, synth.py  [NEW]
  integrations/
    notion.py        §5  Notion API write/sync (distinct from app/notion.py import) [NEW]
    anki.py          §5  AnkiConnect sync                                           [NEW]
  study_planner.py   §6  assessments, calendar(.ics), spaced repetition, progress   [NEW]
  secrets.py         §10 OS-keyring-backed secret store                             [NEW]
  analytics.py       §13 local usage stats / funnels (reads DB, no cloud)           [NEW]
  core.py / sources.py / notion.py / flashcards.py / study.py / transcribe.py / main.py  [EXTEND]
```

---

## Dependency graph & build order

```
§1 Persistence + Multi-course  ──────────────┐  (FOUNDATION — nothing else starts cleanly without it)
   ├─► §2 Library & Search (needs DB index)
   ├─► §3 Job Reliability (hardens §1 DB queue)
   ├─► §6 Study Planner (needs assessments/study_sessions tables)
   └─► §8 Frontend course-switcher / dashboard
§2 ──► §4 AI Layer (RAG needs the index)         §10 Security  ─ cross-cutting (secrets gate §4 cloud + §5)
§4 ──► §5 Integrations (sync summaries/cards)    §12 Testing   ─ cross-cutting (every phase ships tests)
§4 ──► §9 Export Engine (AI packs)
§1/§2 ──► §7 Import Expansion (writes to index)
§1..§9 ──► §11 Packaging
§1 ──► §13 Analytics & Local Feedback (reads job/export rows; no cloud)
```

**Recommended order:** §1 → §3 → §2 → §10(secrets) → §4 → §6 → §5 → §7 → §9 → §8 → §11. §12 + §13 continuous.
Rationale: lock the data layer (§1) and make it reliable (§3) before building features on it; stand up the index (§2) before AI/RAG (§4); land secret storage (§10) before any cloud call; planner (§6) is independent of AI so it can run in parallel; packaging (§11) and frontend polish (§8) last. §13 (local analytics) accretes once §1 gives it tables to read.

---

## Conventions (apply to every phase)

- **DB access:** all SQL lives in `app/database.py` + per-entity DAOs. No raw SQL in routes or feature modules. Single `sqlite3` connection per request/thread; `WAL` mode; foreign keys ON.
- **Migrations:** `schema_version` row in `settings`. `database.migrate()` applies ordered, idempotent steps on startup. Never mutate a shipped migration — add a new one. Every migration has a round-trip test (§12).
- **Backward compatibility:** first run with an existing `transcripts/` folder must **import existing files into the DB index** (one-time backfill), not orphan them.
- **Optional deps:** gate every heavy import behind a capability check surfaced in `GET /api/status`; UI disables the feature with a reason string when missing (mirror the existing whisper pattern).
- **Offline default:** any feature that can reach the network defaults to off/local; cloud requires explicit per-course opt-in + a stored secret.
- **API style:** keep the existing thin-JSON-over-`app/main.py` pattern. New resources are RESTful (`/api/<resource>`). Long operations return a job id and stream progress via `/api/jobs`.
- **Tests:** each new module ships a `tests/test_<module>.py`; each new route gets an API test via `fastapi.testclient`. No phase is "done" until its Done-when checklist is green under `pytest -q`.
- **Path safety:** reuse the existing path-traversal guard for every new filesystem path (user-supplied paths in §7 especially).

---

## §1 — Persistence & Multi-Course Foundation

**Status:** ✅ **Shipped.** `database.py`/`models.py`/`courses.py`/`settings_store.py` added, `jobs.py` is DB-backed with restart recovery, course + settings routes live, existing `transcripts/` backfilled on startup, top-bar course switcher wired. 212 tests green (30 new); verified in-browser (create/switch/persist across reload).

**Goal:** Replace transient state (in-memory jobs, string-tag course, localStorage prefs) with durable SQLite; support N concurrent courses with an active-course concept.
**Depends:** — (foundation)
**Files:** `app/database.py` [NEW], `app/models.py` [NEW], `app/courses.py` [NEW], `app/settings_store.py` [NEW], `app/jobs.py` [REWRITE → DB-backed], `app/main.py` [+routes], `static/app.js` [course switcher wiring]. DB file: `OUTPUT_DIR/course_assistant.db`.

**Schema** (SQLite; `*_at` = ISO8601 TEXT; FKs ON DELETE CASCADE unless noted):

```sql
courses(        id PK, name, code, semester, year INT,
                created_at, updated_at, archived INT DEFAULT 0)
documents(      id PK, course_id FK→courses, title, path, type,
                import_source, created_at)
transcripts(    id PK, course_id FK, document_id FK NULL, title, week INT, topic,
                date, path, duration, metadata_json)
jobs(           id PK, type, status, progress REAL, payload_json, result_json,
                error, attempts INT DEFAULT 0, created_at, updated_at)
exports(        id PK, course_id FK, type, path, created_at)
settings(       key PK, value)                         -- incl. schema_version, active_course
assessments(    id PK, course_id FK, name, due_date, weight REAL, status)   -- §6 uses
study_sessions( id PK, course_id FK, started_at, duration, activity_type)   -- §6 uses
```

**API (Course Manager):**
```
GET    /api/courses              list (incl. archived flag)
POST   /api/courses              create {name, code?, semester?, year?}
PATCH  /api/courses/{id}         rename / archive / set fields
DELETE /api/courses/{id}         delete (cascade)
POST   /api/courses/{id}/duplicate
POST   /api/courses/{id}/export  → course archive (defers to §9)
GET/PUT /api/settings            persistent prefs (active_course, theme, export defaults, ai, sync)
```

**Persistent jobs (minimum here; hardened in §3):** survive restart; on startup, mark `running`→`interrupted` and offer resume; expose historical job list with logs.
**Persistent preferences:** migrate `localStorage` keys → `settings` table on first load (active course, theme, export defaults, AI settings, sync settings).

**Done-when:**
- DB created + migrated on startup; existing `transcripts/` backfilled into `documents`/`transcripts`.
- Full course CRUD + duplicate + archive + active-course switch, all persisted.
- Restarting the server preserves jobs, prefs, and the active course.
- All existing tests pass + new `tests/test_database.py`, `tests/test_courses.py`, migration round-trip test.

---

## §2 — Library & Search System

**Status:** ✅ **Shipped (on-demand index).** `search.py` flattens the library into an index with inferred week/topic/type/tags/mtime; `/api/index` does filter (week/type/tag) + sort (date/name/week); `/api/search` adds metadata filters + a fuzzy title fallback; `/api/related` surfaces same-week/topic/type; saved views (7 built-ins + DB-persisted user views). Library tab gained a filter/sort bar. (Durable DB-index reindex + semantic/embedding search remain for a later pass.)

**Goal:** Turn file storage into a searchable knowledge base with a durable index, auto metadata, and saved/related views.
**Depends:** §1.
**Files:** `app/index.py` [NEW], `app/search.py` [NEW], `app/core.py` [reuse listing/summary], `app/main.py` [+routes], `static/app.js` [filters/views].

**Index:** transcripts, converted documents, Moodle content, Notion content, exports → rows keyed by course. Rebuildable from disk; incremental on import.
**Metadata extraction** (auto-infer, reuse `core.infer_week/infer_topic`): `course, week, topic, lecture, module, assessment, date, tags`.
**Entity extraction:** detect topics, module names, assessments, dates, and key terms from content; store as `tags`/metadata to drive facets and related-content (lexical/regex now; LLM-assisted when §4 present).
**Search:** fuzzy + metadata filters + relevance ranking + snippets. Leave a **semantic-search hook** (embeddings) wired to §4's provider but disabled when no provider configured.
**Saved views** (queries persisted in `settings`/new table): `Unread, Pending Transcription, Needs Flashcards, Exam Revision, Assignments, Recent Imports`.
**Related content:** surface `similar lectures / same topic / same week / same assessment` (lexical now; embeddings when §4 present).
**Smart collections:** one view that bundles every asset for a lecture/week (transcript + documents + exports + flashcards + quizzes + assessments), so navigating by lecture surfaces all linked artifacts.

**API:**
```
GET  /api/library?course=&filter=&sort=     categorised, indexed listing
GET  /api/search?q=&course=&type=&week=     ranked results + snippets
GET  /api/views                              saved views
POST /api/views                              create/update a saved view
GET  /api/related?id=                        related content for an item
GET  /api/collections?course=&lecture=       all assets linked to a lecture/week
```

**Done-when:** index survives restart and updates on import; fuzzy + metadata search returns ranked snippets sub-second on the sample course; ≥6 saved views work; related-content + smart-collection return plausible results; `tests/test_index.py`, `tests/test_search.py` green.

---

## §3 — Reliability & Job Infrastructure

**Status:** ✅ **Shipped (core).** Cooperative cancel, retry-from-payload (job-factory registry), classified failures (`network|authentication|dependency|filesystem|invalid_source|unknown`), persisted per-job logs, dead-letter via `status='error'` listing; Jobs panel gained Cancel/Retry/Logs controls. (Pause/duplicate + backoff scheduler remain for a later pass.)

**Goal:** Make long-running operations (transcription, conversion, AI) resilient and controllable.
**Depends:** §1 (DB queue).
**Files:** `app/jobs.py` [extend], `app/main.py` [+controls].

- **Retry:** automatic (exponential backoff, capped) + manual retry; `attempts` tracked.
- **Controls:** `pause / resume / cancel / restart / duplicate` per job.
- **Failure categories:** `network | authentication | dependency | filesystem | invalid_source | unknown` (classify on error; drive UI hints + retry policy).
- **Persisted logs:** stdout, stderr, stack traces, timestamps per job (table or per-job log file referenced by row). UI: `show logs` + `copy error` controls (§8).
- **Dead-letter queue:** jobs that exhaust retries land in a `failed`/dead-letter state, retained with full logs for inspection and manual re-queue (never silently dropped).
- **Resumable work / partial completion:** preserve partial output and resume downloads/transcriptions where the engine allows (skip-existing already does some of this); a cancelled or interrupted job keeps what it produced rather than discarding it.
- **Graceful shutdown:** on server stop, stop accepting new work, checkpoint in-flight jobs to `interrupted`, and flush logs before exit.
- **Resource protection:** honor/extend existing `PANOPTO_WORKERS` / `PANOPTO_NICE` / `PANOPTO_CPU_THREADS`; add memory/thread/GPU/worker limits; never let a feed exhaust RAM/VRAM (preserve current single-in-flight default).

**API:**
```
POST /api/jobs/{id}/retry|cancel|pause|resume|restart|duplicate
GET  /api/jobs/{id}/logs
GET  /api/jobs?status=dead_letter            inspect unrecoverable jobs
```

**Done-when:** killing/restarting mid-job recovers to a known state; cancel actually stops work and preserves partial output; retried jobs increment `attempts`; exhausted jobs reach the dead-letter state and are re-queueable; failure category shown; logs persisted and retrievable; `tests/test_jobs_*` cover lifecycle + each control + each failure category + dead-letter.

---

## §4 — AI / LLM Layer (Optional)

**Status:** ✅ **Shipped (backend).** `llm.py` provider abstraction (ollama, llama.cpp/LM-Studio, openai, anthropic; default cloud model `claude-opus-4-8`); import never touches the network; per-course AI config in settings with API keys redacted from all responses (keyring is §10). `ai.py` ships summarise / flashcards / quiz / RAG-chat — each with a dependency-free extractive fallback so features never vanish when AI is off; outputs labelled `generated: ai|extractive`. Routes under `/api/llm/*`; `/api/status.ai` reports detected providers. (Chat/settings UI lands in the §8 frontend pass; topic-synthesis/outline-cleanup later.)

**Goal:** Advanced AI features behind a provider abstraction, with zero impact on offline-first when disabled.
**Depends:** §2 (index for RAG), §10 (secrets for cloud keys).
**Files:** `app/llm.py` [NEW provider abstraction], `app/ai/summarize.py`, `app/ai/flashcards_ai.py`, `app/ai/quiz.py`, `app/ai/rag.py`, `app/ai/synth.py` [NEW]; `app/flashcards.py` [keep heuristic path as fallback].

**Providers** (uniform interface: `complete()`, `embed()`, `stream()`):
- Local: Ollama, LM Studio, llama.cpp.
- Cloud: OpenAI, Anthropic, Google Gemini, OpenRouter.
- Default Anthropic model for cloud: **`claude-opus-4-8`** (configurable). All cloud usage opt-in + keyed via §10.

**Features:**
- **Summarization:** lecture / weekly / topic / course (on-demand; cache to `documents`/`exports`).
- **Flashcards (AI):** replace/augment heuristics → `Q&A | Cloze | Definition | Concept | Why-How`. Heuristic path remains when no provider.
- **Quiz generation:** `MCQ | ShortAnswer | Cloze | TrueFalse | Matching`; difficulty `Easy | Medium | Hard | Adaptive`.
- **RAG Chat ("Chat with Course"):** retrieve over transcripts/documents/Moodle/Notion/exports; **require citations + source snippets + confidence indicator**.
- **Topic synthesis:** compare lectures, recurring concepts, study guides, prerequisite chains.
- **Outline cleanup:** reorganise messy source material (auto-transcripts, scraped notes) into a cleaner topic hierarchy; output is a suggested re-org the user can accept.
- **AI study planning:** read revision history (§6 `study_sessions`/progress) to explain weak areas and suggest next review topics (advisory layer over §6's deterministic scheduler).
- **Exam prep:** likely topics, revision summaries, practice exams.

**AI settings (per-course, in `settings`/course config):** `provider, model, temperature, max_tokens, retrieval_depth`.
**Controls & safety:**
- **Source toggles** per request: transcripts / documents / Moodle / Notion / combined pack — user chooses what context is sent.
- **Prompt templates:** stored, editable, per-feature defaults (so users can tune summarise/quiz/flashcard prompts).
- **Token & usage controls:** show/limit tokens per call; surface estimated usage before a cloud call runs.
- **Metadata redaction:** optional stripping of sensitive metadata (paths, names) before any cloud send.
- **Provenance labels:** every output is tagged `AI-generated` vs `extracted` so users never confuse synthesis with source text (pairs with §10 transparency labels).

**API:**
```
GET  /api/status                     +AI providers detected/configured
GET  /api/ai/prompts                  list/get editable prompt templates
PUT  /api/ai/prompts/{key}            edit a prompt template
POST /api/ai/summarize  {scope, id, course, sources[]}
POST /api/ai/flashcards {selection, types[], course, sources[]}
POST /api/ai/quiz       {scope, types[], difficulty, course, sources[]}
POST /api/ai/chat       {course, query, history?, sources[]}   → answer + citations
POST /api/ai/synthesize {scope, mode, course, sources[]}
POST /api/ai/cleanup    {scope, course}                        → suggested outline re-org
```

**Done-when:** with no provider configured, app behaves exactly as today (heuristic flashcards, no chat UI); with a local provider, summarize/flashcards/quiz/chat work fully offline; RAG answers carry citations; outputs labeled generated-vs-extracted; source toggles + prompt templates persist per course; cloud calls show a usage estimate first; per-course AI config persists; `tests/test_llm.py` (mocked providers) + feature tests green.

---

## §5 — Integrations (write/sync side)

**Status:** ✅ **Shipped (core sync).** `integrations/notion.py` (live API: create DB/page/update, incremental + title-dedup, editable field map, dry-run) and `integrations/anki.py` (AnkiConnect: auto-create deck, tagged notes, duplicate-aware, dry-run) both ship with injectable transports so the planning/dedup logic is fully tested offline. `integrations/state.py` persists connection config + last-sync timestamps under the `sync` settings key (tokens never echoed). Routes: `/api/sync/{notion,anki}` + `/dryrun`, `/api/sync/status`, `PUT /api/sync/mapping`. Anki cards are sourced from the §4 flashcard generator. (Conflict-resolution UI + a dedicated retry queue beyond §3's job infra remain for a later pass; token storage moves to the keyring in §10.)

**Goal:** Remove manual export/import round-trips via live API sync. (Distinct from existing import-side `app/notion.py`.)
**Depends:** §1, §4 (sync AI summaries/cards), §10 (tokens).
**Files:** `app/integrations/notion.py` [NEW], `app/integrations/anki.py` [NEW].

**Notion (official API):** database creation, page creation/updates, **incremental sync**, duplicate detection, **editable field mapping** (local course structure → Notion properties). Sync: lecture summaries, metadata, course structure, assignments, study status.
**Anki (AnkiConnect):** deck creation, card creation, tag syncing (course/week/topic/difficulty), incremental updates, duplicate detection; syncs generated cards, edited cards, and imported decks.
**Sync UX (shared):** `Sync now` buttons in Export; connection-status indicator; last-sync timestamp; **dry-run preview** (show what would change before writing); **conflict handling** (local vs remote edits); a **retry queue** for sync failures (reuses §3 job infra).
**Future (stub interfaces only):** Obsidian (Markdown vault conventions), Google Calendar / iCal, OneNote, CSV/TSV spreadsheet pipelines.

**API:**
```
POST /api/sync/notion        {course, scope}      incremental
POST /api/sync/notion/dryrun {course, scope}      preview diff, write nothing
POST /api/sync/anki          {course, deck, scope}
POST /api/sync/anki/dryrun   {course, deck, scope}
GET  /api/sync/status?course=                     connection + last-sync time
PUT  /api/sync/mapping       {target, fields}      editable field mapping
```

**Done-when:** re-running a sync updates only changed items (no duplicates); dry-run lists the exact diff and writes nothing; conflicts are surfaced not silently overwritten; tokens stored via §10; failures classified (§3), queued, and retryable; connection status + last-sync time shown; `tests/test_integrations_notion.py` / `_anki.py` against mocked endpoints green.

---

## §6 — Study Planner

**Goal:** Turn content into actionable, scheduled study workflows.
**Depends:** §1 (`assessments`, `study_sessions` tables).
**Files:** `app/study_planner.py` [NEW]; `app/study.py` [keep CSV export, reuse].

- **Assessment tracker:** `assignments | quizzes | tests | exams | projects`; fields `due_date, weight, status (not_started|in_progress|submitted|graded), progress`.
- **Calendar generation:** `.ics` / iCal / Google Calendar export (lecture schedule, assignment deadlines, exam dates, revision reminders).
- **Revision scheduler:** spaced repetition + assessment deadlines + lecture dates → daily / weekly / exam study plans; honors a user-set **available study-hours-per-week** budget and generates **catch-up plans** for missed lectures.
- **Progress tracking:** completion %, review status, study hours, streaks, mastery scores (feeds §2 views, §8 dashboard).
- **Self-testing:** practice quizzes / mock exams / topic drills, with timed / topic / weak-area modes (uses §4 when present; heuristic fallback otherwise); attempts recorded.

**Data model additions (extend §1):** `review_items` (spaced-repetition cards: item ref, interval, due, ease/overdue), `quiz_attempts` (quiz ref, score, taken_at, mode), plus `mastery`/progress columns derived from `study_sessions` + `quiz_attempts`.

**API:**
```
GET/POST/PATCH/DELETE /api/assessments
GET  /api/plan?course=&horizon=&hours=  generated study plan (hours = weekly budget)
GET  /api/calendar.ics?course=
POST /api/study-sessions                log a session
GET  /api/reviews?course=&due=          due/overdue spaced-repetition items
POST /api/quiz-attempts                 record a self-test attempt
GET  /api/progress?course=
```

**Done-when:** assessments CRUD persisted with the 4 status states; `.ics` validates and imports into a real calendar app; spaced-repetition schedule is deterministic + tested; weekly-hours budget and missed-lecture catch-up reflected in the plan; progress/mastery metrics computed from `study_sessions` + `quiz_attempts`; `tests/test_study_planner.py` green.

---

## §7 — Import Expansion

**Goal:** Ingest more real-world sources into the same index.
**Depends:** §1/§2 (write to index); reuses `app/sources.py`, `app/transcribe.py`, `app/core.py` docs→MD.
**Files:** extend `app/transcribe.py` (video sources), `app/core.py` (doc types), `app/sources.py` (LMS), + `app/imports/` helpers as needed.

- **Video:** YouTube playlists/channels, Vimeo, Media RSS (via yt-dlp, already a dep). **Capture existing subtitles** where available (skip transcription when the source already has captions).
- **Documents:** PDF, DOCX, PPTX, XLSX, HTML, EPUB, Markdown, CSV, TXT (markitdown; broaden coverage).
- **OCR (optional):** Tesseract / PaddleOCR for scanned PDFs, slides, images — gated in `/api/status`; page-level fallback when text extraction yields nothing.
- **LMS:** expand `sources.py` beyond Moodle (support more export shapes + nested resource folders) → Canvas, Blackboard, Brightspace.
- **Folder imports:** recursive scan, mixed-content-type detection, structure-preserving auto-categorization, bulk import.
- **Preflight validation:** before an import runs, warn on huge files, missing engines/deps, and show the **expected output** (counts, target folders) so the user confirms before work starts.

**Done-when:** each new source type imports into the index with correct metadata; subtitles reused when present; OCR absent → feature disabled with reason; bulk folder import categorizes into week/lecture/module; preflight surfaces size/dependency warnings + expected output before starting; `tests/test_sources.py`/`test_docs.py` extended with synthetic fixtures per source.

---

## §8 — Frontend Modernization

**Goal:** Better usability with **no frontend framework / no build step** (preserve vanilla SPA).
**Depends:** §1 (course switcher), §2 (filters), §3 (job stages), §6 (dashboard metrics).
**Files:** `static/index.html`, `static/app.js`, `static/style.css`.

- **Navigation:** course switcher, breadcrumbs, quick actions, dashboard (course summary: current week, next deadline, assessment countdown, study-progress snapshot).
- **Library:** always-visible search bar, filter chips, sort menu, multi-select + batch actions/exports, **drag-and-drop import zones**, file-type badges, inline preview panes of transcript/document excerpts.
- **Feedback:** toast notifications for background actions, better empty states, richer **confirmation dialogs for destructive actions** (delete/archive/clear).
- **Accessibility:** keyboard nav throughout, ARIA labels, visible/consistent focus states, screen-reader support.
- **Progress:** determinate bars with stage breakdown, job stages, ETA (from §3 data).
- **Mobile:** responsive layouts, tablet support, read-only / light-editing mobile workflow; dark/light theme refinement.

**Done-when:** course switch is one click and reflows the whole UI; library batch actions + drag-drop import work; destructive actions require confirmation and emit a toast; keyboard-only navigation reaches every action; passes an automated a11y check (axe); layout usable at tablet/phone widths; frontend smoke tests + a11y check in §12.

---

## §9 — Export Engine

**Goal:** Exports become reusable, scoped, preset-driven workflows.
**Depends:** §1 (scope = multi-course), §4 (AI packs). Builds on existing NotebookLM/Anki/Notion-CSV/formats exporters.
**Files:** `app/core.py` exporters [extend], `app/study.py`, `app/flashcards.py` [reuse]; consider `app/exports.py` aggregator.

- **Targets:** NotebookLM, Anki, Notion, Markdown, TXT, JSON, CSV, SRT, VTT, ICS, Quiz Packs, AI Packs.
- **Presets:** `Revision | AI | Exam | Notion | Anki | Archive`.
- **Scope controls:** single lecture | week | topic | course | all courses.
- **Refinements:** export **preview** (show artifacts before writing), **diff-based re-export** (only regenerate changed items), include/exclude toggles per source type, formatting/metadata templates, per-course configurable naming conventions.
- **Course archive:** portable package = course metadata + documents + transcripts + exports + settings (round-trips with §11 backup/restore and §1 import).

**API:**
```
POST /api/export         {target|preset, scope, course, include[], exclude[]}  → job
POST /api/export/preview {target|preset, scope, course}    list artifacts, write nothing
GET  /api/export/{id}
```
**Done-when:** every preset produces correct artifacts at every scope; preview lists outputs without writing; re-export only touches changed items; naming conventions applied per course; course archive exports and re-imports losslessly into a fresh DB; `tests/test_exports.py` covers preset × scope matrix.

---

## §10 — Security & Privacy (cross-cutting)

**Goal:** Preserve trust as AI + integrations add network surface. Land **before** any cloud call (§4) or sync (§5).
**Files:** `app/secrets.py` [NEW], plus guards across modules.

- **Secrets:** store API keys / cookies / tokens via OS keyring (Windows Credential Manager / macOS Keychain / libsecret); never in plaintext config or DB. Fallback = encrypted file with clear warning.
- **Data transparency:** every action labeled `local-only | local+internet | cloud-processed` in the UI before it runs; a **privacy panel** explains where each feature's data goes.
- **Hardening:** path-traversal protection (reuse existing guard on all new paths), input validation, safe/sandboxed file writes, dependency isolation; **dependency & update integrity checks**.
- **Audit log:** record external sync/cloud actions (what, when, where to) so the user can review what left the machine.
- **Retention:** explicit, documented retention policy for caches and temp files; auto-cleanup honors it.
- **User controls:** clear cache, clear sync history, clear AI history, remove credentials.

**Done-when:** no secret ever written in plaintext (test asserts DB/config contain no key material); every networked action carries a transparency label and is recorded in the audit log; `clear *` controls verifiably wipe their data; cache/temp retention enforced; path-traversal tests cover all new endpoints.

---

## §11 — Packaging & Distribution

**Goal:** Non-technical, double-click deployment.
**Depends:** §1–§9 stable.
**Files:** PyInstaller spec(s), installer scripts, extend existing `start-*`/`install-extras-*`.

- **Build:** PyInstaller for Windows / macOS / Linux; bundled one-click launchers; improved auto-port selection (extends `run.py`).
- **Install modes:** optional **lite** (core only) vs **full** (whisper/OCR/markitdown) install paths; guided dependency installer + **environment checker** that reports what's present/missing.
- **Installer features:** first-run wizard, dependency detection, auto-updates with **changelog summaries**, portable mode (all state under one folder), backup/restore (pairs with §9 archive), migration tools (pairs with §1 migrations), **one-click recovery/reset** for broken config, and a **"migrate to a new computer"** package (settings + DB + library export/import).

**Done-when:** clean machine (no Python) runs the bundled app; first-run wizard sets output dir + optional add-ons; lite/full modes install the right deps; environment checker flags missing engines; portable mode keeps all state under one folder; backup/restore and migrate-to-new-computer round-trip; one-click reset recovers a broken config; upgrade migrates the DB without data loss and shows a changelog.

---

## §12 — Testing & Quality (cross-cutting, continuous)

**Goal:** Reliability scales with complexity. Every phase contributes tests; this section is the standing checklist.

- **Backend:** database, jobs, imports, exports, sync, AI (mocked), planner, search, analytics (local-only, no network).
- **API:** contract tests, schema validation, error handling (per route).
- **Frontend:** critical-workflow smoke tests, accessibility (axe) checks.
- **Security:** path traversal, credential handling, input sanitization.
- **Migration:** DB upgrades, version compatibility, backup restoration.

**Done-when (gate for the whole roadmap):** `pytest -q` green; new modules each have a test file; AI/integration tests run without network (mocks); migration round-trip + backup-restore tests exist.

---

## §13 — Analytics & Local Feedback (cross-cutting, **no cloud tracking**)

**Goal:** Understand which workflows succeed or stall — entirely from local data, never phoning home. This is privacy-first telemetry: nothing leaves the machine unless the user explicitly exports it.
**Depends:** §1 (reads `jobs`, `exports`, `study_sessions` rows; optional `events` table).
**Files:** `app/analytics.py` [NEW]; reads existing tables, writes an optional local `events` table.

- **Local usage stats:** counts per feature (imports, transcriptions, exports, syncs, AI calls) — derived from existing rows, no new tracking required.
- **Completion funnels:** import→transcribe→export drop-off; where users abandon a workflow.
- **Failure insights:** failed-job counts by §3 category; surfaces recurring pain points.
- **Throughput:** time-to-process metrics (job duration percentiles), export frequency by type.
- **Feedback prompt:** after repeated failures of the same kind, offer a user-facing prompt (purely local; no submission).
- **Optional diagnostics export:** a single anonymised local JSON the user can choose to share when reporting a bug — explicit, manual, never automatic.

**API:**
```
GET  /api/analytics?course=        local usage stats + funnels + failure insights
POST /api/analytics/export         write an anonymised diagnostics JSON (user-initiated)
```

**Invariants:** off-by-default network posture preserved — analytics are computed and stored locally; the only egress path is a user-initiated diagnostics export. No third-party analytics SDK.
**Done-when:** stats/funnels/failure-insights computed from local rows; diagnostics export is opt-in and contains no secrets/PII (asserted by test); no network call exists in `analytics.py`; `tests/test_analytics.py` green.

---

## Long-term flow (end state)

```
Course material → Import → Transcription/Conversion → Persistent Knowledge Base
  → Search + AI + Planning → Flashcards + Quizzes + Revision
  → Notion / Anki / Calendar sync → Long-term learning archive
```

**End state:** multi-course · persistent · offline-first · optional AI · integrated study planning + revision · direct sync with learning tools · deployable by non-technical students · capable of managing an entire degree from first lecture to final exam.
