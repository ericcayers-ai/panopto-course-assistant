"""Edge-case tests for app.core (and a couple of API behaviours).

Run with:  python -m pytest -q
These are deliberately exhaustive about messy real-world input: malformed feeds,
missing fields, odd titles, timestamp rounding, and the export/search/reorganize
flows that tie the app together.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from app import core


# ---------------------------------------------------------------------------
# safe_name / slug-ish helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected_contains",
    [
        ("Week 1: Intro/Outro", "Week_1_IntroOutro"),
        ('bad<>:"/\\|?*chars', "badchars"),
        ("   spaced   out   ", "spaced_out"),
        ("___leading_trailing___", "leading_trailing"),
    ],
)
def test_safe_name_sanitizes(raw, expected_contains):
    assert core.safe_name(raw) == expected_contains


def test_safe_name_empty_and_long():
    assert core.safe_name("") == "lecture"
    assert core.safe_name("   ") == "lecture"
    assert len(core.safe_name("a" * 500)) <= 120


# ---------------------------------------------------------------------------
# human_size / human_duration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "n,expected",
    [(0, "?"), (-5, "?"), (512, "512 B"), (1536, "1.5 KB"), (196369884, "187.3 MB")],
)
def test_human_size(n, expected):
    assert core.human_size(n) == expected


@pytest.mark.parametrize("s,expected", [(0, "?"), (-1, "?"), (5552, "1:32:32"), (60, "0:01:00")])
def test_human_duration(s, expected):
    assert core.human_duration(s) == expected


# ---------------------------------------------------------------------------
# pubdate parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("Mon, 02 Mar 2026 02:14:43 GMT", dt.date(2026, 3, 2)),
        ("2026-03-02", dt.date(2026, 3, 2)),
        ("02/03/2026", dt.date(2026, 3, 2)),  # %d/%m/%Y wins: day=02, month=03
        ("2026-03-02T02:14:43Z", dt.date(2026, 3, 2)),
        ("", None),
        ("not a date", None),
    ],
)
def test_parse_pubdate(value, expected):
    assert core.parse_pubdate(value) == expected


# ---------------------------------------------------------------------------
# week / topic inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,week",
    [
        ("Week_1_OS_Basics_Processes", 1),
        ("Week2_CPU_Scheduling", 2),
        ("Week 08 - Application Layer", 8),
        ("W3 Foo", 3),
        ("Week10 Net", 10),
        ("Week11 Link layer", 11),
        ("No week here", None),
        ("", None),
    ],
)
def test_infer_week(title, week):
    assert core.infer_week(title) == week


def test_infer_topic_strips_week_and_noise():
    assert core.infer_topic("Week 09 - Transport layer") == "Transport_layer"
    assert core.infer_topic("Week4_Memory_mgmt (old)") != ""
    assert core.infer_topic("") == "uncategorized"


# ---------------------------------------------------------------------------
# feed parsing
# ---------------------------------------------------------------------------

VALID_FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>COMPX234 Test</title>
    <item>
      <title>Week2_CPU_Scheduling</title>
      <itunes:author>vkumar</itunes:author>
      <itunes:summary>summary text</itunes:summary>
      <enclosure url="http://x/y.mp4" length="266448060" type="video/mp4" />
      <guid>http://x/y.mp4</guid>
      <pubDate>Mon, 09 Mar 2026 02:13:40 GMT</pubDate>
      <itunes:duration>6572</itunes:duration>
    </item>
    <item>
      <title>No enclosure here</title>
    </item>
    <item>
      <enclosure url="" />
    </item>
    <item>
      <enclosure url="http://x/z.mp4" length="not-a-number" />
      <itunes:duration>bad</itunes:duration>
    </item>
  </channel>
</rss>"""


def test_parse_feed_skips_invalid_items():
    items = core.parse_feed_bytes(VALID_FEED)
    # only items with a usable enclosure url survive (item 1 and item 4)
    assert len(items) == 2
    first = items[0]
    assert first.title == "Week2_CPU_Scheduling"
    assert first.size == 266448060
    assert first.duration == 6572
    assert first.author == "vkumar"
    assert first.week == 2
    # bad numeric fields degrade to 0, do not crash
    assert items[1].size == 0
    assert items[1].duration == 0


def test_channel_title():
    assert core.channel_title(VALID_FEED) == "COMPX234 Test"
    assert core.channel_title(b"not xml") == ""


