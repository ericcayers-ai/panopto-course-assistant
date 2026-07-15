"""schemas.py - Pydantic request bodies for the HTTP API (§17).

Split out of main.py so every router imports its request models from one
place instead of reaching back into the app module."""
from __future__ import annotations

from pydantic import BaseModel
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from . import tts as tts_mod


class FeedRequest(BaseModel):
    source: str
    cookies: str = ""

class TranscribeRequest(BaseModel):
    lecture: Dict[str, Any]          # a lecture dict as returned by /api/feed
    engine: str = "auto"             # auto | faster-whisper | whisper | granite | …
    model: str = "auto"
    language: str = "en"
    device: str = "auto"
    organize: str = "auto"
    # Canonical set written for every transcription: clean text, Markdown, rich
    # JSON (everything else is derived from it), and a study summary. Subtitles
    # and other formats are generated on demand from the Export step.
    outputs: List[str] = ["txt", "md", "json", "summary"]
    interval: int = 30
    keep_media: bool = False
    audio_only: bool = False
    skip_existing: bool = True
    force: bool = False
    cookies: str = ""
    course: str = ""
    # Adaptive STT (4.0) — Auto profile + adaptive router are the defaults.
    profile: str = "auto"            # auto | quality | fast | live | eco | legacy
    code_switch: bool = False
    word_timestamps: bool = True
    diarization: str = "off"         # off | auto | on
    speakers: Optional[int] = None
    vocabulary: Optional[List[str]] = None
    caption_first: bool = True
    caption_url: str = ""
    resume: bool = True
    chunk_seconds: int = 180
    compute: str = "auto"
    hotwords: str = ""
    initial_prompt: str = ""
    use_adaptive: bool = True


class STTRouteRequest(BaseModel):
    profile: str = "auto"
    language: str = "auto"
    device: str = "auto"
    code_switch: bool = False
    engine: str = ""
    model: str = ""
    caption_first: bool = True
    has_usable_captions: bool = False


class STTLiveStartRequest(BaseModel):
    course: str = ""
    language: str = "en"
    model: str = ""
    save: bool = True


class STTModelActionRequest(BaseModel):
    engine: str
    model_id: str = ""

class OrganizeRequest(BaseModel):
    by: str = "week"                 # auto | none | date | week | lecture | module | topic

class MoodleRequest(BaseModel):
    path: str                        # mirror folder or course/view_php.html
    save_outline: bool = False       # also write the outline as a source file

class NotionRequest(BaseModel):
    path: str                        # a Notion .html page or an export folder
    combined: bool = False           # also write a single notion_pack.md

class PdfRequest(BaseModel):
    input_path: str
    suffix: str = "_copy"
    include_subfolders: bool = True
    overwrite: bool = False

class DocsRequest(BaseModel):
    input_path: str
    exts: Optional[List[str]] = None     # default: all supported types
    include_subfolders: bool = True
    overwrite: bool = False
    target: str = "ai"                   # "ai" (_docs) | "copy" (sibling *_copy)
    combined: bool = False               # one documents_pack.md (ai target only)
    keep_images: bool = True             # extract & attach embedded images (default on)

class NotebookLMRequest(BaseModel):
    selection: Optional[List[str]] = None  # ["folder/stem", ...]; None = all
    combined: bool = False                 # also write a single course_pack.md
    course: str = ""                       # optional course name for headers
    output_dir: Optional[str] = None       # also copy files to this folder

class ExportAllRequest(BaseModel):
    combined: bool = True                  # write a single everything_pack.md
    course: str = ""                       # optional course name for headers
    output_dir: Optional[str] = None       # also copy files to this folder

class FormatsRequest(BaseModel):
    formats: List[str] = ["srt"]           # srt | vtt | txt | md | notebooklm | summary
    interval: int = 30

class FlashcardGenRequest(BaseModel):
    selection: Optional[List[str]] = None  # limit to these lecture stems; None = all
    course: str = ""
    deck: str = "flashcards"
    max_cards: int = 50                    # total max cards to generate
    output_dir: Optional[str] = None       # custom output folder

class FlashcardCatRequest(BaseModel):
    text: str = ""                         # pasted CSV/TSV deck (front, back[, tags])
    path: str = ""                         # …or a path to a .csv/.tsv/.txt deck
    course: str = ""
    deck: str = "categorized"

class StudyCsvRequest(BaseModel):
    course: str = ""
    filename: str = "study_database"
    output_dir: Optional[str] = None       # also copy CSV to this folder

class SrtExportRequest(BaseModel):
    output_dir: Optional[str] = None       # folder to copy SRT files alongside videos
    include_recordings: bool = True        # also place the lecture videos in that folder

class PickFolderRequest(BaseModel):
    title: str = "Choose a folder"

class PickSaveRequest(BaseModel):
    title: str = "Save as"
    default_name: str = ""
    ext: str = ""                          # e.g. ".pdf" / ".csv" for the save dialog

