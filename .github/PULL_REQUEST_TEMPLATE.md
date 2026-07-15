## Summary

Briefly describe **why** this change exists (bug fix, feature, docs, refactor).

## Changes

- 

## How tested

- [ ] `python -m pytest -q` (or name the tests you ran)
- [ ] Manual check in the UI (note which panel / flow)
- [ ] N/A — docs / meta only

`PANOPTO_OUTPUT` must point at a temp directory before importing `app.main` in
tests so a real `./transcripts` library is never used.

### Optional packs used while testing

- [ ] None (core only)
- [ ] STT base / `requirements-transcribe.txt`
- [ ] STT quality / speakers / live / specialist
- [ ] TTS (`requirements-tts.txt`)
- [ ] Browser (`requirements-browser.txt`)

## Checklist

- [ ] No secrets, cookies, transcripts, audio, or model weights committed
- [ ] Migrations (if any) are append-only; existing steps unchanged
- [ ] Follows [CONTRIBUTING.md](../CONTRIBUTING.md) and the [Code of Conduct](../CODE_OF_CONDUCT.md)
