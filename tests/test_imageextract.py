"""Image/diagram preservation: extract embedded images from docs so they aren't
lost on conversion, and attach them to the Markdown."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app import imageextract, core, notion

PIL = pytest.importorskip("PIL")
from PIL import Image


def _png_bytes(color=(200, 30, 30), size=(16, 16)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _make_office(path: Path, media_dir: str):
    """Minimal Office/EPUB zip with two images under a media folder."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(f"{media_dir}/image1.png", _png_bytes((10, 200, 10)))
        zf.writestr(f"{media_dir}/image2.jpeg", _png_bytes((10, 10, 200)))
        zf.writestr(f"{media_dir}/diagram.emf", b"\x01\x00\x00\x00vector")


def test_capability_office_always_pdf_optional():
    cap = imageextract.capability()
    assert cap["office_epub"] is True
    assert isinstance(cap["pdf"], bool)


def test_extract_pptx_images(tmp_path: Path):
    pptx = tmp_path / "Lecture3_Slides.pptx"
    _make_office(pptx, "ppt/media")
    assets = tmp_path / "assets"
    imgs = imageextract.extract_images(pptx, assets)
    # 2 renderable (png, jpeg) + 1 preserved-but-not-inline (emf)
    assert len(imgs) == 3
    assert sum(1 for i in imgs if i["renderable"]) == 2
    # every reported file is actually written to disk
    for rec in imgs:
        assert (assets / rec["file"]).exists()
    assert any(f.suffix == ".png" for f in assets.iterdir())
    assert any(f.suffix == ".jpeg" for f in assets.iterdir())


def test_extract_docx_images(tmp_path: Path):
    docx = tmp_path / "Assignment1.docx"
    _make_office(docx, "word/media")
    imgs = imageextract.extract_images(docx, tmp_path / "a")
    assert len([i for i in imgs if i["renderable"]]) == 2


def test_images_markdown_inlines_renderables(tmp_path: Path):
    imgs = [{"file": "image01.png", "index": 1, "ext": ".png", "renderable": True},
            {"file": "diagram.emf", "index": 2, "ext": ".emf", "renderable": False}]
    md = imageextract.images_markdown(imgs, "slides_assets", "Lecture")
    assert "## Images & diagrams" in md
    assert "![Figure 1](slides_assets/image01.png)" in md
    assert "1 additional vector image" in md


def test_pdf_image_extraction_if_supported(tmp_path: Path):
    if not imageextract.capability()["pdf"]:
        pytest.skip("no PDF image backend installed")
    pdf = tmp_path / "Notes.pdf"
    Image.new("RGB", (40, 30), (123, 50, 200)).save(str(pdf), "PDF")
    imgs = imageextract.extract_images(pdf, tmp_path / "passets")
    assert len(imgs) >= 1
    assert any((tmp_path / "passets" / i["file"]).exists() for i in imgs)


def test_convert_documents_attaches_images(tmp_path: Path):
    pptx = tmp_path / "Week2_Slides.pptx"
    _make_office(pptx, "ppt/media")
    out = tmp_path / "out"
    res = core.convert_documents(pptx, out, target="ai",
                                 converter=lambda p: "Slide text body\n")
    rec = res["files"][0]
    assert rec.get("images") == 3
    md_path = out / "_docs" / "Week2_Slides.md"
    body = md_path.read_text(encoding="utf-8")
    assert "## Images & diagrams" in body
    assert "Week2_Slides_assets.zip" in body
    assert (out / "_docs" / "Week2_Slides_assets.zip").exists()
    assert not (out / "_docs" / "Week2_Slides_assets").exists()


def test_convert_documents_keep_images_false(tmp_path: Path):
    pptx = tmp_path / "S.pptx"
    _make_office(pptx, "ppt/media")
    out = tmp_path / "out"
    res = core.convert_documents(pptx, out, target="ai", keep_images=False,
                                 converter=lambda p: "text\n")
    assert "images" not in res["files"][0]
    assert not (out / "_docs" / "S_assets").exists()


def test_images_preserved_even_when_text_conversion_fails(tmp_path: Path):
    pptx = tmp_path / "Broken_Slides.pptx"
    _make_office(pptx, "ppt/media")
    out = tmp_path / "out"

    def boom(p):
        raise RuntimeError("markitdown cannot read this")

    res = core.convert_documents(pptx, out, target="ai", converter=boom)
    rec = res["files"][0]
    assert rec.get("images") == 3 and "text_error" in rec
    body = (out / "_docs" / "Broken_Slides.md").read_text(encoding="utf-8")
    assert "Text could not be extracted" in body
    assert "## Images & diagrams" in body
    assert (out / "_docs" / "Broken_Slides_assets.zip").exists()
    assert not (out / "_docs" / "Broken_Slides_assets").exists()


def test_pack_assets_to_zip(tmp_path: Path):
    pptx = tmp_path / "Deck.pptx"
    _make_office(pptx, "ppt/media")
    assets = tmp_path / "Deck_assets"
    imgs = imageextract.extract_images(pptx, assets)
    assert assets.is_dir()
    zip_path = imageextract.pack_assets_to_zip(assets)
    assert zip_path == tmp_path / "Deck_assets.zip"
    assert zip_path.exists()
    assert not assets.exists()                  # folder removed
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert all(img["file"] in names for img in imgs)


def test_images_markdown_packed():
    imgs = [{"file": "image01.png", "index": 1, "ext": ".png", "renderable": True},
            {"file": "image02.jpg", "index": 2, "ext": ".jpg", "renderable": True, "page": 3}]
    md = imageextract.images_markdown_packed(imgs, "slides_assets.zip", "Lecture")
    assert "## Images & diagrams" in md
    assert "slides_assets.zip" in md
    assert "Figure 1" in md
    assert "Figure 2" in md and "p.3" in md
    assert "![" not in md   # no broken inline image syntax


def test_notion_preserves_and_copies_images(tmp_path: Path):
    # a Notion-style export folder: one html page referencing a local image
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "diagram.png").write_bytes(_png_bytes())
    html = ('<html><head><title>Networks Notes</title></head><body>'
            '<h1>Networks Notes</h1><p>The TCP handshake:</p>'
            '<img src="img/diagram.png" alt="three-way handshake"></body></html>')
    (tmp_path / "Networks Notes.html").write_text(html, encoding="utf-8")
    out = tmp_path / "out"
    res = notion.convert_notion_export(tmp_path, out)
    assert res["count"] == 1
    md_path = out / res["files"][0]               # robust to safe_name spacing
    md = md_path.read_text(encoding="utf-8")
    assert "three-way handshake" in md            # alt text preserved
    assert "_assets/image01.png" in md            # rewritten to copied asset
    assert list((out / "_notion").rglob("image01.png"))   # asset actually copied