# Panopto's audio-podcast feed: the itunes namespace is declared as *https* and
# enclosure URLs carry a ?mediaTargetType=audioPodcast query. Both must work.
AUDIO_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:itunes="https://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>COMPX234-26A &amp; (TGA) - Systems and Networks</title>
    <item>
      <title>Week_1_OS_Basics_Processes</title>
      <itunes:author>elearn\\vkumar</itunes:author>
      <itunes:summary>Teams Meeting</itunes:summary>
      <enclosure url="https://waikato.au.panopto.com/Panopto/Podcast/Syndication/abc.mp4?mediaTargetType=audioPodcast" length="40604986" type="video/mp4" />
      <guid>https://waikato.au.panopto.com/Panopto/Podcast/Syndication/abc.mp4</guid>
      <pubDate>Mon, 02 Mar 2026 02:14:43 GMT</pubDate>
      <itunes:duration>5552</itunes:duration>
    </item>
    <item>
      <title>Week3_semaphore_usage</title>
      <enclosure url="https://waikato.au.panopto.com/Panopto/Podcast/Syndication/def.mp4?mediaTargetType=audioPodcast" length="13679611" type="video/mp4" />
      <guid>https://waikato.au.panopto.com/Panopto/Podcast/Syndication/def.mp4</guid>
      <pubDate>Tue, 07 Apr 2026 04:09:49 GMT</pubDate>
      <itunes:duration>857</itunes:duration>
    </item>
  </channel>
