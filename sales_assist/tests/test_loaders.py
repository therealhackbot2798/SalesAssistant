"""
Tests for document loaders.

Creates sample files of each type (where possible without binary deps)
and verifies the loader returns correct PageContent.

Run with: python -m pytest sales_assist/tests/test_loaders.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from sales_assist.docs.loaders import LoadResult, load_file, load_folder


# ---------------------------------------------------------------------------
# Fixtures: create sample files in a temp dir
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample files of various types."""

    # TXT
    (tmp_path / "readme.txt").write_text(
        "Hello World\n\nThis is a sample text file for testing.",
        encoding="utf-8",
    )

    # MD
    (tmp_path / "notes.md").write_text(
        "# Notes\n\n## Section 1\n\nSome markdown content.\n\n## Section 2\n\nMore content.",
        encoding="utf-8",
    )

    # CSV
    (tmp_path / "data.csv").write_text(
        "Name,Age,City\nAlice,30,Mumbai\nBob,25,Delhi\n",
        encoding="utf-8",
    )

    # JSON
    (tmp_path / "config.json").write_text(
        json.dumps({"product": "Sales Assist", "version": "1.0", "features": ["STT", "RAG"]}),
        encoding="utf-8",
    )

    # HTML
    (tmp_path / "page.html").write_text(
        "<html><body><h1>Title</h1><p>Paragraph one.</p>"
        "<script>var x = 1;</script><p>Paragraph two.</p></body></html>",
        encoding="utf-8",
    )

    # Empty file
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    # Binary file (unknown type)
    (tmp_path / "image.xyz").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Subfolder with a file
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "nested.txt").write_text("Nested file content.", encoding="utf-8")

    # Hidden file (should be skipped)
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Tests: individual file loading
# ---------------------------------------------------------------------------

class TestLoadFile:
    def test_load_txt(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "readme.txt")
        assert result.success
        assert len(result.pages) == 1
        assert "Hello World" in result.pages[0].text
        assert result.pages[0].source == "readme.txt"

    def test_load_md(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "notes.md")
        assert result.success
        assert "# Notes" in result.pages[0].text

    def test_load_csv(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "data.csv")
        assert result.success
        assert "Alice" in result.pages[0].text
        assert "Mumbai" in result.pages[0].text

    def test_load_json(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "config.json")
        assert result.success
        assert "Sales Assist" in result.pages[0].text

    def test_load_html(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "page.html")
        assert result.success
        text = result.pages[0].text
        assert "Title" in text
        assert "Paragraph one" in text
        assert "Paragraph two" in text
        # Script content should be stripped
        assert "var x" not in text

    def test_load_empty_file(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "empty.txt")
        assert result.success
        assert len(result.pages) == 0

    def test_load_unknown_binary(self, sample_dir: Path) -> None:
        result = load_file(sample_dir / "image.xyz")
        # Should not crash, might fail gracefully
        assert isinstance(result, LoadResult)

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        result = load_file(tmp_path / "does_not_exist.txt")
        assert not result.success
        assert "not found" in result.error.lower()


# ---------------------------------------------------------------------------
# Tests: folder loading
# ---------------------------------------------------------------------------

class TestLoadFolder:
    def test_loads_all_supported(self, sample_dir: Path) -> None:
        results = load_folder(sample_dir)
        sources = {r.source for r in results}
        # Should include files from root and subfolder
        assert "readme.txt" in sources
        assert "notes.md" in sources
        assert "data.csv" in sources
        assert "config.json" in sources
        assert "page.html" in sources
        assert "nested.txt" in sources

    def test_skips_hidden_files(self, sample_dir: Path) -> None:
        results = load_folder(sample_dir)
        sources = {r.source for r in results}
        assert ".hidden" not in sources

    def test_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_folder"
        empty.mkdir()
        results = load_folder(empty)
        assert results == []
