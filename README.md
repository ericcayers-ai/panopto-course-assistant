# Panopto Course Assistant

A small **web app** for working with [Panopto](https://www.panopto.com/) lecture
recordings published as an RSS podcast feed (originally built for the University
of Waikato **COMPX234 – Systems and Networks** course).

It is the web version of the original single-file CLI tool: a **Python /
FastAPI** backend with a plain **HTML + JavaScript** frontend (no build step).

## Features

| Tab | What it does |
| --- | --- |
| **Lectures** | Paste a Panopto RSS feed URL (or upload the `.xml`) and list every lecture, with week/date/duration parsed from the title and metadata. Queue any lecture for transcription. |
| **Transcripts** | Browse generated transcripts (grouped by lecture, organised into Week/Date/Topic folders) and read any `.txt` / `.srt` / `.vtt` / `.md` / `.json` output inline. |
| **Search** | Full-text search across every transcript with highlighted snippets. |
| **PDF → Markdown** | Point at a folder of lecture-slide PDFs and convert them all to Markdown (mirrors the folder structure into a `*_copy` folder), via [MarkItDown](https://github.com/microsoft/markitdown). |
| **Jobs** | Live progress of running transcription jobs (download → transcribe → write), with a polling progress bar. |
| **Materials** | Browse any local folder (e.g. the course slides / source code) from the browser. |

The transcription engine ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)
or [openai-whisper](https://github.com/openai/whisper)) is **optional**. The app
runs fine for feed parsing, search, viewing, PDF conversion and browsing without
it — the **Lectures** tab simply shows that no engine is installed.

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
- `POST /api/transcribe` – queue a transcription job
- `GET  /api/jobs` / `GET /api/jobs/{id}` – job status
- `POST /api/pdf/convert` `{input_path, ...}` – convert a PDF folder
- `GET  /api/materials?path=` – list a local folder

Interactive API docs are available at `/docs` when the server is running.

## Project layout

```
panopto-course-assistant/
├── app/
│   ├── core.py         # feed parsing, organisation, writers, search, PDF→MD
│   ├── transcribe.py   # optional download + whisper engines (lazy imports)
│   ├── jobs.py         # in-memory background job manager
│   └── main.py         # FastAPI app + routes
├── static/             # index.html, app.js, style.css (vanilla frontend)
├── requirements.txt
├── requirements-transcribe.txt
└── run.py
```

## Notes

- State (the job list) is in memory and resets when the server restarts — this
  is a single-user local tool, not a multi-user service.
- Only download/transcribe content you are entitled to access. For auth-gated
  feeds, export cookies (Netscape format) and pass the path in the feed form.

## License

MIT — see [LICENSE](LICENSE).
