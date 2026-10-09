# Sales Assist — Real-Time AI Sales Co-Pilot Desktop Application

> Production-ready Windows desktop copilot that listens to live sales calls (Zoom, Microsoft Teams, Google Meet), transcribes prospect speech in real time, retrieves product context from a local knowledge base (RAG), and streams instant, fact-checked answers onto a transparent, always-on-top overlay.
> Designed for invisible operation, low-latency response generation, local document security, and zero-install client distribution.

---

## 📋 Overview

Sales Assist is an intelligent, real-time sales co-pilot designed to assist account executives and sales representatives during live client conversations. The system captures system audio output directly from the operating system, transcribes prospect inquiries, checks them against company product collateral, and displays concise 2–3 bullet-point answers without ever interrupting the conversation or requiring a bot to join the meeting.

The application supports:

- **Bot-Free Audio Capture:** Uses Windows WASAPI loopback to record incoming call audio without browser extensions or visible meeting bots.
- **Sub-Second Live Transcription:** Streams raw 16 kHz audio chunks to Sarvam AI over WebSockets with server-side Voice Activity Detection (VAD).
- **Universal Knowledge Ingestion:** Automatically indexes PDFs, Word documents (`.docx`), Excel sheets (`.xlsx`), PowerPoint slides (`.pptx`), CSVs, and text files.
- **Local FAISS Vector Database:** Performs low-latency semantic search over company documents using OpenAI `text-embedding-3-small` embeddings.
- **Intelligent Trigger & Objection Detection:** Detects questions, pricing discussions, objections, competitor mentions, and buying signals using hybrid heuristics and LLM classification.
- **Fact-Checked Answer Generation:** Streams concise, factual 2–3 bullet-point responses from `gpt-4o-mini` with strict adherence to retrieved documents.
- **Cancellable Response Streaming:** Instantly cancels and re-triggers answer generation if the prospect asks a follow-up question mid-stream.
- **Floating Invisible Overlay:** Frameless, draggable, always-on-top, semi-transparent PySide6 overlay that never steals focus from the meeting window.
- **Dual UI Interfaces:** Includes both an ultra-minimalist floating overlay (PySide6) and a full-featured split-pane desktop dashboard (CustomTkinter).
- **Automatic File Watcher:** Uses `watchdog` to monitor the `knowledge_base/` folder with debouncing and incremental SHA-256 hash-based re-indexing.
- **Standalone Packaging:** Pre-configured PyInstaller build pipeline for single-folder zero-dependency executable distribution.

---

## 🏗 Architecture

```text
Incoming Call Audio (Zoom / Teams / Meet)
   │
   │ Output Device Loopback
   ▼
WASAPI Loopback Capture (PyAudioWPatch)
   │
   │ 16 kHz, 16-bit Mono PCM Resampling (100 ms Chunks)
   ▼
Sarvam AI WebSocket Client (saaras:v4)
   │
   │ Server-side VAD & Real-Time Transcription
   ▼
Live Transcript Stream (Partial & Final)
   │
   ├── Trigger Detection Engine (Heuristics + LLM Classifier)
   │
   └── Question / Objection Identified
   │
   ▼
FAISS Vector Database (DocumentIndex)
   │
   │ Top-K Semantic Similarity Search (OpenAI Embeddings)
   ▼
Retrieved Product Context Chunks
   │
   ▼
OpenAI LLM Engine (gpt-4o-mini, temp=0.3)
   │
   │ Token-by-Token Streaming (Cancellable)
   ▼
PySide6 Floating Overlay UI / CustomTkinter GUI
   │
   ├── Live Prospect Transcript View
   └── 2-3 Bullet-Point Real-Time Answer Cards
```

The primary runtime processing loop is:

```text
Audio Capture 
  → 16 kHz Resample 
  → WebSocket Stream 
  → Sarvam STT 
  → Transcript Emit 
  → Trigger Detection 
  → FAISS Retrieval 
  → GPT-4o-mini Stream 
  → Desktop Overlay Display
```

---

## 🛠 Technology Stack

