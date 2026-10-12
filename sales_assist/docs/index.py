"""
Persistent FAISS vector index with OpenAI embeddings.

Features:
- Embeds chunks using OpenAI text-embedding-3-small.
- Stores vectors in a FAISS IndexFlatIP (inner-product / cosine) index.
- Persists the index + metadata to disk so restarts are instant.
- Tracks file hashes for incremental updates (add new, update changed,
  remove deleted).
- Provides top-k similarity search for RAG retrieval.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from fastembed import TextEmbedding
from openai import OpenAI

from sales_assist import config
from sales_assist.docs.chunker import Chunk, chunk_pages
from sales_assist.docs.loaders import LoadResult, load_file, load_folder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_DIM = 1536

LOCAL_EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5 vector dimension

INDEX_DIR_NAME = ".sales_assist_index"
INDEX_FILE = "faiss.index"
META_FILE = "metadata.json"
HASHES_FILE = "file_hashes.json"
BATCH_SIZE = 64  # Batch limit for embeddings


# ---------------------------------------------------------------------------
# Metadata for stored chunks
# ---------------------------------------------------------------------------
@dataclass
class ChunkMeta:
    """Metadata stored alongside each FAISS vector."""
    text: str
    source: str
    page: int
    chunk_index: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ChunkMeta:
        return cls(**d)


# ---------------------------------------------------------------------------
# Ingestion summary
# ---------------------------------------------------------------------------
@dataclass
class IngestionSummary:
    """Human-readable ingestion report."""
    indexed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)
    failed: list[tuple[str, str]] = field(default_factory=list)   # (filename, error)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    total_chunks: int = 0
    elapsed_seconds: float = 0.0

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("  📚 INGESTION SUMMARY")
        print("=" * 60)
        if self.indexed:
            print(f"\n  ✅ Indexed ({len(self.indexed)}):")
            for f in self.indexed:
                print(f"     • {f}")
        if self.updated:
            print(f"\n  🔄 Updated ({len(self.updated)}):")
            for f in self.updated:
                print(f"     • {f}")
        if self.removed:
            print(f"\n  🗑️  Removed ({len(self.removed)}):")
            for f in self.removed:
                print(f"     • {f}")
        if self.skipped:
            print(f"\n  ⏭️  Skipped ({len(self.skipped)}):")
            for f, reason in self.skipped:
                print(f"     • {f}: {reason}")
        if self.failed:
            print(f"\n  ❌ Failed ({len(self.failed)}):")
            for f, error in self.failed:
                print(f"     • {f}: {error}")
        print(f"\n  📊 Total chunks in index: {self.total_chunks}")
        print(f"  ⏱️  Elapsed: {self.elapsed_seconds:.1f}s")
        print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Document Index
# ---------------------------------------------------------------------------
class DocumentIndex:
    """
    Manages a persistent FAISS index with OpenAI embeddings.

    Usage::

        idx = DocumentIndex(persist_dir="./my_index")
        summary = idx.ingest_folder(Path("./docs"))
        results = idx.search("What is the pricing?", top_k=5)
    """

    def __init__(self, persist_dir: Optional[str | Path] = None) -> None:
        self._persist_dir = Path(persist_dir or INDEX_DIR_NAME).resolve()
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        self._provider: str = config.EMBEDDING_PROVIDER
        self._embedding_dim: int = (
            LOCAL_EMBEDDING_DIM if self._provider == "local" else OPENAI_EMBEDDING_DIM
        )
        self._client: Optional[OpenAI] = None
        self._local_embedder: Optional[TextEmbedding] = None

        self._index: Optional[faiss.IndexFlatIP] = None
        self._metadata: list[ChunkMeta] = []
        self._file_hashes: dict[str, str] = {}  # filename -> sha256
        self._query_cache: dict[str, np.ndarray] = {}  # normalized_query -> query_vec

        # Warm up local model immediately on startup so first query has 0 warmup delay
        if self._provider == "local":
            self._warmup_local_model()

        self._load_from_disk()

    # ------------------------------------------------------------------
    # Local Model Warmup
    # ------------------------------------------------------------------
    def _warmup_local_model(self) -> None:
        """
        Loads the local BAAI/bge-small-en-v1.5 model from the local models folder
        and executes a warmup embedding pass immediately so the first user query
        does not suffer any warmup latency.
        """
        if self._local_embedder is not None:
            return

        t0 = time.perf_counter()
        models_dir = Path(config.LOCAL_MODELS_DIR).resolve()
        models_dir.mkdir(parents=True, exist_ok=True)
        model_name = config.LOCAL_EMBEDDING_MODEL

        logger.info("Initializing local embedding model '%s' from '%s' ...", model_name, models_dir)
        self._local_embedder = TextEmbedding(
            model_name=model_name,
            cache_dir=str(models_dir),
        )

        t_warm0 = time.perf_counter()
        # Warmup pass (run on dummy text)
        _ = list(self._local_embedder.embed(["warmup query for bge small en v1.5"]))
        t_now = time.perf_counter()

        load_ms = (t_warm0 - t0) * 1000.0
        warmup_ms = (t_now - t_warm0) * 1000.0

        logger.info(
            "Local embedding model '%s' ready (init: %.1fms, warmup: %.1fms, dim: %d).",
            model_name, load_ms, warmup_ms, self._embedding_dim,
        )
        print(
            f"[*] [EMBEDDINGS READY] Local model '{model_name}' loaded & warmed up in {load_ms + warmup_ms:.1f} ms (dim={self._embedding_dim}, local CPU)",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Embedding client (lazy init for OpenAI fallback)
    # ------------------------------------------------------------------
    def _get_client(self) -> OpenAI:
        if self._client is None:
            api_key = config.OPENAI_API_KEY
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set in .env")
            self._client = OpenAI(api_key=api_key)
        return self._client

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_from_disk(self) -> None:
        """Load existing index, metadata, and hashes from disk."""
        index_path = self._persist_dir / INDEX_FILE
        meta_path = self._persist_dir / META_FILE
        hashes_path = self._persist_dir / HASHES_FILE

        if index_path.exists() and meta_path.exists():
            self._index = faiss.read_index(str(index_path))
            if self._index.d != self._embedding_dim:
                logger.warning(
                    "Existing FAISS index dim (%d) does not match configured embedding dim (%d). Recreating index.",
                    self._index.d, self._embedding_dim,
                )
                self._index = faiss.IndexFlatIP(self._embedding_dim)
                self._metadata = []
                self._file_hashes = {}
            else:
                with open(meta_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._metadata = [ChunkMeta.from_dict(d) for d in raw]
                logger.info(
                    "Loaded existing index: %d vectors, %d metadata entries.",
                    self._index.ntotal, len(self._metadata),
                )
        else:
            self._index = faiss.IndexFlatIP(self._embedding_dim)
            self._metadata = []
            logger.info("Created new empty FAISS index (dim=%d).", self._embedding_dim)

        if hashes_path.exists():
            with open(hashes_path, "r", encoding="utf-8") as f:
                self._file_hashes = json.load(f)

    def _save_to_disk(self) -> None:
        """Persist index, metadata, and hashes."""
        faiss.write_index(self._index, str(self._persist_dir / INDEX_FILE))
        with open(self._persist_dir / META_FILE, "w", encoding="utf-8") as f:
            json.dump([m.to_dict() for m in self._metadata], f, ensure_ascii=False)
        with open(self._persist_dir / HASHES_FILE, "w", encoding="utf-8") as f:
            json.dump(self._file_hashes, f)
        logger.info("Index saved to %s (%d vectors).", self._persist_dir, self._index.ntotal)

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------
    @staticmethod
    def _file_hash(path: Path) -> str:
        """SHA-256 hash of a file's contents."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                h.update(block)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """
        Embed a list of texts using local fastembed (BGE) or OpenAI API.
        Returns an (N, dim) float32 array, L2-normalized for cosine similarity.
        """
        if self._provider == "local":
            if self._local_embedder is None:
                self._warmup_local_model()
            embeddings = list(self._local_embedder.embed(texts))
            arr = np.array(embeddings, dtype=np.float32)
            faiss.normalize_L2(arr)
            return arr

        # OpenAI fallback
        client = self._get_client()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            response = client.embeddings.create(
                input=batch,
                model=OPENAI_EMBEDDING_MODEL,
            )
            batch_embs = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embs)

        arr = np.array(all_embeddings, dtype=np.float32)
        faiss.normalize_L2(arr)
        return arr

    # ------------------------------------------------------------------
    # Index mutation helpers
    # ------------------------------------------------------------------
    def _remove_by_source(self, source: str) -> int:
        """Remove all vectors belonging to a given source file. Returns count removed."""
        indices_to_keep = [
            i for i, m in enumerate(self._metadata) if m.source != source
        ]
        removed = len(self._metadata) - len(indices_to_keep)

        if removed == 0:
            return 0

        if indices_to_keep:
            # Rebuild the index with only the kept vectors
            kept_vectors = np.array(
                [self._index.reconstruct(i) for i in indices_to_keep],
                dtype=np.float32,
            )
            kept_meta = [self._metadata[i] for i in indices_to_keep]

            self._index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self._index.add(kept_vectors)
            self._metadata = kept_meta
        else:
            self._index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self._metadata = []

        return removed

    def _add_chunks(self, chunks: list[Chunk]) -> int:
        """Embed and add chunks to the index. Returns count added."""
        if not chunks:
            return 0

        texts = [c.text for c in chunks]
        embeddings = self._embed_texts(texts)

        self._index.add(embeddings)
        for chunk in chunks:
            self._metadata.append(ChunkMeta(
                text=chunk.text,
                source=chunk.source,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
            ))

        return len(chunks)

    # ------------------------------------------------------------------
    # Public API: Ingestion
    # ------------------------------------------------------------------
    def ingest_folder(
        self,
        folder: Path,
        max_tokens: int = 500,
        overlap_tokens: int = 80,
    ) -> IngestionSummary:
        """
        Ingest all files from a folder incrementally.

        - New files are added.
        - Changed files (different hash) are re-indexed.
        - Deleted files are removed from the index.
        """
        t0 = time.time()
        summary = IngestionSummary()
        folder = Path(folder).resolve()

        # Load all files
        results = load_folder(folder)

        # Build a map of current files and their hashes
        current_files: dict[str, tuple[Path, str]] = {}  # source -> (path, hash)
        for result in results:
            if result.success and result.pages:
                # Reconstruct the full path from pages
                fpath = folder  # We'll find it
                for root, dirs, files in os.walk(folder):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fname in files:
                        if fname == result.source:
                            fpath = Path(root) / fname
                            break
                file_hash = self._file_hash(fpath)
                current_files[result.source] = (fpath, file_hash)

        # Detect removed files
        for old_source in list(self._file_hashes.keys()):
            if old_source not in current_files:
                removed = self._remove_by_source(old_source)
                del self._file_hashes[old_source]
                summary.removed.append(old_source)
                logger.info("Removed %s (%d chunks).", old_source, removed)

        # Process each result
        for result in results:
            if not result.success:
                if result.error:
                    summary.failed.append((result.source, result.error))
                continue

            if not result.pages:
                summary.skipped.append((result.source, "No text content extracted"))
                continue

            source = result.source
            if source not in current_files:
                summary.skipped.append((source, "Could not locate file"))
                continue

            _, file_hash = current_files[source]

            # Check if already indexed with same hash
            old_hash = self._file_hashes.get(source)
            if old_hash == file_hash:
                summary.skipped.append((source, "Unchanged (same hash)"))
                continue

            # If hash changed, remove old chunks first
            if old_hash is not None:
                self._remove_by_source(source)
                summary.updated.append(source)
            else:
                summary.indexed.append(source)

            # Chunk and embed
            chunks = chunk_pages(result.pages, max_tokens, overlap_tokens)
            added = self._add_chunks(chunks)
            self._file_hashes[source] = file_hash
            logger.info("Indexed %s: %d chunks.", source, added)

        # Save
        self._save_to_disk()

        summary.total_chunks = self._index.ntotal
        summary.elapsed_seconds = time.time() - t0
        return summary

    def ingest_file(
        self,
        path: Path,
        max_tokens: int = 500,
        overlap_tokens: int = 80,
    ) -> IngestionSummary:
        """Ingest a single file."""
        t0 = time.time()
        summary = IngestionSummary()
        path = Path(path).resolve()

        result = load_file(path)
        if not result.success:
            summary.failed.append((result.source, result.error or "Unknown error"))
            summary.elapsed_seconds = time.time() - t0
            return summary

        if not result.pages:
            summary.skipped.append((result.source, "No text content extracted"))
            summary.elapsed_seconds = time.time() - t0
            return summary

        file_hash = self._file_hash(path)
        old_hash = self._file_hashes.get(result.source)

        if old_hash == file_hash:
            summary.skipped.append((result.source, "Unchanged (same hash)"))
            summary.total_chunks = self._index.ntotal
            summary.elapsed_seconds = time.time() - t0
            return summary

        if old_hash is not None:
            self._remove_by_source(result.source)
            summary.updated.append(result.source)
        else:
            summary.indexed.append(result.source)

        chunks = chunk_pages(result.pages, max_tokens, overlap_tokens)
        self._add_chunks(chunks)
        self._file_hashes[result.source] = file_hash
        self._save_to_disk()

        summary.total_chunks = self._index.ntotal
        summary.elapsed_seconds = time.time() - t0
        return summary

    # ------------------------------------------------------------------
    # Public API: Search
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 5) -> list[tuple[ChunkMeta, float]]:
        """
        Search the index for the most similar chunks.

        Returns a list of (ChunkMeta, score) tuples sorted by descending
        similarity.
        """
        if self._index.ntotal == 0:
            logger.warning("Index is empty – nothing to search.")
            return []

        norm_query = " ".join(query.strip().lower().split())
        if norm_query in self._query_cache:
            query_vec = self._query_cache[norm_query]
        else:
            query_vec = self._embed_texts([query])
            if len(self._query_cache) >= 500:
                self._query_cache.pop(next(iter(self._query_cache)))
            self._query_cache[norm_query] = query_vec

        scores, indices = self._index.search(query_vec, min(top_k, self._index.ntotal))

        results: list[tuple[ChunkMeta, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            results.append((self._metadata[idx], float(score)))

        return results

    # ------------------------------------------------------------------
    # Public API: Info
    # ------------------------------------------------------------------
    @property
    def total_chunks(self) -> int:
        return self._index.ntotal if self._index else 0

    @property
    def indexed_files(self) -> list[str]:
        return list(self._file_hashes.keys())

    def clear(self) -> None:
        """Remove all data from the index."""
        self._index = faiss.IndexFlatIP(self._embedding_dim)
        self._metadata = []
        self._file_hashes = {}
        self._query_cache.clear()
        self._save_to_disk()
        logger.info("Index cleared.")
