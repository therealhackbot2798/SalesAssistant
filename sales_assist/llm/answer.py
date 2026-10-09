"""
LLM processing and trigger logic.

Handles:
- Quick classification (should we answer?)
- Context retrieval from FAISS
- Streaming response generation using gpt-4o-mini
- Cancellation of in-flight requests if a new question arrives
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Generator

from openai import OpenAI

from sales_assist import config
from sales_assist.docs.index import DocumentIndex
from sales_assist.llm import prompts

logger = logging.getLogger(__name__)

# Type alias for streaming callbacks: (token_str) -> None
StreamCallback = Callable[[str], None]


class AnswerEngine:
    """
    Coordinates trigger logic, retrieval, and streaming LLM answers.
    """

    def __init__(self, index: DocumentIndex) -> None:
        self._index = index
        self._client: Optional[OpenAI] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        
        # Buffer to keep the last few utterances for context
        self._recent_utterances: list[str] = []

    def _get_client(self) -> OpenAI:
        if self._client is None:
            api_key = config.OPENAI_API_KEY
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set in .env")
            self._client = OpenAI(api_key=api_key)
        return self._client

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
        Determine if the utterance should trigger an answer.
        Uses a cheap local rule first, then falls back to a fast LLM call.
        """
        text = utterance.lower()
        
        # Fast local heuristics
        trigger_words = [
            "?", "what", "how", "why", "who", "when", "where",
            "price", "cost", "expensive", "competitor", "discount",
            "budget", "contract", "security", "integration", "support"
        ]
        
        # If it's very short, it's likely just filler (e.g. "ok", "yeah")
        if len(text.split()) < 3 and "?" not in text:
            return False
            
        if any(w in text for w in trigger_words):
            return True
            
        # Fallback to LLM classifier if no cheap rules hit
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "user", "content": prompts.CLASSIFICATION_PROMPT.format(utterance=utterance)}
                ],
                max_tokens=5,
                temperature=0.0,
            )
            ans = response.choices[0].message.content.strip().upper()
            return "YES" in ans
        except Exception as e:
            logger.warning("Classifier failed: %s", e)
            return False

    def cancel_current(self) -> None:
        """Signal any running streaming generation to stop immediately."""
        self._cancel_event.set()

    def generate_answer(self, query: str, callback: StreamCallback) -> None:
        """
        Retrieves context, calls the LLM, and streams tokens to the callback.
        This blocks until done or cancelled. Should be run on a worker thread.
        """
        # 1. Reset cancellation state
        self._cancel_event.clear()
        
        # 2. Build full query context (last few utterances)
        with self._lock:
            # We already pushed the current query in push_utterance,
            # so the history contains the current query.
            search_query = " ".join(self._recent_utterances).strip()
            
        if not search_query:
            search_query = query
        
        # 3. Retrieve context
        try:
            results = self._index.search(search_query, top_k=3)
        except Exception as e:
            logger.error("Search failed: %s", e)
            results = []
            
        context_parts = []
        for meta, score in results:
            context_parts.append(f"--- From {meta.source} ---\n{meta.text}")
            
        context_str = "\n\n".join(context_parts)
        
        # 4. Construct prompt
        system_msg = prompts.ANSWER_SYSTEM_PROMPT
        user_msg = f"Context:\n{context_str}\n\nProspect said:\n{search_query}"
        
        # 5. Stream LLM response
        try:
            client = self._get_client()
            stream = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                stream=True,
                temperature=0.3, # Low temp for factual answers
            )
            
            for chunk in stream:
                if self._cancel_event.is_set():
                    logger.info("Generation cancelled by new question.")
                    break
                    
                content = chunk.choices[0].delta.content
                if content:
                    callback(content)
                    
        except Exception as e:
            logger.error("LLM generation failed: %s", e)
            callback("\n[Error generating answer]")

