"""
Tests for text chunking.

Run with: python -m pytest sales_assist/tests/test_chunker.py -v
"""

from __future__ import annotations

import pytest

from sales_assist.docs.chunker import chunk_pages
from sales_assist.docs.loaders import PageContent


class TestChunker:
    def test_short_text_single_chunk(self) -> None:
        page = PageContent(text="This is a short text.", source="file.txt")
        chunks = chunk_pages([page], max_tokens=100)
        assert len(chunks) == 1
        assert chunks[0].text == "This is a short text."
        assert chunks[0].chunk_index == 0
        assert chunks[0].source == "file.txt"

    def test_paragraph_split(self) -> None:
        # Create text with clear paragraph breaks
        # ~4 chars per token, so 40 chars = 10 tokens
        para1 = "A" * 40
        para2 = "B" * 40
        para3 = "C" * 40
        text = f"{para1}\n\n{para2}\n\n{para3}"

        page = PageContent(text=text, source="doc.txt")
        # Max tokens 15 means it should fit one paragraph per chunk
        chunks = chunk_pages([page], max_tokens=15, overlap_tokens=0)
        
        # We expect 3 chunks
        assert len(chunks) == 3
        assert chunks[0].text == para1
        assert chunks[1].text == para2
        assert chunks[2].text == para3

    def test_overlap(self) -> None:
        # Two paragraphs
        para1 = "A" * 80  # ~20 tokens
        para2 = "B" * 80  # ~20 tokens
        text = f"{para1}\n\n{para2}"
        page = PageContent(text=text, source="doc.txt")
        
        # Max 25 tokens, overlap 5 tokens (~20 chars)
        chunks = chunk_pages([page], max_tokens=25, overlap_tokens=5)
        
        assert len(chunks) == 2
        assert chunks[0].text == para1
        
        # Second chunk should start with the overlap from para1
        expected_overlap = "A" * 20
        assert chunks[1].text.startswith(expected_overlap)
        assert "B" * 80 in chunks[1].text

    def test_sentence_split(self) -> None:
        # A single long paragraph with sentences
        sent1 = "This is sentence one."
        sent2 = "This is sentence two."
        sent3 = "This is sentence three."
        text = f"{sent1} {sent2} {sent3}"
        
        page = PageContent(text=text, source="doc.txt")
        # Force splitting on sentences
        chunks = chunk_pages([page], max_tokens=10, overlap_tokens=0)
        
        # Should split along sentence boundaries
        # Depending on char counts, it might group them differently,
        # but we definitely shouldn't just have 1 chunk
        assert len(chunks) > 1

    def test_hard_character_split(self) -> None:
        # A single massive word/string with no spaces or punctuation
        text = "X" * 200
        page = PageContent(text=text, source="doc.txt")
        
        # Force a hard split
        chunks = chunk_pages([page], max_tokens=20, overlap_tokens=5)
        assert len(chunks) > 1
        assert chunks[0].text.startswith("X")

    def test_multiple_pages(self) -> None:
        page1 = PageContent(text="Page 1 content.", source="doc.txt", page=0)
        page2 = PageContent(text="Page 2 content.", source="doc.txt", page=1)
        
        chunks = chunk_pages([page1, page2], max_tokens=50)
        assert len(chunks) == 2
        assert chunks[0].page == 0
        assert chunks[1].page == 1
        assert chunks[0].chunk_index == 0
        assert chunks[1].chunk_index == 1
