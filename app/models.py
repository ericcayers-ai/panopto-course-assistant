"""
models.py - light dataclasses mapping SQLite rows to/from dicts (§1).

These keep the API layer honest (one place that knows a row's shape) without
pulling in a heavier ORM. ``from_row`` accepts a ``sqlite3.Row``; ``to_dict``
produces the JSON the frontend consumes.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class Course:
    id: int
    name: str
    code: str = ""
    semester: str = ""
    year: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Course":
        return cls(
            id=row["id"],
            name=row["name"],
            code=row["code"] or "",
            semester=row["semester"] or "",
            year=row["year"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived=bool(row["archived"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["archived"] = bool(self.archived)
        return d


@dataclass
class Document:
    id: int
    course_id: Optional[int]
    title: str
    path: str
    type: str = ""
    import_source: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Document":
        return cls(
            id=row["id"], course_id=row["course_id"], title=row["title"],
            path=row["path"], type=row["type"] or "",
            import_source=row["import_source"] or "", created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    id: int
    course_id: Optional[int]
    title: str
    path: str
    week: Optional[int] = None
    topic: str = ""
    date: str = ""
    duration: Optional[int] = None
    document_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Transcript":
        meta = None
        raw = row["metadata_json"]
        if raw:
            try:
                meta = json.loads(raw)
            except Exception:
                meta = None
        return cls(
            id=row["id"], course_id=row["course_id"], title=row["title"],
            path=row["path"], week=row["week"], topic=row["topic"] or "",
            date=row["date"] or "", duration=row["duration"],
            document_id=row["document_id"], metadata=meta,
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
