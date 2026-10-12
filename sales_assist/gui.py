"""
Sales Assist - Desktop GUI Application using CustomTkinter
"""
import customtkinter as ctk
import queue
import threading
import logging
import sys

from sales_assist import config
from sales_assist.audio.loopback import LoopbackCapture
from sales_assist.docs.index import DocumentIndex
from sales_assist.docs.watcher import DocsWatcher
from sales_assist.llm.answer import AnswerEngine
from sales_assist.stt.base import FinalTranscript, PartialTranscript, TranscriptEvent
from sales_assist.stt.sarvam import SarvamSTTProvider

# Silence noisy logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class SalesAssistApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Sales Assist AI Co-Pilot")
        self.geometry("1100x700")
        self.minsize(800, 500)

        self.ui_queue = queue.Queue()
        self.shutdown_requested = False

        # Backend components
        self.index = None
        self.engine = None
        self.docs_watcher = None
        self.stt = None
        self.capture = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start backend initialization in a background thread
        threading.Thread(target=self._init_backend, daemon=True).start()

        # Start polling the thread-safe queue for UI updates
        self.after(50, self.process_queue)

    def _build_ui(self):
        # Main Grid Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Headers
        self.lbl_transcript = ctk.CTkLabel(self, text="Live Transcript", font=("Inter", 20, "bold"))
        self.lbl_transcript.grid(row=0, column=0, pady=(20, 10), padx=20, sticky="w")

        self.lbl_ai = ctk.CTkLabel(self, text="AI Co-Pilot", font=("Inter", 20, "bold"))
        self.lbl_ai.grid(row=0, column=1, pady=(20, 10), padx=20, sticky="w")

        # Transcript Text Box
        self.txt_transcript = ctk.CTkTextbox(self, font=("Inter", 14), wrap="word", fg_color="#1E1E1E", text_color="#E0E0E0")
        self.txt_transcript.grid(row=1, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        self.txt_transcript.configure(state="disabled")

        # AI Co-Pilot Text Box
        self.txt_ai = ctk.CTkTextbox(self, font=("Inter", 15), wrap="word", fg_color="#1a2b3c", text_color="#FFFFFF")
        self.txt_ai.grid(row=1, column=1, padx=(10, 20), pady=(0, 20), sticky="nsew")
        self.txt_ai.configure(state="disabled")

        # Status Bar
        self.status_bar = ctk.CTkLabel(self, text="Initializing...", anchor="w", fg_color="#2b2b2b", padx=10)
        self.status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")

    def log_transcript(self, text):
        self.txt_transcript.configure(state="normal")
        self.txt_transcript.insert("end", f"✓ {text}\n\n")
        self.txt_transcript.see("end")
        self.txt_transcript.configure(state="disabled")

    def set_ai_trigger(self, trigger_text):
        self.txt_ai.configure(state="normal")
        self.txt_ai.delete("1.0", "end")
        self.txt_ai.insert("end", f"💡 QUESTION DETECTED:\n{trigger_text}\n\n----------------------------\n\nGenerating Answer...\n")
        self.txt_ai.see("end")
        self.txt_ai.configure(state="disabled")

    def append_ai_answer(self, token):
        self.txt_ai.configure(state="normal")
        # Remove the "Generating Answer..." if it's there
        content = self.txt_ai.get("1.0", "end")
        if "Generating Answer..." in content:
            self.txt_ai.delete("1.0", "end")
            self.txt_ai.insert("end", content.replace("Generating Answer...\n", ""))
        
        self.txt_ai.insert("end", token)
        self.txt_ai.see("end")
        self.txt_ai.configure(state="disabled")

    def process_queue(self):
        try:
            while True:
                msg_type, data = self.ui_queue.get_nowait()
                
                if msg_type == "status":
                    self.status_bar.configure(text=data)
                elif msg_type == "transcript":
                    self.log_transcript(data)
                elif msg_type == "trigger":
                    self.set_ai_trigger(data)
                elif msg_type == "answer_chunk":
                    self.append_ai_answer(data)

                self.ui_queue.task_done()
        except queue.Empty:
            pass
        
        if not self.shutdown_requested:
            self.after(50, self.process_queue)

    def _init_backend(self):
        self.ui_queue.put(("status", "Loading config..."))
        errors = config.validate()
        if errors:
            self.ui_queue.put(("status", f"Config Error: {errors[0]}"))
            return

        self.ui_queue.put(("status", "Loading Knowledge Base..."))
        self.index = DocumentIndex()
        self.engine = AnswerEngine(self.index)
        
        self.docs_watcher = DocsWatcher(self.index, config.DOCS_FOLDER)
        self.docs_watcher.start()

        def _on_llm_token(token: str):
            self.ui_queue.put(("answer_chunk", token))

        def _start_llm_thread(text: str):
            self.engine.cancel_current()
            self.ui_queue.put(("trigger", text))
            
            def _run():
                self.engine.generate_answer(text, _on_llm_token)
            
            threading.Thread(target=_run, daemon=True).start()

        def _on_transcript(event: TranscriptEvent):
            if isinstance(event, PartialTranscript):
                if config.SPECULATIVE_PREFETCH and self.engine.is_trigger(event.text):
                    threading.Thread(
                        target=self.engine.prefetch_context,
                        args=(event.text,),
                        daemon=True,
                    ).start()
            elif isinstance(event, FinalTranscript):
                clean_text = event.text.replace('\n', ' ').strip()
                self.ui_queue.put(("transcript", clean_text))
                
                self.engine.push_utterance(event.text)
                def _check():
                    if self.engine.is_trigger(event.text):
                        _start_llm_thread(event.text)
                threading.Thread(target=_check, daemon=True).start()

        self.ui_queue.put(("status", "Connecting to Sarvam STT..."))
        self.stt = SarvamSTTProvider(callback=_on_transcript)
        self.capture = LoopbackCapture(callback=self.stt.feed_audio)

        try:
            self.stt.start()
            self.capture.start()
            self.ui_queue.put(("status", "🟢 Active: Listening to system audio (WASAPI loopback)"))
        except Exception as exc:
            self.ui_queue.put(("status", f"Error: {exc}"))

    def on_closing(self):
        self.shutdown_requested = True
        self.ui_queue.put(("status", "Shutting down..."))
        
        def _shutdown():
            if self.capture: self.capture.stop()
            if self.stt: self.stt.stop()
            if self.docs_watcher: self.docs_watcher.stop()
            if self.engine: self.engine.cancel_current()
            self.quit()

        threading.Thread(target=_shutdown, daemon=True).start()

if __name__ == "__main__":
    app = SalesAssistApp()
    app.mainloop()
