"""
Sales Assist – Stage 3 console runner.

Captures WASAPI loopback audio, streams it to Sarvam STT,
detects questions/objections in the final transcripts,
retrieves from FAISS index, and streams gpt-4o-mini answers to the console.

Usage:
    python -m sales_assist.app
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from sales_assist import config
from sales_assist.audio.loopback import LoopbackCapture
from sales_assist.docs.index import DocumentIndex
from sales_assist.docs.watcher import DocsWatcher
from sales_assist.llm.answer import AnswerEngine
from sales_assist.stt.base import (
    FinalTranscript,
    PartialTranscript,
    TranscriptEvent,
)
from sales_assist.stt.sarvam import SarvamSTTProvider

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sales_assist")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Global State for Console UI
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _on_llm_token(token: str) -> None:
    """Stream LLM tokens to console."""
    with _print_lock:
        sys.stdout.write(token)
        sys.stdout.flush()

def _start_llm_thread(engine: AnswerEngine, text: str) -> None:
    """Cancel any existing generation and start a new one."""
    engine.cancel_current()
    
    def _run():
        with _print_lock:
            print(f"\n💡 [TRIGGER DETECTED]: {text}")
            
        # Generate answer blocking (streams via callback and prints step latencies)
        engine.generate_answer(text, _on_llm_token)
            
    threading.Thread(target=_run, daemon=True, name="llm-worker").start()


def get_transcript_callback(engine: AnswerEngine) -> Callable[[TranscriptEvent], None]:
    """Closure to capture the engine for the transcript callback."""
    
    def _on_transcript(event: TranscriptEvent) -> None:

        with _print_lock:
            if isinstance(event, FinalTranscript):
                clean_text = event.text.replace('\n', ' ').strip()
                print(f"  ✓ [{event.utterance_id[:6]}] {clean_text}")
                
        # Outside the print lock, handle the logic
        if isinstance(event, PartialTranscript):
            if config.SPECULATIVE_PREFETCH and engine.is_trigger(event.text):
                threading.Thread(
                    target=engine.prefetch_context,
                    args=(event.text,),
                    daemon=True,
                ).start()
        elif isinstance(event, FinalTranscript):
            engine.push_utterance(event.text)
            
            # Fire-and-forget trigger check
            def _check():
                if engine.is_trigger(event.text):
                    _start_llm_thread(engine, event.text)
            threading.Thread(target=_check, daemon=True).start()

    return _on_transcript


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # 1. Validate config
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        sys.exit(1)

    logger.info("=== Sales Assist – Stage 3: Audio + STT + LLM Answers ===")

    # 2. Init FAISS Index and Answer Engine
    index = DocumentIndex()
    engine = AnswerEngine(index)
    
    # Start the automatic document watcher
    docs_watcher = DocsWatcher(index, config.DOCS_FOLDER)
    docs_watcher.start()

    # 3. Create STT provider
    stt = SarvamSTTProvider(callback=get_transcript_callback(engine))

    # 4. Create audio capture
    capture = LoopbackCapture(callback=stt.feed_audio)

    # 5. Graceful shutdown
    shutdown_requested = False

    def _shutdown(sig: int = 0, frame=None) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        logger.info("Shutting down …")
        engine.cancel_current()
        capture.stop()
        stt.stop()
        docs_watcher.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 6. Start
    try:
        stt.start()
        capture.start()
    except Exception as exc:
        logger.error("Failed to start: %s", exc)
        _shutdown()
        sys.exit(1)

    print()
    print("  🎧  Listening to system audio (WASAPI loopback).")
    print("  🗣️  Speak! If a question is detected, gpt-4o-mini will answer.")
    print("  ⏹️  Press Ctrl+C to stop.\n")

    try:
        while not shutdown_requested:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()

    logger.info("Goodbye.")


if __name__ == "__main__":
    main()
