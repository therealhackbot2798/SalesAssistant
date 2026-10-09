"""
WASAPI loopback audio capture via PyAudioWPatch.

Captures the system's default output device (what the prospect says through
Teams/Meet/Zoom) and re-samples it to 16 kHz mono 16-bit PCM suitable for
Sarvam STT.
"""

from __future__ import annotations

import audioop
import logging
import struct
import threading
import time
from collections.abc import Callable
from typing import Optional

import numpy as np
import pyaudiowpatch as pyaudio

from sales_assist import config

logger = logging.getLogger(__name__)

# Type alias: callback receives raw 16 kHz mono 16-bit PCM bytes
AudioCallback = Callable[[bytes], None]


class LoopbackCapture:
    """Captures system audio via WASAPI loopback and delivers 16 kHz mono PCM chunks."""

    def __init__(self, callback: AudioCallback, device_index: Optional[int] = None) -> None:
        """
        Args:
            callback: Called on the capture thread with PCM16 bytes every chunk.
            device_index: Specific WASAPI loopback device index.
                          If None, the default output device's loopback is used.
        """
        self._callback = callback
        self._device_index = device_index
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Open the loopback stream and begin capturing on a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Capture already running.")
            return

        self._stop_event.clear()
        self._pa = pyaudio.PyAudio()

        device_info = self._resolve_device()
        native_rate = int(device_info["defaultSampleRate"])
        native_channels = device_info["maxInputChannels"]

        logger.info(
            "Loopback device: %s  (rate=%d, ch=%d, idx=%d)",
            device_info["name"],
            native_rate,
            native_channels,
            device_info["index"],
        )

        # Compute how many native frames correspond to one output chunk
        # so we produce ~AUDIO_CHUNK_DURATION_MS of 16 kHz audio per callback.
        native_chunk_frames = native_rate * config.AUDIO_CHUNK_DURATION_MS // 1000

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=native_channels,
            rate=native_rate,
            input=True,
            input_device_index=device_info["index"],
            frames_per_buffer=native_chunk_frames,
        )

        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(native_rate, native_channels, native_chunk_frames),
            daemon=True,
            name="loopback-capture",
        )
        self._thread.start()
        logger.info("Loopback capture started.")

    def stop(self) -> None:
        """Stop the capture thread and release resources."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
        logger.info("Loopback capture stopped.")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------
    def _resolve_device(self) -> dict:
        """Find the WASAPI loopback device to use."""
        assert self._pa is not None

        if self._device_index is not None:
            info = self._pa.get_device_info_by_index(self._device_index)
            logger.info("Using explicitly selected device index %d", self._device_index)
            return info

        # Use the helper that PyAudioWPatch provides
        try:
            return self._pa.get_default_wasapi_loopback()
        except Exception:
            logger.error(
                "Could not find a WASAPI loopback device. "
                "Make sure you are on Windows with a working audio output device."
            )
            raise

    @staticmethod
    def list_loopback_devices() -> list[dict]:
        """Return a list of available WASAPI loopback input devices."""
        devices: list[dict] = []
        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and "loopback" in info.get("name", "").lower():
                    devices.append(info)
        finally:
            pa.terminate()
        return devices

    # ------------------------------------------------------------------
    # Capture loop (runs on background thread)
    # ------------------------------------------------------------------
    def _capture_loop(
        self, native_rate: int, native_channels: int, chunk_frames: int
    ) -> None:
        """Read from the stream, resample to 16 kHz mono, and invoke callback."""
        target_rate = config.AUDIO_SAMPLE_RATE
        sample_width = config.AUDIO_SAMPLE_WIDTH  # 2 bytes (16-bit)

        logger.debug(
            "Capture loop started: native %d Hz %d ch -> %d Hz mono",
            native_rate,
            native_channels,
            target_rate,
        )

        # audioop.ratecv state for streaming resampler
        resample_state = None

        while not self._stop_event.is_set():
            try:
                raw = self._stream.read(chunk_frames, exception_on_overflow=False)
            except OSError as exc:
                if self._stop_event.is_set():
                    break
                logger.warning("Stream read error: %s – retrying", exc)
                time.sleep(0.05)
                continue

            # --- Down-mix to mono ---------------------------------------------------
            if native_channels > 1:
                raw = audioop.tomono(raw, sample_width, 1.0 / native_channels, 1.0 / native_channels)

            # --- Resample to 16 kHz -------------------------------------------------
            if native_rate != target_rate:
                raw, resample_state = audioop.ratecv(
                    raw,
                    sample_width,
                    1,               # mono after down-mix
                    native_rate,
                    target_rate,
                    resample_state,
                )

            # --- Deliver PCM16 chunk ------------------------------------------------
            try:
                self._callback(raw)
            except Exception:
                logger.exception("Error in audio callback")

        logger.debug("Capture loop exited.")
