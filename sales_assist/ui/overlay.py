"""
The floating Sales Assist overlay window (PySide6).
"""

from __future__ import annotations

import sys
from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QMouseEvent, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QTextBrowser, QFrame, QSizePolicy
)

class DraggableHeader(QFrame):
    """A custom frame that acts as a draggable title bar."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #2D2D30; color: white; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        
        self.title = QLabel("Sales Assist", self)
        self.title.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        self.btn_settings = QPushButton("⚙️", self)
        self.btn_settings.setFixedSize(24, 24)
        self.btn_settings.setStyleSheet("QPushButton { border: none; background: transparent; } QPushButton:hover { background: #454545; border-radius: 4px; }")
        
        self.btn_close = QPushButton("✕", self)
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("QPushButton { border: none; background: transparent; } QPushButton:hover { background: #E81123; border-radius: 4px; }")
        
        layout.addWidget(self.title)
        layout.addStretch()
        layout.addWidget(self.btn_settings)
        layout.addWidget(self.btn_close)
        
        self._start_pos = None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._start_pos is not None:
            self.window().move(event.globalPosition().toPoint() - self._start_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._start_pos = None
        event.accept()


class OverlayWindow(QWidget):
    """
    Frameless, always-on-top, semi-transparent overlay.
    """
    
    open_settings_requested = Signal()
    close_requested = Signal()

    def __init__(self):
        super().__init__()
        
        # Window properties
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool  # Doesn't show in taskbar in some OSs, less intrusive
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.resize(400, 500)
        self.setMinimumSize(300, 300)
        
        # State
        self._finals: list[str] = []
        self._current_partial: str = ""
        self._is_transcript_visible = True
        
        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Central frame with rounded corners and semi-transparent background
        self.bg_frame = QFrame(self)
        self.bg_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 30, 30, 220);
                border-radius: 8px;
                border: 1px solid #444;
            }
        """)
        bg_layout = QVBoxLayout(self.bg_frame)
        bg_layout.setContentsMargins(0, 0, 0, 0)
        bg_layout.setSpacing(0)
        
        # 1. Header
        self.header = DraggableHeader(self)
        self.header.btn_settings.clicked.connect(self.open_settings_requested.emit)
        self.header.btn_close.clicked.connect(self.close_requested.emit)
        bg_layout.addWidget(self.header)
        
        # Content Layout
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(15, 15, 15, 15)
        content_layout.setSpacing(10)
        bg_layout.addLayout(content_layout)
        
        # 2. Controls
        controls_layout = QHBoxLayout()
        self.btn_toggle_transcript = QPushButton("Hide Transcript")
        self.btn_copy = QPushButton("Copy Transcript")
        for btn in (self.btn_toggle_transcript, self.btn_copy):
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #444; color: white; border: none; padding: 4px 8px; border-radius: 4px;
                }
                QPushButton:hover { background-color: #555; }
            """)
        controls_layout.addWidget(self.btn_toggle_transcript)
        controls_layout.addWidget(self.btn_copy)
        controls_layout.addStretch()
        content_layout.addLayout(controls_layout)
        
        self.btn_toggle_transcript.clicked.connect(self._toggle_transcript)
        self.btn_copy.clicked.connect(self._copy_transcript)
        
        # 3. Transcript Zone
        self.transcript_view = QTextBrowser()
        self.transcript_view.setStyleSheet("background: transparent; color: #CCC; border: none; font-size: 13px;")
        self.transcript_view.setOpenLinks(False)
        self.transcript_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.transcript_view.setMaximumHeight(120)
        content_layout.addWidget(self.transcript_view)
        
        # 4. Trigger Zone
        self.trigger_label = QLabel("")
        self.trigger_label.setStyleSheet("color: #FFB900; font-weight: bold; font-size: 14px; background: transparent; border: none;")
        self.trigger_label.setWordWrap(True)
        self.trigger_label.hide()
        content_layout.addWidget(self.trigger_label)
        
        # 5. Suggestion Zone
        self.suggestion_view = QTextBrowser()
        self.suggestion_view.setStyleSheet("background: transparent; color: #FFF; border: none; font-size: 15px; font-weight: 500;")
        self.suggestion_view.setOpenLinks(False)
        self.suggestion_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        content_layout.addWidget(self.suggestion_view)
        
        self.main_layout.addWidget(self.bg_frame)
        
        # Repaint throttler (30fps = ~33ms)
        self._partial_dirty = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._flush_partial)
        self._timer.start(33)

    def _toggle_transcript(self):
        self._is_transcript_visible = not self._is_transcript_visible
        if self._is_transcript_visible:
            self.transcript_view.show()
            self.btn_toggle_transcript.setText("Hide Transcript")
        else:
            self.transcript_view.hide()
            self.btn_toggle_transcript.setText("Show Transcript")

    def _copy_transcript(self):
        text = "\n".join(self._finals)
        QApplication.clipboard().setText(text)

    def _update_transcript_ui(self):
        # Keep last 5 finals
        display_finals = self._finals[-5:]
        
        html = ""
        # Fade older lines
        for i, text in enumerate(display_finals):
            opacity = 0.4 + (0.6 * (i / max(1, len(display_finals) - 1)))
            color = f"rgba(255, 255, 255, {opacity})"
            html += f'<div style="color: {color}; margin-bottom: 4px;">✓ {text}</div>'
            
        if self._current_partial:
            html += f'<div style="color: #888; font-style: italic; margin-bottom: 4px;">⟳ {self._current_partial}</div>'
            
        self.transcript_view.setHtml(html)
        
        # Auto-scroll to bottom
        sb = self.transcript_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # --- Slots for backend signals ---

    def on_partial(self, uid: str, text: str):
        self._current_partial = text
        self._partial_dirty = True

    def _flush_partial(self):
        if self._partial_dirty:
            self._update_transcript_ui()
            self._partial_dirty = False

    def on_final(self, uid: str, text: str):
        self._current_partial = ""
        self._finals.append(text)
        if len(self._finals) > 50:
            self._finals.pop(0)
        self._update_transcript_ui()
        self._partial_dirty = False

    def on_trigger(self, trigger_text: str):
        self.trigger_label.setText(f"💡 {trigger_text}")
        self.trigger_label.show()
        self.suggestion_view.clear()

    def on_llm_token(self, token: str):
        cursor = self.suggestion_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.suggestion_view.setTextCursor(cursor)
        
        # Auto-scroll
        sb = self.suggestion_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_llm_done(self):
        pass # Optional cleanup

    def on_error(self, err: str):
        self.trigger_label.setText(f"❌ Error: {err}")
        self.trigger_label.show()
        self.suggestion_view.clear()
