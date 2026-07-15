"""
suites.py - shared study-suite tree writer + format adapters + sync.

Canonical vault layout (Obsidian / Notion / OneNote)::

    {suite-root}/
      README.md
      IMPORT.md
      Master/
        Study Plan2.md
        Task Schedule/          # timetable + task notes
        Task Graphs/            # semester gantt + overview canvas
        Calendar/               # semester.ics + google-calendar.csv
      Courses/
        {PAPER}/
          README.md
          Guide/
          Lectures/
          Lecture Recordings/
          Anki Flashcards/
          Sample Questions/
          Misc/
          Source Code/
          Textbooks/
          Study Timetable/
          {PAPER}_Mindmap.canvas
      .obsidian/                # Obsidian only
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import core, settings_store
from .database import Database

SUITE_FORMATS = ("obsidian", "notion", "onenote")

COURSE_SUBFOLDERS = (
    "Guide",
    "Lectures",
    "Lecture Recordings",
    "Anki Flashcards",
    "Sample Questions",
    "Misc",
    "Source Code",
    "Textbooks",
    "Study Timetable",
)

# Top-level / Master folders used when zipping empty placeholders.
SUITE_FOLDERS = (
    "Master",
    "Master/Task Schedule",
    "Master/Task Graphs",
    "Master/Calendar",
    "Courses",
)

SETTING_DESTINATIONS = "suite.destinations"
SETTING_ENABLED = "suite.enabled"
SETTING_AUTO_SYNC = "suite.auto_sync"
SETTING_LAST_SYNC = "suite.last_sync"


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", (text or "").strip()).strip("-").lower()


def _paper_base(code: str) -> str:
    return (code or "").upper().split("-")[0].strip()


def collect_subjects(
    tasks: Sequence[Dict[str, Any]],
    outlines: Optional[Sequence[Dict[str, Any]]] = None,
    moodle_events: Optional[Sequence[Dict[str, Any]]] = None,
    paper_codes: Optional[Sequence[str]] = None,
) -> List[str]:
    subjects: Set[str] = set()
    for code in paper_codes or []:
        base = _paper_base(code)
        if base:
            subjects.add(base)
    subjects.update(_paper_base(t.get("subject") or "") for t in tasks if t.get("subject"))
    subjects.update(
        _paper_base(o.get("paper_code") or "")
        for o in (outlines or []) if o.get("paper_code")
    )
    subjects.update(
        _paper_base(e.get("paper_code") or "")
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


def _ensure_master_folders(root: Path) -> None:
    (root / "Master" / "Task Schedule").mkdir(parents=True, exist_ok=True)
    (root / "Master" / "Task Graphs").mkdir(parents=True, exist_ok=True)
    (root / "Master" / "Calendar").mkdir(parents=True, exist_ok=True)
    (root / "Courses").mkdir(parents=True, exist_ok=True)


def _ensure_course_folders(course_root: Path) -> None:
    for folder in COURSE_SUBFOLDERS:
        (course_root / folder).mkdir(parents=True, exist_ok=True)
    # Empty placeholder so Graph/vault explorers show the folder.
    write_file(course_root / "Untitled" / ".gitkeep", "")


def _link(target: str, label: Optional[str] = None, *, style: str = "wikilink") -> str:
    if style == "wikilink":
        return f"[[{target}|{label}]]" if label else f"[[{target}]]"
    href = target if target.endswith((".md", ".canvas", ".ics", ".csv")) else f"{target}.md"
    return f"[{label or target}]({href})"


def _canvas_file(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> str:
    return json.dumps({"nodes": nodes, "edges": edges}, indent=2) + "\n"


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _write_obsidian_config(root: Path) -> List[str]:
    written: List[str] = []
    obsidian = root / ".obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)
    app = {
        "alwaysUpdateLinks": True,
        "newFileLocation": "folder",
        "newFileFolderPath": "Courses",
        "attachmentFolderPath": "Master/Task Graphs",
        "useMarkdownLinks": False,
        "showUnsupportedFiles": True,
    }
    core_plugins = {
        "file-explorer": True,
        "global-search": True,
        "graph": True,
        "backlink": True,
        "canvas": True,
        "outgoing-link": True,
        "tag-pane": True,
        "page-preview": True,
        "templates": False,
        "daily-notes": False,
    }
    write_file(obsidian / "app.json", json.dumps(app, indent=2) + "\n")
    write_file(obsidian / "core-plugins.json", json.dumps(core_plugins, indent=2) + "\n")
    written += [".obsidian/app.json", ".obsidian/core-plugins.json"]
    return written


def _write_root_readme(root: Path, title: str, subjects: List[str], *, style: str) -> str:
    lines = [
        f"# {title}",
        "",
        "Course Assistant study suite — open this folder as your vault / import pack.",
        "",
        f"See {_link('IMPORT', 'IMPORT.md', style=style)} for format-specific steps.",
        "",
        "## Master",
        "",
        f"- {_link('Master/Study Plan2', 'Study Plan2', style=style)}",
        f"- {_link('Master/Task Graphs/Semester Gantt', 'Semester Gantt', style=style)}",
        f"- {_link('Master/Task Schedule/Timetable Sheet - Markdown ver', 'Timetable', style=style)}",
        f"- {_link('Master/Calendar/semester.ics', 'Calendar (.ics)', style=style)}",
        "",
        "## Courses",
        "",
    ]
    for s in subjects:
        lines.append(f"- {_link(f'Courses/{s}/README', s, style=style)}")
    lines.append("")
    path = "README.md"
    write_file(root / path, "\n".join(lines) + "\n")
    return path


def _write_import_md(root: Path, format: str) -> str:
    if format == "obsidian":
        body = [
            "# Import into Obsidian",
            "",
            "1. Open Obsidian → **Open folder as vault** → choose this suite root.",
            "2. Graph and Canvas use the included `.obsidian` config.",
            "3. Start at `Master/Study Plan2.md`; follow wikilinks into `Courses/{PAPER}/`.",
            "4. Prefer Sync from Course Assistant so destinations stay mirrored.",
            "",
        ]
    elif format == "notion":
        body = [
            "# Import into Notion",
            "",
            "1. Open Notion → **Import** → **CSV**.",
            "2. Import `Master/Task Schedule/Tasks.csv` as a database.",
            "3. Optionally import `Courses/*/Lectures.csv` and announcement CSVs.",
            "4. Drag Markdown folders (`Master/`, `Courses/`) onto a Notion page to convert notes.",
            "5. If a Notion integration is configured in Course Assistant, Sync can also push live.",
            "",
        ]
    else:
        body = [
            "# Import into OneNote",
            "",
            "1. Open the `_onenote/` HTML pack (and `manifest.json`).",
            "2. Create notebooks/sections for **Master** and each `Courses/{PAPER}` section.",
            "3. Open each HTML page and paste into the matching OneNote section,",
            "   or use Insert → File / print-to-OneNote.",
            "4. Keep Sync destinations pointed at this pack for refreshable updates.",
            "",
        ]
    write_file(root / "IMPORT.md", "\n".join(body))
    return "IMPORT.md"


def _build_suite_content(
    root: Path,
    *,
    format: str,
    title: str,
    tasks: List[Dict[str, Any]],
    outlines: List[Dict[str, Any]],
    moodle_events: Optional[List[Dict[str, Any]]],
    announcements: Optional[List[Dict[str, Any]]],
    forums: Optional[List[Dict[str, Any]]],
    library_dir: Optional[Path],
    paper_codes: Optional[List[str]] = None,
) -> List[str]:
    from . import task_schedule

    style = "wikilink" if format == "obsidian" else "markdown"
    written: List[str] = []
    subjects = collect_subjects(tasks, outlines, moodle_events, paper_codes)
    _ensure_master_folders(root)

    written.append(_write_root_readme(root, title, subjects, style=style))
    written.append(_write_import_md(root, format))

    gantt = task_schedule.build_mermaid_gantt(
        tasks, outlines=outlines, moodle_events=moodle_events, title=title,
    )

    # --- Master study plan ---
    plan_lines = [
        "---",
        "tags: [study-plan, semester, master]",
        "aliases: [Study Plan, Study Plan2]",
        "---",
        "",
        f"# {title} — Study plan",
        "",
        "## Courses",
        "",
    ]
    for s in subjects:
        plan_lines.append(f"- {_link(f'Courses/{s}/README', s, style=style)}")
    plan_lines += [
        "",
        f"## Timeline · {_link('Master/Task Graphs/Semester Gantt', 'Gantt', style=style)}",
        "",
    ]
    for t in tasks:
        due = t.get("due_date") or "TBD"
        subj = _paper_base(t.get("subject") or "")
        task_link = _link(
            f"Master/Task Schedule/{t['id']}", t.get("name", ""), style=style,
        )
        course_bit = f" · {_link(f'Courses/{subj}/README', subj, style=style)}" if subj else ""
        plan_lines.append(f"- {due} · {task_link} ({t.get('type', '')}{course_bit})")
    write_file(root / "Master" / "Study Plan2.md", "\n".join(plan_lines) + "\n")
    written.append("Master/Study Plan2.md")

    # --- Master task schedule ---
    timetable = task_schedule._timetable_markdown(tasks)
    write_file(
        root / "Master" / "Task Schedule" / "Timetable Sheet - Markdown ver.md",
        timetable,
    )
    written.append("Master/Task Schedule/Timetable Sheet - Markdown ver.md")
    for t in tasks:
        subj = _paper_base(t.get("subject") or "")
        body = [
            "---",
            f"tags: [task, {(subj or 'general').lower()}]",
            "---",
            "",
            f"# {t.get('name', 'Task')}",
            "",
            f"- Paper: {_link(f'Courses/{subj}/README', subj, style=style) if subj else '—'}",
            f"- Type: {t.get('type', '')}",
            f"- Due: {t.get('due_date', '')}",
            f"- Weight: {t.get('weight', '')}%" if t.get("weight") is not None else "- Weight:",
            f"- Status: {t.get('status', '')}",
            f"- Source: {t.get('source', '')}",
            "",
            f"{_link('Master/Study Plan2', 'Study Plan2', style=style)} · "
            f"{_link('Master/Task Graphs/Semester Gantt', 'Gantt', style=style)}",
            "",
        ]
        write_file(root / "Master" / "Task Schedule" / f"{t['id']}.md", "\n".join(body) + "\n")
        written.append(f"Master/Task Schedule/{t['id']}.md")

    # --- Master graphs + calendar ---
    gantt_body = [
        "---",
        "tags: [gantt, semester, master]",
        "aliases: [Semester Timeline, Semester Gantt]",
        "---",
        "",
        f"# {title} — Gantt chart",
        "",
        gantt.rstrip(),
        "",
    ]
    write_file(root / "Master" / "Task Graphs" / "Semester Gantt.md", "\n".join(gantt_body) + "\n")
    written.append("Master/Task Graphs/Semester Gantt.md")

    overview_nodes: List[Dict[str, Any]] = []
    overview_edges: List[Dict[str, Any]] = []
    center = _new_id()
    overview_nodes.append({
        "id": center, "type": "text", "text": title,
        "x": 0, "y": 0, "width": 260, "height": 60,
    })
    for i, s in enumerate(subjects):
        nid = _new_id()
        overview_nodes.append({
            "id": nid, "type": "file",
            "file": f"Courses/{s}/README.md",
            "x": -320 + (i % 4) * 220, "y": 140 + (i // 4) * 140,
            "width": 200, "height": 80,
        })
        overview_edges.append({
            "id": _new_id(), "fromNode": center, "fromSide": "bottom",
            "toNode": nid, "toSide": "top",
        })
    write_file(
        root / "Master" / "Task Graphs" / "Overview.canvas",
        _canvas_file(overview_nodes, overview_edges),
    )
    written.append("Master/Task Graphs/Overview.canvas")

    ics = task_schedule.export_calendar_ics(tasks, outlines=outlines, title=title)
    write_file(root / "Master" / "Calendar" / "semester.ics", ics)
    written.append("Master/Calendar/semester.ics")
    gcal = task_schedule.export_google_calendar_csv(tasks, outlines=outlines)
    write_file(root / "Master" / "Calendar" / "google-calendar.csv", gcal)
    written.append("Master/Calendar/google-calendar.csv")

    # --- Per-course trees ---
    for s in subjects:
        course_root = root / "Courses" / s
        _ensure_course_folders(course_root)
        paper_tasks = [t for t in tasks if _paper_base(t.get("subject") or "") == s]
        paper_outlines = [
            o for o in outlines if _paper_base(o.get("paper_code") or "") == s
        ]
        paper_events = [
            e for e in (moodle_events or [])
            if _paper_base(e.get("paper_code") or "") == s
        ]

        readme = [
            "---",
            f"tags: [{s.lower()}, course]",
            "---",
            "",
            f"# {s}",
            "",
            f"Back to {_link('Master/Study Plan2', 'Study Plan2', style=style)} · "
            f"{_link(f'Courses/{s}/{s}_Mindmap.canvas', 'Mindmap', style=style)}",
            "",
            "## Folders",
            "",
            "- Guide · Lectures · Lecture Recordings · Anki Flashcards",
            "- Sample Questions · Misc · Source Code · Textbooks · Study Timetable",
            "",
            "## Tasks",
            "",
        ]
        for t in paper_tasks:
            readme.append(
                f"- {_link(f'Master/Task Schedule/{t['id']}', t.get('name', ''), style=style)} "
                f"({t.get('due_date', 'TBD')})"
            )
        if paper_outlines:
            readme += ["", "## Outline", ""]
            for o in paper_outlines:
                note = task_schedule._outline_note(o)
                write_file(course_root / "Guide" / f"{s} Outline.md", note)
                written.append(f"Courses/{s}/Guide/{s} Outline.md")
                readme.append(f"- {_link(f'Courses/{s}/Guide/{s} Outline', f'{s} Outline', style=style)}")

        paper_gantt = task_schedule.build_mermaid_gantt(
            paper_tasks, outlines=paper_outlines, moodle_events=paper_events,
            title=f"{s} — {title}",
        )
        write_file(
            course_root / "Guide" / f"{s} Gantt.md",
            "\n".join([
                "---", f"tags: [{s.lower()}, gantt]", "---", "",
                f"# {s} Gantt", "", paper_gantt.rstrip(), "",
            ]) + "\n",
        )
        written.append(f"Courses/{s}/Guide/{s} Gantt.md")

        if paper_tasks:
            write_file(
                course_root / "Study Timetable" / "Timetable.md",
                task_schedule._timetable_markdown(paper_tasks),
            )
            written.append(f"Courses/{s}/Study Timetable/Timetable.md")

        write_file(course_root / "README.md", "\n".join(readme) + "\n")
        written.append(f"Courses/{s}/README.md")

        # Mindmap canvas linking course README + guide + tasks
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        hub = _new_id()
        nodes.append({
            "id": hub, "type": "file", "file": f"Courses/{s}/README.md",
            "x": 0, "y": 0, "width": 260, "height": 100,
        })
        guide_id = _new_id()
        nodes.append({
            "id": guide_id, "type": "file",
            "file": f"Courses/{s}/Guide/{s} Gantt.md",
            "x": 320, "y": -40, "width": 240, "height": 80,
        })
        edges.append({
            "id": _new_id(), "fromNode": hub, "fromSide": "right",
            "toNode": guide_id, "toSide": "left",
        })
        for i, t in enumerate(paper_tasks[:12]):
            tid = _new_id()
            nodes.append({
                "id": tid, "type": "file",
                "file": f"Master/Task Schedule/{t['id']}.md",
                "x": -80 + (i % 3) * 200, "y": 160 + (i // 3) * 110,
                "width": 180, "height": 70,
            })
            edges.append({
                "id": _new_id(), "fromNode": hub, "fromSide": "bottom",
                "toNode": tid, "toSide": "top",
            })
        write_file(course_root / f"{s}_Mindmap.canvas", _canvas_file(nodes, edges))
        written.append(f"Courses/{s}/{s}_Mindmap.canvas")

        # Placeholders so empty dirs persist
        for folder in COURSE_SUBFOLDERS:
            keep = course_root / folder / ".gitkeep"
            if not any((course_root / folder).glob("*")):
                write_file(keep, "")
                written.append(f"Courses/{s}/{folder}/.gitkeep")

    # Announcements → first course Misc or Master Misc
    if announcements:
        dest = root / "Courses" / (subjects[0] if subjects else "_shared") / "Misc" / "Announcements"
        if not subjects:
            dest = root / "Master" / "Task Schedule" / "Announcements"
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.mkdir(parents=True, exist_ok=True)
        ann_index = ["# Announcements", ""]
        for a in announcements:
            slug, body = task_schedule._announcement_note(a)
            write_file(dest / f"{slug}.md", body)
            rel = dest.relative_to(root).as_posix()
            written.append(f"{rel}/{slug}.md")
            ann_index.append(f"- {_link(f'{rel}/{slug}', a.get('title', 'Post'), style=style)}")
        write_file(dest / "index.md", "\n".join(ann_index) + "\n")
        written.append(f"{dest.relative_to(root).as_posix()}/index.md")

    if forums:
        written += _write_forums_into_misc(root, forums, subjects, style=style)

    written += _copy_library_into_courses(root, library_dir, subjects)

    if format == "obsidian":
        written += _write_obsidian_config(root)

    return written


def _write_forums_into_misc(
    root: Path,
    forums: List[Dict[str, Any]],
    subjects: List[str],
    *,
    style: str,
) -> List[str]:
    written: List[str] = []
    base = root / "Courses" / (subjects[0] if subjects else "GENERAL") / "Misc" / "Forums"
    if not subjects:
        base = root / "Master" / "Task Schedule" / "Forums"
    base.mkdir(parents=True, exist_ok=True)
    index = ["# Forums", ""]
    for forum in forums:
        slug = _slug(forum.get("title") or forum.get("name") or "forum")[:50] or "forum"
        body = [
            f"# {forum.get('title') or forum.get('name') or 'Forum'}",
            "",
            forum.get("body") or forum.get("content") or "",
        ]
        if forum.get("posts"):
            body += ["", "## Posts"]
            for post in forum["posts"]:
                body.append(f"### {post.get('subject') or post.get('title') or 'Post'}")
                body.append("")
                body.append(post.get("message") or post.get("body") or "")
                body.append("")
        write_file(base / f"{slug}.md", "\n".join(body) + "\n")
        rel = (base / f"{slug}.md").relative_to(root).as_posix()
        written.append(rel)
        index.append(f"- {_link(rel[:-3] if rel.endswith('.md') else rel, forum.get('title') or slug, style=style)}")
    write_file(base / "index.md", "\n".join(index) + "\n")
    written.append((base / "index.md").relative_to(root).as_posix())
    return written


def _copy_library_into_courses(
    root: Path,
    library_dir: Optional[Path],
    subjects: List[str],
) -> List[str]:
    """Copy transcript/md sources into Lectures/; other files into Misc/."""
    if not library_dir or not Path(library_dir).is_dir():
        return []
    written: List[str] = []
    fallback = subjects[0] if subjects else None
    if not fallback:
        dest = root / "Master" / "Task Schedule" / "Library"
        result = mirror_tree(Path(library_dir), dest)
        return [f"Master/Task Schedule/Library/{p}" for p in (result["copied"] + result["updated"])]

    for path in Path(library_dir).rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith(".") or "_suites" in path.parts:
            continue
        name_u = path.as_posix().upper()
        matched = next((s for s in subjects if s in name_u), fallback)
        folder = "Lectures" if path.suffix.lower() in {".md", ".txt", ".json", ".srt", ".vtt"} else "Misc"
        if path.suffix.lower() in {".apkg", ".tsv"} or "flashcard" in path.name.lower():
            folder = "Anki Flashcards"
        if any(k in path.name.lower() for k in ("quiz", "sample", "practice", "exam")):
            folder = "Sample Questions"
        if path.suffix.lower() in {".py", ".java", ".c", ".cpp", ".js", ".ts"}:
            folder = "Source Code"
        if path.suffix.lower() in {".pdf", ".epub"} and "text" in path.name.lower():
            folder = "Textbooks"
        rel = path.relative_to(library_dir)
        target = root / "Courses" / matched / folder / rel.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                shutil.copy2(path, target)
                written.append(target.relative_to(root).as_posix())
            except Exception:
                continue
    return written


def _write_notion_extras(
    root: Path,
    *,
    tasks: List[Dict[str, Any]],
    announcements: Optional[List[Dict[str, Any]]],
    library_index: Optional[List[Dict[str, Any]]],
    subjects: List[str],
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
    write_file(root / "Master" / "Task Schedule" / "Tasks.csv", buf.getvalue())
    written.append("Master/Task Schedule/Tasks.csv")

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
    lectures_csv = root / "Master" / "Task Schedule" / "Lectures.csv"
    write_file(lectures_csv, buf2.getvalue())
    written.append("Master/Task Schedule/Lectures.csv")

    buf3 = io.StringIO()
    w3 = csv.writer(buf3)
    w3.writerow(["Title", "Author", "Posted", "Body"])
    for a in (announcements or []):
        w3.writerow([
            a.get("title", ""), a.get("author", ""),
            a.get("posted_at", ""), (a.get("body") or "")[:2000],
        ])
    write_file(root / "Master" / "Task Schedule" / "Announcements.csv", buf3.getvalue())
    written.append("Master/Task Schedule/Announcements.csv")
    return written


def _md_to_simple_html(title: str, md: str) -> str:
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
            lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
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
    written: List[str] = []
    pages: List[Dict[str, str]] = []
    for md_path in root.rglob("*.md"):
        if md_path.name in ("IMPORT.md",):
            continue
        if ".obsidian" in md_path.parts:
            continue
        rel = md_path.relative_to(root)
        section = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else "Root"
        html_name = md_path.with_suffix(".html").name
        section_dir = root / "_onenote" / section.replace("/", "__")
        html = _md_to_simple_html(md_path.stem, md_path.read_text(encoding="utf-8"))
        write_file(section_dir / html_name, html)
        rel_html = f"_onenote/{section_dir.name}/{html_name}"
        written.append(rel_html)
        pages.append({"section": section, "title": md_path.stem, "path": rel_html})

    manifest = {
        "format": "onenote_html_pack",
        "version": 2,
        "layout": "Master + Courses/{PAPER}",
        "pages": pages,
        "instructions": (
            "Create OneNote sections for Master and each Courses/{PAPER} folder, "
            "then paste the matching HTML pages (see IMPORT.md)."
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
    paper_codes: Optional[List[str]] = None,
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

    tasks = list(tasks or [])
    outlines = list(outlines or [])
    codes = list(paper_codes or [])
    if not codes:
        codes = collect_subjects(tasks, outlines, moodle_events)

    written = _build_suite_content(
        root,
        format=format,
        title=title,
        tasks=tasks,
        outlines=outlines,
        moodle_events=moodle_events,
        announcements=announcements,
        forums=forums,
        library_dir=library_dir,
        paper_codes=codes,
    )

    subjects = collect_subjects(tasks, outlines, moodle_events, codes)
    if format == "notion":
        written += _write_notion_extras(
            root, tasks=tasks, announcements=announcements,
            library_index=library_index, subjects=subjects,
        )
    elif format == "onenote":
        written += _write_onenote_pack(root)

    return {
        "format": format,
        "root": str(root),
        "root_name": root.name,
        "files": written,
        "file_count": len(written),
        "subjects": subjects,
    }


def zip_suite(suite_root: Path, zip_path: Path) -> Path:
    """Zip a suite directory (including the root folder name)."""
    suite_root = Path(suite_root)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    parent = suite_root.parent
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in SUITE_FOLDERS:
            zf.writestr(f"{suite_root.name}/{folder}/", "")
        # Ensure every Courses/*/subfolder appears even if empty of .gitkeep
        courses = suite_root / "Courses"
        if courses.is_dir():
            for course_dir in courses.iterdir():
                if course_dir.is_dir():
                    for sub in COURSE_SUBFOLDERS:
                        zf.writestr(f"{suite_root.name}/Courses/{course_dir.name}/{sub}/", "")
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


def get_last_sync(db: Database) -> Optional[Dict[str, Any]]:
    raw = settings_store.get(db, SETTING_LAST_SYNC, None)
    return raw if isinstance(raw, dict) else None


def set_last_sync(db: Database, report: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(report or {})
    settings_store.set(db, SETTING_LAST_SYNC, payload)
    return payload


def preview_suite(
    *,
    format: str = "obsidian",
    title: str = "Semester plan",
    tasks: Optional[List[Dict[str, Any]]] = None,
    outlines: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    subjects = collect_subjects(tasks or [], outlines or [])
    estimate = (
        4  # readme + import + study plan2 + gantt
        + 2  # calendar
        + 1  # timetable
        + len(subjects) * (3 + len(COURSE_SUBFOLDERS))  # course tree approx
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
        "folders": list(SUITE_FOLDERS) + [f"Courses/{{PAPER}}/{s}" for s in COURSE_SUBFOLDERS],
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
    paper_codes = plan_payload.get("paper_codes") or []
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
            paper_codes=paper_codes,
        )
        dest = destinations.get(fmt)
        mirror_result: Dict[str, Any] = {}
        if dest:
            # Mirror tree contents into destination (structure-preserving).
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
        for key in ("code", "shortname", "fullname", "name"):
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
