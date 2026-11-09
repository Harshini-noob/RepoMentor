"""
Tests for the Phase 6 interview question generator.

Uses mongomock for DB, fake chat_fn for LLM calls.
No real API keys or MongoDB required.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import mongomock
import pytest
import pytest_asyncio

from app.agent.state import LLMReply
from app.interview.concepts import CONCEPTS
from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.schema import InterviewQuestion, KeyPoint
from app.interview.seeds import pick_seed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_client():
    return mongomock.MongoClient()


def _make_db(client=None):
    c = client or _make_client()
    return c["repomentor"]


def _make_dossier(**overrides) -> dict:
    base = {
        "project_id": "proj_test",
        "version": 1,
        "generated_from_sha": "abc1234",
        "sections": {
            "decisions": [],
            "technologies": [],
            "data": [],
            "components": [],
            "apis": [],
            "features": [],
            "identity": [],
            "timeline": [],
            "quality": [],
        },
    }
    base.update(overrides)
    return base


def _fact(claim: str, evidence: list[str], commit: str | None = None) -> dict:
    details: dict = {}
    if commit:
        details["commit"] = commit
    return {"claim": claim, "tag": "fact", "evidence": evidence, "details": details}


def _inferred(claim: str, evidence: list[str]) -> dict:
    return {"claim": claim, "tag": "inferred", "evidence": evidence, "details": {}}


def _make_llm_reply(data: dict) -> LLMReply:
    return LLMReply(
        content=json.dumps(data),
        tool_calls=[],
        usage={"input_tokens": 10, "output_tokens": 20},
    )


def _valid_llm_data(concept: str = "database-choice") -> dict:
    return {
        "question": "Why did you choose MongoDB for this project?",
        "concept": concept,
        "key_points": [
            {"point": "Schema flexibility for evolving data models", "kind": "general"},
            {"point": "Horizontal scaling via sharding", "kind": "general"},
            {"point": "Chosen over PostgreSQL due to variable document structure", "kind": "repo"},
        ],
        "common_mistakes": [
            "Confusing schema flexibility with absence of data validation",
            "Ignoring transaction support differences",
        ],
    }


# ---------------------------------------------------------------------------
# Tests: pick_seed — history round
# ---------------------------------------------------------------------------

class TestPickSeedHistory:
    def test_prefers_commit_with_more_fact_claims(self):
        """Commit with more fact claims (especially with commit: evidence) should be preferred."""
        dossier = _make_dossier(sections={
            "decisions": [
                _fact("Chose MongoDB", ["commit:abc1234:app/db.py", "commit:abc1234:README.md"], "abc1234"),
                _fact("Rejected SQL for flexibility", ["commit:abc1234:docs/adr.md"], "abc1234"),
                _fact("Single decision for other commit", ["commit:def5678:app/x.py"], "def5678"),
            ],
            **{s: [] for s in ["technologies","data","components","apis","features","identity","timeline","quality"]},
        })
        # Run many times to verify abc1234 is chosen more often (it has higher score)
        commits_chosen = []
        for _ in range(30):
            seed = pick_seed(dossier, "history", asked=[])
            commits_chosen.append(seed["commit"])
        # abc1234 must appear — it has score 4 vs def5678's score 2
        assert "abc1234" in commits_chosen

    def test_skips_already_asked_commit(self):
        """Commits already asked should be skipped when alternatives exist."""
        dossier = _make_dossier(sections={
            "decisions": [
                _fact("Choice A", ["commit:abc1234:app/a.py"], "abc1234"),
                _fact("Choice B", ["commit:def5678:app/b.py"], "def5678"),
            ],
            **{s: [] for s in ["technologies","data","components","apis","features","identity","timeline","quality"]},
        })
        asked = [{"seed": {"commit": "abc1234"}}]
        seeds = {pick_seed(dossier, "history", asked=asked)["commit"] for _ in range(20)}
        # Only def5678 should be chosen
        assert seeds == {"def5678"}

    def test_honours_explicit_commit(self):
        dossier = _make_dossier(sections={
            "decisions": [
                _fact("Choice A", ["commit:abc1234:app/a.py"], "abc1234"),
                _fact("Choice B", ["commit:def5678:app/b.py"], "def5678"),
            ],
            **{s: [] for s in ["technologies","data","components","apis","features","identity","timeline","quality"]},
        })
        seed = pick_seed(dossier, "history", asked=[], commit="abc1234")
        assert seed["commit"] == "abc1234"
        assert all(c["claim"] in {"Choice A"} for c in seed["claims"])

    def test_explicit_commit_not_in_dossier_raises(self):
        dossier = _make_dossier(sections={
            "decisions": [
                _fact("Choice A", ["commit:abc1234:app/a.py"], "abc1234"),
            ],
            **{s: [] for s in ["technologies","data","components","apis","features","identity","timeline","quality"]},
        })
        with pytest.raises((ValueError, RuntimeError)):
            pick_seed(dossier, "history", asked=[], commit="ffffff0")

    def test_no_decisions_raises(self):
        dossier = _make_dossier()
        with pytest.raises(RuntimeError, match="run the dossier builder first"):
            pick_seed(dossier, "history", asked=[])

    def test_seed_claims_max_6(self):
        claims = [_fact(f"Claim {i}", [f"commit:abc1234:f{i}.py"], "abc1234") for i in range(10)]
        dossier = _make_dossier(sections={
            "decisions": claims,
            **{s: [] for s in ["technologies","data","components","apis","features","identity","timeline","quality"]},
        })
        seed = pick_seed(dossier, "history", asked=[])
        assert len(seed["claims"]) <= 6


# ---------------------------------------------------------------------------
# Tests: pick_seed — tech round
# ---------------------------------------------------------------------------

class TestPickSeedTech:
    def _dossier_with_tech(self, include_plain=True):
        claims = [
            _fact("Uses MongoDB as the primary database", ["fact:database:mongodb", "file:app/db.py#L1-5"]),
            _fact("Uses Groq LLM API", ["fact:llm_provider:groq", "file:app/llm.py#L1-10"]),
        ]
        if include_plain:
            # This is a plain dependency claim — no special prefix
            claims.append(_fact("FastAPI is a runtime dependency", ["file:requirements.txt#L3"]))
        return _make_dossier(sections={
            "technologies": claims,
            **{s: [] for s in ["decisions","data","components","apis","features","identity","timeline","quality"]},
        })

    def test_skips_plain_dependency_claims(self):
        dossier = self._dossier_with_tech(include_plain=True)
        for _ in range(20):
            seed = pick_seed(dossier, "tech", asked=[])
            # None of the claims should be the plain dependency one
            assert all(
                "FastAPI is a runtime dependency" != c["claim"]
                for c in seed["claims"]
            )

    def test_skips_already_asked_claim(self):
        dossier = self._dossier_with_tech(include_plain=False)
        asked = [{"seed": {"claims": [{"claim": "Uses MongoDB as the primary database"}]}}]
        for _ in range(20):
            seed = pick_seed(dossier, "tech", asked=asked)
            assert all("MongoDB" not in c["claim"] for c in seed["claims"][:1])

    def test_no_tech_candidates_raises(self):
        dossier = _make_dossier(sections={
            "technologies": [
                _fact("FastAPI is a runtime dependency", ["file:requirements.txt#L3"])
            ],
            **{s: [] for s in ["decisions","data","components","apis","features","identity","timeline","quality"]},
        })
        with pytest.raises(RuntimeError, match="run the dossier builder first"):
            pick_seed(dossier, "tech", asked=[])


# ---------------------------------------------------------------------------
# Tests: generate_question
# ---------------------------------------------------------------------------

class TestGenerateQuestion:
    def _db_with_dossier(self, dossier_extra: dict | None = None):
        db = _make_db()
        dossier = _make_dossier(sections={
            "technologies": [
                _fact("Uses MongoDB as main DB", ["fact:database:mongodb", "file:app/db.py#L1-5"]),
            ],
            **{s: [] for s in ["decisions","data","components","apis","features","identity","timeline","quality"]},
        })
        if dossier_extra:
            dossier.update(dossier_extra)
        db.dossiers.insert_one(dossier)
        return db

    @pytest.mark.asyncio
    async def test_parses_fenced_json(self):
        """Generator should strip code fences and parse the JSON correctly."""
        data = _valid_llm_data()
        fenced = f"```json\n{json.dumps(data)}\n```"
        fake_chat = AsyncMock(return_value=LLMReply(content=fenced, tool_calls=[], usage={}))
        db = self._db_with_dossier()

        result = await generate_question(
            db, "proj_test", "tech", 2, chat_fn=fake_chat, save=False
        )

        assert isinstance(result, InterviewQuestion)
        assert result.question == data["question"]
        assert result.concept == data["concept"]
        assert len(result.key_points) == 3
        assert len(result.common_mistakes) == 2

    @pytest.mark.asyncio
    async def test_unknown_concept_becomes_general(self):
        """An unrecognised concept string should be coerced to 'general'."""
        data = _valid_llm_data(concept="made-up-concept-xyz")
        fake_chat = AsyncMock(return_value=_make_llm_reply(data))
        db = self._db_with_dossier()

        result = await generate_question(
            db, "proj_test", "tech", 1, chat_fn=fake_chat, save=False
        )
        assert result.concept == "general"

    @pytest.mark.asyncio
    async def test_valid_result_saved_to_db(self):
        """When save=True, the generated question should be persisted."""
        data = _valid_llm_data()
        fake_chat = AsyncMock(return_value=_make_llm_reply(data))
        db = self._db_with_dossier()

        result = await generate_question(
            db, "proj_test", "tech", 2, chat_fn=fake_chat, save=True
        )

        saved = db.interview_questions.find_one({"question_id": result.question_id})
        assert saved is not None
        assert saved["project_id"] == "proj_test"
        assert "seed" in saved
        assert "claims" in saved["seed"]

    @pytest.mark.asyncio
    async def test_dry_run_does_not_save(self):
        """When save=False (dry-run), nothing should be persisted."""
        data = _valid_llm_data()
        fake_chat = AsyncMock(return_value=_make_llm_reply(data))
        db = self._db_with_dossier()

        await generate_question(db, "proj_test", "tech", 2, chat_fn=fake_chat, save=False)

        assert db.interview_questions.count_documents({}) == 0

    @pytest.mark.asyncio
    async def test_retry_on_non_json_first_reply(self):
        """If the first reply is not JSON, the generator should retry once and succeed."""
        data = _valid_llm_data()
        bad_reply = LLMReply(content="Sorry, I cannot help.", tool_calls=[], usage={})
        good_reply = _make_llm_reply(data)
        # First call: bad; second call: good (the retry)
        fake_chat = AsyncMock(side_effect=[bad_reply, good_reply])
        db = self._db_with_dossier()

        result = await generate_question(
            db, "proj_test", "tech", 2, chat_fn=fake_chat, save=False
        )
        assert result.question == data["question"]
        # Should have called the LLM exactly twice
        assert fake_chat.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_filename_triggers_retry_then_error(self):
        """
        A filename token not in seed facts triggers one retry with guidance.
        If it still appears after retry, InterviewGenerationError is raised with nothing saved.
        """
        # The seed only contains "app/db.py" — mention "mystery_module.py" which is not in seed
        data = _valid_llm_data()
        data["question"] = "Why does `mystery_module.py` exist in this project?"

        fake_chat = AsyncMock(return_value=_make_llm_reply(data))
        db = self._db_with_dossier()

        with pytest.raises(InterviewGenerationError, match="mystery_module.py"):
            await generate_question(
                db, "proj_test", "tech", 2, chat_fn=fake_chat, save=True
            )
        # Nothing should be saved
        assert db.interview_questions.count_documents({}) == 0

    @pytest.mark.asyncio
    async def test_missing_dossier_raises(self):
        db = _make_db()  # empty — no dossier
        fake_chat = AsyncMock(return_value=_make_llm_reply(_valid_llm_data()))

        with pytest.raises(InterviewGenerationError, match="dossier"):
            await generate_question(
                db, "proj_no_dossier", "tech", 2, chat_fn=fake_chat, save=False
            )


# ---------------------------------------------------------------------------
# Guard: no file in app/interview reads evals/questions.json
# ---------------------------------------------------------------------------

class TestNoEvalsQuestionsJsonRead:
    def test_no_interview_file_reads_evals_questions_json(self):
        """
        Statically verify no source file under app/interview imports or opens evals/questions.json.
        """
        interview_dir = Path(__file__).resolve().parents[1] / "app" / "interview"
        assert interview_dir.is_dir(), f"Expected directory: {interview_dir}"

        for py_file in interview_dir.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            assert "evals/questions.json" not in source, (
                f"{py_file} contains a reference to evals/questions.json — "
                "interview modules must never read the eval fixture file."
            )
            assert "questions.json" not in source or "evals" not in source, (
                f"{py_file} may reference evals/questions.json — "
                "verify this is not a path to the eval fixture."
            )
