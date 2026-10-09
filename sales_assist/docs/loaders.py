"""
Universal document loaders.

Accepts any file type and extracts text content. Never crashes on a bad file –
returns an empty list of pages and logs the reason.

Supported formats:
    PDF         – PyMuPDF (fitz), with pytesseract OCR fallback for scanned pages
    DOCX        – python-docx (includes tables)
    DOC         – LibreOffice headless conversion to DOCX, then python-docx
    TXT/MD/CSV  – plain text with encoding detection
    JSON        – pretty-printed dump
    HTML        – stripped tags via html.parser
    XLSX        – openpyxl sheet text
    PPTX        – python-pptx slide text
    Unknown     – attempt text decode, otherwise skip
"""

from __future__ import annotations

import csv
import html.parser
import io
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PageContent:
    """A single page / section of extracted text."""
    text: str
    source: str          # filename
    page: int = 0        # 0-based page/sheet/slide number
    metadata: dict = field(default_factory=dict)


@dataclass
class LoadResult:
    """Result of loading a single file."""
    source: str
    pages: list[PageContent] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Individual loaders
# ---------------------------------------------------------------------------

def _load_pdf(path: Path) -> list[PageContent]:
    """Extract text from PDF using PyMuPDF, with OCR fallback."""
    import fitz  # PyMuPDF

    pages: list[PageContent] = []
    doc = fitz.open(str(path))

    for i, page in enumerate(doc):
        text = page.get_text("text").strip()

        # OCR fallback: if the page has very little text but has images
        if len(text) < 50:
            try:
                import pytesseract
                from PIL import Image

                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                ocr_text = pytesseract.image_to_string(img).strip()
                if ocr_text:
                    text = ocr_text
                    logger.debug("OCR fallback used on page %d of %s", i, path.name)
            except ImportError:
                logger.debug(
                    "pytesseract/Pillow not installed – skipping OCR for page %d of %s",
                    i, path.name,
                )
            except Exception as exc:
                logger.warning("OCR failed on page %d of %s: %s", i, path.name, exc)

        if text:
            pages.append(PageContent(text=text, source=path.name, page=i))

    doc.close()
    return pages


def _load_docx(path: Path) -> list[PageContent]:
    """Extract text from DOCX including tables."""
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())

    # Extract tables
    for table in doc.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append("\n".join(rows))

    if parts:
        return [PageContent(text="\n\n".join(parts), source=path.name, page=0)]
    return []


def _load_doc(path: Path) -> list[PageContent]:
    """Convert DOC to DOCX via LibreOffice headless, then extract."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Try soffice (LibreOffice)
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--convert-to", "docx",
                    "--outdir", tmpdir,
                    str(path),
                ],
                capture_output=True,
                timeout=60,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "LibreOffice conversion failed for %s: %s. "
                "Install LibreOffice for .doc support.",
                path.name, exc,
            )
            return []

        docx_file = Path(tmpdir) / (path.stem + ".docx")
        if docx_file.exists():
            return _load_docx(docx_file)

    return []


def _load_text(path: Path) -> list[PageContent]:
    """Load plain text with encoding detection."""
    # Try UTF-8 first, then fall back to chardet/charset-normalizer
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = path.read_text(encoding=encoding).strip()
            if text:
                return [PageContent(text=text, source=path.name, page=0)]
            return []
        except (UnicodeDecodeError, ValueError):
            continue

    # Last resort: try charset_normalizer
    try:
        from charset_normalizer import from_path
        result = from_path(str(path)).best()
        if result:
            text = str(result).strip()
            if text:
                return [PageContent(text=text, source=path.name, page=0)]
    except ImportError:
        pass

    return []


def _load_csv_file(path: Path) -> list[PageContent]:
    """Load CSV as text table."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    reader = csv.reader(io.StringIO(text))
    rows = [" | ".join(row) for row in reader]
    content = "\n".join(rows).strip()
    if content:
        return [PageContent(text=content, source=path.name, page=0)]
    return []