class PickFileRequest(BaseModel):
    title: str = "Open file"
    ext: str = ""                          # e.g. ".md" to filter the dialog

class TtsGenerateRequest(BaseModel):
    md_path: str                           # absolute path to the source .md file
    voice: str = "af_heart"
    model_path: str = tts_mod.MODEL_ID
    speed: float = 1.0                     # Kokoro speaking rate (1.0 = normal)

class OllamaPullRequest(BaseModel):
    model: str = ""

class OllamaUseRequest(BaseModel):
    model: str = ""

class OllamaInitRequest(BaseModel):
    model: str = ""

class CheatsheetRequest(BaseModel):
    course: str = ""
    max_pages: int = 1                     # A4 page budget for the cheat sheet
    save_path: Optional[str] = None        # exact PDF path chosen via Save As

class PracticeExamRequest(BaseModel):
    """Practice pack (default n=100) or configurable exam builder.

    ``n`` must be 10–150 (enforced by the practice-exam API route).
    """
    course: str = ""
    n: int = 100
    types: Optional[List[str]] = None      # mcq, short, long, cloze, truefalse
    difficulty: str = "medium"             # easy | medium | hard | mixed
    scope: str = "course"                  # lecture | week | topic | course
    target: str = ""                       # path / week# / topic string
    weights: Optional[Dict[str, float]] = None  # topic -> percent
    seed: Optional[str] = None
    include_answer_key: bool = True
    time_minutes: Optional[int] = None
    total_marks: Optional[int] = None
    kind: str = "practice"                 # practice | exam
    formats: Optional[List[str]] = None    # pdf, md
    save_path: Optional[str] = None

class CourseCreate(BaseModel):
    name: str
    code: str = ""
    semester: str = ""
    year: Optional[int] = None

class CourseUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    semester: Optional[str] = None
    year: Optional[int] = None
    archived: Optional[bool] = None

class SettingsUpdate(BaseModel):
    # Arbitrary preference bag (active_course, theme, export defaults, ai, sync…).
    # Stored JSON-encoded; reserved keys (schema_version) are ignored.
    values: Dict[str, Any]

class SavedViewCreate(BaseModel):
    name: str
    query: Dict[str, Any] = {}

class LLMSettings(BaseModel):
    values: Dict[str, Any]            # provider, model, temperature, max_tokens, retrieval_depth, host, api_key

class SummarizeReq(BaseModel):
    scope: str = "course"             # lecture | week | topic | course
    target: str = ""                  # path (lecture) | week number | topic

class FlashcardsAIReq(BaseModel):
    selection: Optional[List[str]] = None
    types: Optional[List[str]] = None
    course: str = ""
    max_cards: int = 20

class QuizReq(BaseModel):
    scope: str = "course"
    target: str = ""
    types: Optional[List[str]] = None
    difficulty: str = "medium"
    n: int = 8
    course: str = ""
    weights: Optional[Dict[str, float]] = None
    seed: Optional[str] = None
    include_answer_key: bool = True
    time_minutes: Optional[int] = None
    total_marks: Optional[int] = None
    kind: str = "quiz"                     # quiz | practice | exam
    formats: Optional[List[str]] = None
    save_path: Optional[str] = None

class ChatReq(BaseModel):
    query: str
    history: Optional[List[Dict[str, str]]] = None

class NotionSyncReq(BaseModel):
    course: str = ""                   # course name for the Course property
    token: str = ""                    # overrides stored/env token
    database_id: str = ""              # target Notion DB (overrides stored)

class AnkiSyncReq(BaseModel):
    deck: str = "Course Assistant"
    course: str = ""
    selection: Optional[List[str]] = None   # limit flashcard source lectures
    url: str = ""                      # overrides stored AnkiConnect URL

class MappingReq(BaseModel):
    target: str                        # "notion"
    fields: Dict[str, str]             # local field -> remote property name

class AssessmentReq(BaseModel):
    name: str
    due_date: str = ""
    weight: Optional[float] = None
    status: str = "not_started"
    course_id: Optional[int] = None    # defaults to the active course

class AssessmentUpdate(BaseModel):
    name: Optional[str] = None
    due_date: Optional[str] = None
    weight: Optional[float] = None
    status: Optional[str] = None

class StudySessionReq(BaseModel):
    duration: int                      # minutes
    activity_type: str = ""
    course_id: Optional[int] = None

class QuizAttemptReq(BaseModel):
    scope: str = ""
    score: float = 0
    total: int = 0
    mode: str = ""
    course_id: Optional[int] = None

class GradeReq(BaseModel):
    quality: int                       # 0–5 recall score (SM-2)

class NoteReq(BaseModel):
    path: str
    body: str
    course_id: Optional[int] = None
    timestamp_s: Optional[float] = None
    bookmark: bool = False

class NoteUpdate(BaseModel):
    body: Optional[str] = None
    timestamp_s: Optional[float] = None
    bookmark: Optional[bool] = None

class ItemTagReq(BaseModel):
    path: str
    name: str
    course_id: Optional[int] = None

