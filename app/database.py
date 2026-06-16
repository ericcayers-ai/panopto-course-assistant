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
    (
        2,
        [
            # §3 — job reliability: cooperative cancel, persisted logs, classified failures.
            "ALTER TABLE jobs ADD COLUMN logs TEXT DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN failure_category TEXT DEFAULT ''",
            # §2 — persisted saved views.
            """CREATE TABLE IF NOT EXISTS saved_views (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id  INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                query_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )""",
            # §6 — spaced-repetition review items + quiz attempt history.
            """CREATE TABLE IF NOT EXISTS review_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id  INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                ref        TEXT DEFAULT '',
                front      TEXT NOT NULL,
                back       TEXT NOT NULL DEFAULT '',
                interval   INTEGER NOT NULL DEFAULT 1,
                due        TEXT NOT NULL,
                ease       REAL NOT NULL DEFAULT 2.5,
                reps       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS quiz_attempts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                scope     TEXT DEFAULT '',
                score     REAL NOT NULL DEFAULT 0,
                total     INTEGER NOT NULL DEFAULT 0,
                mode      TEXT DEFAULT '',
                taken_at  TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_review_due ON review_items(due)",
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
                   "failure_category", "updated_at"}
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

    # -- jobs DAO: reliability (§3) ----------------------------------------

    def append_job_log(self, id: str, line: str) -> None:
        row = self.query_one("SELECT logs FROM jobs WHERE id=?", (id,))
        if row is None:
            return
        stamped = f"[{now_iso()}] {line}"
        existing = row["logs"] or ""
        self.execute(
            "UPDATE jobs SET logs=? WHERE id=?",
            ((existing + "\n" + stamped) if existing else stamped, id),
        )

    def request_cancel(self, id: str) -> None:
        self.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                     (now_iso(), id))

    def cancel_requested(self, id: str) -> bool:
        row = self.query_one("SELECT cancel_requested FROM jobs WHERE id=?", (id,))
        return bool(row and row["cancel_requested"])

    def list_jobs_by_status(self, status: str) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC",
                          (status,))

    # -- assessments DAO (§6) ----------------------------------------------

    def create_assessment(self, course_id: int, name: str, due_date: str = "",
                          weight: Optional[float] = None, status: str = "not_started") -> int:
        cur = self.execute(
            "INSERT INTO assessments(course_id, name, due_date, weight, status) "
            "VALUES(?, ?, ?, ?, ?)", (course_id, name, due_date, weight, status))
        return int(cur.lastrowid)

    def list_assessments(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM assessments ORDER BY due_date")
        return self.query("SELECT * FROM assessments WHERE course_id=? ORDER BY due_date",
                          (course_id,))

    def get_assessment(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM assessments WHERE id=?", (id,))

    def update_assessment(self, id: int, **fields: Any) -> bool:
        allowed = {"name", "due_date", "weight", "status"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return False
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = self.execute(f"UPDATE assessments SET {cols} WHERE id=?",
                           (*sets.values(), id))
        return cur.rowcount > 0

    def delete_assessment(self, id: int) -> bool:
        return self.execute("DELETE FROM assessments WHERE id=?", (id,)).rowcount > 0

    # -- study sessions DAO (§6) -------------------------------------------

    def log_study_session(self, course_id: int, started_at: str, duration: int,
                         activity_type: str = "") -> int:
        cur = self.execute(
            "INSERT INTO study_sessions(course_id, started_at, duration, activity_type) "
            "VALUES(?, ?, ?, ?)", (course_id, started_at, duration, activity_type))
        return int(cur.lastrowid)

    def list_study_sessions(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM study_sessions ORDER BY started_at DESC")
        return self.query(
            "SELECT * FROM study_sessions WHERE course_id=? ORDER BY started_at DESC",
            (course_id,))

    # -- review items DAO (§6 spaced repetition) ---------------------------

    def add_review_item(self, course_id: int, front: str, back: str, due: str,
                       ref: str = "", interval: int = 1, ease: float = 2.5) -> int:
        cur = self.execute(
            "INSERT INTO review_items(course_id, ref, front, back, interval, due, ease, "
            "reps, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (course_id, ref, front, back, interval, due, ease, now_iso()))
        return int(cur.lastrowid)

    def list_review_items(self, course_id: Optional[int] = None,
                         due_before: Optional[str] = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM review_items"
        clauses, params = [], []
        if course_id is not None:
            clauses.append("course_id=?"); params.append(course_id)
        if due_before is not None:
            clauses.append("due<=?"); params.append(due_before)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY due"
        return self.query(sql, tuple(params))

    def update_review_item(self, id: int, **fields: Any) -> bool:
        allowed = {"interval", "due", "ease", "reps"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return False
        cols = ", ".join(f"{k}=?" for k in sets)
        return self.execute(f"UPDATE review_items SET {cols} WHERE id=?",
                            (*sets.values(), id)).rowcount > 0

    # -- quiz attempts DAO (§6) --------------------------------------------

    def record_quiz_attempt(self, course_id: int, scope: str, score: float,
                           total: int, mode: str = "") -> int:
        cur = self.execute(
            "INSERT INTO quiz_attempts(course_id, scope, score, total, mode, taken_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (course_id, scope, score, total, mode, now_iso()))
        return int(cur.lastrowid)

    def list_quiz_attempts(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM quiz_attempts ORDER BY taken_at DESC")
        return self.query(
            "SELECT * FROM quiz_attempts WHERE course_id=? ORDER BY taken_at DESC",
            (course_id,))

    # -- saved views DAO (§2) ----------------------------------------------

    def create_saved_view(self, name: str, query_json: str,
                          course_id: Optional[int] = None) -> int:
        cur = self.execute(
            "INSERT INTO saved_views(course_id, name, query_json, created_at) "
            "VALUES(?, ?, ?, ?)", (course_id, name, query_json, now_iso()))
        return int(cur.lastrowid)

    def list_saved_views(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM saved_views ORDER BY name COLLATE NOCASE")
        return self.query(
            "SELECT * FROM saved_views WHERE course_id IS NULL OR course_id=? "
            "ORDER BY name COLLATE NOCASE", (course_id,))

    def delete_saved_view(self, id: int) -> bool:
        return self.execute("DELETE FROM saved_views WHERE id=?", (id,)).rowcount > 0


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
