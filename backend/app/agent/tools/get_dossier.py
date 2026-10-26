"""Read the saved, tagged project understanding for a repository."""

from typing import Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput
from app.core.db import get_db


class DossierArgs(BaseModel):
    section: Literal[
        "identity",
        "technologies",
        "components",
        "apis",
        "data",
        "features",
        "decisions",
        "timeline",
        "quality",
    ] | None = None


class DossierTool(Tool):
    name = "get_dossier"
    description = (
        "Saved project understanding with tagged, cited claims. Use first for questions "
        "about components, technologies, APIs, data, history, decisions."
    )
    args_schema = DossierArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: DossierArgs) -> ToolOutput:
        dossier = await self._get_db().dossiers.find_one(
            {"project_id": project_id}, {"_id": 0}
        )
        if not dossier:
            return ToolOutput(text="No dossier saved for this project. Use the other tools.")

        sections = dossier.get("sections") or {}
        if args.section is None:
            lines = [
                f"Dossier version: {dossier.get('version', 'unknown')}",
                f"Built from commit: {dossier.get('generated_from_sha') or 'unknown'}",
                "Sections:",
            ]
            lines.extend(
                f"- {name}: {len(claims)} claims"
                for name, claims in sections.items()
            )
            lines.append("Call get_dossier(section=<name>) for the claims.")
            return ToolOutput(text="\n".join(lines))

        claims = sections.get(args.section, [])
        lines = []
        evidence_ids: list[str] = []
        tag_labels = {"fact": "Fact", "inferred": "Inferred", "uncertain": "Uncertain"}
        for claim in claims[:15]:
            tag = tag_labels.get(claim.get("tag"), "Uncertain")
            text = f"[{tag}] {claim.get('claim', '')}"
            ids = [
                evidence_id
                for evidence_id in claim.get("evidence", [])
                if isinstance(evidence_id, str) and not any(c.isspace() for c in evidence_id)
            ][:3]
            if ids:
                text += f" [{', '.join(ids)}]"
                evidence_ids.extend(ids)
            commit = (claim.get("details") or {}).get("commit")
            if commit:
                text += f" (commit {str(commit)[:7]})"
            if claim.get("source") == "user":
                text += " (verified by the project owner)"
            lines.append(text)
        return ToolOutput(text="\n".join(lines) or "No claims saved for this section.", evidence_ids=list(dict.fromkeys(evidence_ids)))
