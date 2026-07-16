"""
database.py - SQLite persistence layer (§1 of the roadmap).

This is the single place that owns SQL. Feature modules (`courses.py`,
`settings_store.py`, `jobs.py`) and routes call the DAO methods here; they never
embed raw SQL. A single connection guarded by an ``RLock`` is plenty for a
single-user local app and keeps the worker threads + request threads honest.

Design notes
------------
* WAL journal mode + ``foreign_keys=ON`` (with ``ON DELETE CASCADE`` so deleting
  a course wipes its rows).
* Migrations are ordered, idempotent steps keyed by an integer ``schema_version``
  row in ``settings``. Never mutate a shipped migration - append a new one.
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
# greater than the DB's current schema_version. Append-only - editing a shipped
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
            # §3 - job reliability: cooperative cancel, persisted logs, classified failures.
            "ALTER TABLE jobs ADD COLUMN logs TEXT DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN failure_category TEXT DEFAULT ''",
            # §2 - persisted saved views.
            """CREATE TABLE IF NOT EXISTS saved_views (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id  INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                query_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )""",
            # §6 - spaced-repetition review items + quiz attempt history.
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
    (
        3,
        [
            # §10 - audit trail for anything that leaves the machine (sync/cloud).
            """CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                action     TEXT NOT NULL,
                target     TEXT DEFAULT '',
                detail     TEXT DEFAULT '',
                label      TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)",
        ],
    ),
    (
        4,
        [
            "ALTER TABLE jobs ADD COLUMN started_at TEXT",
        ],
    ),
    (
        5,
        [
            # v3 - per-lecture notes & timestamped bookmarks.
            """CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                path        TEXT NOT NULL DEFAULT '',
                body        TEXT NOT NULL DEFAULT '',
                timestamp_s REAL,
                bookmark    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path)",
            "CREATE INDEX IF NOT EXISTS idx_notes_course ON notes(course_id)",
            # v3 - user-defined tags on library items (distinct from inferred tags).
            """CREATE TABLE IF NOT EXISTS tags (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT NOT NULL,
                color TEXT DEFAULT '',
                UNIQUE(name)
            )""",
            """CREATE TABLE IF NOT EXISTS item_tags (
                tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                path       TEXT NOT NULL,
                course_id  INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tag_id, path)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_item_tags_path ON item_tags(path)",
        ],
    ),
    (
        6,
        [
            """CREATE TABLE IF NOT EXISTS paper_outlines (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_code   TEXT NOT NULL,
                title        TEXT DEFAULT '',
                outline_json TEXT NOT NULL DEFAULT '{}',
                fetched_at   TEXT NOT NULL,
                UNIQUE(paper_code)
            )""",
            """CREATE TABLE IF NOT EXISTS class_schedules (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name          TEXT NOT NULL DEFAULT '',
                source_path   TEXT DEFAULT '',
                schedule_json TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS task_schedules (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id         INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name              TEXT NOT NULL DEFAULT '',
                schedule_json     TEXT NOT NULL DEFAULT '{}',
                paper_codes       TEXT DEFAULT '',
                class_schedule_id INTEGER REFERENCES class_schedules(id) ON DELETE SET NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS moodle_announcements (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id        INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                moodle_course_id TEXT DEFAULT '',
                title            TEXT NOT NULL DEFAULT '',
                body             TEXT DEFAULT '',
                author           TEXT DEFAULT '',
                posted_at        TEXT DEFAULT '',
                source_url       TEXT DEFAULT '',
                fetched_at       TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_class_sched_course ON class_schedules(course_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_sched_course ON task_schedules(course_id)",
            "CREATE INDEX IF NOT EXISTS idx_moodle_ann_course ON moodle_announcements(course_id)",
        ],
    ),
    (
        7,
        [
            # afterhours: notes library folders, session attach, flashcard sets,
            # assessment kinds, and essay-grade history.
            """CREATE TABLE IF NOT EXISTS note_folders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                parent_id   INTEGER REFERENCES note_folders(id) ON DELETE CASCADE,
                created_at  TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_note_folders_course ON note_folders(course_id)",
            "ALTER TABLE notes ADD COLUMN title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE notes ADD COLUMN folder_id INTEGER REFERENCES note_folders(id) ON DELETE SET NULL",
            "ALTER TABLE notes ADD COLUMN session_type TEXT NOT NULL DEFAULT ''",
            """CREATE TABLE IF NOT EXISTS flashcard_sets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                name          TEXT NOT NULL DEFAULT '',
                source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                source_path   TEXT DEFAULT '',
                card_count    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_flashcard_sets_course ON flashcard_sets(course_id)",
            "ALTER TABLE assessments ADD COLUMN kind TEXT NOT NULL DEFAULT 'assignment'",
            "ALTER TABLE assessments ADD COLUMN week INTEGER",
            """CREATE TABLE IF NOT EXISTS essay_grades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id    INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                title        TEXT NOT NULL DEFAULT '',
                essay_text   TEXT NOT NULL DEFAULT '',
                rubric_text  TEXT NOT NULL DEFAULT '',
                result_json  TEXT NOT NULL DEFAULT '{}',
                score        REAL,
                originality  REAL,
                created_at   TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_essay_grades_course ON essay_grades(course_id)",
            "CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id)",
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
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.query_one("SELECT id FROM documents WHERE path=?", (path,))
        return int(row["id"]) if row else 0

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
                   "failure_category", "updated_at", "started_at"}
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
                          weight: Optional[float] = None, status: str = "not_started",
                          *, kind: str = "assignment",
                          week: Optional[int] = None) -> int:
        cur = self.execute(
            "INSERT INTO assessments(course_id, name, due_date, weight, status, kind, week) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (course_id, name, due_date, weight, status, kind or "assignment", week))
        return int(cur.lastrowid)

    def list_assessments(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM assessments ORDER BY due_date")
        return self.query("SELECT * FROM assessments WHERE course_id=? ORDER BY due_date",
                          (course_id,))

    def get_assessment(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM assessments WHERE id=?", (id,))

    def update_assessment(self, id: int, **fields: Any) -> bool:
        allowed = {"name", "due_date", "weight", "status", "kind", "week"}
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

    # -- exports DAO (§9/§13) ----------------------------------------------

    def list_exports(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM exports ORDER BY created_at DESC")
        return self.query("SELECT * FROM exports WHERE course_id=? ORDER BY created_at DESC",
                          (course_id,))

    # -- audit log DAO (§10) -----------------------------------------------

    def add_audit(self, action: str, target: str = "", detail: str = "",
                 label: str = "") -> int:
        cur = self.execute(
            "INSERT INTO audit_log(action, target, detail, label, created_at) "
            "VALUES(?, ?, ?, ?, ?)", (action, target, detail, label, now_iso()))
        return int(cur.lastrowid)

    def list_audit(self, limit: int = 200) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                          (limit,))

    def clear_audit(self) -> int:
        return self.execute("DELETE FROM audit_log").rowcount

    # -- notes & bookmarks DAO (v3 / afterhours) ---------------------------

    def add_note(self, path: str, body: str, *, course_id: Optional[int] = None,
                 timestamp_s: Optional[float] = None, bookmark: bool = False,
                 title: str = "", folder_id: Optional[int] = None,
                 session_type: str = "") -> int:
        ts = now_iso()
        cur = self.execute(
            "INSERT INTO notes(course_id, path, body, timestamp_s, bookmark, "
            "title, folder_id, session_type, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, path or "", body, timestamp_s, 1 if bookmark else 0,
             title or "", folder_id, session_type or "", ts, ts))
        return int(cur.lastrowid)

    def list_notes(self, path: Optional[str] = None,
                   course_id: Optional[int] = None, *,
                   folder_id: Optional[int] = None,
                   session_type: Optional[str] = None) -> List[sqlite3.Row]:
        clauses, params = [], []
        if path is not None:
            clauses.append("path=?"); params.append(path)
        if course_id is not None:
            clauses.append("course_id=?"); params.append(course_id)
        if folder_id is not None:
            clauses.append("folder_id=?"); params.append(folder_id)
        if session_type is not None:
            clauses.append("session_type=?"); params.append(session_type)
        sql = "SELECT * FROM notes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # bookmarks (which carry a timestamp) sort by position; plain notes by recency.
        sql += " ORDER BY bookmark DESC, timestamp_s IS NULL, timestamp_s, created_at DESC"
        return self.query(sql, tuple(params))

    def get_note(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM notes WHERE id=?", (id,))

    def update_note(self, id: int, **fields: Any) -> bool:
        allowed = {"body", "timestamp_s", "bookmark", "title", "folder_id",
                   "session_type", "path"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        if "bookmark" in sets:
            sets["bookmark"] = 1 if sets["bookmark"] else 0
        sets["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in sets)
        return self.execute(f"UPDATE notes SET {cols} WHERE id=?",
                            (*sets.values(), id)).rowcount > 0

    def delete_note(self, id: int) -> bool:
        return self.execute("DELETE FROM notes WHERE id=?", (id,)).rowcount > 0

    def count_notes(self, course_id: Optional[int] = None) -> int:
        if course_id is None:
            return int(self.query_one("SELECT COUNT(*) AS n FROM notes")["n"])
        return int(self.query_one(
            "SELECT COUNT(*) AS n FROM notes WHERE course_id=?", (course_id,))["n"])

    # -- note folders -------------------------------------------------------

    def create_note_folder(self, course_id: Optional[int], name: str,
                           parent_id: Optional[int] = None) -> int:
        cur = self.execute(
            "INSERT INTO note_folders(course_id, name, parent_id, created_at) "
            "VALUES(?, ?, ?, ?)",
            (course_id, (name or "").strip(), parent_id, now_iso()))
        return int(cur.lastrowid)

    def list_note_folders(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM note_folders ORDER BY name COLLATE NOCASE")
        return self.query(
            "SELECT * FROM note_folders WHERE course_id=? ORDER BY name COLLATE NOCASE",
            (course_id,))

    def get_note_folder(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM note_folders WHERE id=?", (id,))

    def rename_note_folder(self, id: int, name: str) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        return self.execute("UPDATE note_folders SET name=? WHERE id=?",
                            (name, id)).rowcount > 0

    def delete_note_folder(self, id: int) -> bool:
        # Detach notes first so ON DELETE SET NULL is explicit even on older SQLite.
        self.execute("UPDATE notes SET folder_id=NULL WHERE folder_id=?", (id,))
        return self.execute("DELETE FROM note_folders WHERE id=?", (id,)).rowcount > 0

    # -- flashcard sets -----------------------------------------------------

    def create_flashcard_set(self, course_id: Optional[int], name: str, *,
                             source_note_id: Optional[int] = None,
                             source_path: str = "",
                             card_count: int = 0) -> int:
        ts = now_iso()
        cur = self.execute(
            "INSERT INTO flashcard_sets(course_id, name, source_note_id, source_path, "
            "card_count, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (course_id, (name or "").strip() or "Untitled set", source_note_id,
             source_path or "", card_count, ts, ts))
        return int(cur.lastrowid)

    def list_flashcard_sets(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM flashcard_sets ORDER BY updated_at DESC")
        return self.query(
            "SELECT * FROM flashcard_sets WHERE course_id=? ORDER BY updated_at DESC",
            (course_id,))

    def get_flashcard_set(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM flashcard_sets WHERE id=?", (id,))

    def update_flashcard_set(self, id: int, **fields: Any) -> bool:
        allowed = {"name", "card_count", "source_note_id", "source_path"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        sets["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in sets)
        return self.execute(f"UPDATE flashcard_sets SET {cols} WHERE id=?",
                            (*sets.values(), id)).rowcount > 0

    def delete_flashcard_set(self, id: int) -> bool:
        return self.execute("DELETE FROM flashcard_sets WHERE id=?", (id,)).rowcount > 0

    # -- essay grades -------------------------------------------------------

    def add_essay_grade(self, course_id: Optional[int], title: str, essay_text: str,
                        rubric_text: str, result_json: str, *,
                        score: Optional[float] = None,
                        originality: Optional[float] = None) -> int:
        cur = self.execute(
            "INSERT INTO essay_grades(course_id, title, essay_text, rubric_text, "
            "result_json, score, originality, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, title or "", essay_text or "", rubric_text or "",
             result_json or "{}", score, originality, now_iso()))
        return int(cur.lastrowid)

    def list_essay_grades(self, course_id: Optional[int] = None,
                          limit: int = 50) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query(
                "SELECT * FROM essay_grades ORDER BY created_at DESC LIMIT ?", (limit,))
        return self.query(
            "SELECT * FROM essay_grades WHERE course_id=? ORDER BY created_at DESC LIMIT ?",
            (course_id, limit))

    def get_essay_grade(self, id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM essay_grades WHERE id=?", (id,))

    # -- tags DAO (v3) ------------------------------------------------------

    def get_or_create_tag(self, name: str, color: str = "") -> int:
        name = (name or "").strip()
        row = self.query_one("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,))
        if row:
            return int(row["id"])
        cur = self.execute("INSERT INTO tags(name, color) VALUES(?, ?)", (name, color))
        return int(cur.lastrowid)

    def list_tags(self) -> List[sqlite3.Row]:
        # name + how many items carry it, busiest first.
        return self.query(
            "SELECT t.id, t.name, t.color, COUNT(it.path) AS n "
            "FROM tags t LEFT JOIN item_tags it ON it.tag_id = t.id "
            "GROUP BY t.id ORDER BY n DESC, t.name COLLATE NOCASE")

    def add_item_tag(self, path: str, name: str,
                     course_id: Optional[int] = None) -> None:
        tag_id = self.get_or_create_tag(name)
        self.execute(
            "INSERT OR IGNORE INTO item_tags(tag_id, path, course_id, created_at) "
            "VALUES(?, ?, ?, ?)", (tag_id, path, course_id, now_iso()))

    def remove_item_tag(self, path: str, name: str) -> bool:
        row = self.query_one("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,))
        if not row:
            return False
        return self.execute("DELETE FROM item_tags WHERE tag_id=? AND path=?",
                            (int(row["id"]), path)).rowcount > 0

    def tags_for_path(self, path: str) -> List[str]:
        rows = self.query(
            "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
            "WHERE it.path=? ORDER BY t.name COLLATE NOCASE", (path,))
        return [r["name"] for r in rows]

    def paths_for_tag(self, name: str) -> List[str]:
        rows = self.query(
            "SELECT it.path FROM item_tags it JOIN tags t ON t.id = it.tag_id "
            "WHERE t.name=? COLLATE NOCASE", (name,))
        return [r["path"] for r in rows]

    def all_item_tags(self) -> Dict[str, List[str]]:
        """path -> [tag names], for bulk decoration of a library listing."""
        rows = self.query(
            "SELECT it.path AS path, t.name AS name FROM item_tags it "
            "JOIN tags t ON t.id = it.tag_id ORDER BY t.name COLLATE NOCASE")
        out: Dict[str, List[str]] = {}
        for r in rows:
            out.setdefault(r["path"], []).append(r["name"])
        return out

    def prune_unused_tags(self) -> int:
        return self.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM item_tags)"
        ).rowcount

    # -- semester planner DAO -----------------------------------------------

    def upsert_paper_outline(self, paper_code: str, outline_json: str,
                             title: str = "") -> int:
        ts = now_iso()
        cur = self.execute(
            "INSERT INTO paper_outlines(paper_code, title, outline_json, fetched_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(paper_code) DO UPDATE SET "
            "title=excluded.title, outline_json=excluded.outline_json, "
            "fetched_at=excluded.fetched_at",
            (paper_code, title, outline_json, ts),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.query_one("SELECT id FROM paper_outlines WHERE paper_code=?",
                             (paper_code,))
        return int(row["id"]) if row else 0

    def get_paper_outline(self, paper_code: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM paper_outlines WHERE paper_code=?",
                              (paper_code,))

    def list_paper_outlines(self) -> List[sqlite3.Row]:
        return self.query("SELECT * FROM paper_outlines ORDER BY paper_code")

    def create_class_schedule(self, course_id: int, name: str, schedule_json: str,
                              source_path: str = "") -> int:
        cur = self.execute(
            "INSERT INTO class_schedules(course_id, name, source_path, schedule_json, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (course_id, name, source_path, schedule_json, now_iso()),
        )
        return int(cur.lastrowid)

    def get_class_schedule(self, schedule_id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM class_schedules WHERE id=?", (schedule_id,))

    def list_class_schedules(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM class_schedules ORDER BY created_at DESC")
        return self.query(
            "SELECT * FROM class_schedules WHERE course_id=? ORDER BY created_at DESC",
            (course_id,))

    def create_task_schedule(self, course_id: int, name: str, schedule_json: str,
                             paper_codes: str = "",
                             class_schedule_id: Optional[int] = None) -> int:
        if not course_id or self.get_course(int(course_id)) is None:
            raise ValueError(
                f"Invalid course_id {course_id!r}: create or activate a course first "
                "(FOREIGN KEY requires a real courses row)."
            )
        # Stale/wrong class_schedule_id must not trip the FK — drop the link.
        if class_schedule_id is not None:
            row = self.get_class_schedule(int(class_schedule_id))
            if row is None:
                class_schedule_id = None
            elif row["course_id"] is not None and int(row["course_id"]) != int(course_id):
                class_schedule_id = None
        ts = now_iso()
        cur = self.execute(
            "INSERT INTO task_schedules(course_id, name, schedule_json, paper_codes, "
            "class_schedule_id, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (int(course_id), name, schedule_json, paper_codes, class_schedule_id, ts, ts),
        )
        return int(cur.lastrowid)

    def get_task_schedule(self, schedule_id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM task_schedules WHERE id=?", (schedule_id,))

    def list_task_schedules(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query("SELECT * FROM task_schedules ORDER BY updated_at DESC")
        return self.query(
            "SELECT * FROM task_schedules WHERE course_id=? ORDER BY updated_at DESC",
            (course_id,))

    def replace_moodle_announcements(self, course_id: int, moodle_course_id: str,
                                     rows: List[Dict[str, Any]]) -> int:
        self.execute("DELETE FROM moodle_announcements WHERE course_id=? AND moodle_course_id=?",
                     (course_id, moodle_course_id))
        n = 0
        for r in rows:
            self.execute(
                "INSERT INTO moodle_announcements(course_id, moodle_course_id, title, body, "
                "author, posted_at, source_url, fetched_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (course_id, moodle_course_id, r.get("title", ""), r.get("body", ""),
                 r.get("author", ""), r.get("posted_at", ""), r.get("source_url", ""),
                 r.get("fetched_at", now_iso())),
            )
            n += 1
        return n

    def list_moodle_announcements(self, course_id: Optional[int] = None) -> List[sqlite3.Row]:
        if course_id is None:
            return self.query(
                "SELECT * FROM moodle_announcements ORDER BY posted_at DESC, fetched_at DESC")
        return self.query(
            "SELECT * FROM moodle_announcements WHERE course_id=? "
            "ORDER BY posted_at DESC, fetched_at DESC",
            (course_id,))


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
