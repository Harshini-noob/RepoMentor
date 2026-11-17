# RepoMentor

RepoMentor helps developers explore a GitHub repository and practice explaining their own project. The Python backend combines repository ingestion and analysis, an evidence-grounded investigation agent, and interview-practice workflows.

## What’s implemented

- Ingest GitHub repositories and store project data in MongoDB.
- Parse dependency manifests and derive repository facts and timeline information.
- Ask project-specific questions through an agent that uses registered repository tools and evidence citations.
- Generate repository-grounded interview questions, evaluate answers against a rubric, and track performance by concept.
- Run interview sessions, review reports and profiles, and generate teaching lessons for missed concepts.
- Expose project and analysis data through a FastAPI API and command-line interface.

## Quick start

See the [backend README](backend/README.md) for setup, environment variables, API endpoints, and CLI usage.

In short, configure MongoDB and an LLM API key in `backend/.env`, install the backend dependencies, then run from the `backend` directory:

```powershell
uvicorn app.main:app --reload --port 8000
```

The health endpoint is `http://localhost:8000/health`; interactive API documentation is at `http://localhost:8000/docs`.

To run the tests:

```powershell
cd backend
python -m pytest -q
```

## MCP status

The agent passes its in-process repository tools through an OpenAI-compatible chat API. MCP (Model Context Protocol) integration is not currently implemented.
