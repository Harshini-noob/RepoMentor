import pytest
from mongomock_motor import AsyncMongoMockClient

from app.dossier.agent_sections import (
    SECTION_GOALS,
    build_decisions,
    build_simple_section,
)
from app.dossier.edit import apply_corrections
from app.dossier.schema import Claim, Dossier


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_dossier_edit"]


async def _save_initial_dossier(db, claims):
    dossier = Dossier(
        project_id="project-1",
        sections={"components": claims},
    )
    await db.dossiers.insert_one(dossier.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_replace_unique_match_edits_and_marks_user(mock_db):
    await _save_initial_dossier(
        mock_db,
        [Claim(claim="Router handles requests", tag="fact", source="agent")],
    )

    result = await apply_corrections(
        "project-1",
        mock_db,
        {"replace": [{"section": "components", "match": "ROUTER", "text": "Router serves API"}]},
    )

    claim = result.sections["components"][0]
    assert claim.claim == "Router serves API"
    assert claim.details["edited_from"] == "Router handles requests"
    assert claim.source == "user"
    assert claim.tag == "fact"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        [],
        [
            Claim(claim="Router handles requests", tag="fact"),
            Claim(claim="The router responds to requests", tag="fact"),
        ],
    ],
)
async def test_replace_non_unique_match_is_skipped(mock_db, claims, capsys):
    await _save_initial_dossier(mock_db, claims)

    result = await apply_corrections(
        "project-1",
        mock_db,
        {"replace": [{"section": "components", "match": "router", "text": "Edited"}]},
    )

    assert [claim.claim for claim in result.sections["components"]] == [
        claim.claim for claim in claims
    ]
    assert "expected exactly one match" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_add_is_idempotent(mock_db):
    corrections = {
        "add": [
            {
                "section": "features",
                "tag": "inferred",
                "text": "Users can search",
                "evidence": [],
                "commit": "abc1234",
            }
        ]
    }
    first = await apply_corrections("project-1", mock_db, corrections)
    second = await apply_corrections("project-1", mock_db, corrections)

    assert len(second.sections["features"]) == 1
    claim = first.sections["features"][0]
    assert claim.source == "user"
    assert claim.confidence == 0.7
    assert claim.details == {"commit": "abc1234"}


@pytest.mark.asyncio
async def test_invalid_commit_evidence_rejects_add(mock_db, capsys):
    result = await apply_corrections(
        "project-1",
        mock_db,
        {
            "add": [
                {
                    "section": "decisions",
                    "tag": "fact",
                    "text": "A change occurred",
                    "evidence": ["commit:deadbee"],
                }
            ]
        },
    )

    assert result.sections["decisions"] == []
    assert "invalid evidence ID 'commit:deadbee'" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_dry_run_does_not_save(mock_db):
    result = await apply_corrections(
        "project-1",
        mock_db,
        {
            "add": [
                {"section": "features", "tag": "fact", "text": "A feature", "evidence": []}
            ]
        },
        dry_run=True,
    )

    assert result.sections["features"][0].claim == "A feature"
    assert await mock_db.dossiers.count_documents({"project_id": "project-1"}) == 0


@pytest.mark.asyncio
async def test_agent_reparsing_preserves_user_claims(mock_db):
    project_id = "project-1"
    sha = "abc1234" + "0" * 33
    dossier = Dossier(
        project_id=project_id,
        sections={
            "components": [
                Claim(claim="Manual component", tag="fact", source="user"),
                Claim(claim="Old generated component", tag="fact", source="agent"),
            ],
            "decisions": [
                Claim(
                    claim="Manual decision",
                    tag="fact",
                    source="user",
                    details={"commit": "abc1234"},
                ),
                Claim(
                    claim="Old generated decision",
                    tag="fact",
                    source="agent",
                    details={"commit": "abc1234"},
                ),
            ],
        },
    )
    await mock_db.dossiers.insert_one(dossier.model_dump(mode="json"))
    await mock_db.commits.insert_one(
        {"project_id": project_id, "sha": sha, "date": "2026-01-01", "message": "change"}
    )
    await mock_db.agent_traces.insert_many(
        [
            {
                "project_id": project_id,
                "stop_reason": "answered",
                "goal": SECTION_GOALS["components"][:60] + " rest",
                "final_answer": "[Fact] Fresh component [commit:abc1234]",
                "steps": [{"evidence_ids": ["commit:abc1234"]}],
            },
            {
                "project_id": project_id,
                "stop_reason": "answered",
                "goal": "Commit abc1234 (2026-01-01, +1/-0) details",
                "final_answer": "[Fact] Fresh decision [commit:abc1234]",
                "steps": [{"evidence_ids": ["commit:abc1234"]}],
            },
        ]
    )

    result = await build_simple_section(dossier, mock_db, project_id, "components", True)
    result = await build_decisions(result, mock_db, project_id, "abc1234", 45000, False, True)

    assert any(
        claim.claim == "Manual component" and claim.source == "user"
        for claim in result.sections["components"]
    )
    assert any(
        claim.claim == "Manual decision" and claim.source == "user"
        for claim in result.sections["decisions"]
    )
