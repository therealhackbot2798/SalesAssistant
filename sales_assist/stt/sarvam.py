"""
Sarvam AI real-time STT provider.

Uses the ``wss://api.sarvam.ai/speech-to-text-realtime/ws`` WebSocket
endpoint which supports:
  - ``transcript.partial`` events (interim text as the user speaks)
  - ``transcript.final`` events (committed text when utterance ends)
  - ``config.update`` for mid-call reconfiguration
  - Built-in server-side VAD

Audio is sent as base64-encoded 16 kHz mono 16-bit PCM in ``audio_input``
JSON messages.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Optional

import websocket  # websocket-client

from sales_assist import config
from sales_assist.stt.base import (
    FinalTranscript,
    PartialTranscript,
    STTProvider,
    TranscriptCallback,
)

logger = logging.getLogger(__name__)


class SarvamSTTProvider(STTProvider):
    """
    Streams audio to Sarvam's real-time WebSocket STT endpoint and
    emits :class:`PartialTranscript` / :class:`FinalTranscript` events.
    """

    def __init__(
        self,
        callback: TranscriptCallback,
        language: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(callback)
        self._language = language or config.STT_LANGUAGE
        self._model = model or config.STT_MODEL
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._connected = threading.Event()
        self._stop_event = threading.Event()
        self._current_utterance_id: str = self.new_utterance_id()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API (from STTProvider)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the WebSocket connection to Sarvam STT."""
        if self._ws_thread is not None and self._ws_thread.is_alive():
            logger.warning("SarvamSTT already running.")
            return

        self._stop_event.clear()
        self._connected.clear()

        api_key = config.SARVAM_API_KEY
        if not api_key:
            raise RuntimeError("SARVAM_API_KEY is not set. Check your .env file.")

        # Build the WebSocket URL with query params
        ws_url = (
            f"{config.SARVAM_WS_URL}"
            f"?language_code={self._language}"
            f"&model={self._model}"
        )

        logger.info("Connecting to Sarvam STT: %s", ws_url)

        self._ws = websocket.WebSocketApp(
            ws_url,
            header=[f"Api-Subscription-Key: {api_key}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(
            target=self._run_ws,
            daemon=True,
            name="sarvam-stt-ws",
        )
        self._ws_thread.start()

        # Wait up to 10 s for the connection to establish
        if not self._connected.wait(timeout=10.0):
            raise RuntimeError(
                "Timed out waiting for Sarvam WebSocket connection. "
                "Check your API key and network."
            )

        logger.info("Sarvam STT connected and ready.")

    def stop(self) -> None:
        """Gracefully close the WebSocket connection."""
        self._stop_event.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=5.0)
            self._ws_thread = None
        self._connected.clear()
        logger.info("Sarvam STT stopped.")

    def feed_audio(self, pcm16_bytes: bytes) -> None:
        """
        Send a chunk of 16 kHz mono 16-bit PCM audio to Sarvam.

        The audio is base64-encoded and sent as an ``audio_input`` event.
        """
        if not self._connected.is_set() or self._ws is None:
            return  # silently drop if not connected

        payload = json.dumps({
            "event": "audio_input",
            "audio": base64.b64encode(pcm16_bytes).decode("ascii"),
        })

        try:
            self._ws.send(payload)
        except Exception as exc:
            logger.warning("Failed to send audio chunk: %s", exc)

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WebSocket connection opened.")
        self._connected.set()

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Handle incoming transcript events from Sarvam."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Non-JSON message received: %s", message[:200])
            return

        event_type = data.get("event") or data.get("type") or ""

        if event_type == "transcript.partial":
            text = data.get("transcript") or data.get("text") or ""
            if text.strip():
                with self._lock:
                    uid = self._current_utterance_id
                self._emit(PartialTranscript(text=text.strip(), utterance_id=uid))

        elif event_type == "transcript.final":
            text = data.get("transcript") or data.get("text") or ""
            if text.strip():
                with self._lock:
                    uid = self._current_utterance_id
                    # Rotate utterance ID for the next utterance
                    self._current_utterance_id = self.new_utterance_id()
                self._emit(FinalTranscript(text=text.strip(), utterance_id=uid))

        elif event_type == "speech_start":
            # New utterance starting – rotate ID
            with self._lock:
                self._current_utterance_id = self.new_utterance_id()
            logger.debug("Speech start detected, new utterance_id=%s", self._current_utterance_id)

        elif event_type == "speech_end":
            logger.debug("Speech end detected.")

        elif event_type in ("error", "server_error"):
            err_msg = data.get("message") or data.get("error") or str(data)
            logger.error("Sarvam STT error: %s", err_msg)

        else:
            logger.debug("Unhandled Sarvam event: %s -> %s", event_type, str(data)[:300])

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("WebSocket error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: Optional[int],
        close_msg: Optional[str],
    ) -> None:
        self._connected.clear()
        logger.info(
            "WebSocket closed (code=%s, msg=%s).", close_status_code, close_msg
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_ws(self) -> None:
        """Run the WebSocket event loop (blocking). Called on its own thread."""
        while not self._stop_event.is_set():
            try:
                assert self._ws is not None
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logger.error("WebSocket run error: %s", exc)

            if self._stop_event.is_set():
                break

            # If we get here, the socket closed unexpectedly – reconnect
            self._connected.clear()
            logger.warning("Sarvam WebSocket disconnected – reconnecting in 2 s …")
            time.sleep(2.0)

            # Rebuild the WebSocketApp for a fresh connection
            api_key = config.SARVAM_API_KEY
            ws_url = (
                f"{config.SARVAM_WS_URL}"
                f"?language_code={self._language}"
                f"&model={self._model}"
            )
            self._ws = websocket.WebSocketApp(
                ws_url,
                header=[f"Api-Subscription-Key: {api_key}"],
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

        logger.debug("WebSocket thread exiting.")
