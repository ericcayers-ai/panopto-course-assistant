# Course Assistant

A **local-first web app** that turns university course material into study
resources. Point it at a [Panopto](https://www.panopto.com/) lecture feed and/or
your course documents, and it transcribes, converts, organises, and **exports
clean sources** for [NotebookLM](https://notebooklm.google.com/),
[Anki](https://apps.ankiweb.net/), [Notion](https://notion.so/), Obsidian, and
related workflows. Built around University of Waikato courses but
**course-agnostic**.

A **Python / FastAPI** backend with a plain **HTML + JavaScript** frontend (no
build step), light/dark themes, and everything under your control on
`127.0.0.1` — **no lecture audio leaves your machine**.

Current release: **v4.2.0**.

## What's new in v4.2.0

**Navigation clarity and QOL polish.** Sidebar regrouped into Get material →
Work with it → System. Home leads with Moodle / Speech / Library CTAs, a Continue
strip from next-up, Speech in the signal path, and a collapsible Environment
drawer. Wayfinding adds hash routes, last-tab restore, Commands palette launcher,
and `Course › Section` context. Visual polish keeps Signal & Instrument while
quieting card chrome and tightening motion / sticky panel heads.

## What's new in v4.1.0

**Navigation, courses, Speech home, one-click Windows EXE.** Simple mode hides
Semester and advanced STT/export chrome. Home pipeline starts with Moodle.
Active course is switcher-first (Moodle import creates/activates a DB course with
paper code). Transcription settings live only in **Speech**; Moodle step 2 is a
thin handoff. Home **Environment** installs optional packs via
`POST /api/setup/install-extras`. Windows release ZIP includes a portable onedir
`CourseAssistant.exe` + embedded runtime (extras install into that runtime).

## What's new in v4.0.0

**Adaptive offline STT + Speech hub.** Transcription is a local model
router (profiles **Auto / Quality / Fast / Live / Eco**), not a single Whisper
call. Caption-first Panopto reuse when captions are usable, resumable chunking
with checkpoints, schema-v2 segments (optional speaker / words / confidence),
and optional Granite, Qwen3, Parakeet, Moonshine, plus faster-whisper as the
universal fallback. Heavy engines run in a subprocess worker. The **Speech**
panel is **Transcribe | Read aloud** (Kokoro TTS).

| Pack | Install |
| --- | --- |
| Base (recommended) | `pip install -r requirements-stt-base.txt` (also via `requirements-transcribe.txt`) |
| Quality (Granite / Qwen) | `pip install -r requirements-stt-quality.txt` |
| Speakers (pyannote) | `pip install -r requirements-stt-speakers.txt` |
| Live (Moonshine) | `pip install -r requirements-stt-live.txt` |
| Specialist (FireRed / Omnilingual) | `pip install -r requirements-stt-specialist.txt` |
| In-app (any pack) | Home → Environment → Install |

Model weights stay in a local cache — never in git or release ZIPs. Details:
[docs/STT.md](docs/STT.md). Health: `GET /api/health`; setup:
`GET /api/setup/preflight`.

Earlier highlights still in the product:

- **Speech TTS** — Kokoro-82M read-aloud (`requirements-tts.txt`) for long-form
  lectures and Markdown.
- **Study + Export** — glossary, study guides, quizzes, intent-driven export
  presets, practice/exam packs, and **Study suites** (Master + Courses vault /
  sync toward Obsidian, Notion, or OneNote).
- **Moodle** — course link / SSO token / browser scrape paths; guided Simple
  window for import → transcribe → export.

## An all-in-one course workflow

Work around **one course** (named in the top bar so imports and exports stay
tagged), then move through the sidebar:

| Panel | What it does |
| --- | --- |
| **Home** | Overview, environment status, and at-a-glance counts for the current course. |
| **Moodle** | Guided / advanced Moodle import (outline, resources, Panopto feeds). |
| **Import** | Lectures (Panopto RSS — URL, local `.xml`, or upload), documents (PDF / Office / HTML / EPUB / … → Markdown), Notion export zips, and browse-on-disk. |
| **Library** | Everything imported — transcripts, documents, exports — with search, viewer, and folder reorganize. |
| **Study** | Streak / next review, glossary, study guides, and planners tied to your library. |
| **Export** | NotebookLM packs, Anki decks, subtitle formats, Notion CSV, study suites / sync, and “export everything for AI”. |
| **Speech** | Offline **Transcribe** (adaptive STT profiles) and **Read aloud** (Kokoro). |
| **Jobs** | Live progress for transcription and related background work. |

### Transcription

Install the STT base pack (or `requirements-transcribe.txt`) when you want
offline transcription. Without it, the app still parses feeds, converts
documents, searches the library, and exports — Speech / Import simply reports
that no engine is installed.

Settings cover profile/engine, language, device (auto/cuda/cpu), skip-already-
transcribed, audio-only download, and an optional cookies file for auth-gated
feeds. Each job writes a canonical set: clean `.txt`, Markdown, rich `.json`
(schema v2 when available), and an extractive study summary (no cloud LLM
required). Subtitles (`srt` / `vtt`) and other formats are generated on demand
from **Export**.

## Quick start (no terminal needed)

You only need **[Python 3.10+](https://www.python.org/downloads/)** installed
(on Windows, tick *“Add python.exe to PATH”* in the installer).

| Your computer | Double-click |
| --- | --- |
| **Windows** | `start-windows.bat` |
| **macOS** | `start-unix.sh` (rename to `start-unix.command` to double-click) |
| **Linux** | `./start-unix.sh` |

The first run creates a private environment and installs the core dependencies;
after that it starts the app and opens **http://127.0.0.1:8000**. Leave the
launcher window open while you use the app; close it to stop.

Release ZIPs can also launch via root `installandrun.bat` next to
`CourseAssistant/`.

### Optional add-ons

| Need | Windows | macOS / Linux |
| --- | --- | --- |
| Transcription + full document conversion | `install-extras-windows.bat` | `./install-extras-unix.sh` |
| Or install packs by hand | see table in **What's new** / [CONTRIBUTING.md](CONTRIBUTING.md) | same |

The extras script adds the STT base stack (`faster-whisper`, `yt-dlp`,
`markitdown`) and can install Playwright + Chromium for Moodle browser scrape.
The first transcription downloads the chosen model into a local cache.

### Manual start (for developers)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python run.py                     # http://127.0.0.1:8000
```

## Exporting to NotebookLM

[NotebookLM](https://notebooklm.google.com/) works best with clean prose — raw
per-segment timestamps add noise. **Export → NotebookLM sources** (or
`POST /api/export/notebooklm`) writes NotebookLM-ready Markdown under
`transcripts/_notebooklm/`:

- **One file per lecture**, mirroring week/topic folders, with a clear heading,
  compact metadata, and paragraphs without fragmented timestamps.
- **Optional combined `course_pack.md`** with a table of contents.

Upload the `.md` files (or just `course_pack.md`) as NotebookLM sources. The
exporter prefers the richest on-disk form (`.json` → `.txt` → `.md`).

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `PANOPTO_OUTPUT` | `./transcripts` | Library directory (transcripts, SQLite DB, exports). |
| `HOST` | `127.0.0.1` | Bind address for `run.py`. |
| `PORT` | `8000` | Preferred port (auto-bumps if taken). |
| `PANOPTO_WORKERS` | `1` | Max concurrent transcription jobs. |
| `PANOPTO_NICE` | `1` | Below-normal process priority while jobs run (`0` disables). |
| `PANOPTO_CPU_THREADS` | *cores − 2* | CPU threads for CPU transcription. |

## API

The frontend is a thin client over a JSON API. Interactive docs are at `/docs`
when the server is running. Common groups:

- Status / health — `GET /api/status`, `GET /api/health`, `GET /api/setup/preflight`
- Feeds & library — `/api/feed*`, `/api/transcripts`, `/api/library`, `/api/search`
- Export & flashcards — `/api/export/*`, `/api/flashcards/*`
- Documents / Notion / Moodle — `/api/docs/*`, `/api/notion/*`, `/api/moodle/*`
- Transcription jobs — `POST /api/transcribe`, `GET /api/jobs`
- Adaptive STT — `/api/stt/capabilities`, `/api/stt/route`, `/api/stt/models*`, WebSocket `/ws/stt/live`

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

**Important:** set `PANOPTO_OUTPUT` to a temp directory **before** importing
`app.main` so tests never touch a real `./transcripts` library. Existing tests
already do this; new API tests should follow the same pattern. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Project layout

```
panopto-course-assistant/
├── app/
│   ├── main.py            # FastAPI app + router mounts
│   ├── routers/           # HTTP surface (one module per resource group)
│   ├── core.py            # feed parsing, library, exporters, summaries
│   ├── transcribe.py      # compatibility façade over adaptive STT
│   ├── stt/               # offline STT: routers, engines, chunking, workers
│   ├── database.py        # SQLite persistence (jobs, courses, settings)
│   ├── jobs.py            # background job manager
│   └── …                  # flashcards, study, exports, Moodle, TTS, …
├── static/                # index.html, app.js, style.css (vanilla SPA)
├── tests/                 # pytest suite
├── docs/STT.md            # adaptive STT profiles and ops notes
├── requirements.txt
├── requirements-transcribe.txt   # → STT base pack
├── requirements-stt-*.txt        # optional STT capability packs
├── requirements-tts.txt
├── requirements-dev.txt
└── run.py
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Please open issues with the provided
templates under `.github/ISSUE_TEMPLATE/`.

## Notes

- Persistent state (courses, jobs, settings) lives in SQLite under
  `PANOPTO_OUTPUT` (`course_assistant.db`). Secrets use OS keyring / encrypted
  file storage — never the DB.
- This is a single-user local tool, not a multi-user hosted service.
- Only download/transcribe content you are entitled to access. For auth-gated
  feeds, export cookies (Netscape format) and pass the path in the feed form.

## License

MIT — see [LICENSE](LICENSE).
