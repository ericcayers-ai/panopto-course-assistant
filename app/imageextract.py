"""
imageextract.py — preserve imagery/diagrams when converting documents (§7/§9).

`markitdown` gives us clean *text* but drops every image — so diagrams in lecture
slides, figures in assignments and worked-example screenshots are silently lost.
This module pulls those images **out** of the source and saves them next to the
converted Markdown, so the `.md` can reference the real pictures (accuracy-first)
instead of describing them.

Backends, cheapest first:
* **Office / EPUB** (`.pptx .docx .xlsx .epub`) are ZIP containers — embedded
  images live under ``*/media/*`` and come out with the **standard library only**
  (no extra deps). This covers the cases the user named: slides and assignments.
* **PDF** uses PyMuPDF (``fitz``) when present, else **pdfplumber + Pillow**
  (already optional deps). Absent both, PDFs degrade gracefully (text still
  converts; ``capability()`` reports the gap).

Images are written to a sibling ``<stem>_assets/`` folder and returned as records
so the caller can append an "Images & diagrams" section to the Markdown.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Raster/vector formats we can reference inline in Markdown (emf/wmf are preserved
# on disk but not inlined — most renderers/NotebookLM can't display them).
RENDERABLE = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
_ALL_IMG = RENDERABLE | {".tif", ".tiff", ".emf", ".wmf"}

_ZIP_MEDIA_DIRS = ("media",)            # ppt/media, word/media, xl/media, OEBPS/images…
_ZIP_EXTS = {".pptx", ".docx", ".xlsx", ".epub"}


def _have(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def capability() -> Dict[str, Any]:
    """What image extraction can do on this machine (for /api/status)."""
    office = True                       # stdlib zip — always available
    pdf_backend = "pymupdf" if _have("fitz") else (
        "pdfplumber" if (_have("pdfplumber") and _have("PIL")) else None)
    return {
        "office_epub": office,
        "pdf": pdf_backend is not None,
        "pdf_backend": pdf_backend,
        "reason": "" if pdf_backend else
                  "Install PyMuPDF (pip install pymupdf) to keep images from PDFs.",
    }


def supports(path: Path) -> bool:
    ext = Path(path).suffix.lower()
    if ext in _ZIP_EXTS:
        return True
    if ext == ".pdf":
        return capability()["pdf"]
    return False


# ---------------------------------------------------------------------------
# Office / EPUB (ZIP) — stdlib only
# ---------------------------------------------------------------------------


def _extract_zip_media(src: Path, assets_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        zf = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError):
        return out
    with zf:
        members = [m for m in zf.namelist()
                   if any(f"/{d}/" in ("/" + m) for d in _ZIP_MEDIA_DIRS)
                   and Path(m).suffix.lower() in _ALL_IMG]
        # Stable order (image1, image2, …) so figures keep their document order.
        members.sort(key=lambda m: (len(m), m))
        assets_dir.mkdir(parents=True, exist_ok=True)
        for i, m in enumerate(members, 1):
            ext = Path(m).suffix.lower()
            name = f"image{i:02d}{ext}"
            try:
                data = zf.read(m)
            except Exception:
                continue
            (assets_dir / name).write_bytes(data)
            out.append({"file": name, "index": i, "ext": ext,
                        "renderable": ext in RENDERABLE,
                        "source_member": m})
    return out


# ---------------------------------------------------------------------------
# PDF — PyMuPDF preferred, pdfplumber + Pillow fallback
# ---------------------------------------------------------------------------


def _extract_pdf_pymupdf(src: Path, assets_dir: Path) -> List[Dict[str, Any]]:
    import fitz  # PyMuPDF
    out: List[Dict[str, Any]] = []
    doc = fitz.open(str(src))
    assets_dir.mkdir(parents=True, exist_ok=True)
    seen, i = set(), 0
    for pno in range(len(doc)):
        for img in doc.get_page_images(pno, full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:          # CMYK/other → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                i += 1
                name = f"image{i:02d}.png"
                pix.save(str(assets_dir / name))
                out.append({"file": name, "index": i, "ext": ".png",
                            "renderable": True, "page": pno + 1})
            except Exception:
                continue
    doc.close()
    return out


def _extract_pdf_pdfplumber(src: Path, assets_dir: Path) -> List[Dict[str, Any]]:
    import pdfplumber
    from PIL import Image
    out: List[Dict[str, Any]] = []
    assets_dir.mkdir(parents=True, exist_ok=True)
    i = 0
    with pdfplumber.open(str(src)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for im in page.images:
                stream = im.get("stream")
                if stream is None:
                    continue
                try:
                    rec = _pdf_image_to_file(stream, im, assets_dir, i + 1, pno, Image)
                except Exception:
                    rec = None
                if rec:
                    i += 1
                    out.append(rec)
    return out


def _pdf_image_to_file(stream, im, assets_dir: Path, idx: int, pno: int, Image) -> Optional[Dict[str, Any]]:
    attrs = getattr(stream, "attrs", {}) or {}
    filt = attrs.get("Filter")
    names = _filter_names(filt)
    w = int(attrs.get("Width") or im.get("width") or 0)
    h = int(attrs.get("Height") or im.get("height") or 0)

    # JPEG-compressed image data is already a valid file — write it straight out.
    if "DCTDecode" in names or "JPXDecode" in names:
        data = stream.get_data()
        ext = ".jpg" if "DCTDecode" in names else ".jp2"
        name = f"image{idx:02d}{ext}"
        (assets_dir / name).write_bytes(data)
        return {"file": name, "index": idx, "ext": ext, "renderable": ext == ".jpg",
                "page": pno}

    # Raw (Flate/none) pixel data → rebuild via Pillow from width/height/colorspace.
    if not (w and h):
        return None
    data = stream.get_data()
    mode = _pdf_color_mode(attrs, len(data), w, h)
    if mode is None:
        return None
    img = Image.frombytes(mode, (w, h), data)
    if mode == "CMYK":
        img = img.convert("RGB")
    name = f"image{idx:02d}.png"
    img.save(str(assets_dir / name))
    return {"file": name, "index": idx, "ext": ".png", "renderable": True, "page": pno}


def _filter_names(filt) -> List[str]:
    if filt is None:
        return []
    if isinstance(filt, (list, tuple)):
        return [getattr(f, "name", str(f)) for f in filt]
    return [getattr(filt, "name", str(filt))]


def _pdf_color_mode(attrs: Dict[str, Any], nbytes: int, w: int, h: int) -> Optional[str]:
    """Best-effort Pillow mode from the colorspace, validated against byte count."""
    cs = attrs.get("ColorSpace")
    cs_name = getattr(cs, "name", str(cs)) if cs is not None else ""
    for mode, channels in (("RGB", 3), ("L", 1), ("CMYK", 4)):
        if nbytes >= w * h * channels and (
            (mode == "RGB" and "RGB" in cs_name) or
            (mode == "L" and "Gray" in cs_name) or
            (mode == "CMYK" and "CMYK" in cs_name)):
            return mode
    # Fall back by sheer size when the colorspace is indirect/indexed.
    for mode, channels in (("RGB", 3), ("L", 1)):
        if nbytes == w * h * channels:
            return mode
    return None


def _extract_pdf(src: Path, assets_dir: Path) -> List[Dict[str, Any]]:
    if _have("fitz"):
        return _extract_pdf_pymupdf(src, assets_dir)
    if _have("pdfplumber") and _have("PIL"):
        return _extract_pdf_pdfplumber(src, assets_dir)
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_images(src: Path, assets_dir: Path) -> List[Dict[str, Any]]:
    """Extract embedded images from ``src`` into ``assets_dir``. Returns a record
    per image: ``{file, index, ext, renderable, page?/source_member?}``. Never
    raises on a bad source — returns ``[]`` so text conversion still succeeds."""
    src = Path(src)
    ext = src.suffix.lower()
    try:
        if ext in _ZIP_EXTS:
            return _extract_zip_media(src, assets_dir)
        if ext == ".pdf":
            return _extract_pdf(src, assets_dir)
    except Exception:
        return []
    return []


def images_markdown(images: List[Dict[str, Any]], assets_relname: str,
                   title: str = "") -> str:
    """An "Images & diagrams" Markdown section linking the extracted files
    relative to the converted ``.md`` (so the real pictures travel with it)."""
    if not images:
        return ""
    lines = ["", "## Images & diagrams",
             f"_{len(images)} image(s) preserved from the original{(' — ' + title) if title else ''}._", ""]
    extra = 0
    for img in images:
        rel = f"{assets_relname}/{img['file']}"
        page = f" (p.{img['page']})" if img.get("page") else ""
        if img.get("renderable", True):
            lines.append(f"![Figure {img['index']}{page}]({rel})")
            lines.append("")
        else:
            extra += 1
    if extra:
        lines.append(f"> {extra} additional vector image(s) preserved in `{assets_relname}/` "
                     "(not inline-renderable).")
    return "\n".join(lines).rstrip() + "\n"
