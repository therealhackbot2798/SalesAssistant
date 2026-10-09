"""
Text chunker for document ingestion.

Splits extracted page text into overlapping chunks of approximately
``max_tokens`` tokens (estimated at ~4 characters per token) with
``overlap_tokens`` of overlap, preserving source metadata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sales_assist.docs.loaders import PageContent

logger = logging.getLogger(__name__)

# Approximate characters per token (for English / mixed-language text)
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Chunk:
    """A chunk of text ready for embedding."""
    text: str
    source: str          # original filename
    page: int            # 0-based page/sheet/slide
    chunk_index: int     # sequential index within the source file
    metadata: dict = field(default_factory=dict)

    @property
    def estimated_tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN


def chunk_pages(
    pages: list[PageContent],
    max_tokens: int = 500,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """
    Split a list of :class:`PageContent` into overlapping :class:`Chunk` objects.

    Strategy:
    1. First try to split on paragraph boundaries (double newline).
    2. If a paragraph is still too long, split on sentence boundaries.
    3. As a last resort, split on character count.

    Args:
        pages: Extracted page content from a loader.
        max_tokens: Target maximum tokens per chunk.
        overlap_tokens: Number of tokens of overlap between adjacent chunks.

    Returns:
        List of :class:`Chunk` objects with source metadata preserved.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    all_chunks: list[Chunk] = []
    chunk_idx = 0

    for page in pages:
        text = page.text.strip()
        if not text:
            continue

        # Split into paragraphs
        paragraphs = re.split(r"\n{2,}", text)

        buffer = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            candidate = (buffer + "\n\n" + para).strip() if buffer else para

            if _est_tokens(candidate) <= max_tokens:
                buffer = candidate
            else:
                # Flush current buffer as a chunk
                if buffer:
                    all_chunks.append(Chunk(
                        text=buffer,
                        source=page.source,
                        page=page.page,
                        chunk_index=chunk_idx,
                        metadata=page.metadata,
                    ))
                    chunk_idx += 1

                    # Start next buffer with overlap from end of previous
                    overlap_text = buffer[-overlap_chars:] if len(buffer) > overlap_chars else buffer
                    buffer = overlap_text
                else:
                    buffer = ""

                # If the paragraph itself is too long, split it further
                if _est_tokens(para) > max_tokens:
                    sub_chunks = _split_long_text(
                        para, max_chars, overlap_chars,
                        page.source, page.page, chunk_idx, page.metadata,
                    )
                    all_chunks.extend(sub_chunks)
                    chunk_idx += len(sub_chunks)
                    # Overlap from the last sub-chunk
                    if sub_chunks:
                        last_text = sub_chunks[-1].text
                        buffer = last_text[-overlap_chars:] if len(last_text) > overlap_chars else last_text
                    else:
                        buffer = ""
                else:
                    candidate = (buffer + "\n\n" + para).strip() if buffer else para
                    if _est_tokens(candidate) <= max_tokens:
                        buffer = candidate
                    else:
                        # Flush overlap buffer and start fresh with para
                        buffer = para

        # Flush remaining buffer
        if buffer.strip():
            all_chunks.append(Chunk(
                text=buffer.strip(),
                source=page.source,
                page=page.page,
                chunk_index=chunk_idx,
                metadata=page.metadata,
            ))
            chunk_idx += 1

    logger.info(
        "Chunked %d page(s) into %d chunks (target ~%d tokens, ~%d overlap).",
        len(pages), len(all_chunks), max_tokens, overlap_tokens,
    )
    return all_chunks


def _est_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return len(text) // CHARS_PER_TOKEN


def _split_long_text(
    text: str,
    max_chars: int,
    overlap_chars: int,
    source: str,
    page: int,
    start_idx: int,
    metadata: dict,
) -> list[Chunk]:
    """Split a single long text into sentence-boundary chunks."""
    # Try sentence splitting first
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks: list[Chunk] = []
    buffer = ""
    idx = start_idx

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        candidate = (buffer + " " + sent).strip() if buffer else sent

        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            if buffer:
                chunks.append(Chunk(
                    text=buffer,
                    source=source,
                    page=page,
                    chunk_index=idx,
                    metadata=metadata,
                ))
                idx += 1
                overlap_text = buffer[-overlap_chars:] if len(buffer) > overlap_chars else buffer
                buffer = (overlap_text + " " + sent).strip()
            else:
                # Single sentence is too long – hard split by character
                for i in range(0, len(sent), max_chars - overlap_chars):
                    slice_text = sent[i : i + max_chars]
                    if slice_text.strip():
                        chunks.append(Chunk(
                            text=slice_text.strip(),
                            source=source,
                            page=page,
                            chunk_index=idx,
                            metadata=metadata,
                        ))
                        idx += 1
                buffer = ""

    if buffer.strip():
        chunks.append(Chunk(
            text=buffer.strip(),
            source=source,
            page=page,
            chunk_index=idx,
            metadata=metadata,
        ))

    return chunks