def _load_json_file(path: Path) -> list[PageContent]:
    """Load JSON file as pretty-printed text."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    try:
        data = json.loads(text)
        pretty = json.dumps(data, indent=2, ensure_ascii=False)
        return [PageContent(text=pretty, source=path.name, page=0)]
    except json.JSONDecodeError:
        # Fall back to raw text
        if text.strip():
            return [PageContent(text=text.strip(), source=path.name, page=0)]
    return []


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Simple HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _load_html(path: Path) -> list[PageContent]:
    """Load HTML with tags stripped."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    extractor = _HTMLTextExtractor()
    extractor.feed(text)
    content = extractor.get_text().strip()
    if content:
        return [PageContent(text=content, source=path.name, page=0)]
    return []


def _load_xlsx(path: Path) -> list[PageContent]:
    """Extract text from all sheets in an Excel file."""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    pages: list[PageContent] = []

    for i, sheet_name in enumerate(wb.sheetnames):
        ws = wb[sheet_name]
        rows: list[str] = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            line = " | ".join(cells).strip()
            if line and line != "| " * len(cells):
                rows.append(line)
        if rows:
            text = f"[Sheet: {sheet_name}]\n" + "\n".join(rows)
            pages.append(PageContent(text=text, source=path.name, page=i))

    wb.close()
    return pages


def _load_pptx(path: Path) -> list[PageContent]:
    """Extract text from PowerPoint slides."""
    from pptx import Presentation

    prs = Presentation(str(path))
    pages: list[PageContent] = []

    for i, slide in enumerate(prs.slides):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    if para.text.strip():
                        parts.append(para.text.strip())
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    parts.append(" | ".join(cells))
        if parts:
            pages.append(
                PageContent(text="\n".join(parts), source=path.name, page=i)
            )

    return pages


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

# Map of file extension -> loader function
_LOADER_MAP: dict[str, callable] = {
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".doc": _load_doc,
    ".txt": _load_text,
    ".md": _load_text,
    ".csv": _load_csv_file,
    ".json": _load_json_file,
    ".html": _load_html,
    ".htm": _load_html,
    ".xlsx": _load_xlsx,
    ".xls": _load_xlsx,   # openpyxl may not support old xls; will log warning
    ".pptx": _load_pptx,
}


def load_file(path: Path) -> LoadResult:
    """
    Load a single file and return extracted pages.

    Never raises – catches all exceptions and returns a LoadResult with
    success=False and the error message.
    """
    path = Path(path).resolve()

    if not path.exists():
        return LoadResult(source=str(path), success=False, error="File not found")

    if not path.is_file():
        return LoadResult(source=str(path), success=False, error="Not a file")

    ext = path.suffix.lower()
    loader = _LOADER_MAP.get(ext)

    try:
        if loader is not None:
            pages = loader(path)
        else:
            # Unknown type: attempt plain text decode
            logger.info("Unknown file type '%s' for %s – attempting text decode.", ext, path.name)
            pages = _load_text(path)
            if not pages:
                return LoadResult(
                    source=path.name,
                    success=False,
                    error=f"Unknown file type '{ext}' and text decode failed",
                )
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path.name, exc)
        return LoadResult(source=path.name, success=False, error=str(exc))

    return LoadResult(source=path.name, pages=pages, success=True)


def load_folder(folder: Path) -> list[LoadResult]:
    """
    Recursively load all files from a folder.

    Skips hidden files/directories (starting with '.').
    """
    folder = Path(folder).resolve()
    results: list[LoadResult] = []

    if not folder.is_dir():
        logger.error("Not a directory: %s", folder)
        return results

    for root, dirs, files in os.walk(folder):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = Path(root) / fname
            result = load_file(fpath)
            results.append(result)
            if result.success:
                total_chars = sum(len(p.text) for p in result.pages)
                logger.info(
                    "  Loaded %s: %d page(s), %d chars",
                    result.source, len(result.pages), total_chars,
                )
            else:
                logger.warning("  Skipped %s: %s", result.source, result.error)

    return results
