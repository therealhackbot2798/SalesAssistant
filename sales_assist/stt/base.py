"""
Abstract STT provider interface.

Every STT backend must inherit from :class:`STTProvider` and emit
:class:`PartialTranscript` / :class:`FinalTranscript` events through
the registered callback.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Union


class TranscriptType(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True)
class PartialTranscript:
    """Interim text that is replaced in-place until the utterance ends."""
    text: str
    utterance_id: str
    type: TranscriptType = field(default=TranscriptType.PARTIAL, init=False)


@dataclass(frozen=True)
class FinalTranscript:
    """Committed text once the utterance is complete."""
    text: str
    utterance_id: str
    type: TranscriptType = field(default=TranscriptType.FINAL, init=False)


TranscriptEvent = Union[PartialTranscript, FinalTranscript]
TranscriptCallback = Callable[[TranscriptEvent], None]


class STTProvider(ABC):
    """
    Base class for speech-to-text providers.

    Subclasses must implement ``start``, ``stop``, and ``feed_audio``.
    They emit transcript events through ``self._emit(event)``.
    """

    def __init__(self, callback: TranscriptCallback) -> None:
        self._callback = callback

    def _emit(self, event: TranscriptEvent) -> None:
        """Deliver a transcript event to the registered callback."""
        self._callback(event)

    @staticmethod
    def new_utterance_id() -> str:
        """Generate a unique utterance ID."""
        return uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Prepare the provider (e.g. open WebSocket connection)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Tear down the provider."""
        ...

    @abstractmethod
    def feed_audio(self, pcm16_bytes: bytes) -> None:
        """
        Accept a chunk of 16 kHz mono 16-bit PCM audio.

        The provider should buffer / forward this data to the STT backend.
        """
        ...
