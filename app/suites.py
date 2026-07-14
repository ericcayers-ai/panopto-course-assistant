"""
suites.py - shared study-suite tree writer + format adapters + sync.

Canonical vault layout (shared across Obsidian / Notion / OneNote)::

    {suite-root}/
      README.md
      Study Plan.md
      Semester Gantt.md
      Calendar/
        semester.ics
        google-calendar.csv
      Study Timetable/
        Timetable Sheet - Markdown ver.md
      Guide/{PAPER} Gantt.md
      Outlines/{paper}.md
      Announcements/
      papers/{paper}.md
      tasks/{id}.md
      Library/          # mirrored transcripts/docs when available
      Forums/           # browser-path scrapes only
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import core, settings_store
from .database import Database

SUITE_FORMATS = ("obsidian", "notion", "onenote")
SUITE_FOLDERS = (
    "Calendar", "Study Timetable", "Guide", "Outlines",
    "Announcements", "papers", "tasks", "Library", "Forums",
)

SETTING_DESTINATIONS = "suite.destinations"
SETTING_ENABLED = "suite.enabled"
SETTING_AUTO_SYNC = "suite.auto_sync"


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", (text or "").strip()).strip("-").lower()


def collect_subjects(
    tasks: Sequence[Dict[str, Any]],
    outlines: Optional[Sequence[Dict[str, Any]]] = None,
    moodle_events: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    subjects: Set[str] = {
        (t.get("subject") or "").upper().split("-")[0]
        for t in tasks if t.get("subject")
    }
    subjects.update(
        (o.get("paper_code") or "").upper().split("-")[0]
        for o in (outlines or []) if o.get("paper_code")
    )
    subjects.update(
        (e.get("paper_code") or "").upper().split("-")[0]
        for e in (moodle_events or []) if e.get("paper_code")
    )
    return sorted(s for s in subjects if s)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_file(path: Path, content: str | bytes, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding=encoding)


def mirror_tree(src: Path, dest: Path) -> Dict[str, Any]:
    """Structure-preserving copy; skip files whose size+mtime (or hash) match."""
    src = Path(src)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    skipped: List[str] = []
    updated: List[str] = []
    if not src.is_dir():
        return {"copied": copied, "skipped": skipped, "updated": updated,
                "new_files": 0, "updated_files": 0}

    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        if target.exists() and target.is_file():
            same_meta = (
                target.stat().st_size == path.stat().st_size
                and int(target.stat().st_mtime) == int(path.stat().st_mtime)
            )
            if same_meta or _file_hash(target) == _file_hash(path):
                skipped.append(rel)
                continue
            shutil.copy2(path, target)
            updated.append(rel)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append(rel)
    return {
        "copied": copied,
        "skipped": skipped,
        "updated": updated,
        "new_files": len(copied),
        "updated_files": len(updated),
    }


def _ensure_folders(root: Path) -> None:
    for folder in SUITE_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)


def _build_shared_markdown(
    root: Path,
    *,
    title: str,
    tasks: List[Dict[str, Any]],
    outlines: List[Dict[str, Any]],
    moodle_events: Optional[List[Dict[str, Any]]],
    announcements: Optional[List[Dict[str, Any]]],
    link_style: str = "wikilink",
) -> List[str]:
    """Write the shared Markdown skeleton. Returns list of relative paths written."""
    from . import task_schedule

    written: List[str] = []
    subjects = collect_subjects(tasks, outlines, moodle_events)
    gantt = task_schedule.build_mermaid_gantt(
        tasks, outlines=outlines, moodle_events=moodle_events, title=title,
    )

    def link(target: str, label: Optional[str] = None) -> str:
        if link_style == "wikilink":
            return f"[[{target}|{label}]]" if label else f"[[{target}]]"
        # Markdown links for Notion/OneNote HTML conversion sources
        href = target if target.endswith(".md") else f"{target}.md"
        return f"[{label or target}]({href})"

    readme = [
        f"# {title}",
        "",
        "Semester plan exported from Course Assistant.",
        "",
        "## Quick links",
        "",
        f"- {link('Study Plan')}",
        f"- {link('Semester Gantt')}",
        f"- {link('Study Timetable/Timetable Sheet - Markdown ver')}",
        "",
        "## Papers",
        "",
    ]
    for s in subjects:
        readme.append(f"- {link(f'papers/{_slug(s)}', s)}")
    if outlines:
        readme += ["", "## Outlines", ""]
        for o in outlines:
            code = (o.get("paper_code") or "").split("-")[0]
            readme.append(f"- {link(f'Outlines/{_slug(code)}', code)}")
    if announcements:
        readme += ["", "## Announcements", ""]
        readme.append(f"- {link('Announcements/index', 'All announcements')}")
    write_file(root / "README.md", "\n".join(readme) + "\n")
    written.append("README.md")

    study_plan = [
        "---",
        "tags: [study-plan, semester]",
        "---",
        "",
        f"# {title} — Study plan",
        "",
        "## Timeline",
        "",
    ]
    for t in tasks:
        due = t.get("due_date") or "TBD"
        study_plan.append(
            f"- {due} · {link(f'tasks/{t['id']}', t.get('name', ''))} "
            f"({t.get('type', '')}{', ' + str(t['weight']) + '%' if t.get('weight') else ''})"
        )
    write_file(root / "Study Plan.md", "\n".join(study_plan) + "\n")
    written.append("Study Plan.md")

    gantt_body = [
        "---",
        "tags: [gantt, semester, reference]",
        "aliases: [Semester Timeline]",
        "---",
        "",
        f"# {title} — Gantt chart",
        "",
        "Visual timeline of assessments, classes, and key dates.",
        "",
        gantt.rstrip(),
        "",
    ]
    write_file(root / "Semester Gantt.md", "\n".join(gantt_body) + "\n")
    written.append("Semester Gantt.md")

    timetable = task_schedule._timetable_markdown(tasks)
    write_file(root / "Study Timetable" / "Timetable Sheet - Markdown ver.md", timetable)
    written.append("Study Timetable/Timetable Sheet - Markdown ver.md")

    ics = task_schedule.export_calendar_ics(
        tasks, outlines=outlines, title=title,
    )
    write_file(root / "Calendar" / "semester.ics", ics)
    written.append("Calendar/semester.ics")
    gcal = task_schedule.export_google_calendar_csv(tasks, outlines=outlines)
    write_file(root / "Calendar" / "google-calendar.csv", gcal)
    written.append("Calendar/google-calendar.csv")

    for s in subjects:
        paper_tasks = [t for t in tasks if t.get("subject") == s]
        lines = [
            "---",
            f"tags: [{s.lower()}, paper]",
            "---",
            "",
            f"# {s}",
            "",
            f"See also: {link('Semester Gantt')} · {link('Study Plan')}",
            "",
        ]
        for t in paper_tasks:
            lines.append(
                f"- {link(f'tasks/{t['id']}', t.get('name', ''))} ({t.get('due_date', 'TBD')})"
            )
        write_file(root / "papers" / f"{_slug(s)}.md", "\n".join(lines) + "\n")
        written.append(f"papers/{_slug(s)}.md")

        paper_events = [
            e for e in (moodle_events or [])
            if (e.get("paper_code") or "").upper() == s
        ]
        paper_gantt = task_schedule.build_mermaid_gantt(
            [t for t in tasks if t.get("subject") == s],
            outlines=[o for o in outlines if (o.get("paper_code") or "").startswith(s)],
            moodle_events=paper_events,
            title=f"{s} — {title}",
        )
        pg = [
            "---",
            f"tags: [{s.lower()}, gantt]",
            "---",
            "",
            f"# {s} Gantt",
            "",
            paper_gantt.rstrip(),
            "",
        ]
        write_file(root / "Guide" / f"{s} Gantt.md", "\n".join(pg) + "\n")
        written.append(f"Guide/{s} Gantt.md")

    for o in outlines:
        code = (o.get("paper_code") or "outline").split("-")[0]
        write_file(root / "Outlines" / f"{_slug(code)}.md", task_schedule._outline_note(o))
        written.append(f"Outlines/{_slug(code)}.md")

    if announcements:
        ann_index = ["# Moodle announcements", ""]
        for a in announcements:
            slug, body = task_schedule._announcement_note(a)
            ann_index.append(f"- {link(f'Announcements/{slug}', a.get('title', 'Post'))}")
            write_file(root / "Announcements" / f"{slug}.md", body)
            written.append(f"Announcements/{slug}.md")
        write_file(root / "Announcements" / "index.md", "\n".join(ann_index) + "\n")
        written.append("Announcements/index.md")

    for t in tasks:
        subj = t.get("subject", "")
        typ = t.get("type", "")
        fm_tags = ["task"]
        if subj:
            fm_tags.append(subj.lower())
        if typ:
            fm_tags.append(_slug(typ))
        body = [
            "---",
            f"tags: [{', '.join(fm_tags)}]",
            "---",
            "",
            f"# {t.get('name', 'Task')}",
            "",
            f"- Paper: {link(f'papers/{_slug(subj)}', subj)}",
            f"- Type: {typ}",
            f"- Due: {t.get('due_date', '')}",
            f"- Weight: {t.get('weight', '')}%" if t.get("weight") is not None else "- Weight:",
            f"- Status: {t.get('status', '')}",
            f"- Source: {t.get('source', '')}",
            "",
            f"{link('Semester Gantt')} · {link('Study Plan')}",
        ]
        write_file(root / "tasks" / f"{t['id']}.md", "\n".join(body) + "\n")
        written.append(f"tasks/{t['id']}.md")

    return written


def _copy_library_into(root: Path, library_dir: Optional[Path]) -> List[str]:
    """Mirror transcripts/docs into Library/ when a library_dir is provided."""
    if not library_dir or not Path(library_dir).is_dir():
        return []
    lib = root / "Library"
    lib.mkdir(parents=True, exist_ok=True)
    result = mirror_tree(Path(library_dir), lib)
    return result["copied"] + result["updated"]


def _write_forums(root: Path, forums: Optional[List[Dict[str, Any]]]) -> List[str]:
    written: List[str] = []
    if not forums:
        return written
    index = ["# Forums", ""]
    for forum in forums:
        slug = _slug(forum.get("title") or forum.get("name") or "forum")[:50] or "forum"
        body = [
            f"# {forum.get('title') or forum.get('name') or 'Forum'}",
            "",
            forum.get("body") or forum.get("content") or "",
        ]
        if forum.get("posts"):
            body.append("")
            body.append("## Posts")
            for post in forum["posts"]:
                body.append(f"### {post.get('subject') or post.get('title') or 'Post'}")
                body.append("")
                body.append(post.get("message") or post.get("body") or "")
                body.append("")
        write_file(root / "Forums" / f"{slug}.md", "\n".join(body) + "\n")
        written.append(f"Forums/{slug}.md")
        index.append(f"- [[{slug}|{forum.get('title') or slug}]]")
    write_file(root / "Forums" / "index.md", "\n".join(index) + "\n")
    written.append("Forums/index.md")
    return written


def _write_notion_csvs(
    root: Path,
    *,
    tasks: List[Dict[str, Any]],
    announcements: Optional[List[Dict[str, Any]]],
    library_index: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    written: List[str] = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Paper", "Type", "Due", "Weight", "Status", "Source"])
    for t in tasks:
        w.writerow([
            t.get("name", ""), t.get("subject", ""), t.get("type", ""),
            t.get("due_date", ""), t.get("weight", ""), t.get("status", ""),
            t.get("source", ""),
        ])
    write_file(root / "Tasks.csv", buf.getvalue())
    written.append("Tasks.csv")

    buf2 = io.StringIO()
    w2 = csv.writer(buf2)
    w2.writerow(["Name", "Week", "Topic", "Course", "Path"])
    for it in (library_index or []):
        if it.get("type") != "transcript":
            continue
        w2.writerow([
            it.get("title", ""), it.get("week", ""), it.get("topic", ""),
            it.get("course", ""), it.get("path", ""),
        ])
    write_file(root / "Lectures.csv", buf2.getvalue())
    written.append("Lectures.csv")

    buf3 = io.StringIO()
    w3 = csv.writer(buf3)
    w3.writerow(["Title", "Author", "Posted", "Body"])
    for a in (announcements or []):
        w3.writerow([
            a.get("title", ""), a.get("author", ""),
            a.get("posted_at", ""), (a.get("body") or "")[:2000],
        ])
    write_file(root / "Announcements.csv", buf3.getvalue())
    written.append("Announcements.csv")

    import_md = [
        "# Import this suite into Notion",
        "",
        "1. Open Notion → **Import** → **CSV**.",
        "2. Import `Tasks.csv`, `Lectures.csv`, and `Announcements.csv` as separate databases.",
        "3. Optionally drag the Markdown folders into a Notion page (Notion will convert them).",
        "4. If you configured a Notion integration token in Course Assistant, Sync will also push live.",
        "",
    ]
    write_file(root / "IMPORT.md", "\n".join(import_md))
    written.append("IMPORT.md")
    return written


def _md_to_simple_html(title: str, md: str) -> str:
    """Minimal Markdown→HTML for OneNote paste/import workflows (no external dep)."""
    lines = []
    in_code = False
    for line in md.splitlines():
        if line.startswith("```"):
            if in_code:
                lines.append("</pre>")
                in_code = False
            else:
                lines.append("<pre>")
                in_code = True
            continue
        if in_code:
            lines.append(
                line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            continue
        if line.startswith("# "):
            lines.append(f"<h1>{_esc(line[2:])}</h1>")
        elif line.startswith("## "):
            lines.append(f"<h2>{_esc(line[3:])}</h2>")
        elif line.startswith("### "):
            lines.append(f"<h3>{_esc(line[4:])}</h3>")
        elif line.startswith("- "):
            lines.append(f"<li>{_esc(line[2:])}</li>")
        elif line.strip() == "---":
            lines.append("<hr/>")
        elif line.strip():
            lines.append(f"<p>{_esc(line)}</p>")
    body = "\n".join(lines)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>{_esc(title)}</title></head><body>\n{body}\n</body></html>\n"
    )


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _write_onenote_pack(root: Path) -> List[str]:
    """Convert Markdown notes into an HTML section pack + manifest.json."""
    written: List[str] = []
    pages: List[Dict[str, str]] = []
    sections = {
        "Root": ["README.md", "Study Plan.md", "Semester Gantt.md"],
        "Calendar": [],
        "Study Timetable": [],
        "Guide": [],
        "Outlines": [],
        "Announcements": [],
        "papers": [],
        "tasks": [],
        "Forums": [],
    }
    for md_path in root.rglob("*.md"):
        if md_path.name in ("IMPORT.md",):
            continue
        rel = md_path.relative_to(root)
        section = rel.parts[0] if len(rel.parts) > 1 else "Root"
        if section not in sections:
            sections[section] = []
        html_name = md_path.with_suffix(".html").name
        section_dir = root / "_onenote" / section
        html = _md_to_simple_html(md_path.stem, md_path.read_text(encoding="utf-8"))
        write_file(section_dir / html_name, html)
        rel_html = f"_onenote/{section}/{html_name}"
        written.append(rel_html)
        pages.append({"section": section, "title": md_path.stem, "path": rel_html})

    manifest = {
        "format": "onenote_html_pack",
        "version": 1,
        "pages": pages,
        "instructions": (
            "Open each HTML file and paste into OneNote, or use OneNote's "
            "Insert → File attachment / print-to-OneNote workflows."
        ),
    }
    write_file(root / "_onenote" / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    written.append("_onenote/manifest.json")
    write_file(root / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    written.append("manifest.json")
    return written


def build_suite_tree(
    dest_dir: Path,
    *,
    format: str = "obsidian",
    title: str = "Semester plan",
    tasks: Optional[List[Dict[str, Any]]] = None,
    outlines: Optional[List[Dict[str, Any]]] = None,
    moodle_events: Optional[List[Dict[str, Any]]] = None,
    announcements: Optional[List[Dict[str, Any]]] = None,
    forums: Optional[List[Dict[str, Any]]] = None,
    library_dir: Optional[Path] = None,
    library_index: Optional[List[Dict[str, Any]]] = None,
    root_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a suite directory for ``format`` (obsidian|notion|onenote)."""
    format = (format or "obsidian").lower()
    if format not in SUITE_FORMATS:
        raise ValueError(f"unknown suite format {format!r}; choose from {SUITE_FORMATS}")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir / (root_name or _slug(title) or "semester-plan")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _ensure_folders(root)

    tasks = list(tasks or [])
    outlines = list(outlines or [])
    link_style = "wikilink" if format == "obsidian" else "markdown"
    written = _build_shared_markdown(
        root, title=title, tasks=tasks, outlines=outlines,
        moodle_events=moodle_events, announcements=announcements,
        link_style=link_style,
    )
    written += _write_forums(root, forums)
    lib_files = _copy_library_into(root, library_dir)
    written += [f"Library/{p}" for p in lib_files]

    extras: List[str] = []
    if format == "notion":
        extras = _write_notion_csvs(
            root, tasks=tasks, announcements=announcements,
            library_index=library_index,
        )
    elif format == "onenote":
        extras = _write_onenote_pack(root)
    written += extras

    return {
        "format": format,
        "root": str(root),
        "root_name": root.name,
        "files": written,
        "file_count": len(written),
        "subjects": collect_subjects(tasks, outlines, moodle_events),
    }