</rss>"""


def test_parse_audio_podcast_feed():
    assert "Systems and Networks" in core.channel_title(AUDIO_FEED)
    items = core.parse_feed_bytes(AUDIO_FEED)
    assert len(items) == 2
    first = items[0]
    # the audioPodcast query must be preserved on the media URL we download from
    assert first.url.endswith("?mediaTargetType=audioPodcast")
    assert first.week == 1 and first.duration == 5552 and first.size == 40604986
    # https itunes namespace still resolves author/summary
    assert first.author == "elearn\\vkumar"
    assert first.summary == "Teams Meeting"
    # an item with no itunes:summary must still parse
    assert items[1].week == 3 and items[1].summary == ""


def test_parse_feed_bytes_no_channel():
    assert core.parse_feed_bytes(b"<rss></rss>") == []


def test_parse_feed_empty_source_raises():
    with pytest.raises(ValueError):
        core.parse_feed("")


def test_lecture_to_dict_roundtrip():
    item = core.LectureItem(title="Week3_Sync", url="u", size=10, duration=60,
                            pub_date="Mon, 16 Mar 2026 02:10:37 GMT")
    d = item.to_dict()
    assert d["week"] == 3
    assert d["date"] == "2026-03-16"
    assert d["duration_human"] == "0:01:00"
    assert d["safe_title"] == "Week3_Sync"


# ---------------------------------------------------------------------------
# timestamp formatters (including ms == 1000 rounding edge)
# ---------------------------------------------------------------------------

def test_timestamp_formats():
    assert core.ts_hhmmss(3661) == "01:01:01"
    assert core.ts_srt(1.5) == "00:00:01,500"
    assert core.ts_vtt(1.5) == "00:01.500"
    assert core.ts_vtt(3661.0) == "01:01:01.000"


def test_timestamp_rounding_carry():
    # 0.9996s rounds to 1000ms -> must carry into seconds, not print ,1000
    assert core.ts_srt(0.9996) == "00:00:01,000"
    assert core.ts_vtt(0.9996) == "00:01.000"


def test_negative_timestamp_clamped():
    assert core.ts_hhmmss(-5) == "00:00:00"


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------

SEGS = [
    {"start": 0, "end": 4, "text": "Hello there."},
    {"start": 4, "end": 8, "text": "TCP is reliable."},
    {"start": 35, "end": 40, "text": "Goodbye now."},
]


def test_render_txt_buckets_by_interval():
    out = core.render_txt(SEGS, interval=30)
    # second block is stamped with the first segment's actual start (35s), not the
    # bucket boundary (30s) — segments 0-8s collapse into bucket 0, 35s into bucket 1
    assert "[00:00:00]" in out and "[00:00:35]" in out
    assert out.endswith("\n")


def test_render_txt_skips_empty_segments():
    out = core.render_txt([{"start": 0, "text": "  "}, {"start": 1, "text": "real"}], 30)
    assert "real" in out


def test_render_srt_and_vtt():
    srt = core.render_srt(SEGS)
    assert srt.startswith("1\n")
    assert "-->" in srt
    vtt = core.render_vtt(SEGS)
    assert vtt.startswith("WEBVTT")


def test_render_srt_handles_end_before_start():
    out = core.render_srt([{"start": 10, "end": 5, "text": "x"}])
    # end clamped to start; both timestamps identical, no crash
    assert "00:00:10,000 --> 00:00:10,000" in out


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summarize_short_text_returns_all():
    out = core.summarize_text("One sentence only.")
    assert out == ["One sentence only."]


def test_summarize_empty():
    assert core.summarize_text("") == []
    assert core.summarize_text("   ") == []


def test_summarize_picks_salient_and_keeps_order():
    text = (
        "TCP provides reliable data transfer. "
        "TCP uses a three way handshake. "
        "The weather is sunny today. "
        "TCP reliability uses retransmission. "
        "Bananas are yellow. "
        "TCP congestion control prevents overload. "
        "Cats sleep a lot. "
        "TCP flow control uses windows. "
        "Random unrelated filler sentence here. "
        "TCP powers HTTP and email."
    )
    out = core.summarize_text(text, max_sentences=4)
    assert len(out) == 4
    # TCP-heavy sentences should dominate
    assert sum("TCP" in s for s in out) >= 3
    # order preserved: selected sentences appear in their original document order
    positions = [text.index(s) for s in out]
    assert positions == sorted(positions)


def test_render_summary_markdown():
    item = core.LectureItem(title="Week2_CPU", url="u")
    md = core.render_summary(item, SEGS, "")
    assert md.startswith("# Summary — Week2_CPU")
    assert "Key points" in md


# ---------------------------------------------------------------------------
# write_outputs / listing
# ---------------------------------------------------------------------------

def _seed(tmp_path: Path, title="Week2_CPU_Scheduling", formats=("txt", "srt", "md", "json")):
    item = core.LectureItem(title=title, url="u", duration=60,
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT", author="vkumar")
    out = core.output_dir_for(tmp_path, item, "week")
    core.write_outputs(item, SEGS, "Hello there. TCP is reliable. Goodbye now.",
                       out, list(formats), 30,
                       {"engine": "fw", "model": "small", "language": "en", "course": "COMPX234"})
    return item


def test_write_outputs_writes_requested_formats(tmp_path):
    _seed(tmp_path, formats=("txt", "srt", "vtt", "md", "json", "notebooklm", "summary"))
    groups = core.list_transcripts(tmp_path)
    assert len(groups) == 1
    assert set(groups[0]["formats"]) == {"txt", "srt", "vtt", "md", "json", "notebooklm", "summary"}
    assert groups[0]["folder"] == "Week_02"


def test_write_outputs_invalid_format_ignored(tmp_path):
    item = core.LectureItem(title="X", url="u")
    w = core.write_outputs(item, SEGS, "t", tmp_path, ["txt", "bogus"], 30, {})
    assert "txt" in w and "bogus" not in w


def test_json_output_has_segments_and_text(tmp_path):
    _seed(tmp_path, formats=("json",))
    j = next(tmp_path.rglob("*.json"))
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["text"].startswith("Hello there.")
    assert len(data["segments"]) == 3
    assert data["week"] == 2


# ---------------------------------------------------------------------------
# read_transcript_file + path traversal guard
# ---------------------------------------------------------------------------

def test_read_transcript_file_ok(tmp_path):
    _seed(tmp_path)
    rel = core.list_transcripts(tmp_path)[0]["formats"]["txt"]
    assert "Hello there." in core.read_transcript_file(tmp_path, rel)


def test_read_transcript_traversal_blocked(tmp_path):
    with pytest.raises(ValueError):
        core.read_transcript_file(tmp_path, "../../etc/passwd")


def test_read_transcript_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        core.read_transcript_file(tmp_path, "nope.txt")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def test_search_one_result_per_lecture(tmp_path):
    _seed(tmp_path)  # has txt + md, both contain "TCP"
    res = core.search_transcripts(tmp_path, "TCP")
    assert len(res) == 1
    assert res[0]["count"] >= 1
    assert res[0]["lecture"] == "Week2_CPU_Scheduling"


def test_search_empty_query(tmp_path):
    _seed(tmp_path)
    assert core.search_transcripts(tmp_path, "") == []
    assert core.search_transcripts(tmp_path, "   ") == []


def test_search_no_match(tmp_path):
    _seed(tmp_path)
    assert core.search_transcripts(tmp_path, "zzzznotfound") == []


def test_search_case_insensitive(tmp_path):
    _seed(tmp_path)
    assert core.search_transcripts(tmp_path, "tcp")
    assert core.search_transcripts(tmp_path, "TCP")


def test_search_special_chars_no_regex_crash(tmp_path):
    _seed(tmp_path)
    # str.find is used, so regex metachars are literal and safe
    assert core.search_transcripts(tmp_path, "a.b*c[") == []


def test_search_empty_dir(tmp_path):
    assert core.search_transcripts(tmp_path, "x") == []


# ---------------------------------------------------------------------------
# NotebookLM export
# ---------------------------------------------------------------------------

def test_paragraphs_from_texts():
    texts = ["Short.", "Another bit here", "more words to push over the limit." * 20]
    paras = core.paragraphs_from_texts(texts, target_chars=50)
    assert len(paras) >= 1
    assert all(p.strip() for p in paras)


def test_paragraphs_handles_no_terminal_punctuation():
    # never-ending sentence with no . ! ? must still flush as one paragraph
    paras = core.paragraphs_from_texts(["word " * 300], target_chars=100)
    assert len(paras) == 1


def test_clean_txt_strips_timestamps():
    raw = "[00:00:00]  Hello there.\n\n[00:00:30]  TCP is reliable."
    out = core.clean_txt_to_notebooklm(raw, title="T", course="C")
    assert "[00:00:00]" not in out
    assert "Hello there." in out
    assert out.startswith("# T")


def test_export_notebooklm_from_json(tmp_path):
    _seed(tmp_path, formats=("json",))
    res = core.export_notebooklm(tmp_path, combined=True, course="COMPX234")
    assert res["count"] == 1
    assert res["combined"] is not None
    body = (tmp_path / res["files"][0]).read_text(encoding="utf-8")
    assert "### " not in body and "[00:" not in body
    assert body.startswith("# ")


def test_export_notebooklm_from_txt_only(tmp_path):
    _seed(tmp_path, formats=("txt",))
    res = core.export_notebooklm(tmp_path)
    assert res["count"] == 1


def test_export_notebooklm_selection_filter(tmp_path):
    _seed(tmp_path, title="Week2_CPU_Scheduling", formats=("json",))
    _seed(tmp_path, title="Week3_Sync", formats=("json",))
    res = core.export_notebooklm(tmp_path, selection=["Week_03/Week3_Sync"])
    assert res["count"] == 1
    assert "Week3_Sync" in res["files"][0]


def test_export_notebooklm_nothing(tmp_path):
    res = core.export_notebooklm(tmp_path)
    assert res["count"] == 0
    assert res["combined"] is None


def test_export_all_sources_combines_everything(tmp_path):
    # a transcript, a converted document, and a Notion page in the library
    _seed(tmp_path, title="Week2_CPU_Scheduling", formats=("json",))
    docs = core.ensure_dir(tmp_path / core.DOCS_DIRNAME)
    (docs / "Lecture_Slides.md").write_text("# Slides\nslide content\n", encoding="utf-8")
    (docs / "documents_pack.md").write_text("combined — must be skipped\n", encoding="utf-8")
    notion = core.ensure_dir(tmp_path / core.NOTION_DIRNAME)
    (notion / "Study_Notes.md").write_text("# Notes\nnotion content\n", encoding="utf-8")

    res = core.export_all_sources(tmp_path, combined=True, course="COMPX234")
    assert res["transcripts"] == 1
    assert res["documents"] == 1          # the pack file is excluded
    assert res["notion"] == 1
    assert res["count"] == 3
    pack = (tmp_path / res["combined"]).read_text(encoding="utf-8")
    assert "All sources" in pack
    assert "slide content" in pack and "notion content" in pack
    # the self-generated combined pack must not be folded back in
    assert "must be skipped" not in pack


def test_export_all_sources_nothing(tmp_path):
    res = core.export_all_sources(tmp_path)
    assert res["count"] == 0
    assert res["combined"] is None


def test_export_formats_generates_from_json(tmp_path):
    _seed(tmp_path, title="Week2_CPU_Scheduling", formats=("json",))
    res = core.export_formats(tmp_path, ["srt", "vtt"])
    assert res["count"] == 2
    assert sorted(res["formats"]) == ["srt", "vtt"]
    # files now appear in the library as extra formats on the lecture
    fmts = core.list_transcripts(tmp_path)[0]["formats"]
    assert "srt" in fmts and "vtt" in fmts


def test_export_formats_ignores_json_and_unknown(tmp_path):
    _seed(tmp_path, formats=("json",))
    res = core.export_formats(tmp_path, ["json", "bogus"])
    assert res["count"] == 0


def test_list_library_categorises_everything(tmp_path):
    _seed(tmp_path, title="Week2_CPU_Scheduling", formats=("txt", "json"))
    core.ensure_dir(tmp_path / core.DOCS_DIRNAME).joinpath("Slides.md").write_text("x", encoding="utf-8")
    core.ensure_dir(tmp_path / core.NOTION_DIRNAME).joinpath("Notes.md").write_text("y", encoding="utf-8")
    core.ensure_dir(tmp_path / core.NOTEBOOKLM_DIRNAME).joinpath("pack.md").write_text("z", encoding="utf-8")
    (tmp_path / "COMPX_outline.md").write_text("# outline", encoding="utf-8")

    lib = core.list_library(tmp_path)
    c = lib["counts"]
    assert c["transcripts"] == 1
    assert c["documents"] == 1
    assert c["notion"] == 1
    assert c["exports"] == 1
    # the top-level outline (not part of a transcript group) shows under "others"
    assert any(f["name"] == "COMPX_outline.md" for f in lib["categories"]["others"])
    assert c["total"] >= 5


def test_export_excluded_from_listing_and_search(tmp_path):
    _seed(tmp_path, formats=("txt", "json"))
    core.export_notebooklm(tmp_path, combined=True, course="C")
    # exported files live under _notebooklm/ and must not appear as lectures
    stems = [g["stem"] for g in core.list_transcripts(tmp_path)]
    assert stems == ["Week2_CPU_Scheduling"]
    # nor inflate search results
    assert len(core.search_transcripts(tmp_path, "TCP")) == 1


# ---------------------------------------------------------------------------
# reorganize
# ---------------------------------------------------------------------------

def test_reorganize_moves_into_week_folders(tmp_path):
    # seed flat (organize none) then reorganize by week
    item = core.LectureItem(title="Week5_Sockets", url="u",
                            pub_date="Mon, 30 Mar 2026 02:00:00 GMT")
    core.write_outputs(item, SEGS, "t", tmp_path, ["txt", "json"], 30, {})
    assert core.list_transcripts(tmp_path)[0]["folder"] == ""
    moved = core.reorganize_outputs(tmp_path, "week")
    assert moved
    assert core.list_transcripts(tmp_path)[0]["folder"] == "Week_05"


def test_reorganize_idempotent(tmp_path):
    _seed(tmp_path)  # already in Week_02
    moved = core.reorganize_outputs(tmp_path, "week")
    assert moved == []  # nothing to move


# ---------------------------------------------------------------------------
# split_stem_format helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,stem,fmt",
    [
        ("Lecture.txt", "Lecture", "txt"),
        ("Lecture.notebooklm.md", "Lecture", "notebooklm"),
        ("Lecture.summary.md", "Lecture", "summary"),
        ("Lecture_3.1.json", "Lecture_3.1", "json"),
        ("noext", "noext", ""),
    ],
)
def test_split_stem_format(name, stem, fmt):
    assert core._split_stem_format(name) == (stem, fmt)


# ---------------------------------------------------------------------------
# Panopto podcast feed variants + audio/video merge
# ---------------------------------------------------------------------------

def test_panopto_feed_variants_swaps_type():
    base = ("https://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?"
            "courseid=cd705e4f-59d7-4966-9b28-b3fb01759331&type=mp4")
    v = core.panopto_feed_variants(base)
    assert "type=mp3" in v["audio"]
    assert "type=mp4" in v["video"]
    assert "courseid=cd705e4f-59d7-4966-9b28-b3fb01759331" in v["audio"]


def test_panopto_feed_variants_without_type_is_passthrough():
    url = "https://example.com/feed.xml"
    v = core.panopto_feed_variants(url)
    assert v == {"audio": url, "video": url}


def test_merge_panopto_variants_pairs_audio_with_video():
    audio = [core.LectureItem(title="Week11_Link Layer", url="https://x/a/w11.mp3"),
             core.LectureItem(title="Week10 Net", url="https://x/a/w10.mp3")]
    video = [core.LectureItem(title="Week11_Link Layer", url="https://x/v/w11.mp4"),
             core.LectureItem(title="Week10 Net", url="https://x/v/w10.mp4")]
    merged = core.merge_panopto_variants(audio, video)
    assert merged[0]["url"] == "https://x/a/w11.mp3"           # transcribe from audio
    assert merged[0]["video_url"] == "https://x/v/w11.mp4"     # SRT export uses video


def test_merge_panopto_variants_video_only_fallback():
    video = [core.LectureItem(title="Week11", url="https://x/v/w11.mp4")]
    merged = core.merge_panopto_variants([], video)
    assert merged[0]["url"] == "https://x/v/w11.mp4"
    assert merged[0]["video_url"] == "https://x/v/w11.mp4"
