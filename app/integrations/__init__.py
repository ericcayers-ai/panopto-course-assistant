"""Live write/sync integrations (§5): Notion API and Anki (AnkiConnect).

Distinct from the *import-side* ``app/notion.py`` (which reads Notion HTML
exports). Everything here talks to an external service at call time and is built
around an injectable ``transport`` so the logic (planning, dedup, dry-run) is
testable without a network.
"""