def zip_suite(suite_root: Path, zip_path: Path) -> Path:
    """Zip a suite directory (including the root folder name)."""
    suite_root = Path(suite_root)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    parent = suite_root.parent
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Explicit empty dirs so extractors show the full vault layout.
        for folder in SUITE_FOLDERS:
            zf.writestr(f"{suite_root.name}/{folder}/", "")
        for path in suite_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(parent).as_posix())
    return zip_path


def get_destinations(db: Database) -> Dict[str, str]:
    raw = settings_store.get(db, SETTING_DESTINATIONS, {}) or {}
    return {k: str(v) for k, v in raw.items() if v}


def set_destinations(db: Database, destinations: Dict[str, str]) -> Dict[str, str]:
    cleaned = {
        k: str(Path(v).expanduser())
        for k, v in (destinations or {}).items()
        if k in SUITE_FORMATS and str(v or "").strip()
    }
    # Preserve empty clears explicitly passed as ""
    for k, v in (destinations or {}).items():
        if k in SUITE_FORMATS and not str(v or "").strip():
            cleaned.pop(k, None)
    settings_store.set(db, SETTING_DESTINATIONS, cleaned)
    return cleaned


def get_enabled(db: Database) -> List[str]:
    raw = settings_store.get(db, SETTING_ENABLED, list(SUITE_FORMATS[:1]))
    if not isinstance(raw, list):
        return ["obsidian"]
    return [f for f in raw if f in SUITE_FORMATS] or ["obsidian"]