- **Language:** Python 3.10+ (64-bit Windows)
- **Audio Capture Subsystem:** `PyAudioWPatch` (Windows WASAPI loopback)
- **Audio Processing:** `numpy`, `audioop` (16 kHz mono 16-bit PCM resampling)
- **Speech-to-Text (STT):** Sarvam AI Real-Time WebSocket API (`saaras:v4` model)
- **Vector Database:** `faiss-cpu` (Facebook AI Similarity Search)
- **Embedding Model:** OpenAI `text-embedding-3-small` (1536 dimensions)
- **Inference LLM:** OpenAI `gpt-4o-mini` (temperature: 0.3 for low hallucination)
- **Primary Desktop UI:** `PySide6` (Qt 6 for Python) — Frameless, always-on-top overlay
- **Secondary Desktop GUI:** `customtkinter` — Split-pane live transcript & copilot window
- **Document Extractors:** `PyMuPDF` (`fitz`), `python-docx`, `openpyxl`, `python-pptx`
- **File System Monitoring:** `watchdog` (Background file watcher with debouncing)
- **Packaging / Executable:** `pyinstaller` (Standalone Windows `.exe` build)
- **Configuration:** `python-dotenv` (`.env` file loader with fallback cascades)
- **Logging:** Python `logging` with `RotatingFileHandler` (5 MB rollover, 3 backups)
- **Testing:** `pytest` unit test suite

---

## 📁 Project Structure

```text
Sales_assistant_Assistant/
│
├── sales_assist/                   # Core application package
│   ├── __init__.py
│   ├── config.py                   # Centralized configuration & environment loader
│   ├── main.py                     # Primary entry point: PySide6 Floating Overlay
│   ├── gui.py                      # Alternative entry point: CustomTkinter Dashboard
│   ├── app.py                      # CLI runner and headless orchestrator
│   ├── ingest.py                   # Standalone CLI document ingestion utility
│   ├── requirements.txt            # Application Python dependencies
│   │
│   ├── audio/                      # Audio capture subsystem
│   │   ├── __init__.py
│   │   └── loopback.py             # WASAPI loopback capture & 16kHz resampling
│   │
│   ├── stt/                        # Speech-to-text integration
│   │   ├── __init__.py
│   │   ├── base.py                 # Abstract STT client interface & data structures
│   │   └── sarvam.py               # Sarvam AI WebSocket client with reconnection logic
│   │
│   ├── docs/                       # RAG & Document ingestion pipeline
│   │   ├── __init__.py
│   │   ├── loaders.py              # Universal document loaders (PDF, DOCX, XLSX, PPTX)
│   │   ├── chunker.py              # Text chunker with recursive sliding window
│   │   ├── index.py                # FAISS vector store, OpenAI embeddings & hash cache
│   │   └── watcher.py              # Watchdog file system observer with debounce
│   │
│   ├── llm/                        # Intelligence & Generation engine
│   │   ├── __init__.py
│   │   ├── prompts.py              # System prompts, trigger heuristics & templates
│   │   └── answer.py               # Question detector & cancellable streaming generator
│   │
│   ├── ui/                         # PySide6 Desktop Overlay implementation
│   │   ├── __init__.py
│   │   ├── overlay.py              # Frameless, transparent, draggable overlay widget
│   │   ├── settings.py             # API keys and opacity configuration dialog
│   │   └── worker.py               # QThread pipeline coordinator bridging audio to UI
│   │
│   └── tests/                      # Automated test suite
│       ├── __init__.py
│       ├── test_chunker.py         # Chunk boundary & token counting tests
│       ├── test_loaders.py         # Document parsing validation tests
│       └── test_stt_base.py        # STT payload and callback event tests
│
├── knowledge_base/                 # Local directory for sales collateral and brochures
│   └── company_info.txt            # Sample product documentation
│
├── build.py                        # Automated PyInstaller packaging script
├── SalesAssist.spec                # PyInstaller specification file
├── Run_Sales_Assist.bat            # One-click Windows launch script
├── project_review.md               # Comprehensive codebase architecture review
├── Codebase_Per_File_Review.md     # Per-file technical specification document
├── .env.example                    # Environment variable configuration template
├── .gitignore                      # Git exclusion rules (protects API keys & builds)
├── LICENSE                         # MIT License
└── README.md                       # Project documentation
```

---

## ⚙️ Setup / Prerequisites / Installation

### Prerequisites

