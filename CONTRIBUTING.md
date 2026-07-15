# Contributing

Thanks for helping improve Course Assistant — a local-first tool for turning
university course material into study resources. Please read this guide before
opening a pull request.

By participating, you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## What belongs in this repo

Do **not** commit:

- Secrets, API tokens, cookies files, or Hugging Face credentials
- Lecture audio/video, personal course exports, or real student data
- Model weights or large binary caches
- Contents of `transcripts/` (local library + SQLite DB)

If you need fixtures for tests, use synthetic or anonymized samples only.

## Development setup

You need **Python 3.10+**.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
python run.py
```

Core features (feed parsing, library, export, Moodle flows) work without the
optional speech or document stacks.

### Optional extras

| Need | Install |
| --- | --- |
| Transcription + docs (STT base) | `pip install -r requirements-transcribe.txt` |
| Quality STT (Granite / Qwen) | `pip install -r requirements-stt-quality.txt` |
| Speaker diarization | `pip install -r requirements-stt-speakers.txt` |
| Live / mic STT | `pip install -r requirements-stt-live.txt` |
| Specialist engines | `pip install -r requirements-stt-specialist.txt` |
| Read-aloud TTS (Kokoro) | `pip install -r requirements-tts.txt` |
| Moodle browser scrape | `pip install -r requirements-browser.txt` |

STT extras are **optional**. PRs that touch the adaptive STT stack (`app/stt/`,
Speech hub) should note which packs were installed when testing. See
[docs/STT.md](docs/STT.md) for profiles, schema v2, and health endpoints.

## Running tests

Always point the library directory at a temp path so tests never write into a
real `./transcripts` folder. Set `PANOPTO_OUTPUT` **before** importing
`app.main` (new test files should follow the pattern already used in `tests/`).

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Single file / test:

```bash
python -m pytest tests/test_core.py -q
python -m pytest tests/test_moodle_api.py::test_fetch_token_sso_rejection -q
```

## Pull request expectations

1. **Stay focused.** One concern per PR when practical (bugfix, feature, docs).
2. **Match the codebase.** Prefer existing patterns in `app/routers/`, `app/core.py`,
   and the vanilla SPA under `static/` (no new framework without discussion).
3. **Keep routes thin.** Business logic lives in domain modules; HTTP stays in
   routers / `main.py`.
4. **Add or update tests** for behaviour you change, especially parsers, exporters,
   and API edges.
5. **Describe the why.** Use the PR template: summary, how you tested, and any
   optional STT/TTS packs involved.
6. **Local-first.** Do not add cloud transcription or analytics that send course
   audio/text off-machine without an explicit, opt-in design discussion.

## Architecture pointers

| Area | Start here |
| --- | --- |
| Feed / library / exporters | `app/core.py` |
| Adaptive STT | `app/stt/`, façade `app/transcribe.py` |
| Speech hub API | `app/routers/stt.py`, `app/routers/tts.py` |
| Jobs / SQLite | `app/jobs.py`, `app/database.py` |
| UI | `static/index.html`, `static/app.js`, `static/style.css` |

DB schema migrations in `database.py` are append-only — never edit a shipped
migration step.

## Questions

Open a GitHub issue (bug or feature template) if you are unsure where something
belongs. Prefer a short design note over a large speculative PR.