class ExportNamedRequest(BaseModel):
    course: str = ""
    output_dir: Optional[str] = None   # also copy the file to this folder

class PracticeGradeReq(BaseModel):
    questions: List[Dict[str, Any]]
    answers: List[Any]
    course_id: Optional[int] = None
    record: bool = True

class MoodleUrlReq(BaseModel):
    url: str                           # .../course/view.php?id=NNNNN
    cookies: str = ""                  # browser session cookies (header/txt)
    follow_sections: bool = True       # also crawl linked section.php pages
    save_outline: bool = True          # write the outline as an AI source
    create_course: bool = False        # create + activate a course from the title

class MoodleFetchReq(BaseModel):
    url: str                           # .../course/view.php?id=NNNNN
    cookies: str = ""                  # browser session cookies (header/txt)
    keep_images: bool = True           # attach images to converted docs (default on)
    convert: bool = True               # convert downloaded files to Markdown
    export: str = ""                   # ""|"notebooklm"|"all" - also export after
    grab_lectures: bool = True         # detect & return Panopto lecture feeds
    grab_docs: bool = True             # download + convert resource documents

class MoodleConnectReq(BaseModel):
    url: str                           # site or course URL (host identifies the site)
    username: str = ""                 # for login/token.php token grant
    password: str = ""
    token: str = ""                    # …or paste a mobile web-service token (SSO sites)

class MoodleApiImportReq(BaseModel):
    url: str                           # site or course URL (host -> stored token)
    course_id: int                     # the course to import
    grab_lectures: bool = True         # surface lecture/recording feeds for transcription
    grab_docs: bool = True             # download + convert document files
    convert: bool = True               # convert downloaded files to Markdown
    keep_images: bool = True           # attach images to converted docs
    create_course: bool = False        # create + activate a local course from the title
    export: str = ""                   # ""|"notebooklm"|"all" - also export after

class FolderImportReq(BaseModel):
    path: str
    include_subfolders: bool = True
    course_id: Optional[int] = None    # defaults to the active course

class PreflightReq(BaseModel):
    path: str

class SecretReq(BaseModel):
    value: str

class ExportReq(BaseModel):
    preset: str = ""                   # revision|ai|exam|notion|anki|archive
    target: str = ""                   # …or a single target directly
    scope: str = "course"              # lecture|week|topic|course|all
    scope_target: str = ""             # path/week/topic for narrowed scopes
    course: str = ""

class RestoreReq(BaseModel):
    path: str
    overwrite: bool = False

class PanoptoDownloadRequest(BaseModel):
    lectures: List[Dict[str, Any]]    # recording dicts from /api/moodle/panopto-feed
    output_dir: str                   # folder to save the videos in
    cookies: str = ""

class DecodeLaunchTokenReq(BaseModel):
    raw: str   # full moodlemobile://token=... URL pasted from the browser address bar

class SsoCallbackReq(BaseModel):
    raw: str   # full courseassistant://token=… URL sent by the OS protocol handler

class PaperSearchReq(BaseModel):
    query: str
    year: int = 2026

class PaperOutlineFetchReq(BaseModel):
    paper_code: str = ""
    url: str = ""
    html: str = ""

class TaskScheduleBuildReq(BaseModel):
    paper_codes: List[str]
    class_schedule_id: Optional[int] = None
    name: str = ""
    course_id: Optional[int] = None

class MoodleAnnouncementsReq(BaseModel):
    url: str
    cookies: str = ""
    course_id: Optional[int] = None

class MoodleCalendarUrlReq(BaseModel):
    url: str = ""

class SemesterSyncAllReq(BaseModel):
    paper_codes: List[str]
    class_schedule_id: Optional[int] = None
    course_id: Optional[int] = None
    name: str = ""
    moodle_announcements_url: str = ""
    moodle_cookies: str = ""
    calendar_url: str = ""

class SuiteBuildReq(BaseModel):
    format: str = "obsidian"          # obsidian | notion | onenote
    output: str = "folder"            # folder | zip
    plan_id: Optional[int] = None
    title: str = ""
    dest_dir: Optional[str] = None    # write folder here (defaults to OUTPUT/_suites)

class SuiteSyncReq(BaseModel):
    formats: Optional[List[str]] = None
    plan_id: Optional[int] = None
    paper_codes: Optional[List[str]] = None
    course_id: Optional[int] = None
    name: str = ""
    push_live: bool = True
    moodle_announcements_url: str = ""
    moodle_cookies: str = ""
    calendar_url: str = ""
    class_schedule_id: Optional[int] = None
    discover_panopto: bool = True
    panopto_url: str = ""
    use_browser: bool = False

class SuiteSettingsReq(BaseModel):
    destinations: Optional[Dict[str, str]] = None
    enabled: Optional[List[str]] = None
    auto_sync: Optional[bool] = None

class PanoptoDiscoverReq(BaseModel):
    moodle_html: str = ""
    moodle_url: str = ""
    panopto_url: str = ""
    cookies: str = ""
    use_playwright: bool = False