- **Operating System:** Windows 10 or Windows 11 (64-bit required for WASAPI loopback)
- **Python:** Python 3.10, 3.11, or 3.12 (64-bit)
- **C++ Runtime:** Microsoft Visual C++ 2015–2022 Redistributable (x64)
- **Hardware:** Active audio output device (speakers or headphones)
- **API Keys:**
  - Sarvam AI API Key ([sarvam.ai](https://www.sarvam.ai/))
  - OpenAI API Key ([platform.openai.com](https://platform.openai.com/))
- **Network Access:** Outbound HTTPS and WebSocket access to `wss://api.sarvam.ai` and `https://api.openai.com` (Port 443)

### Installation

```powershell
# 1. Clone repository
git clone https://github.com/therealhackbot2798/SalesAssistant.git
cd SalesAssistant

# 2. Create virtual environment
python -m venv .venv

# 3. Activate virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows Command Prompt:
.venv\Scripts\activate.bat

# 4. Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r sales_assist/requirements.txt
```

### Configuration Setup

Copy the sample environment file to `.env`:

```powershell
copy .env.example sales_assist\.env
```

Open `sales_assist/.env` and insert your API keys:

```env
# Sarvam AI API Key (required for real-time STT)
SARVAM_API_KEY=your_sarvam_api_key_here

# OpenAI API Key (required for embeddings and GPT answers)
OPENAI_API_KEY=your_openai_api_key_here

# STT Configuration
STT_LANGUAGE=auto
STT_MODEL=saaras:v4

# Knowledge base folder to watch & ingest
DOCS_FOLDER=knowledge_base
```

Before running the application, verify:

- Microphone and audio output devices are functioning in Windows.
- The `.env` file contains valid, active API credentials.
- Company sales collateral (PDF, DOCX, XLSX, PPTX) is placed inside the `knowledge_base/` folder.

---

## 🚀 Running the Application

### 1. Launch the Floating Overlay (Recommended)

To start the floating, transparent PySide6 desktop overlay:

```powershell
python -m sales_assist.main
```

*Or double-click the included Windows batch file:*
```text
Run_Sales_Assist.bat
```

**Overlay Controls:**
- **Drag:** Click and drag anywhere on the title bar or top edge to reposition the overlay.
- **Opacity:** Click the **Gear icon (Settings)** to adjust overlay transparency (20% to 100%).
- **Copy Transcript:** Click the **Copy icon** to copy the full call transcript to your clipboard.
- **Clear:** Click the **Trash icon** to clear the current transcript and answer cards.
- **Close:** Click the **X icon** to stop audio capture and exit cleanly.

### 2. Launch the Full Split-Pane Dashboard (CustomTkinter)

To run the wide split-pane desktop dashboard featuring a side-by-side transcript and copilot feed:

```powershell
python -m sales_assist.gui
```

### 3. Ingest Documents Manually

To manually build or rebuild the FAISS vector index from documents in `knowledge_base/`:

```powershell
python -m sales_assist.ingest
```

### 4. Run the Test Suite

Execute unit tests covering document loaders, text chunking, and STT callbacks:

```powershell
pytest -v
```

---

## 📦 Building Standalone Executable (.exe)

The application includes an automated build script to package the entire project into a self-contained executable folder using PyInstaller:

```powershell
python build.py
```

### Build Output

The build script compiles all binaries into:
```text
dist/SalesAssist/
├── SalesAssist.exe          # Main application launcher
├── _internal/               # Bundled Python runtime, Qt binaries, DLLs, and dependencies
├── knowledge_base/          # Bundled product documentation
└── .env                     # Bundled API keys configuration
```

### Distribution to Sales Representatives

1. Compress the `dist/SalesAssist/` directory into a `.zip` archive (`SalesAssist.zip`).
2. Share `SalesAssist.zip` with the sales executive.
3. The sales executive extracts the zip file and runs `SalesAssist.exe`.
4. **No Python installation, command line, or external dependencies are required on the client machine.**

---

## 🔐 Environment / Configuration

Runtime behavior is controlled via environment variables loaded from `.env`:

| Variable | Type | Default | Description |
|---|---|---|---|
| `SARVAM_API_KEY` | `string` | *(Required)* | Sarvam AI API key for WebSocket speech-to-text |
| `OPENAI_API_KEY` | `string` | *(Required)* | OpenAI API key for `text-embedding-3-small` and `gpt-4o-mini` |
| `STT_LANGUAGE` | `string` | `auto` | Language code for STT (`auto`, `hi-IN`, `en-IN`, etc.) |
| `STT_MODEL` | `string` | `saaras:v4` | Sarvam STT model version |
| `DOCS_FOLDER` | `string` | `knowledge_base` | Local directory to watch and ingest into FAISS |
| `AUDIO_SAMPLE_RATE` | `int` | `16000` | Target sample rate for Sarvam STT (16 kHz) |
| `AUDIO_CHANNELS` | `int` | `1` | Audio channel configuration (Mono) |
| `AUDIO_CHUNK_DURATION_MS` | `int` | `100` | Milliseconds of PCM audio per WebSocket transmission |

> [!CAUTION]
> Never commit `.env` files or API credentials to version control. The repository's [.gitignore](file:///c:/Users/mangesh.bondre/Desktop/Sales_assistant_Assistant/.gitignore) explicitly protects all `.env` files, cached vector indices, and binary build artifacts.

---

## ⚠️ Important Notes

- **WASAPI Loopback Constraints:** Audio capture monitors the *system default output device*. On Zoom, Teams, or Google Meet, ensure the meeting audio output is routed to your default Windows playback device (headphones or speakers).
- **Audio Resampling:** Audio is automatically converted from native device rates (e.g., 44.1 kHz or 48 kHz stereo) to 16 kHz mono 16-bit PCM required by the Sarvam API.
- **Incremental Indexing:** Document chunks are indexed using SHA-256 file hashes stored in `.sales_assist_index/hash_cache.json`. Unmodified documents are never re-embedded on startup, conserving OpenAI API credits.
- **SmartScreen Warning:** When running the packaged `SalesAssist.exe` for the first time, Windows Defender SmartScreen may display an unrecognized publisher notice. Users should select **More info** $\rightarrow$ **Run anyway**.
- **Antivirus False Positives:** PyInstaller executables may occasionally trigger corporate antivirus heuristics. Adding an exclusion for the `SalesAssist` folder resolves this.
- **Safe State Cleanup:** Exiting the overlay gracefully terminates the WASAPI capture thread, closes WebSocket streams, and stops the `watchdog` observer.

---

## 📚 Documentation

| Document | Description |
|---|---|
| `README.md` | Primary setup, architecture, and operational guide |
| [project_review.md](file:///c:/Users/mangesh.bondre/Desktop/Sales_assistant_Assistant/project_review.md) | Exhaustive 1,000+ line technical architecture walkthrough and code review |
| [Codebase_Per_File_Review.md](file:///c:/Users/mangesh.bondre/Desktop/Sales_assistant_Assistant/Codebase_Per_File_Review.md) | Granular, file-by-file class and function specifications |
| [SalesAssist.spec](file:///c:/Users/mangesh.bondre/Desktop/Sales_assistant_Assistant/SalesAssist.spec) | PyInstaller compilation specification and binary bundle definition |

---

## 👥 Ownership / Contact

| Role | Responsibility |
|---|---|
| **Author & Lead Developer** | Mangesh Raju Bondre (`msrkvbondre2798@gmail.com`) |
| **Speech-to-Text Infrastructure** | Sarvam AI Real-Time WebSocket API |
| **LLM & Embeddings** | OpenAI API (`gpt-4o-mini` & `text-embedding-3-small`) |
| **Repository** | [therealhackbot2798/SalesAssistant](https://github.com/therealhackbot2798/SalesAssistant) |

For operational questions or bug reports, include:
- Windows version (`winver`)
- Default audio playback device details
- Application log output from `sales_assist.log`
- Input document types in `knowledge_base/`

---

## 🏁 Conclusion

Sales Assist provides a reliable, non-intrusive copilot for high-stakes sales meetings. By orchestrating Windows WASAPI loopback capture, Sarvam AI speech-to-text, local FAISS document retrieval, and OpenAI streaming intelligence behind a discreet floating UI, the application empowers sales executives with immediate, factual answers while keeping customer interactions completely natural.

---

*Sales Assist — Real-Time AI Sales Co-Pilot*
