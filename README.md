# Course Assistant

A **web app** that turns university course material into study resources. Point
it at a [Panopto](https://www.panopto.com/) lecture feed and/or your course
documents, and it transcribes, converts, organises and **exports clean sources
for [NotebookLM](https://notebooklm.google.com/), [Anki](https://apps.ankiweb.net/)
and [Notion](https://notion.so/)**. Built around the University of Waikato
courses but **course-agnostic** (validated against 7 different Moodle exports).

A **Python / FastAPI** backend with a plain **HTML + JavaScript** frontend
(no build step), a sidebar dashboard, and light/dark themes.

## What's new

**v3.3 - a visual identity, not a template.** The v3.2 redesign still read as a
generic "warm paper + serif + single accent" AI-default look. This pass replaces
it with an identity grounded in what the app actually does - turn a spoken
recording into written knowledge:

- **"Signal & Instrument" palette and type.** Cool brushed-aluminium surfaces,
  a copper "signal" accent (a VU needle at speaking volume, not a brand blue or
  a terracotta), a technical signage face (Bahnschrift) for headings and panel
  labels, and every count/timecode/duration set in tabular monospace - the one
  place numbers are meant to look "read out" rather than typeset. Verified
  against WCAG AA arithmetically, both themes.
- **The Home pipeline reads as a patch bay.** The four workspace stages are
  connected by jack connectors that light up copper when either side of the
  seam is live - a signature drawn from the subject (audio signal chains), not
  a decorative flourish.
- **A channel-strip sidebar.** Each nav row carries a selector edge that lights
  when its channel is active, and progress bars render as a segmented level
  meter rather than a smooth loading bar.
- **The brand mark is a waveform**, not a generic graduation cap.

**v3.2 - interface and codebase revamp.** A ground-up pass on how the app looks
and how it is put together:

- **A deliberate visual identity.** A paper-and-ink palette with a single teal
  accent (not the default dashboard blue), serif headings, and a token-driven
  stylesheet. Every emoji glyph is replaced by an inline SVG icon set that
  inherits the theme. Light and dark both meet WCAG AA contrast, checked
  arithmetically in the test suite.
- **Accessibility.** Keyboard-reachable controls with a visible focus ring,
  accessible names on every icon-only button, dialogs that trap focus and
  return it on close, and live-region announcements for background work.
- **One coherent suite.** `GET /api/collections` gathers everything derived from
  a lecture - glossary terms, keywords, citations, notes, tags and sibling
  lectures - so a lecture reads as one thing, and the Study panel cross-links
  the guide to the glossary it was built from.
- **A backend that is one app, not 129 loose routes.** `main.py` is now app
  setup plus router mounts; the HTTP surface lives in `app/routers/` (one module
  per resource group). Every integration failure renders through one shared
  error envelope (`app/errors.py`) instead of six bespoke shapes.
- **One-click launch that offers the add-ons.** The first run of
  `start-windows.bat` now offers the transcription, document and speech extras,
  so a new user is set up in a single double-click.

**v2.2 - download a whole course from its link.** `POST /api/moodle/fetch-course` parses the course, **downloads every resource file** (slides, assignment briefs, PDFs) using your browser session cookies, converts them to Markdown, and reports the Panopto feeds for transcription - one call from the course URL. The Moodle "Simple" window now runs this end-to-end (import → files → auto-transcribe → export). Resource download URLs are captured from the page, Panopto's duplicated `itpc://`/`https://` feeds are de-duplicated, and there's an **"include images & diagrams" toggle (on by default)** on both the document import and the Moodle flow.

**v2.1 - imagery & diagrams are no longer lost.** Converting a PDF/PPTX/DOCX used to keep only the text; now embedded **figures, diagrams and screenshots are extracted and attached** to the Markdown (`![Figure N (p.X)](…_assets/…)`) so worked examples, assignment figures and lecture-slide diagrams survive into NotebookLM/AI exports and the course archive. Office/EPUB images come out with the standard library; PDFs use PyMuPDF or pdfplumber. Images are preserved even when text extraction fails, and Notion-export images are copied across too.

### v2.0

A big step from "single-session tool" toward a persistent, multi-course study platform:

- **Pick how you work** - a launcher with two windows (*Full workspace* vs *Just my Moodle course*), each in **Simple** or **Advanced** mode. Simple uses best defaults; Advanced exposes every knob.
- **Import a Moodle course from its link** - paste `…/course/view.php?id=…`; with your browser session it pulls the outline, activities and Panopto lecture feeds (crawls the linked section pages).
- **Guided Simple flow** - import → auto-transcribe with the best detected settings for your machine → one export choice (NotebookLM or general AI). Transcription progress refreshes ~every 30 s.
- **Study planner** - assessments, an SM-2 spaced-repetition scheduler, `.ics` calendar export, an hours-budgeted study plan, and progress/mastery tracking.
- **Live sync** - incremental, duplicate-aware Notion & Anki sync with a dry-run preview.
- **Export engine** - intent presets (revision/ai/exam/notion/anki/archive) × scopes, preview-before-write, and a portable course archive.
- **Privacy & safety** - secrets in the OS keyring (never plaintext), data-egress labels, an audit log, local-only analytics, and one-file backup/restore.

## An all-in-one course workflow

The app is organised as **one flow around a single course**, not a pile of
separate tools. You **name your course once** (in the bar at the top - it tags
every import and export automatically), then move through four steps:

| Step | What it does |
| --- | --- |
| 🏠 **Home** | Overview: how the flow works, environment status, and at-a-glance counts for the current course. |
| 🎓 **1 · Course** | Name your course, or **grab its real title + week/topic outline from a Moodle export** (any Moodle course) and use it as the course name in one click. |
| 📥 **2 · Import** | One hub to **keep importing whatever you have**, with a sub-switch: **Lectures** (load a Panopto RSS feed - URL, local `.xml`, or upload - then transcribe one, a selection, or all pending), **Documents** (PDF / PowerPoint / Word / Excel / HTML / EPUB / CSV… → Markdown via [MarkItDown](https://github.com/microsoft/markitdown)), **Notion** (upload the export **`.zip`** - nested `ExportBlock` zips and all - or a single `.html`/folder → clean Markdown), and **Browse files** (find a folder path on disk). Everything lands in your Library. |
| 📚 **3 · Library** | **Everything** you've imported, in one place and fully categorised - transcripts (with format chips), converted documents, Notion pages, generated exports and any other source files - with full-text **search**, a **viewer**, and **reorganize** into auto/week/lecture/module/date/topic folders. |
| 📤 **4 · Export** | Turn the library into study material. **Export everything for AI** brings *all* your imported sources (transcripts + documents + Notion pages) together into one combined `everything_pack.md` for NotebookLM or any other AI; or export one kind at a time - **NotebookLM** sources, **subtitles & extra formats** (SRT/VTT/TXT/MD generated on demand), an **Anki** flashcard deck (auto-tagged by course·week·topic, plus categorize-an-existing-deck), and a **Notion study-database CSV** - all tagged with your course name. |
| ⚙️ **Jobs** | Live progress of transcription jobs with a count badge; finished jobs refresh the lecture badges. |

### Transcription options

The **Transcription settings** panel is intentionally minimal - engine, model,
language and device (auto/cuda/cpu), plus **skip already-transcribed** and
**audio-only download** (saves bandwidth) toggles, and an optional cookies-file
path for auth-gated feeds. Every transcription writes a sensible **canonical
set** - clean text, Markdown, rich JSON, and an extractive **study summary** (no
LLM required) - which is enough to power the Library, search and every export.
Lectures are auto-organised into folders (`auto` detects whichever of
Week/Lecture/Module/Unit/Session/Lab a title uses, so non-"Week N" courses still
organise sensibly); you can re-sort any time from the Library.

**Subtitles and other formats** (`srt`, `vtt`, extra `txt`/`md`) are generated on
demand from the **Export** step rather than cluttering the transcribe screen -
they're rebuilt from the stored JSON whenever you want them. Your settings, feed
URL and course name are remembered in the browser between visits.

The transcription engine ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)
or [openai-whisper](https://github.com/openai/whisper)) is **optional**. The app
runs fine for feed parsing, search, viewing, PDF conversion, export and browsing
without it - the **Import → Lectures** view simply shows that no engine is
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
clean, readable prose - per-segment timestamps fragment sentences and add noise.
The **Export → NotebookLM sources** button (or `POST /api/export/notebooklm`)
turns your existing transcripts into NotebookLM-ready Markdown:

- **One file per lecture** under `transcripts/_notebooklm/`, mirroring the
  Week/Topic folder structure. Each file has a clear `# Title` heading, a compact
  metadata line (week · date · duration) to help NotebookLM ground its citations,
  and the transcript re-flowed into clean paragraphs with timestamps removed.
- **Optional combined `course_pack.md`** - every lecture in one document with a
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
- `GET  /api/library` – comprehensive, categorised listing of every file
- `GET  /api/transcript?path=` – read one transcript file
- `GET  /api/search?q=` – full-text search
- `POST /api/export/notebooklm` `{selection?, combined?, course?}` – NotebookLM export
- `POST /api/export/all` `{combined?, course?}` – combine transcripts + documents + Notion into one AI export
- `POST /api/export/formats` `{formats, interval?}` – generate subtitles / alternate formats
- `POST /api/export/notion-csv` `{course?, filename?}` – Notion study-database CSV
- `POST /api/flashcards/generate` `{selection?, course?, deck?, prefer?, max_per_lecture?}` – Anki cards
- `POST /api/flashcards/categorize` `{text|path, course?, extra_keywords?, deck?}` – tag a deck
- `POST /api/docs/convert` `{input_path, exts?, target, combined?, ...}` – documents → Markdown
- `POST /api/transcribe` – queue a transcription job
- `POST /api/organize` `{by}` – reorganize existing transcripts into folders
- `POST /api/moodle/parse` `{path, save_outline?}` – parse a whole Moodle course export (sections, activities, resources)
- `POST /api/notion/convert` `{path, combined?}` – Notion export (.zip/.html/folder) → Markdown
- `POST /api/notion/upload` – convert an uploaded Notion export (.zip/.html)
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

- State (the job list) is in memory and resets when the server restarts - this
  is a single-user local tool, not a multi-user service.
- Only download/transcribe content you are entitled to access. For auth-gated
  feeds, export cookies (Netscape format) and pass the path in the feed form.

## License

MIT - see [LICENSE](LICENSE).
