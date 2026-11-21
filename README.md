# RepoMentor

**RepoMentor** is an AI-powered technical interview coach and repository intelligence platform. It analyzes your GitHub codebase, extracts verifiable architectural facts, and provides grounded conversational mentoring and realistic mock interview practice so you can master and defend your own software projects with confidence.

---

## Architecture Overview

RepoMentor integrates a full-stack architecture featuring a React frontend, evidence-grounded ReAct agent, adaptive interview state machine, and FastAPI backend backed by MongoDB:

```
                  +--------------------------------------------------+
                  |                 GitHub Repository                |
                  +--------------------------------------------------+
                                           |
                                [ Ingestion & Parsers ]
                                           v
                  +--------------------------------------------------+
                  |         MongoDB (Dossiers, Facts, Sessions)      |
                  +--------------------------------------------------+
                                           |
                   +-----------------------+-----------------------+
                   |                                               |
                   v                                               v
     +---------------------------+                   +---------------------------+
     |   RepoMentor Chat Agent   |                   |  Interview Engine Machine |
     |  (Grounded ReAct Loop,    |                   |   (Adaptive Plan, Rubric, |
     |   Citations & Repair)     |                   |   Teaching Cycle & Eval)  |
     +---------------------------+                   +---------------------------+
                   |                                               |
                   +-----------------------+-----------------------+
                                           |
                                           v
                  +--------------------------------------------------+
                  |         FastAPI REST API & SSE Streaming         |
                  |     (Canonical Resolver, Concurrency Lock)       |
                  +--------------------------------------------------+
                                           |
                   +-----------------------+-----------------------+
                   |                                               |
                   v                                               v
     +---------------------------+                   +---------------------------+
     |     React 18 Frontend     |                   |       CLI Interface       |
     | (Dashboard, Dossier, Chat,|                   |   (`python -m app.cli`)   |
     |  Interview, Progress Hub) |                   |                           |
     +---------------------------+                   +---------------------------+
```

---

## Features Achieved So Far

### 1. Repository Ingestion & Codebase Intelligence
- **Multi-Source Ingestion:** Fetches file trees, commits, pull requests, issues, and metadata from GitHub.
- **Dependency Manifest Parsing:** Extracts dependencies across Python (`pyproject.toml`, `requirements.txt`, `Pipfile`), Node (`package.json`), Rust (`Cargo.toml`), and Go (`go.mod`).
- **Static Fact Derivation:** Discovers endpoints, database clients, authentication handlers, external services, and component architecture.
- **Commit Timeline Analytics:** Analyzes commit velocity, codebase evolution buckets, author activity, and first-seen component dates.
- **Structured Project Dossier:** Distills repository understanding into 9 curated sections (`identity`, `technologies`, `components`, `apis`, `data`, `features`, `decisions`, `timeline`, `quality`) with confidence tags and user editability.
- **Canonical Project Resolution:** Resolves project identifiers cleanly across URL slugs, project IDs, and repository full names without identifier mismatches.

### 2. Evidence-Grounded Mentor Agent & Policy Engine
- **ReAct Investigation Agent:** Autonomously inspects code, commit logs, and dependency trees using tools (`get_dossier`, `read_file`, `search_code`, `get_commit`, `get_timeline`, `get_dependencies`).
- **Strict Evidence Citation:** Every claim is tagged (`[Fact]`, `[Inferred]`, `[Uncertain]`, `[General]`) and mapped to verified evidence IDs (`file:...`, `commit:...`, `pr:...`, `dep:...`, `fact:...`, `repo:...`).
- **Automated Citation Repair:** Detects unrecognised bracket tokens (e.g. `[dossier:apis]`) and unsupported claims, triggering a targeted repair turn that supplies real collected evidence IDs (`Valid ids you can cite: ...`). If uncorrected, it safely downgrades unverified claims to `[Inferred]` and appends verification notes.
- **Quote Verification:** Strips hallucinated code blocks and warns on quotes not found in actual tool results.
- **Model Fallback & Resilience:** Automatic fallback and exponential backoff across model tiers on 429 rate limits or provider quotas.

### 3. Adaptive Technical Interview & Teaching Engine
- **Targeted Question Generation:** Dynamically generates interview questions across categories (`tech`, `architecture`, `history`, `problem`, `tradeoff`) tailored to difficulty levels (1-5) and specific repository claims.
- **Rubric-Driven Evaluation:** Evaluates candidate answers against multi-point rubrics (`points`, `misconceptions`, `unverified claims`, `strengths`, and `advice`) with objective scoring (0-4).
- **Automated Teaching Cycle:**
  - Identifies weak answers (score $\le 2$).
  - Delivers a grounded micro-lesson on the exact missed concepts.
  - Automatically generates a focused follow-up probe question.
  - Prompts a retry on the original question with before/after progress scoring and copy-paste detection.
