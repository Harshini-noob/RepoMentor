# RepoMentor

**RepoMentor** is an AI-powered technical interview coach and repository intelligence platform. It analyzes your GitHub codebase, extracts verifiable architectural facts, and provides grounded conversational mentoring and realistic interview practice so you can master and defend your own software projects.

---

## Architecture Overview

RepoMentor consists of an evidence-grounded agent, an adaptive interview engine, and a FastAPI service backed by MongoDB:

```
                  +--------------------------------------------------+
                  |                 GitHub Repository                |
                  +--------------------------------------------------+
                                           |
                                [ Ingestion & Parsers ]
                                           v
                  +--------------------------------------------------+
                  |          MongoDB (Dossiers, Facts, Sessions)     |
                  +--------------------------------------------------+
                                           |
                   +-----------------------+-----------------------+
                   |                                               |
                   v                                               v
     +---------------------------+                   +---------------------------+
     |   RepoMentor Chat Agent   |                   |  Interview Engine Machine |
     |  (Grounded ReAct Loop,    |                   |   (Adaptive Plan, Rubric, |
     |   Citations & Tool Calls) |                   |   Teaching Cycle & Eval)  |
     +---------------------------+                   +---------------------------+
                   |                                               |
                   +-----------------------+-----------------------+
                                           |
                                           v
                  +--------------------------------------------------+
                  |         FastAPI REST API & SSE Streaming         |
                  |     (Model Concurrency Lock, Anti-Leak Engine)   |
                  +--------------------------------------------------+
                                           |
                        +------------------+------------------+
                        |                                     |
                        v                                     v
                  CLI Interface                        HTTP API Clients
             (`python -m app.cli`)             (`scripts/api_interview_demo.py`)
```

---

## What Has Been Achieved So Far

### 1. Repository Ingestion & Codebase Intelligence
- **Multi-Source Ingestion:** Fetches code trees, commits, pull requests, issues, and metadata from GitHub.
- **Dependency Manifest Parsing:** Extracts dependencies across Python (`pyproject.toml`, `requirements.txt`, `Pipfile`), Node (`package.json`), Rust (`Cargo.toml`), and Go (`go.mod`).
- **Static Fact Derivation:** Discovers endpoints, database clients, authentication handlers, external services, and component architecture.
- **Commit Timeline Analytics:** Analyzes commit velocity, codebase evolution buckets, author activity, and first-seen component dates.
- **Structured Project Dossier:** Distills repository understanding into 9 curated sections (`technologies`, `apis`, `components`, `data`, `decisions`, `features`, `timeline`, `quality`, `identity`) with confidence tags and user editability.

### 2. Evidence-Grounded Mentor Agent
- **ReAct Investigation Agent:** An agent that inspects real code, commit logs, and dependency trees using tools (`get_dossier`, `read_file`, `search_code`, `get_commit`, `get_timeline`, `get_dependencies`).
- **Strict Evidence Citation:** Every claim is tagged (`[Fact]`, `[Inferred]`, `[Uncertain]`, `[General]`) and mapped to verified evidence IDs (`file:...`, `commit:...`, `pr:...`). Unverified claims and hallucinated quotes are blocked or repaired.
- **Model Fallback & Resilience:** Automatic fallback and exponential backoff on 429 rate limits across LLM providers.
- **Streaming Chat:** Real-time Server-Sent Events (SSE) streaming with live trace steps (`step`, `event`, `answer`, `done`).

### 3. Adaptive Technical Interview & Teaching Engine
- **Targeted Question Generation:** Dynamically generates interview questions across categories (`tech`, `arch`, `behavioral`, `problem`, `tradeoff`) tailored to difficulty levels (1-5) and specific repository claims.
- **Rubric-Driven Evaluation:** Evaluates candidate answers against multi-point rubrics (`points`, `misconceptions`, `unverified claims`, `strengths`, and `advice`) with objective scoring (0-4).
- **Automated Teaching Cycle:**
  - Identifies weak answers (score $\le 2$).
  - Delivers a grounded micro-lesson on the exact missed concepts.
  - Automatically generates a focused follow-up probe question.
  - Prompts a retry on the original question with before/after progress scoring and copy-paste detection.
- **Learner Profile Tracking:** Maintains continuous mastery profiles per concept, recording recurrent gaps and suggesting prioritized revision topics.

### 4. Robust State Machine & HTTP API (Phase 8)
- **Pure State Machine Engine (`app/interview/engine.py`):** Non-printing, stage-based lifecycle (`need_question`, `awaiting_answer`, `need_lesson`, `need_followup`, `need_retry`, `finished`) with `start`, `next_action`, `submit_answer`, `skip`, and `get_state`.
- **Security & Integrity Rules:** Strips rubrics (`key_points`, `common_mistakes`, `seed`) from API responses to prevent candidate cheating.
- **Model Concurrency Locking (`app/api/locks.py`):** Prevents race conditions and model overload between chat and interview endpoints with structured 409 responses.
- **Test-Driven Reliability:** Comprehensive test suite of **349 passing tests** covering state transitions, error handling, lock contention, and grounded evaluations.

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- MongoDB instance (running locally or via MongoDB Atlas)
- LLM API key (Groq, OpenAI, or any OpenAI-compatible provider)

### 2. Installation & Configuration
From the `backend` directory:
```powershell
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

### 3. Start Server
```powershell
uvicorn app.main:app --reload --port 8000
```
- Interactive Swagger API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### 4. Interactive Interview Demo
Test the complete interview flow via HTTP API:
```powershell
python scripts/api_interview_demo.py <project_id>
```

### 5. Run Tests
```powershell
cd backend
python -m pytest
```

---

## What Will Be Achieved Further (Roadmap)

1. **Web User Interface (Frontend)**
   - **Interactive Interview Stage:** Web-based interview studio with timer, Markdown answer editor, instant rubric feedback, interactive lessons, and score improvement visualization.
   - **Visual Repo Dossier:** Exploratory graph of architectural components, endpoints, and key engineering trade-offs.
   - **Learner Profile Dashboard:** Visual concept radar chart highlighting strengths, weak points, and revision priorities.
   - **Mentor Chatroom:** Real-time conversational interface with streaming responses and expandable evidence inspection.

2. **Voice & Live Audio Interview Simulation**
   - WebRTC / WebSocket audio streaming for hands-free verbal technical mock interviews.
   - Speech-to-text with conversational pause detection and natural speaking cadence.

3. **Advanced Static Analysis & Code Graphs**
   - AST-level symbol resolution, call graph construction, and data flow mapping.
   - Automatic detection of complex design patterns (event sourcing, CQRS, dependency injection, middleware pipelines).

4. **IDE & Protocol Integrations (MCP)**
   - Implementation of Model Context Protocol (MCP) server so RepoMentor can directly provide repository insights to Cursor, Antigravity, and VS Code.

5. **Multi-Project Comparison & Benchmarking**
   - Comparative analysis across multiple repositories to measure architectural evolution and candidate readiness across different tech stacks.

