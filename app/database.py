"""
database.py — SQLite persistence layer (§1 of the roadmap).

This is the single place that owns SQL. Feature modules (`courses.py`,
`settings_store.py`, `jobs.py`) and routes call the DAO methods here; they never
embed raw SQL. A single connection guarded by an ``RLock`` is plenty for a
single-user local app and keeps the worker threads + request threads honest.

Design notes
------------
* WAL journal mode + ``foreign_keys=ON`` (with ``ON DELETE CASCADE`` so deleting
  a course wipes its rows).
* Migrations are ordered, idempotent steps keyed by an integer ``schema_version``
  row in ``settings``. Never mutate a shipped migration — append a new one.
* ``init(db_path)`` installs a module-level default used by routes via
  ``get_db()``. Re-calling ``init`` (the test suite reloads ``app.main`` against a
  fresh temp dir) closes the previous connection first, so file handles are
  released on Windows.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import now_iso

# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
# Each entry: (version, [sql statements]). Applied in order for any version
# greater than the DB's current schema_version. Append-only — editing a shipped
# step breaks existing databases; add a new (version, steps) instead.

_MIGRATIONS: List[tuple[int, List[str]]] = [
    (
        1,
        [
            """CREATE TABLE IF NOT EXISTS courses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                code        TEXT DEFAULT '',
                semester    TEXT DEFAULT '',
                year        INTEGER,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                archived    INTEGER NOT NULL DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS documents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                title         TEXT NOT NULL DEFAULT '',
                path          TEXT NOT NULL,
                type          TEXT DEFAULT '',
                import_source TEXT DEFAULT '',
                created_at    TEXT NOT NULL,
                UNIQUE(path)
            )""",
            """CREATE TABLE IF NOT EXISTS transcripts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                document_id   INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                title         TEXT NOT NULL DEFAULT '',
                week          INTEGER,
                topic         TEXT DEFAULT '',
                date          TEXT DEFAULT '',
                path          TEXT NOT NULL,
                duration      INTEGER,
                metadata_json TEXT DEFAULT '',
                created_at    TEXT NOT NULL,
                UNIQUE(path)
            )""",
            """CREATE TABLE IF NOT EXISTS jobs (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL DEFAULT 'job',
                title        TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'queued',
                stage        TEXT DEFAULT '',
                progress     REAL NOT NULL DEFAULT 0,
                payload_json TEXT DEFAULT '',
                result_json  TEXT DEFAULT '',
                error        TEXT DEFAULT '',
                attempts     INTEGER NOT NULL DEFAULT 0,
                course_id    INTEGER REFERENCES courses(id) ON DELETE SET NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS exports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id  INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                type       TEXT DEFAULT '',
                path       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS assessments (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name      TEXT NOT NULL DEFAULT '',
                due_date  TEXT DEFAULT '',
                weight    REAL,
                status    TEXT DEFAULT 'not_started'
            )""",
            """CREATE TABLE IF NOT EXISTS study_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                started_at    TEXT NOT NULL,
                duration      INTEGER,
                activity_type TEXT DEFAULT ''
            )""",
            "CREATE INDEX IF NOT EXISTS idx_documents_course ON documents(course_id)",
            "CREATE INDEX IF NOT EXISTS idx_transcripts_course ON transcripts(course_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
        ],
    ),
]

SCHEMA_VERSION = _MIGRATIONS[-1][0]


class Database:
    """A thread-safe SQLite wrapper that owns the schema and the DAOs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.root = self.path.parent          # the OUTPUT_DIR; used for course folders
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- low-level helpers --------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # -- migrations ---------------------------------------------------------

    def migrate(self) -> None:
        """Apply any migrations newer than the stored ``schema_version``."""
        with self._lock:
            # settings must exist before we can read/write schema_version.
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            ).fetchone()
            current = int(row["value"]) if row else 0
            for version, steps in _MIGRATIONS:
                if version <= current:
                    continue
                for sql in steps:
                    self._conn.execute(sql)
                self._conn.execute(
                    "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(version),),
                )
            self._conn.commit()

    def schema_version(self) -> int:
        row = self.query_one("SELECT value FROM settings WHERE key='schema_version'")
        return int(row["value"]) if row else 0

    # -- settings DAO -------------------------------------------------------

    def get_setting(self, key: str) -> Optional[str]:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM settings WHERE key=?", (key,))

    def all_settings(self) -> Dict[str, str]:
        rows = self.query("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    # -- courses DAO --------------------------------------------------------

    def create_course(self, name: str, code: str = "", semester: str = "",
                      year: Optional[int] = None) -> int:
        ts = now_iso()
        cur = self.execute(
            "INSERT INTO courses(name, code, semester, year, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (name, code, semester, year, ts, ts),
        )
        return int(cur.lastrowid)

    def get_course(self, course_id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM courses WHERE id=?", (course_id,))

    def list_courses(self, include_archived: bool = True) -> List[sqlite3.Row]:
        if include_archived:
            return self.query("SELECT * FROM courses ORDER BY archived, name COLLATE NOCASE")
        return self.query(
            "SELECT * FROM courses WHERE archived=0 ORDER BY name COLLATE NOCASE"
        )

    def update_course(self, course_id: int, **fields: Any) -> bool:
        allowed = {"name", "code", "semester", "year", "archived"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return False
        sets["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = self.execute(
            f"UPDATE courses SET {cols} WHERE id=?", (*sets.values(), course_id)
        )
        return cur.rowcount > 0

    def delete_course(self, course_id: int) -> bool:
        cur = self.execute("DELETE FROM courses WHERE id=?", (course_id,))
        return cur.rowcount > 0

    def count_courses(self) -> int:
        return int(self.query_one("SELECT COUNT(*) AS n FROM courses")["n"])

    # -- documents / transcripts DAO ---------------------------------------

    def insert_document(self, course_id: Optional[int], title: str, path: str,
                       type: str = "", import_source: str = "") -> int:
        cur = self.execute(
            "INSERT OR IGNORE INTO documents(course_id, title, path, type, import_source, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (course_id, title, path, type, import_source, now_iso()),
        )
        return int(cur.lastrowid)

    def list_documents(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM documents ORDER BY title COLLATE NOCASE")
        return self.query(
            "SELECT * FROM documents WHERE course_id=? ORDER BY title COLLATE NOCASE",
            (course_id,),
        )

    def count_documents(self, course_id: Optional[int] = None) -> int:
        if course_id is None:
            return int(self.query_one("SELECT COUNT(*) AS n FROM documents")["n"])
        return int(
            self.query_one("SELECT COUNT(*) AS n FROM documents WHERE course_id=?",
                           (course_id,))["n"]
        )

    def insert_transcript(self, course_id: Optional[int], title: str, path: str,
                         week: Optional[int] = None, topic: str = "", date: str = "",
                         duration: Optional[int] = None, metadata_json: str = "",
                         document_id: Optional[int] = None) -> int:
        cur = self.execute(
            "INSERT OR IGNORE INTO transcripts(course_id, document_id, title, week, topic, "
            "date, path, duration, metadata_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, document_id, title, week, topic, date, path, duration,
             metadata_json, now_iso()),
        )
        return int(cur.lastrowid)

    def list_transcripts(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM transcripts ORDER BY week, title COLLATE NOCASE")
        return self.query(
            "SELECT * FROM transcripts WHERE course_id=? ORDER BY week, title COLLATE NOCASE",
            (course_id,),
        )

    def count_transcripts(self, course_id: Optional[int] = None) -> int:
        if course_id is None:
            return int(self.query_one("SELECT COUNT(*) AS n FROM transcripts")["n"])
        return int(
            self.query_one("SELECT COUNT(*) AS n FROM transcripts WHERE course_id=?",
                           (course_id,))["n"]
        )

    # -- jobs DAO -----------------------------------------------------------

    def insert_job(self, id: str, type: str, title: str, status: str, stage: str,
                  progress: float, payload_json: str, course_id: Optional[int],
                  created_at: str, updated_at: str) -> None:
        self.execute(
            "INSERT INTO jobs(id, type, title, status, stage, progress, payload_json, "
            "course_id, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, type, title, status, stage, progress, payload_json, course_id,
             created_at, updated_at),
        )

    def update_job(self, id: str, **fields: Any) -> None:
        allowed = {"status", "stage", "progress", "result_json", "error", "attempts",
                   "updated_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sets.setdefault("updated_at", now_iso())
        cols = ", ".join(f"{k}=?" for k in sets)
        self.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*sets.values(), id))

    def get_job(self, id: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM jobs WHERE id=?", (id,))

    def list_jobs(self) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM jobs ORDER BY created_at DESC")

    def recover_running_jobs(self) -> int:
        """Mark jobs left ``running``/``queued`` by a crash as ``interrupted`` so a
        restart presents a known state the user can resume (§1 minimum; §3 hardens)."""
        cur = self.execute(
            "UPDATE jobs SET status='interrupted', updated_at=? "
            "WHERE status IN ('running', 'queued')",
            (now_iso(),),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Module-level default instance (used by routes via get_db()).
# ---------------------------------------------------------------------------

_default: Optional[Database] = None


def init(db_path: str | Path) -> Database:
    """(Re)create the default database at ``db_path`` and return it.

    Closes any previous default first so reloading the app against a new output
    directory (the test pattern) doesn't leak connections / file locks.
    """
    global _default
    if _default is not None:
        _default.close()
    _default = Database(db_path)
    return _default


def get_db() -> Optional[Database]:
    return _default
