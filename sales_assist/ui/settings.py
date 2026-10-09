"""
Settings dialog for Sales Assist (PySide6).
"""

from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, 
    QPushButton, QHBoxLayout, QSlider, QLabel
)
from PySide6.QtCore import Qt, Signal

from sales_assist import config

logger = logging.getLogger(__name__)

class SettingsDialog(QDialog):
    """
    Basic settings dialog for API keys and UI preferences.
    """
    
    opacity_changed = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sales Assist - Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.openai_key = QLineEdit(config.OPENAI_API_KEY or "")
        self.openai_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        form.addRow("OpenAI API Key:", self.openai_key)
        
        self.sarvam_key = QLineEdit(config.SARVAM_API_KEY or "")
        self.sarvam_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        form.addRow("Sarvam API Key:", self.sarvam_key)
        
        # Opacity Slider
        opacity_layout = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(90) # default 0.9
        self.opacity_val_label = QLabel("90%")
        
        opacity_layout.addWidget(self.opacity_slider)
        opacity_layout.addWidget(self.opacity_val_label)
        
        self.opacity_slider.valueChanged.connect(self._on_opacity_change)
        form.addRow("Overlay Opacity:", opacity_layout)
        
        layout.addLayout(form)
        
        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        
        layout.addLayout(btn_layout)

    def _on_opacity_change(self, val: int):
        self.opacity_val_label.setText(f"{val}%")
        self.opacity_changed.emit(val / 100.0)

    def save_config(self):
        # In a real app, we'd write these back to .env
        config.OPENAI_API_KEY = self.openai_key.text()
        config.SARVAM_API_KEY = self.sarvam_key.text()
        logger.info("Settings saved (in-memory).")
