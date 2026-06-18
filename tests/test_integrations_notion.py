"""§5 Notion sync: incremental create/update, dedup, dry-run - all offline.

The HTTP transport is injected, so these exercise the real planning/dedup logic
without touching the network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import core
from app.integrations import notion as notion_sync


def _seed(tmp: Path, title: str, text: str = "content"):
    item = core.LectureItem(title=title, url="u",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp, item, "week"), ["txt", "json"], 30, {})


class FakeNotion:
    """Records calls and answers queries from an in-memory page list."""

    def __init__(self, existing_titles=()):
        self.pages = [{"id": f"pg-{i}", "properties": {
            "Name": {"title": [{"plain_text": t}]}}} for i, t in enumerate(existing_titles)]
        self.created = []
        self.updated = []

    def __call__(self, method, url, payload, headers):
        if url.endswith("/query"):
            return {"results": self.pages, "has_more": False}
        if method == "POST" and url.endswith("/pages"):
            self.created.append(payload)
            return {"id": "new"}
        if method == "PATCH":
            self.updated.append(payload)
            return {"id": "patched"}
        if url.endswith("/databases"):
            return {"id": "db-new"}
        return {}


def test_dry_run_writes_nothing_and_plans(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    _seed(tmp_path, "Week2_TCP")
    fake = FakeNotion(existing_titles=["Week1_Intro"])
    res = notion_sync.sync_course(tmp_path, token="t", database_id="db",
                                  dry_run=True, transport=fake)
    assert res["dry_run"] is True
    assert res["summary"] == {"create": 1, "update": 1}
    assert "Week2_TCP" in res["plan"]["create"]
    assert fake.created == [] and fake.updated == []


def test_incremental_creates_new_updates_existing(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    _seed(tmp_path, "Week2_TCP")
    fake = FakeNotion(existing_titles=["Week1_Intro"])
    res = notion_sync.sync_course(tmp_path, token="t", database_id="db",
                                  course="COMPX234", transport=fake)
    assert res["created"] == 1 and res["updated"] == 1
    # second run: now both exist -> nothing created
    fake2 = FakeNotion(existing_titles=["Week1_Intro", "Week2_TCP"])
    res2 = notion_sync.sync_course(tmp_path, token="t", database_id="db",
                                   transport=fake2)
    assert res2["created"] == 0 and res2["updated"] == 2


def test_missing_token_raises(tmp_path: Path):
    with pytest.raises(notion_sync.NotionError):
        notion_sync.sync_course(tmp_path, token="", database_id="db")


def test_field_map_override(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    fake = FakeNotion()
    notion_sync.sync_course(tmp_path, token="t", database_id="db",
                            field_map={"title": "Lecture"}, transport=fake)
    # the created page should key its title under the overridden property name
    assert "Lecture" in fake.created[0]["properties"]
