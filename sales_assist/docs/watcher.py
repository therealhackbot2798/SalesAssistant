"""
Automatic directory watcher for real-time document ingestion.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sales_assist.docs.index import DocumentIndex

logger = logging.getLogger(__name__)

class IngestionEventHandler(FileSystemEventHandler):
    def __init__(self, index: DocumentIndex, folder: Path, debounce_sec: float = 2.0):
        super().__init__()
        self._index = index
        self._folder = folder
        self._debounce_sec = debounce_sec
        
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _trigger_ingest(self):
        logger.info("Changes detected! Running auto-ingestion on %s", self._folder)
        try:
            # Re-ingest. Since the index uses file_hashes, it will only process
            # what actually changed, making this very fast.
            summary = self._index.ingest_folder(self._folder)
            
            # Only print if something actually changed to avoid spamming the UI
            has_changes = any([summary.indexed, summary.updated, summary.removed, summary.failed])
            if has_changes:
                summary.print_report()
            else:
                logger.info("Auto-ingestion complete: No meaningful changes detected.")
        except Exception as e:
            logger.error("Auto-ingestion failed: %s", e)

    def _debounce(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_sec, self._trigger_ingest)
            self._timer.daemon = True
            self._timer.start()

    def on_modified(self, event):
        if not event.is_directory:
            self._debounce()

    def on_created(self, event):
        if not event.is_directory:
            self._debounce()

    def on_deleted(self, event):
        if not event.is_directory:
            self._debounce()

    def on_moved(self, event):
        if not event.is_directory:
            self._debounce()


class DocsWatcher:
    def __init__(self, index: DocumentIndex, folder: Path | str):
        self._index = index
        self._folder = Path(folder)
        self._observer = None

    def start(self):
        if not self._folder.exists():
            logger.info("Folder %s does not exist, creating it.", self._folder)
            self._folder.mkdir(parents=True, exist_ok=True)
            
        # Do an initial sync on startup
        logger.info("Performing startup sync on %s", self._folder)
        try:
            summary = self._index.ingest_folder(self._folder)
            has_changes = any([summary.indexed, summary.updated, summary.removed, summary.failed])
            if has_changes:
                summary.print_report()
            else:
                logger.info("Startup sync complete: No new files to ingest.")
        except Exception as e:
            logger.error("Startup ingestion failed: %s", e)

        # Setup watcher
        event_handler = IngestionEventHandler(self._index, self._folder)
        self._observer = Observer()
        self._observer.schedule(event_handler, str(self._folder.absolute()), recursive=True)
        self._observer.start()
        logger.info("Started watching %s for file changes.", self._folder)

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Stopped watching %s.", self._folder)