def set_enabled(db: Database, enabled: Sequence[str]) -> List[str]:
    vals = [f for f in enabled if f in SUITE_FORMATS]
    settings_store.set(db, SETTING_ENABLED, vals)
    return vals


def get_auto_sync(db: Database) -> bool:
    return bool(settings_store.get(db, SETTING_AUTO_SYNC, False))


def set_auto_sync(db: Database, enabled: bool) -> bool:
    settings_store.set(db, SETTING_AUTO_SYNC, bool(enabled))
    return bool(enabled)


def preview_suite(
    *,
    format: str = "obsidian",
    title: str = "Semester plan",
    tasks: Optional[List[Dict[str, Any]]] = None,
    outlines: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    subjects = collect_subjects(tasks or [], outlines or [])
    estimate = (
        3  # readme + study plan + gantt
        + 2  # calendar files
        + 1  # timetable
        + len(subjects) * 2  # paper + guide
        + len(outlines or [])
        + len(tasks or [])
        + (3 if format == "notion" else 0)
        + (1 if format == "onenote" else 0)
    )
    return {
        "format": format,
        "title": title,
        "subjects": subjects,
        "task_count": len(tasks or []),
        "estimated_files": estimate,
        "folders": list(SUITE_FOLDERS),
    }


def sync_suites_to_destinations(
    *,
    db: Database,
    plan_payload: Dict[str, Any],
    title: str,
    outlines: List[Dict[str, Any]],
    announcements: Optional[List[Dict[str, Any]]] = None,
    forums: Optional[List[Dict[str, Any]]] = None,
    moodle_events: Optional[List[Dict[str, Any]]] = None,
    library_dir: Optional[Path] = None,
    formats: Optional[Sequence[str]] = None,
    staging_dir: Optional[Path] = None,
    push_live: bool = True,
) -> Dict[str, Any]:
    """Build configured suites and mirror them into destination folders."""
    formats = list(formats or get_enabled(db))
    destinations = get_destinations(db)
    tasks = plan_payload.get("tasks") or []
    staging = Path(staging_dir or (Path(library_dir or ".") / "_suites"))
    staging.mkdir(parents=True, exist_ok=True)

    destinations_written: Dict[str, Any] = {}
    new_files = 0
    updated = 0
    library_index = None
    if library_dir:
        try:
            from . import search
            library_index = search.build_index(Path(library_dir))
        except Exception:
            library_index = None

    for fmt in formats:
        built = build_suite_tree(
            staging / fmt,
            format=fmt,
            title=title,
            tasks=tasks,
            outlines=outlines,
            moodle_events=moodle_events,
            announcements=announcements,
            forums=forums,
            library_dir=library_dir,
            library_index=library_index,
        )
        dest = destinations.get(fmt)
        mirror_result: Dict[str, Any] = {}
        if dest:
            mirror_result = mirror_tree(Path(built["root"]), Path(dest) / Path(built["root"]).name)
            new_files += mirror_result.get("new_files", 0)
            updated += mirror_result.get("updated_files", 0)
        destinations_written[fmt] = {
            "root": built["root"],
            "destination": dest,
            "file_count": built["file_count"],
            "mirror": mirror_result,
        }

    live: Dict[str, Any] = {}
    if push_live:
        live = _push_live_integrations(db, library_dir=library_dir)

    return {
        "ok": True,
        "formats": formats,
        "destinations_written": destinations_written,
        "new_files": new_files,
        "updated": updated,
        "announcements": len(announcements or []),
        "live": live,
    }


def _push_live_integrations(
    db: Database,
    *,
    library_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Best-effort Notion API + AnkiConnect pushes when credentials exist."""
    from .integrations import state as sync_state

    out: Dict[str, Any] = {}
    token = ""
    try:
        token = sync_state.notion_token(db) or ""
    except Exception:
        token = ""
    notion_db = settings_store.get(db, "notion.database_id", "") or ""
    if not notion_db:
        cfg = sync_state.get(db)
        notion_db = (cfg.get("notion") or {}).get("database_id", "") or ""
    if token and notion_db and library_dir:
        try:
            from .integrations import notion as notion_mod
            out["notion"] = notion_mod.sync_course(
                Path(library_dir), token=token, database_id=str(notion_db),
            )
        except Exception as e:
            out["notion"] = {"ok": False, "error": str(e)}
    else:
        out["notion"] = {"ok": False, "skipped": True, "reason": "not configured"}

    try:
        from .integrations import anki as anki_mod
        from . import flashcards
        cards: List[Dict[str, Any]] = []
        if library_dir:
            deck_dir = Path(library_dir) / "_flashcards"
            if deck_dir.is_dir():
                for tsv in deck_dir.glob("*.tsv"):
                    cards.extend(flashcards.parse_cards_text(tsv.read_text(encoding="utf-8")))
        if cards:
            out["anki"] = anki_mod.sync_flashcards(cards, deck="Course Assistant")
        else:
            out["anki"] = {"ok": False, "skipped": True, "reason": "no flashcards"}
    except Exception as e:
        out["anki"] = {"ok": False, "error": str(e)}
    return out


def detect_paper_codes_from_courses(courses: Iterable[Dict[str, Any]]) -> List[str]:
    """Autofill paper codes from Moodle course shortname/fullname/code fields."""
    from .imports.moodle_api import _course_code
    from .sources import _extract_course_code

    codes: List[str] = []
    seen: Set[str] = set()
    for c in courses:
        for key in ("code", "shortname", "fullname", "name", "fullnamename"):
            raw = c.get(key) or ""
            if not raw:
                continue
            found = _course_code(str(raw)) or _extract_course_code(str(raw).upper())
            if found:
                base = found.upper().split("-")[0]
                if base not in seen:
                    seen.add(base)
                    codes.append(found.upper() if "-" in found.upper() else base)
                break
    return codes
