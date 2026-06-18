"""
integrations/notion.py - live Notion API sync (§5).

Pushes the course library into a Notion study database: one page per lecture,
preserving the course · week · topic tags. Incremental + duplicate-aware (matches
on the title property), supports a **dry-run preview** and an **editable field
mapping**. The HTTP transport is injectable so the planning/dedup logic is fully
testable offline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import search

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# local field -> Notion property name (overridable via /api/sync/mapping)
DEFAULT_FIELD_MAP = {
    "title": "Name", "week": "Week", "topic": "Topic",
    "course": "Course", "status": "Status",
}


class NotionError(Exception):
    pass


Transport = Callable[[str, str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]


def _http_transport(method: str, url: str, payload: Optional[Dict[str, Any]],
                   headers: Dict[str, str]) -> Dict[str, Any]:
    import requests
    try:
        r = requests.request(method, url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        return r.json() if r.content else {}
    except Exception as e:
        raise NotionError(str(e)) from e


class NotionClient:
    def __init__(self, token: str, transport: Optional[Transport] = None) -> None:
        if not token:
            raise NotionError("No Notion token configured (set NOTION_TOKEN).")
        self.token = token
        self._t = transport or _http_transport

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._t(method, API + path, payload, self._headers())

    def query_database(self, database_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        cursor = None
        while True:
            payload: Dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            data = self._call("POST", f"/databases/{database_id}/query", payload)
            out.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return out

    def create_database(self, parent_page_id: str, title: str,
                       field_map: Dict[str, str]) -> Dict[str, Any]:
        props = {
            field_map["title"]: {"title": {}},
            field_map["week"]: {"number": {}},
            field_map["topic"]: {"rich_text": {}},
            field_map["course"]: {"rich_text": {}},
            field_map["status"]: {"select": {"options": [
                {"name": "Not started"}, {"name": "In progress"}, {"name": "Done"}]}},
        }
        return self._call("POST", "/databases", {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": props,
        })

    def create_page(self, database_id: str, props: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", "/pages",
                         {"parent": {"database_id": database_id}, "properties": props})

    def update_page(self, page_id: str, props: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("PATCH", f"/pages/{page_id}", {"properties": props})


def _props_for(item: Dict[str, Any], course: str, field_map: Dict[str, str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        field_map["title"]: {"title": [{"text": {"content": item["title"]}}]},
        field_map["topic"]: {"rich_text": [{"text": {"content": item.get("topic", "")}}]},
        field_map["course"]: {"rich_text": [{"text": {"content": course}}]},
    }
    if item.get("week") is not None:
        props[field_map["week"]] = {"number": item["week"]}
    return props


def _existing_titles(pages: List[Dict[str, Any]], title_prop: str) -> Dict[str, str]:
    """Map existing page title -> page_id (for incremental dedup)."""
    found: Dict[str, str] = {}
    for p in pages:
        try:
            title = "".join(t.get("plain_text", t.get("text", {}).get("content", ""))
                            for t in p["properties"][title_prop]["title"])
        except (KeyError, TypeError):
            continue
        if title:
            found[title] = p.get("id", "")
    return found


def sync_course(output_dir: Path, *, token: str, database_id: str, course: str = "",
               field_map: Optional[Dict[str, str]] = None, dry_run: bool = False,
               transport: Optional[Transport] = None) -> Dict[str, Any]:
    """Sync transcripts into a Notion database. Incremental: existing titles are
    updated, new ones created; nothing duplicated."""
    fmap = {**DEFAULT_FIELD_MAP, **(field_map or {})}
    items = [it for it in search.build_index(output_dir) if it["type"] == "transcript"]
    client = NotionClient(token, transport=transport)
    existing = _existing_titles(client.query_database(database_id), fmap["title"])

    to_create = [it for it in items if it["title"] not in existing]
    to_update = [it for it in items if it["title"] in existing]
    plan = {"create": [it["title"] for it in to_create],
            "update": [it["title"] for it in to_update]}
    if dry_run:
        return {"dry_run": True, "plan": plan,
                "summary": {"create": len(to_create), "update": len(to_update)}}

    created = updated = 0
    for it in to_create:
        client.create_page(database_id, _props_for(it, course, fmap))
        created += 1
    for it in to_update:
        client.update_page(existing[it["title"]], _props_for(it, course, fmap))
        updated += 1
    return {"dry_run": False, "created": created, "updated": updated, "plan": plan}
