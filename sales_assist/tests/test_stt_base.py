"""
Tests for the STT base interface and event types.

Run with: python -m pytest sales_assist/tests/test_stt_base.py -v
"""

from __future__ import annotations

import pytest

from sales_assist.stt.base import (
    FinalTranscript,
    PartialTranscript,
    STTProvider,
    TranscriptEvent,
    TranscriptType,
)


class DummyProvider(STTProvider):
    """Minimal concrete provider for testing the abstract base."""

    def __init__(self, callback):
        super().__init__(callback)
        self.started = False
        self.stopped = False
        self.fed_chunks: list[bytes] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def feed_audio(self, pcm16_bytes: bytes) -> None:
        self.fed_chunks.append(pcm16_bytes)


class TestTranscriptEvents:
    """Verify the data classes behave correctly."""

    def test_partial_transcript_type(self) -> None:
        p = PartialTranscript(text="hello", utterance_id="abc123")
        assert p.type == TranscriptType.PARTIAL
        assert p.text == "hello"
        assert p.utterance_id == "abc123"

    def test_final_transcript_type(self) -> None:
        f = FinalTranscript(text="world", utterance_id="xyz789")
        assert f.type == TranscriptType.FINAL
        assert f.text == "world"

    def test_events_are_frozen(self) -> None:
        p = PartialTranscript(text="hi", utterance_id="aaa")
        with pytest.raises(AttributeError):
            p.text = "changed"  # type: ignore[misc]


class TestSTTProviderBase:
    """Verify the abstract base wiring."""

    def test_emit_calls_callback(self) -> None:
        events: list[TranscriptEvent] = []
        provider = DummyProvider(callback=events.append)

        partial = PartialTranscript(text="hello", utterance_id="u1")
        provider._emit(partial)

        assert len(events) == 1
        assert events[0] is partial

    def test_new_utterance_id_is_unique(self) -> None:
        ids = {STTProvider.new_utterance_id() for _ in range(100)}
        assert len(ids) == 100  # all unique

    def test_feed_audio_collects(self) -> None:
        provider = DummyProvider(callback=lambda e: None)
        provider.feed_audio(b"\x00\x01")
        provider.feed_audio(b"\x02\x03")
        assert provider.fed_chunks == [b"\x00\x01", b"\x02\x03"]

    def test_start_stop_flags(self) -> None:
        provider = DummyProvider(callback=lambda e: None)
        provider.start()
        assert provider.started
        provider.stop()
        assert provider.stopped
