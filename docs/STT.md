# Adaptive offline speech-to-text (v4.0)

Local-only STT. No audio is sent to cloud services.

## Profiles

| Profile | Typical engine | When |
| --- | --- | --- |
| **Auto** | Captions → Granite/Qwen/faster-whisper | Default; caption-first when Panopto captions are usable |
| **Quality** | Granite Speech 4.1 2B (or Qwen3 multilingual) | Best lecture accuracy |
| **Fast** | Parakeet TDT 0.6B v3 → faster-whisper turbo | High throughput batch |
| **Live** | Moonshine (EN) / rolling faster-whisper | Microphone streaming |
| **Eco** | Moonshine Small / faster-whisper Small | CPU / low RAM |

Fallbacks are recorded in job results (`fallbacks_used`, `route_reason`).

## Install packs

```bash
pip install -r requirements-stt-base.txt          # faster-whisper + yt-dlp + markitdown
pip install -r requirements-stt-quality.txt       # Granite / Qwen (torch + transformers)
pip install -r requirements-stt-speakers.txt      # pyannote diarization (HF license)
pip install -r requirements-stt-live.txt          # Moonshine live
pip install -r requirements-stt-specialist.txt    # FireRed / Omnilingual (on demand)
```

`requirements-transcribe.txt` includes the base pack for backward compatibility.

## Schema v2

JSON transcripts keep `start` / `end` / `text` for v1 readers and may add
`speaker`, `language`, `confidence`, and `words[]`. Metadata includes
`schema_version`, engine/model, route reason, timing/diarization sources, and
input fingerprint. Existing libraries are not bulk-rewritten — use enrich /
re-transcribe.

## Operations

- **Resume:** `.stt.partial.json` checkpoints per chunk; cancel/restart continues.
- **Workers:** Granite/Qwen/Parakeet/Moonshine/specialists run in a subprocess (`PANOPTO_STT_INPROCESS=1` forces in-process).
- **Secrets:** Hugging Face token via Settings / `POST /api/stt/hf-token` (OS keyring / encrypted store).
- **Health:** `GET /api/health`, `GET /api/setup/preflight`, `POST /api/diagnostics/bundle`.
- **API:** `/api/stt/capabilities`, `/api/stt/route`, `/api/stt/models*`, WebSocket `/ws/stt/live`.

## Benchmarks

```bash
python scripts/benchmark_stt.py --manifest /local/benchmark_manifest.json --out stt_benchmark_metrics.json
```

Never commit private audio, model weights, or tokens. Metrics-only JSON is fine.
