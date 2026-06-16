# Panopto Course Assistant

A small **web app** for working with [Panopto](https://www.panopto.com/) lecture
recordings published as an RSS podcast feed (originally built for the University
of Waikato **COMPX234 – Systems and Networks** course).

It is the web version of the original single-file CLI tool: a **Python /
FastAPI** backend with a plain **HTML + JavaScript** frontend (no build step).

## Features

| Tab | What it does |
| --- | --- |
| **Lectures** | Paste a Panopto RSS feed URL (or upload the `.xml`) and list every lecture, with week/date/duration/size parsed from the title and metadata. Each lecture shows a **transcribed / pending** badge. Pick output formats and options, then transcribe one, a selection, or **all pending** in a batch. |
| **Transcripts** | Browse generated transcripts (grouped by lecture, one row each, with format chips) and read any `.txt` / `.srt` / `.vtt` / `.md` / `.json` / summary output inline. One-click **Export for NotebookLM** and **Reorganize** into Week/Date/Topic folders. |
| **Search** | Full-text search across every transcript — one result per lecture, ranked by hit count, with snippets and a jump-to-transcript button. |
| **PDF → Markdown** | Point at a folder of lecture-slide PDFs and convert them all to Markdown (mirrors the folder structure into a `*_copy` folder), via [MarkItDown](https://github.com/microsoft/markitdown). |
| **Jobs** | Live progress of running transcription jobs (download → transcribe → write) with a polling progress bar and a count badge; finished jobs refresh the lecture badges automatically. |
| **Materials** | Browse any local folder; **parse a Moodle course export** (any saved Moodle course HTML) for the course title/outline; and **convert a Notion HTML export** (page or folder) into clean Markdown sources. |

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
without it — the **Lectures** tab simply shows that no engine is installed and
disables the transcribe buttons.

## Quick start

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. install the core dependencies
pip install -r requirements.txt

# 3. run it
python run.py
# open http://127.0.0.1:8000
```

### Enable transcription (optional)

```bash
pip install -r requirements-transcribe.txt
```

This adds `faster-whisper` (recommended; runs on CPU with int8, or GPU if you
have CUDA), `yt-dlp` (download fallback for auth-gated media), and `markitdown`
(PDF → Markdown). The first transcription downloads the chosen Whisper model.

## Exporting to NotebookLM

[NotebookLM](https://notebooklm.google.com/) works best when each source is
clean, readable prose — per-segment timestamps fragment sentences and add noise.
The **Transcripts → Export for NotebookLM** button (or `POST /api/export/notebooklm`)
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
2. Open the **Transcripts** tab, optionally type a course name, tick *combined*
   if you want a single file, and click **Export all transcripts**.
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
| `PORT` | `8000` | Port for `run.py`. |

## API

The frontend is a thin client over a JSON API (see `app/main.py`):

- `GET  /api/status` – which engines/deps are installed
- `POST /api/feed` `{source, cookies?}` – parse a feed URL/path
- `POST /api/feed/upload` – parse an uploaded RSS `.xml`
- `GET  /api/transcripts` – list generated transcripts
- `GET  /api/transcript?path=` – read one transcript file
- `GET  /api/search?q=` – full-text search
- `POST /api/export/notebooklm` `{selection?, combined?, course?}` – NotebookLM export
- `POST /api/transcribe` – queue a transcription job
- `POST /api/organize` `{by}` – reorganize existing transcripts into folders
- `POST /api/moodle/parse` `{path, save_outline?}` – parse a Moodle course export
- `POST /api/notion/convert` `{path, combined?}` – Notion HTML export → Markdown
- `GET  /api/jobs` / `GET /api/jobs/{id}` – job status
- `POST /api/pdf/convert` `{input_path, ...}` – convert a PDF folder
- `GET  /api/materials?path=` – list a local folder

Interactive API docs are available at `/docs` when the server is running.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # ~90 unit + API tests
```

The suite covers feed-parsing edge cases (malformed XML, missing/garbage
fields), date/week inference, timestamp rounding, every renderer, the
extractive summary, transcript listing/search, the path-traversal guard, the
NotebookLM export, reorganisation, the background job lifecycle, the
skip/force transcribe flow, and the HTTP API (via `fastapi.testclient`).

## Project layout

```
panopto-course-assistant/
├── app/
│   ├── core.py         # feed parsing, organisation, writers, search, summary, PDF→MD, NotebookLM
│   ├── sources.py      # course-material parsers (Moodle course HTML export)
│   ├── notion.py       # Notion HTML export -> Markdown converter (stdlib only)
│   ├── transcribe.py   # optional download + whisper engines (lazy imports)
│   ├── jobs.py         # in-memory background job manager
│   └── main.py         # FastAPI app + routes
├── static/             # index.html, app.js, style.css (vanilla frontend, no build step)
├── tests/              # pytest suite (core, jobs/transcribe, API)
├── requirements.txt
├── requirements-transcribe.txt   # optional: faster-whisper, yt-dlp, markitdown
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
