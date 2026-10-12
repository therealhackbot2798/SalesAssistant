"""
Tests for latency optimizations in AnswerEngine and DocumentIndex:
- Sub-millisecond zero-network trigger detection.
- Fast filler filtering.
- Query caching.
- Speculative context caching.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock
import numpy as np
import pytest

from sales_assist.docs.index import DocumentIndex, ChunkMeta
from sales_assist.llm.answer import AnswerEngine, _normalize_query


class TestTriggerDetection:
    """Verify zero-network intent detection and speed."""

    @pytest.fixture
    def engine(self):
        dummy_index = MagicMock(spec=DocumentIndex)
        return AnswerEngine(dummy_index)

    @pytest.mark.parametrize(
        "query",
        [
            "What is your pricing?",
            "How much does this cost per seat?",
            "Can you integrate with Salesforce?",
            "Could we get an annual discount?",
            "Is your platform SOC2 compliant?",
            "Do you support on-prem deployments?",
            "Tell me about your security policies.",
            "Explain your backup and recovery roadmap.",
            "How does this compare to Zendesk?",
            "Our budget is tight this quarter.",
            "We have 50 users needing access.",
            "Any limitations with API rate limits?",
            "Is there a refund policy?",
        ],
    )
    def test_triggers_positive(self, engine: AnswerEngine, query: str):
        t0 = time.perf_counter()
        result = engine.is_trigger(query)
        elapsed_us = (time.perf_counter() - t0) * 1_000_000  # microseconds

        assert result is True
        # Must execute in under 1 millisecond (1000 microseconds)
        assert elapsed_us < 1000, f"Trigger check took too long: {elapsed_us:.1f}µs"

    @pytest.mark.parametrize(
        "filler",
        [
            "ok",
            "okay",
            "yeah",
            "yes",
            "got it",
            "sounds good",
            "makes sense",
            "hi",
            "hello",
            "thanks",
            "thank you",
            "bye",
            "great",
            "awesome",
            "right",
        ],
    )
    def test_triggers_negative_fillers(self, engine: AnswerEngine, filler: str):
        assert engine.is_trigger(filler) is False


class TestQueryAndSpeculativeCaching:
    """Verify query vector and speculative context caching."""

    def test_speculative_prefetch_cache_hit(self):
        dummy_index = MagicMock(spec=DocumentIndex)
        meta = ChunkMeta(text="Enterprise pricing is $50/user", source="pricing.pdf", page=1, chunk_index=0)
        dummy_index.search.return_value = [(meta, 0.95)]

        engine = AnswerEngine(dummy_index)
        query = "What is the enterprise pricing?"

        # Prefetch speculatively
        engine.prefetch_context(query)
        dummy_index.search.assert_called_once_with(query, top_k=3)

        # Check internal speculative cache using normalized key
        norm_key = _normalize_query(query)
        assert norm_key in engine._speculative_context

        # Verify cached context
        ctx, q_used = engine._speculative_context[norm_key]
        assert "Enterprise pricing is $50/user" in ctx
        assert q_used == query

    def test_document_index_query_cache(self, tmp_path):
        index = DocumentIndex(persist_dir=tmp_path)
        # Mock _embed_texts to track call count
        fake_vector = np.ones((1, index._embedding_dim), dtype=np.float32)
        index._embed_texts = MagicMock(return_value=fake_vector)
        index._index = MagicMock()
        index._index.ntotal = 5
        index._index.search.return_value = (np.array([[0.9]]), np.array([[0]]))
        index._metadata = [ChunkMeta(text="Sample", source="doc.txt", page=1, chunk_index=0)]

        # First search -> calls _embed_texts
        res1 = index.search("what is pricing?")
        assert index._embed_texts.call_count == 1

        # Second search with same or normalized query -> hits cache, 0 extra embed calls!
        res2 = index.search("  What IS pricing?  ")
        assert index._embed_texts.call_count == 1

    def test_local_bge_small_embedding_warmup(self, tmp_path):
        """Verify BAAI/bge-small-en-v1.5 generates 384-dim vectors locally on CPU."""
        index = DocumentIndex(persist_dir=tmp_path)
        assert index._embedding_dim == 384
        assert index._local_embedder is not None

        # Verify embed produces 384-dimensional normalized float32 vectors
        vectors = index._embed_texts(["Test sales pitch document chunk"])
        assert isinstance(vectors, np.ndarray)
        assert vectors.shape == (1, 384)
        assert vectors.dtype == np.float32
