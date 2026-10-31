"""Read the saved, tagged project understanding for a repository."""

import re
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput
from app.core.db import get_db

SECTION_PRIORITY = {
    "decisions": 0,
    "data": 1,
    "technologies": 2,
    "apis": 3,
    "components": 4,
    "features": 5,
    "timeline": 6,
    "quality": 7,
    "identity": 8,
}

TAG_PRIORITY = {
    "fact": 0,
    "inferred": 1,
    "uncertain": 2,
}


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
    query: str | None = None


class DossierTool(Tool):
    name = "get_dossier"
    description = (
        "Saved project understanding with tagged, cited claims. Use first. "
        "query='supabase' searches all sections; section='decisions' lists one section."
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

        # 1. Search when query is given
        if args.query is not None:
            query_words = list(dict.fromkeys(w.lower() for w in re.findall(r"[a-zA-Z]{3,}", args.query)))
            sections_to_search = (
                [args.section] if args.section and args.section in sections
                else list(sections.keys())
            )
            scored_claims: list[tuple[str, dict, float]] = []

            for sec_name in sections_to_search:
                for claim in sections.get(sec_name, []):
                    claim_text = (claim.get("claim") or "").lower()
                    evidence_list = [
                        eid for eid in claim.get("evidence", [])
                        if isinstance(eid, str) and not any(c.isspace() for c in eid)
                    ]
                    evidence_blob = " ".join(evidence_list).lower()

                    score = 0.0
                    for word in query_words:
                        if word in claim_text:
                            score += 1.0
                        if word in evidence_blob:
                            score += 0.5

                    if score > 0:
                        scored_claims.append((sec_name, claim, score))

            if not scored_claims:
                return ToolOutput(
                    text=f"No dossier claims match '{args.query}'. Call get_dossier() to see the sections, or use search_code."
                )

            scored_claims.sort(
                key=lambda item: (
                    -item[2],
                    SECTION_PRIORITY.get(item[0], 99),
                    TAG_PRIORITY.get(str(item[1].get("tag", "")).lower(), 99),
                )
            )

            top_claims = scored_claims[:8]
            lines = [f"{len(scored_claims)} claims match '{args.query}' across all sections"]
            evidence_ids: list[str] = []
            tag_labels = {"fact": "Fact", "inferred": "Inferred", "uncertain": "Uncertain"}

            for sec_name, claim, _ in top_claims:
                tag = tag_labels.get(str(claim.get("tag", "")).lower(), "Uncertain")
                text = f"{sec_name} | [{tag}] {claim.get('claim', '')}"
                ids = [
                    eid for eid in claim.get("evidence", [])
                    if isinstance(eid, str) and not any(c.isspace() for c in eid)
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

            return ToolOutput(
                text="\n".join(lines),
                evidence_ids=list(dict.fromkeys(evidence_ids)),
            )

        # 2. Existing behaviour: no query, no section -> summary
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

        # 3. Existing behaviour: no query, section given -> section claims
        claims = sections.get(args.section, [])
        lines = []
        evidence_ids = []
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
        return ToolOutput(
            text="\n".join(lines) or "No claims saved for this section.",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
        )
