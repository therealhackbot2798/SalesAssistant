"""
Entry point for the Sales Assist PySide6 Desktop Application (Stage 4).
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from sales_assist import config
from sales_assist.docs.index import DocumentIndex
from sales_assist.docs.watcher import DocsWatcher
from sales_assist.ui.overlay import OverlayWindow
from sales_assist.ui.settings import SettingsDialog
from sales_assist.ui.worker import BackendWorker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
from logging.handlers import RotatingFileHandler

def setup_logging():
    log_formatter = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s", datefmt="%H:%M:%S")
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (5MB, keep 3 backups)
    file_handler = RotatingFileHandler("sales_assist.log", maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    
    # Silence httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("sales_assist")

def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Sales Assist")

    # 1. Initialize FAISS index
    index = DocumentIndex()
    
    # Start the automatic document watcher
    docs_watcher = DocsWatcher(index, config.DOCS_FOLDER)
    docs_watcher.start()

    # 2. Setup UI
    overlay = OverlayWindow()
    settings_dlg = SettingsDialog(overlay)
    
    # 3. Setup Backend Worker
    worker = BackendWorker(index)
    
    # Wire Backend -> UI
    worker.partial_transcript.connect(overlay.on_partial)
    worker.final_transcript.connect(overlay.on_final)
    worker.trigger_detected.connect(overlay.on_trigger)
    worker.llm_token.connect(overlay.on_llm_token)
    worker.llm_done.connect(overlay.on_llm_done)
    worker.error_occurred.connect(overlay.on_error)
    
    # Wire UI -> Actions
    def _open_settings():
        if settings_dlg.exec():
            settings_dlg.save_config()
            
    def _change_opacity(val: float):
        overlay.setWindowOpacity(val)
        
    overlay.open_settings_requested.connect(_open_settings)
    settings_dlg.opacity_changed.connect(_change_opacity)
    
    overlay.close_requested.connect(app.quit)

    # 4. Show and Start
    overlay.show()
    
    # Validate API keys before starting
    errors = config.validate()
    if errors:
        overlay.on_error("Missing API Keys! Open Settings.")
    else:
        worker.start_capture()
    
    # 5. Cleanup on exit
    ret = app.exec()
    worker.stop_capture()
    docs_watcher.stop()
    sys.exit(ret)

if __name__ == "__main__":
    main()