- **Pure State Machine (`app/interview/engine.py`):** Non-printing, stage-based lifecycle (`need_question`, `awaiting_answer`, `need_lesson`, `need_followup`, `need_retry`, `finished`) with anti-cheat protection stripping rubrics from candidate responses.
- **Model Concurrency Locking (`app/api/locks.py`):** Prevents race conditions and model overload between chat and interview endpoints with structured 409 responses.

### 4. React Web Application (`frontend/`)
- **Projects Dashboard:** Overview of imported repositories with language breakdown, stats, and one-click access.
- **Visual Dossier Explorer:**
  - Browse claims across all 9 architectural sections.
  - Filter by claim tags (`[Fact]`, `[Inferred]`, `[Uncertain]`) with live count badges.
  - Instant client-side keyword and stem search within section claims.
  - One-click static dossier rebuild trigger.
- **Interactive Evidence Drawer:**
  - Clickable evidence chips embedded in dossier claims and chat answers.
  - Slide-over drawer with syntax-highlighted code viewer and context line windowing.
  - Commit detail viewer showing commit messages, authors, +/- line stats, and touched files.
- **Real-Time Mentor Chat (`/projects/:id/chat`):**
  - Streaming conversational interface powered by Server-Sent Events (SSE).
  - Live trace step inspection (tool name, arguments, preview, timing, token count).
  - Clickable evidence chips rendered inline without unsafe HTML.
  - Auto-scrolling, textarea resizing, example starter prompts, and model busy handling.
- **Interview Studio (`/projects/:id/interview`):**
  - Full interview stage with question metadata, difficulty badges, and keyboard shortcuts (`Cmd/Ctrl + Enter`).
  - Interactive evaluation card displaying score (handling `0/4` properly), coverage %, rubric points, and advice.
  - Micro-lesson view with grounded explanations for missed concepts.
  - Retry mode with before/after score tracking and copy-paste detection banner.
  - Comprehensive final report: question breakdown table, weakest-concept chips, and improvement history.
- **Learner Progress Dashboard (`/projects/:id/progress`):**
  - Concept mastery cards sorted with weakest first.
  - Concept scores (handling `0/4` and decimals), status pills (`learning`, `mastered`), and attempt metrics.
  - General concepts categorized as `Uncategorised` and listed last.
  - "Revise first" priority list and one-click "Practice weakest" interview trigger.
  - Historical session report inspection modal.

### 5. Rigorous Test Suite
- **Backend:** **367 passing tests** covering ingestion, derivation, agent loop, tools, interview state machine, API endpoints, locks, and evidence resolution.
- **Frontend:** **46 passing tests** across 11 test suites with Vitest, React Testing Library, and TypeScript compilation validation (`tsc && vite build`).

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- Node.js 18+ and npm
- MongoDB instance (running locally or via MongoDB Atlas)
- LLM API key (Groq, OpenAI, or any OpenAI-compatible provider)

### 2. Backend Setup
From the root directory:
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set credentials in `backend/.env`:
```env
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=repomentor
LLM_API_KEY=your-api-key
LLM_MODEL=openai/gpt-oss-120b
LLM_FALLBACK_MODEL=openai/gpt-oss-20b
```

Start the backend server:
```powershell
uvicorn app.main:app --reload --port 8000
```
- Interactive Swagger API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### 3. Frontend Setup
In a new terminal:
```powershell
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

### 4. Running Tests
Run backend tests:
```powershell
cd backend
python -m pytest
```

Run frontend tests and build:
```powershell
cd frontend
npm test
npm run build
```

---

## Roadmap: What Will Be Achieved Further

1. **Voice & Live Audio Interview Simulation**
   - Real-time WebRTC or WebSocket audio streaming for conversational mock interviews.
   - Low-latency speech-to-text with conversational pause detection and audio interruptions.

2. **Advanced Static Analysis & Code Graphs**
   - AST symbol indexing, call graph generation, and data flow tracing across multi-repo dependencies.
   - Automatic architectural pattern recognition (Event-driven, CQRS, Hexagonal, Clean Architecture).

3. **IDE & Agent Extensions (MCP Protocol)**
   - Model Context Protocol (MCP) server integration allowing RepoMentor intelligence to be queried directly inside Antigravity, VS Code, and Cursor.

4. **Multi-Repository Benchmarking & Team Readiness**
   - Cross-repository skill profiles comparing candidate mastery across multiple codebases.
   - Team onboarding tracks with progressive mastery roadmaps.
