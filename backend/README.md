# RepoMentor Backend

RepoMentor analyzes GitHub repositories and helps developers understand and discuss their own projects. The backend provides a FastAPI service, a repository investigation agent, and a CLI for interactive questions and interview practice.

## Features

- **Repository ingestion:** accepts a GitHub repository, gathers source and history data, and stores the project analysis in MongoDB.
- **Analysis API:** exposes project status, dependencies, statically derived code facts, and repository timeline data.
- **Repository investigation agent:** answers project questions by calling registered repository tools and grounding claims in evidence.
- **Interview practice:** generates repository-grounded questions, evaluates answers against a rubric, tracks concept performance, and summarizes practice sessions.
- **Teaching lessons:** explains missed or partly answered concepts using verified seed facts and cited evidence IDs.
- **CLI:** supports one-shot questions, chat sessions, interview workflows, profiles, and lessons.

## Requirements

- Python 3.11 or newer
- MongoDB
- A GitHub token for authenticated repository access (recommended)
- An API key for the configured OpenAI-compatible LLM provider for model-backed features

## Setup

Run commands from the `backend` directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set the required credentials in `.env`. At minimum, configure `LLM_API_KEY` for LLM-backed features. `LLM_BASE_URL` can be set for a compatible provider endpoint. Do not commit `.env` or credentials.

| Variable | Purpose |
| --- | --- |
| `MONGODB_URI` | MongoDB connection URI; defaults to `mongodb://localhost:27017` |
| `MONGODB_DB` | Database name; defaults to `repomentor` |
| `GITHUB_TOKEN` | Optional GitHub API token |
| `LLM_API_KEY` | API key used by the OpenAI-compatible LLM wrapper |
| `LLM_BASE_URL` | Optional compatible API base URL |
| `LLM_MODEL` | Primary model name; defaults to `gemini-3.8-flash` |
| `LLM_FALLBACK_MODEL` | Optional fallback model |
| `LLM_REASONING_EFFORT` | Reasoning effort setting used by supported models |

## Run the API

Start the development server from `backend`:

```powershell
uvicorn app.main:app --reload --port 8000
```

- Health check: `http://localhost:8000/health`
- Interactive API docs: `http://localhost:8000/docs`

### API endpoints

#### Projects & Repository Analysis
| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/projects` | Register a GitHub repository and start background ingestion |
| `GET` | `/projects` | List projects |
| `GET` | `/projects/{id}` | Get project status and ingestion counts |
| `GET` | `/projects/{id}/dependencies` | List parsed dependencies |
| `GET` | `/projects/{id}/facts` | List statically derived code facts; supports `kind`, `limit`, and `skip` |
| `GET` | `/projects/{id}/timeline` | Get commit timeline and related history metrics |

#### Project Dossier
| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/projects/{id}/dossier/summary` | Summary of all 9 dossier sections and claim counts |
| `GET` | `/projects/{id}/dossier/sections/{name}` | Detailed claims and evidence for a specific section |
| `PATCH` | `/projects/{id}/dossier/sections/{name}/claims/{index}` | Edit or revise a specific dossier claim |

#### Mentor Chat (SSE Streaming)
| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/projects/{id}/chat/sessions` | Create a new mentor chat session |
| `GET` | `/projects/{id}/chat/sessions` | List newest chat sessions |
| `GET` | `/projects/{id}/chat/sessions/{session_id}` | Retrieve chat session history |
| `POST` | `/projects/{id}/chat` | SSE stream for mentor answers with live tool execution traces |

#### Learner Profile & Interview State Machine
| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/projects/{id}/profile` | Retrieve learner profile and revision priorities |
| `GET` | `/projects/{id}/interview/plan` | Preview adaptive interview plan without saving |
| `GET` | `/projects/{id}/interview/sessions` | List past interview sessions |
| `POST` | `/projects/{id}/interview/sessions` | Initialize a new interview state machine session |
| `GET` | `/projects/{id}/interview/sessions/{session_id}/state` | Get current state (sanitized without rubrics) |
| `POST` | `/projects/{id}/interview/sessions/{session_id}/next` | Advance state machine (question, lesson, or summary) |
| `POST` | `/projects/{id}/interview/sessions/{session_id}/answer` | Submit answer and trigger evaluation/teaching cycle |
| `POST` | `/projects/{id}/interview/sessions/{session_id}/skip` | Skip the active question |
| `GET` | `/projects/{id}/interview/sessions/{session_id}/report` | Final interview report and score improvement breakdown |


## CLI

Run commands from `backend` using `python -m app.cli`. For example:

```powershell
python -m app.cli ask <project_id> "What are the main components?"
python -m app.cli chat <project_id>
python -m app.cli interview-question <project_id> --round tech --difficulty 2
python -m app.cli interview-answer <project_id> --last
python -m app.cli interview <project_id> --questions 5 --difficulty 2
python -m app.cli interview-report <project_id>
python -m app.cli profile <project_id>
python -m app.cli teach <project_id> --last
```

Use `python -m app.cli` without arguments to see the full usage text. Commands that support `--dry-run` skip saving their generated result.

## Architecture

- `app/api/` defines the FastAPI project and analysis routes.
- `app/ingestion/`, `app/github/`, and `app/dossier/` collect repository data, parse project files, derive facts, and assemble evidence for later use.
- `app/agent/` implements the investigation loop, evidence checks, and in-process repository tool registry.
- `app/mentor/` provides conversational project help and chat session storage.
- `app/interview/` contains question generation, answer evaluation and scoring, interview sessions and summaries, learner profiles, and grounded teaching lessons.
- `app/core/` contains MongoDB access, indexes, logging, and shared utilities.

The LLM integration uses an OpenAI-compatible chat API and passes the application's registered tools to the model. **MCP (Model Context Protocol) is not currently implemented**; these tools are not MCP servers or MCP clients.

## Tests

Run the backend test suite from `backend`:

```powershell
python -m pytest -q
```
