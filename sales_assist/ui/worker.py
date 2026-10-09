"""
Qt Worker for connecting the backend threads to the PySide6 UI.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from sales_assist.audio.loopback import LoopbackCapture
from sales_assist.docs.index import DocumentIndex
from sales_assist.llm.answer import AnswerEngine
from sales_assist.stt.base import FinalTranscript, PartialTranscript, TranscriptEvent
from sales_assist.stt.sarvam import SarvamSTTProvider

logger = logging.getLogger(__name__)


class BackendWorker(QObject):
    """
    Manages the audio, STT, and LLM backend.
    Emits Qt signals when transcripts arrive or LLM tokens stream.
    """

    # --- Signals to UI ---
    partial_transcript = Signal(str, str)  # utterance_id, text
    final_transcript = Signal(str, str)    # utterance_id, text
    trigger_detected = Signal(str)         # question/trigger text
    llm_token = Signal(str)                # streamed chunk
    llm_done = Signal()                    # stream finished
    error_occurred = Signal(str)           # error message

    def __init__(self, index: DocumentIndex) -> None:
        super().__init__()
        self._index = index
        self._engine = AnswerEngine(index)
        
        self._stt = None
        self._capture = None
        self._is_running = False

    def start_capture(self) -> None:
        """Start the audio and STT threads."""
        if self._is_running:
            return

        try:
            self._stt = SarvamSTTProvider(callback=self._on_transcript)
            self._capture = LoopbackCapture(callback=self._stt.feed_audio)
            
            self._stt.start()
            self._capture.start()
            self._is_running = True
            logger.info("BackendWorker started.")
        except Exception as exc:
            logger.error("Failed to start backend: %s", exc)
            self.error_occurred.emit(str(exc))

    def stop_capture(self) -> None:
        """Stop the audio and STT threads."""
        self._is_running = False
        self._engine.cancel_current()
        
        if self._capture:
            self._capture.stop()
            self._capture = None
        if self._stt:
            self._stt.stop()
            self._stt = None
            
        logger.info("BackendWorker stopped.")

    def _on_transcript(self, event: TranscriptEvent) -> None:
        """Callback from STTProvider. Emits Qt signals."""
        if isinstance(event, PartialTranscript):
            self.partial_transcript.emit(event.utterance_id, event.text)
        elif isinstance(event, FinalTranscript):
            self.final_transcript.emit(event.utterance_id, event.text)
            self._engine.push_utterance(event.text)
            
            # Fire and forget trigger check (must not block STT)
            threading.Thread(
                target=self._check_trigger, 
                args=(event.text,), 
                daemon=True
            ).start()

    def _check_trigger(self, text: str) -> None:
        if self._engine.is_trigger(text):
            self.trigger_detected.emit(text)
            self._engine.cancel_current()
            
            def _llm_worker():
                self._engine.generate_answer(text, self._on_llm_token)
                self.llm_done.emit()
                
            threading.Thread(target=_llm_worker, daemon=True).start()

    def _on_llm_token(self, token: str) -> None:
        self.llm_token.emit(token)
