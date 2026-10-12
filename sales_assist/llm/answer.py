"""
LLM processing, fast zero-network trigger logic, and live terminal latency telemetry.

Features:
- Sub-millisecond local regex & intent classification (eliminates 800ms+ LLM classification round-trips).
- Query-focused FAISS retrieval with speculative pre-fetching for PartialTranscripts.
- Fast streaming answers with gpt-4o-mini.
- Live step-by-step terminal latency reporting for real-time pipeline visibility:
    Step 1: Trigger Detection Latency
    Step 2: RAG Context Retrieval Latency (Cache HIT vs fresh FAISS search)
    Step 3: Time-to-First-Token (TTFT)
    Step 4: Stream Completion & token generation speed (tokens/sec)
    Pipeline Total: End-to-end response generation time
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Callable, Optional

from openai import OpenAI

from sales_assist import config
from sales_assist.docs.index import DocumentIndex
from sales_assist.llm import prompts

logger = logging.getLogger(__name__)

# Type alias for streaming callbacks: (token_str) -> None
StreamCallback = Callable[[str], None]

# ---------------------------------------------------------------------------
# Zero-Network Local Intent & Heuristic Patterns
# ---------------------------------------------------------------------------
_FILLER_PATTERN = re.compile(
    r"^\s*(ok(ay)?|yeah|yes|no|yep|nope|sure|right|cool|got it|makes sense|"
    r"sounds good|hello|hi|hey|thanks|thank you|bye|goodbye|awesome|great|"
    r"all right|perfect|fine)[.!, ]*$",
    re.IGNORECASE,
)

_QUESTION_START_PATTERN = re.compile(
    r"^\s*(what|how|why|who|when|where|which|whose|whom|can|could|does|do|did|"
    r"is|are|was|were|will|would|should|has|have|had|tell me|explain)\b",
    re.IGNORECASE,
)

_EMBEDDED_QUESTION_PATTERN = re.compile(
    r"\b(can you|could you|do you|would you|is there|are there|how do|how can|"
    r"what is|what are|what about|tell me about|curious about|wondering if)\b",
    re.IGNORECASE,
)

_COMMERCIAL_INTENT_PATTERN = re.compile(
    r"\b(price|pricing|cost|costs|costing|quote|discount|discounted|discounts|"
    r"cheap|expensive|rate|rates|budget|afford|licens(e|ing)|tier|tiers|"
    r"package|packages|deal|subscription|contract|billing|bill|pay|payment|"
    r"terms|refund|roi|renewal|seats?|users?)\b",
    re.IGNORECASE,
)

_TECHNICAL_INTENT_PATTERN = re.compile(
    r"\b(integrat(e|ion|ions)|support|sla|uptime|reliability|latency|speed|performance|"
    r"security|soc2|gdpr|hipaa|iso|compliance|compliant|privacy|encrypt(ion)?|"
    r"competitor|alternative|compare(d)?|comparison|difference|versus|vs|"
    r"limitation|limitations|drawback|drawbacks|on-prem|cloud|self-hosted|"
    r"roadmap|feature|features|api|sdk|webhook|migration|export|import)\b",
    re.IGNORECASE,
)


def _normalize_query(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for robust caching."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


class AnswerEngine:
    """
    Coordinates zero-network trigger logic, retrieval, speculative pre-fetching,
    and streaming LLM answers with live terminal latency prints for every step.
    """

    def __init__(self, index: DocumentIndex) -> None:
        self._index = index
        self._client: Optional[OpenAI] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

        # Buffer to keep the last few utterances for context
        self._recent_utterances: list[str] = []

        # Cache for speculatively pre-fetched RAG context: normalized_query -> (context_str, query_used)
        self._speculative_context: dict[str, tuple[str, str]] = {}
        self._last_prefetch_time: float = 0.0
        self._last_prefetch_key: str = ""

        # Latency metric tracker
        self._last_trigger_latency_ms: float = 0.0

    def _get_client(self) -> tuple[OpenAI, str]:
        """
        Returns (client, model_name) using OpenAI gpt-4o-mini.
        """
        api_key = config.OPENAI_API_KEY
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        model = config.OPENAI_MODEL or "gpt-4o-mini"
        if self._client is None:
            self._client = OpenAI(api_key=api_key)
        return self._client, model

    def push_utterance(self, text: str) -> None:
        """Add a final utterance to the recent history (keeps last 3)."""
        text = text.strip()
        if not text:
            return

        with self._lock:
            self._recent_utterances.append(text)
            if len(self._recent_utterances) > 3:
                self._recent_utterances.pop(0)

    def is_trigger(self, utterance: str) -> bool:
        """
        Sub-millisecond local intent & heuristic trigger check.
        Requires ZERO network calls.
        """
        t0 = time.perf_counter()
        cleaned = utterance.strip()
        result = False

        if cleaned and not _FILLER_PATTERN.match(cleaned):
            words = cleaned.split()
            has_commercial = bool(_COMMERCIAL_INTENT_PATTERN.search(cleaned))
            
            # Short filter: requires at least 3 words unless explicit ? or commercial intent
            if len(words) >= 3 or "?" in cleaned or has_commercial:
                if (
                    "?" in cleaned
                    or _QUESTION_START_PATTERN.search(cleaned)
                    or _EMBEDDED_QUESTION_PATTERN.search(cleaned)
                    or has_commercial
                    or _TECHNICAL_INTENT_PATTERN.search(cleaned)
                ):
                    result = True

        t_elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._last_trigger_latency_ms = t_elapsed_ms
        return result

    def prefetch_context(self, query: str) -> None:
        """
        Speculatively pre-fetches RAG context for a partial or in-flight transcript.
        Caches the retrieved text in memory so generate_answer can skip retrieval.
        """
        q = query.strip()
        if not q or len(q.split()) < 3:
            return

        norm_key = _normalize_query(q)
        now = time.perf_counter()
        with self._lock:
            # Debounce: if prefetch ran < 350ms ago on a query starting with the same prefix, skip
            if (
                norm_key in self._speculative_context
                or (now - self._last_prefetch_time < 0.35 and norm_key.startswith(self._last_prefetch_key))
            ):
                return
            self._last_prefetch_time = now
            self._last_prefetch_key = norm_key

        t_pre0 = time.perf_counter()
        try:
            results = self._index.search(q, top_k=3)
            context_parts = [f"--- From {meta.source} ---\n{meta.text}" for meta, _ in results]
            context_str = "\n\n".join(context_parts)
            with self._lock:
                if len(self._speculative_context) > 20:
                    self._speculative_context.pop(next(iter(self._speculative_context)))
                self._speculative_context[norm_key] = (context_str, q)
            
            t_pre_ms = (time.perf_counter() - t_pre0) * 1000.0
            print(
                f">> [LATENCY Speculative Prefetch] {t_pre_ms:.1f} ms | Context cached in background for: \"{q[:45]}...\"",
                flush=True,
            )
        except Exception as exc:
            logger.debug("Speculative prefetch failed: %s", exc)

    def cancel_current(self) -> None:
        """Signal any running streaming generation to stop immediately."""
        self._cancel_event.set()

    def generate_answer(self, query: str, callback: StreamCallback) -> None:
        """
        Retrieves context, calls the LLM, and streams tokens to the callback.
        Prints step-by-step latency metrics to the terminal in real-time.
        """
        t_start = time.perf_counter()
        self._cancel_event.clear()

        clean_query = query.strip()
        if not clean_query:
            with self._lock:
                clean_query = " ".join(self._recent_utterances).strip()
        if not clean_query:
            return

        norm_key = _normalize_query(clean_query)

        # -------------------------------------------------------------------
        # Step 1: Trigger Latency (recorded during is_trigger check)
        # -------------------------------------------------------------------
        trigger_ms = getattr(self, "_last_trigger_latency_ms", 0.002)

        # -------------------------------------------------------------------
        # Step 2: RAG Context Retrieval
        # -------------------------------------------------------------------
        t_rag_start = time.perf_counter()
        context_str: str = ""
        rag_status = ""

        # Lookup in speculative cache: exact match first, then prefix/subset match
        cached = None
        with self._lock:
            if norm_key in self._speculative_context:
                cached = self._speculative_context.pop(norm_key)
            else:
                for k in list(self._speculative_context.keys()):
                    if norm_key.startswith(k) or k.startswith(norm_key):
                        cached = self._speculative_context.pop(k)
                        break

        if cached is not None:
            context_str, _ = cached
            t_rag_ms = (time.perf_counter() - t_rag_start) * 1000.0
            rag_status = "Cache HIT (pre-fetched from partial speech, 0 network wait)"
        else:
            try:
                results = self._index.search(clean_query, top_k=3)
                context_parts = [f"--- From {meta.source} ---\n{meta.text}" for meta, _ in results]
                context_str = "\n\n".join(context_parts)
                sources = list({meta.source for meta, _ in results})
                source_desc = f"searched {len(results)} chunks from {', '.join(sources)}" if sources else "no chunks matched"
                rag_status = f"Cache MISS ({source_desc})"
            except Exception as e:
                logger.error("Search failed: %s", e)
                context_str = ""
                rag_status = f"Search error ({e})"
            t_rag_ms = (time.perf_counter() - t_rag_start) * 1000.0

        # Print Steps 1 & 2 before LLM begins
        print("\n" + "=" * 70, flush=True)
        print(f">> [LATENCY Step 1/4 - Trigger Detection] {trigger_ms:.3f} ms (zero-network local regex)", flush=True)
        print(f">> [LATENCY Step 2/4 - RAG Retrieval]     {t_rag_ms:.1f} ms ({rag_status})", flush=True)

        # -------------------------------------------------------------------
        # Step 3: LLM Stream & Time-to-First-Token (TTFT)
        # -------------------------------------------------------------------
        system_msg = prompts.ANSWER_SYSTEM_PROMPT
        user_msg = f"Context:\n{context_str}\n\nProspect said:\n{clean_query}"

        token_count = 0
        t_first_token: Optional[float] = None

        try:
            client, model = self._get_client()
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                stream=True,
                max_tokens=config.LLM_MAX_TOKENS,
                temperature=0.1,  # Low temp for instant deterministic generation
            )

            for chunk in stream:
                if self._cancel_event.is_set():
                    logger.info("Generation cancelled by new question.")
                    return

                content = chunk.choices[0].delta.content if chunk.choices else None
                if content:
                    if t_first_token is None:
                        t_first_token = time.perf_counter()
                        ttft_ms = (t_first_token - t_start) * 1000.0
                        # Print Step 3 at the exact moment the first token arrives
                        print(
                            f">> [LATENCY Step 3/4 - TTFT (1st Token)]  {ttft_ms:.1f} ms (Model: {model})",
                            flush=True,
                        )
                        print("-" * 70, flush=True)
                    token_count += 1
                    callback(content)

            if t_first_token is None:
                print(
                    f">> [LATENCY Step 3/4 - TTFT (1st Token)]  N/A (No tokens generated)",
                    flush=True,
                )
                print("-" * 70, flush=True)

            # ---------------------------------------------------------------
            # Step 4: Stream Completion & Overall Pipeline Summary
            # ---------------------------------------------------------------
            t_end = time.perf_counter()
            t_total_ms = (t_end - t_start) * 1000.0
            gen_duration_s = (t_end - t_first_token) if t_first_token else 0.001
            gen_duration_ms = gen_duration_s * 1000.0
            speed_tps = (token_count / gen_duration_s) if gen_duration_s > 0 else 0.0

            print("\n" + "-" * 70, flush=True)
            print(
                f">> [LATENCY Step 4/4 - Stream Complete]   {gen_duration_ms:.1f} ms ({token_count} tokens @ {speed_tps:.1f} tokens/s)",
                flush=True,
            )
            print(
                f"[*] [PIPELINE TOTAL LATENCY]              {t_total_ms:.1f} ms (Trigger -> Finished Answer)",
                flush=True,
            )
            print("=" * 70 + "\n", flush=True)

        except Exception as e:
            logger.error("LLM generation failed: %s", e)
            print(f"\n[!] [LATENCY Error]: LLM generation failed after {(time.perf_counter() - t_start)*1000:.1f} ms: {e}\n", flush=True)
            callback("\n[Error generating answer]")
