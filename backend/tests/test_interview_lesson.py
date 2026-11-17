"""Tests for Phase 7, step 2a: grounded interview lessons."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
import pytest

from app.interview.lesson import LessonGenerationError, generate_lesson


SEED_ID = "file:src/main.py#L1-L2"
GOOD_LESSON = (
    "Good: You identified the main choice.\n"
    "Idea:\n"
    "[General] A design choice should be explained through the constraints it addresses.\n"
    "[General] Compare the benefit with the trade-off.\n"
    "In your project:\n"
    f"[Fact] The project has the verified setup described in the seed. [{SEED_ID}]\n"
    "Say it like this: I chose this approach because it fits the project's needs."
)


def _make_db():
    return mongomock.MongoClient()["repomentor"]


def _insert_attempt(db, attempt_id, question_id, created_at, *, points=None):
    db.interview_questions.insert_one(
        {
            "question_id": question_id,
            "project_id": "proj",
            "question": "Why was this approach chosen?",
            "difficulty": 3,
            "concept": "design-tradeoffs",
            "seed": {
                "claims": [
                    {
                        "tag": "fact",
                        "claim": "The project has a verified setup.",
                        "evidence": [SEED_ID],
                    }
                ]
            },
        }
    )
    db.interview_attempts.insert_one(
        {
            "attempt_id": attempt_id,
            "question_id": question_id,
            "project_id": "proj",
            "concept": "design-tradeoffs",
            "difficulty": 3,
            "answer": "We selected this approach to meet the project requirements.",
            "evaluation": {
                "score": 2,
                "points": points
                or [
                    {
                        "point": "Explain the trade-off",
                        "kind": "general",
                        "status": "missing",
                    }
                ],
            },
            "created_at": created_at,
        }
    )


def _reply(content, *, tokens=12, model=None):
    return SimpleNamespace(
        content=content,
        usage={"total_tokens": tokens, "input_tokens": tokens - 4, "output_tokens": 4},
        model=model,
    )


@pytest.mark.asyncio
async def test_defaults_to_newest_attempt():
    db = _make_db()
    _insert_attempt(db, "old", "q-old", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert_attempt(db, "new", "q-new", datetime(2026, 1, 2, tzinfo=timezone.utc))
    chat = AsyncMock(return_value=_reply(GOOD_LESSON))

    lesson = await generate_lesson(db, project_id="proj", chat_fn=chat, save=False)

    assert lesson["attempt_id"] == "new"
    assert "Question: Why was this approach chosen?" in chat.call_args.args[0][1]["content"]


@pytest.mark.asyncio
async def test_import_only_points_are_dropped_and_empty_case_is_explained():
    db = _make_db()
    points = [
        {
            "point": "Explain imports from app/agent/llm.py",
            "kind": "repo",
            "status": "missing",
        },
        {
            "point": "Explain where the value is used in app/cli.py",
            "kind": "general",
            "status": "partial",
        },
    ]
    _insert_attempt(db, "a1", "q1", datetime.now(timezone.utc), points=points)
    chat = AsyncMock(return_value=_reply(GOOD_LESSON))

    await generate_lesson(db, attempt_id="a1", chat_fn=chat, save=False)

    prompt = chat.call_args.args[0][1]["content"]
    assert "No specific rubric point; teach the idea behind the question." in prompt
    assert "app/agent/llm.py" not in prompt
    assert "app/cli.py" not in prompt


@pytest.mark.asyncio
async def test_missing_label_triggers_one_retry():
    db = _make_db()
    _insert_attempt(db, "a1", "q1", datetime.now(timezone.utc))
    missing_label = GOOD_LESSON.replace("In your project:", "In project:")
    chat = AsyncMock(
        side_effect=[_reply(missing_label), _reply(GOOD_LESSON)]
    )

    lesson = await generate_lesson(db, attempt_id="a1", chat_fn=chat, save=False)

    assert chat.await_count == 2
    assert chat.call_args.args[0][-1]["content"] == "Use exactly the four labelled parts."
    assert "In your project:" in lesson["text"]


@pytest.mark.asyncio
async def test_unsupported_citation_retried_then_removed_and_fact_downgraded():
    db = _make_db()
    _insert_attempt(db, "a1", "q1", datetime.now(timezone.utc))
    unsupported_lesson = GOOD_LESSON.replace(
        f"[{SEED_ID}]", "[file:outside.py#L9]"
    )
    chat = AsyncMock(
        side_effect=[_reply(unsupported_lesson), _reply(unsupported_lesson)]
    )

    lesson = await generate_lesson(db, attempt_id="a1", chat_fn=chat, save=False)

    assert chat.await_count == 2
    assert "These ids are not in the seed: file:outside.py#L9" in chat.call_args.args[0][-1]["content"]
    assert "file:outside.py#L9" not in lesson["text"]
    assert "[Uncertain] The project has the verified setup described in the seed." in lesson["text"]
    assert len(lesson["notes"]) == 2


@pytest.mark.asyncio
async def test_valid_lesson_is_saved_with_attempt_id_and_model_usage():
    db = _make_db()
    _insert_attempt(db, "a1", "q1", datetime.now(timezone.utc))
    chat = AsyncMock(return_value=_reply(GOOD_LESSON, tokens=17, model="test-model"))

    lesson = await generate_lesson(db, attempt_id="a1", chat_fn=chat)

    saved = db.interview_lessons.find_one({"lesson_id": lesson["lesson_id"]})
    assert saved["attempt_id"] == "a1"
    assert saved["tokens"] == 17
    assert saved["model"] == "test-model"


@pytest.mark.asyncio
async def test_no_attempt_is_a_clear_error_without_model_call():
    db = _make_db()
    chat = AsyncMock()

    with pytest.raises(LessonGenerationError, match="No saved interview attempts"):
        await generate_lesson(db, project_id="proj", chat_fn=chat)

    chat.assert_not_awaited()


def test_cli_dry_run_does_not_save_lesson(monkeypatch, capsys):
    import app.cli as cli
    from app.interview import lesson as lesson_module

    db = _make_db()
    _insert_attempt(db, "a1", "q1", datetime.now(timezone.utc))
    fake_chat = AsyncMock(return_value=_reply(GOOD_LESSON))
    monkeypatch.setattr(lesson_module, "chat", fake_chat)
    monkeypatch.setattr(cli, "get_db", lambda: db)
    monkeypatch.setattr(
        "sys.argv", ["app.cli", "teach", "proj", "--last", "--dry-run"]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert db.interview_lessons.count_documents({}) == 0
    output = capsys.readouterr().out
    assert "Concept: design-tradeoffs" in output
    assert "Difficulty: 3" in output
    assert "Score: 2/4" in output
    assert "Tokens used: 12" in output
