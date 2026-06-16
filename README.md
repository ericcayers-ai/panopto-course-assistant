# Course Assistant

A **web app** that turns university course material into study resources. Point
it at a [Panopto](https://www.panopto.com/) lecture feed and/or your course
documents, and it transcribes, converts, organises and **exports clean sources
for [NotebookLM](https://notebooklm.google.com/), [Anki](https://apps.ankiweb.net/)
and [Notion](https://notion.so/)**. Built around the University of Waikato
courses but **course-agnostic** (validated against 7 different Moodle exports).

A **Python / FastAPI** backend with a plain **HTML + JavaScript** frontend
(no build step), a sidebar dashboard, and light/dark themes.

## An all-in-one course workflow

The app is organised as **one flow around a single course**, not a pile of
separate tools. You **name your course once** (in the bar at the top — it tags
every import and export automatically), then move through four steps:

| Step | What it does |
| --- | --- |
| 🏠 **Home** | Overview: how the flow works, environment status, and at-a-glance counts for the current course. |
| 🎓 **1 · Course** | Name your course, or **grab its real title + week/topic outline from a Moodle export** (any Moodle course) and use it as the course name in one click. |
| 📥 **2 · Import** | One hub to **keep importing whatever you have**, with a sub-switch: **Lectures** (load a Panopto RSS feed — URL, local `.xml`, or upload — then transcribe one, a selection, or all pending), **Documents** (PDF / PowerPoint / Word / Excel / HTML / EPUB / CSV… → Markdown via [MarkItDown](https://github.com/microsoft/markitdown)), **Notion** (an HTML export → clean Markdown), and **Browse files** (find a folder path on disk). Everything lands in your Library. |
| 📚 **3 · Library** | Everything you've imported, in one searchable place: full-text **search** across transcripts, a **viewer**, and **reorganize** into auto/week/lecture/module/date/topic folders. |
| 📤 **4 · Export** | Turn the library into study material. **Export everything for AI** brings *all* your imported sources (transcripts + documents + Notion pages) together into one combined `everything_pack.md` for NotebookLM or any other AI; or export one kind at a time — **NotebookLM** sources, an **Anki** flashcard deck (auto-tagged by course·week·topic, plus categorize-an-existing-deck), and a **Notion study-database CSV** — all tagged with your course name. |
| ⚙️ **Jobs** | Live progress of transcription jobs with a count badge; finished jobs refresh the lecture badges. |

### Transcription options

When an engine is installed, the **Transcription settings** panel exposes:
engine, model, language, device (auto/cuda/cpu), folder organisation
(**auto**/week/lecture/module/date/topic/none — `auto` detects whichever of
Week/Lecture/Module/Unit/Session/Lab a title uses, so non-"Week N" courses
still organise sensibly), the TXT timestamp interval, and the output formats —
`txt`, `srt`, `vtt`, `md`, `json`, **`notebooklm`** (clean prose), and
**`summary`** (an extractive key-points study summary, no LLM required). Toggles
cover **audio-only download** (saves bandwidth), **keep media**, **skip
already-transcribed**, and **force re-transcribe**. A cookies-file path lets you
reach auth-gated feeds. Your settings, feed URL and course name are remembered in
the browser between visits.

The transcription engine ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)
or [openai-whisper](https://github.com/openai/whisper)) is **optional**. The app
runs fine for feed parsing, search, viewing, PDF conversion, export and browsing
without it — the **Import → Lectures** view simply shows that no engine is
installed and disables the transcribe buttons.

## Quick start (no terminal needed)

You only need **[Python 3.10+](https://www.python.org/downloads/)** installed
(on Windows, tick *“Add python.exe to PATH”* in the installer).

| Your computer | Double-click |
| --- | --- |
| **Windows** | `start-windows.bat` |
| **macOS** | `start-unix.sh` (rename to `start-unix.command` to double-click) |
| **Linux** | `./start-unix.sh` |

The first run creates a private environment and installs the core dependencies
(about a minute); after that it just starts the app and opens your browser at
**http://127.0.0.1:8000**. Leave the little window open while you use the app;
close it to stop.

### Optional add-ons (transcription + full document conversion)

For Whisper transcription and PDF/PowerPoint/Word/Excel → Markdown, run once:

| Windows | macOS / Linux |
| --- | --- |
| `install-extras-windows.bat` | `./install-extras-unix.sh` |

This adds `faster-whisper` (CPU int8 or CUDA GPU), `yt-dlp`, and
`markitdown[all]`. The first transcription downloads the chosen Whisper model.

### Manual start (for developers)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python run.py                     # http://127.0.0.1:8000
```

## Exporting to NotebookLM

[NotebookLM](https://notebooklm.google.com/) works best when each source is
clean, readable prose — per-segment timestamps fragment sentences and add noise.
The **Export → NotebookLM sources** button (or `POST /api/export/notebooklm`)
turns your existing transcripts into NotebookLM-ready Markdown:

- **One file per lecture** under `transcripts/_notebooklm/`, mirroring the
  Week/Topic folder structure. Each file has a clear `# Title` heading, a compact
  metadata line (week · date · duration) to help NotebookLM ground its citations,
  and the transcript re-flowed into clean paragraphs with timestamps removed.
- **Optional combined `course_pack.md`** — every lecture in one document with a
  table of contents, so you can upload the whole course as a single source.

How to use it:

1. Transcribe some lectures (or drop existing `.txt`/`.json` transcripts in the
   output folder).
2. Open the **Export** step, tick *combined* if you want a single file, and click
   **Export all** (the course name from the top bar is applied automatically).
3. In NotebookLM, click **+ Add source** and upload the `.md` files from
   `transcripts/_notebooklm/` (or just `course_pack.md`).

The exporter reads from the richest source available per lecture (`.json` →
`.txt` → `.md`), so it works even on transcripts produced before this feature
existed. You can also produce a NotebookLM file at transcription time by adding
`notebooklm` to the `outputs` list.

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `PANOPTO_OUTPUT` | `./transcripts` | Where transcripts are written and read. |
| `HOST` | `127.0.0.1` | Bind address for `run.py`. |
| `PORT` | `8000` | Preferred port for `run.py` (auto-bumps to the next free one if taken). |
| `PANOPTO_WORKERS` | `1` | Transcription jobs run concurrently up to this many. Default `1` keeps exactly one job in flight so a whole feed doesn't exhaust RAM/VRAM or freeze the desktop. |
| `PANOPTO_NICE` | `1` | Drop to below-normal process priority while jobs run, so the GUI stays responsive. Set `0` to disable. |
| `PANOPTO_CPU_THREADS` | *cores − 2* | CPU threads for CPU transcription; the default leaves a couple of cores free for the rest of the machine. |

## API

The frontend is a thin client over a JSON API (see `app/main.py`):

- `GET  /api/status` – which engines/deps are installed
- `POST /api/feed` `{source, cookies?}` – parse a feed URL/path
- `POST /api/feed/upload` – parse an uploaded RSS `.xml`
- `GET  /api/transcripts` – list generated transcripts
- `GET  /api/transcript?path=` – read one transcript file
- `GET  /api/search?q=` – full-text search
- `POST /api/export/notebooklm` `{selection?, combined?, course?}` – NotebookLM export
- `POST /api/export/all` `{combined?, course?}` – combine transcripts + documents + Notion into one AI export
- `POST /api/export/notion-csv` `{course?, filename?}` – Notion study-database CSV
- `POST /api/flashcards/generate` `{selection?, course?, deck?, prefer?, max_per_lecture?}` – Anki cards
- `POST /api/flashcards/categorize` `{text|path, course?, extra_keywords?, deck?}` – tag a deck
- `POST /api/docs/convert` `{input_path, exts?, target, combined?, ...}` – documents → Markdown
- `POST /api/transcribe` – queue a transcription job
- `POST /api/organize` `{by}` – reorganize existing transcripts into folders
- `POST /api/moodle/parse` `{path, save_outline?}` – parse a Moodle course export
- `POST /api/notion/convert` `{path, combined?}` – Notion HTML export → Markdown
- `GET  /api/jobs` / `GET /api/jobs/{id}` – job status
- `GET  /api/materials?path=` – list a local folder

Interactive API docs are available at `/docs` when the server is running.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # 160+ unit + API tests
```

The suite covers feed-parsing edge cases (malformed XML, missing/garbage
fields), date/week/sequence inference, the Moodle parser (synthetic + the real
example exports), timestamp rounding, every renderer, the extractive summary,
transcript listing/search, the path-traversal guard, the NotebookLM/Notion-CSV
exports, document conversion, flashcard generation/categorisation, reorganisation,
the background job lifecycle, the skip/force transcribe flow, and the HTTP API
(via `fastapi.testclient`).

## Project layout

```
panopto-course-assistant/
├── app/
│   ├── core.py         # feed parsing, organise, writers, search, summary, NotebookLM, docs→MD
│   ├── sources.py      # Moodle course HTML export parser
│   ├── notion.py       # Notion HTML export -> Markdown converter (stdlib only)
│   ├── flashcards.py   # Anki flashcard generation + categorisation
│   ├── study.py        # Notion study-database CSV export
│   ├── transcribe.py   # optional download + whisper engines (lazy imports)
│   ├── jobs.py         # in-memory background job manager
│   └── main.py         # FastAPI app + routes
├── static/             # index.html, app.js, style.css (vanilla SPA, sidebar + dark mode)
├── tests/              # pytest suite (core, sources, notion, docs, flashcards, study, jobs, API)
├── start-windows.bat / start-unix.sh          # one-click launchers
├── install-extras-windows.bat / -unix.sh      # optional heavy add-ons
├── requirements.txt
├── requirements-transcribe.txt   # optional: faster-whisper, yt-dlp, markitdown[all]
├── requirements-dev.txt          # pytest, httpx
└── run.py
```

## Notes

- State (the job list) is in memory and resets when the server restarts — this
  is a single-user local tool, not a multi-user service.
- Only download/transcribe content you are entitled to access. For auth-gated
  feeds, export cookies (Netscape format) and pass the path in the feed form.

## License

MIT — see [LICENSE](LICENSE).
