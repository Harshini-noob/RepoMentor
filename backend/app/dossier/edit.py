"""Apply manual corrections to a saved dossier.

Run from the backend folder:
  python -m app.dossier.edit <project_id> --file <corrections.json> [--dry-run]
"""

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.agent.evidence import CITE_RE
from app.core.db import get_db
from app.dossier.agent_sections import load_or_build
from app.dossier.builder import save_dossier
from app.dossier.schema import Claim, Dossier, SECTIONS

FILE_EVIDENCE_RE = re.compile(r"^file:(?P<path>.+)#L\d+(?:-L?\d+)?$")
COMMIT_EVIDENCE_RE = re.compile(
    r"^commit:(?P<sha>[0-9a-fA-F]{7,40})(?::(?P<path>.+))?$"
)
DEPENDENCY_EVIDENCE_RE = re.compile(r"^dep:(?P<name>.+)$")
FACT_EVIDENCE_RE = re.compile(r"^fact:(?P<kind>[^:]+):(?P<name>.+)$")
TAG_CONFIDENCE = {"fact": 1.0, "inferred": 0.7, "uncertain": 0.3}


async def _valid_evidence_id(db, project_id: str, evidence_id: str) -> bool:
    if not isinstance(evidence_id, str) or not CITE_RE.fullmatch(evidence_id):
        return False

    file_match = FILE_EVIDENCE_RE.fullmatch(evidence_id)
    if file_match:
        path = file_match["path"]
        return await db.files.find_one(
            {
                "project_id": project_id,
                "$or": [{"path": path}, {"file_path": path}],
            },
            {"_id": 1},
        ) is not None

    commit_match = COMMIT_EVIDENCE_RE.fullmatch(evidence_id)
    if commit_match:
        prefix = commit_match["sha"]
        commit = await db.commits.find_one(
            {
                "project_id": project_id,
                "sha": {"$regex": "^" + re.escape(prefix), "$options": "i"},
            }
        )
        if not commit:
            return False
        path = commit_match["path"]
        files_changed = commit.get("files_changed")
        if path and isinstance(files_changed, list):
            changed_paths = {
                entry if isinstance(entry, str) else entry.get("filename")
                for entry in files_changed
                if isinstance(entry, (str, dict))
            }
            return path in changed_paths
        return True

    dependency_match = DEPENDENCY_EVIDENCE_RE.fullmatch(evidence_id)
    if dependency_match:
        return await db.dependencies.find_one(
            {"project_id": project_id, "name": dependency_match["name"]},
            {"_id": 1},
        ) is not None

    fact_match = FACT_EVIDENCE_RE.fullmatch(evidence_id)
    if fact_match:
        return await db.derived_facts.find_one(
            {
                "project_id": project_id,
                "kind": fact_match["kind"],
                "name": fact_match["name"],
            },
            {"_id": 1},
        ) is not None

    return False


async def _validate_item_evidence(
    db,
    project_id: str,
    item: dict[str, Any],
    operation: str,
    index: int,
    require_evidence: bool = False,
) -> bool:
    if require_evidence and "evidence" not in item:
        print(f"error: {operation} item {index}: evidence is required")
        return False
    evidence = item.get("evidence", [])
    if not isinstance(evidence, list):
        print(f"error: {operation} item {index}: evidence must be a list")
        return False
    valid = True
    for evidence_id in evidence:
        if not await _valid_evidence_id(db, project_id, evidence_id):
            print(f"error: {operation} item {index}: invalid evidence ID {evidence_id!r}")
            valid = False
    return valid


def _correction_list(corrections: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    items = corrections.get(operation, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"'{operation}' must be a list of objects")
    return items


def _valid_section(section: Any) -> bool:
    return isinstance(section, str) and section in SECTIONS


async def apply_corrections(
    project_id: str,
    db,
    corrections: dict[str, Any],
    dry_run: bool = False,
) -> Dossier:
    dossier = await load_or_build(project_id, db)
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"replaced": 0, "added": 0, "skipped": 0}
    )
    touched_sections: set[str] = set()
    changed_claims: dict[str, list[Claim]] = defaultdict(list)

    for index, item in enumerate(_correction_list(corrections, "replace"), start=1):
        section = item.get("section")
        if not _valid_section(section):
            print(f"error: replace item {index}: invalid section {section!r}")
            continue
        touched_sections.add(section)
        if not isinstance(item.get("match"), str) or not isinstance(item.get("text"), str):
            print(f"error: replace item {index}: match and text must be strings")
            counts[section]["skipped"] += 1
            continue
        if not await _validate_item_evidence(db, project_id, item, "replace", index):
            counts[section]["skipped"] += 1
            continue

        matches = [
            claim for claim in dossier.sections.get(section, [])
            if item["match"].casefold() in claim.claim.casefold()
        ]
        if len(matches) != 1:
            print(
                f"error: replace item {index}: expected exactly one match in {section}, "
                f"found {len(matches)}"
            )
            counts[section]["skipped"] += 1
            continue

        claim = matches[0]
        claim.details["edited_from"] = claim.claim
        claim.claim = item["text"]
        claim.source = "user"
        if "evidence" in item:
            claim.evidence = item["evidence"]
        counts[section]["replaced"] += 1
        changed_claims[section].append(claim)

    for index, item in enumerate(_correction_list(corrections, "add"), start=1):
        section = item.get("section")
        if not _valid_section(section):
            print(f"error: add item {index}: invalid section {section!r}")
            continue
        touched_sections.add(section)
        tag = item.get("tag")
        text = item.get("text")
        if (
            not isinstance(tag, str)
            or tag not in TAG_CONFIDENCE
            or not isinstance(text, str)
            or ("commit" in item and not isinstance(item["commit"], str))
        ):
            print(f"error: add item {index}: tag, text, or commit is invalid")
            counts[section]["skipped"] += 1
            continue
        if not await _validate_item_evidence(
            db, project_id, item, "add", index, require_evidence=True
        ):
            counts[section]["skipped"] += 1
            continue
        if any(claim.claim == text for claim in dossier.sections.get(section, [])):
            counts[section]["skipped"] += 1
            continue

        details = {}
        if item.get("commit"):
            details["commit"] = str(item["commit"])[:7]
        claim = Claim(
            claim=text,
            tag=tag,
            evidence=item["evidence"],
            confidence=TAG_CONFIDENCE[tag],
            source="user",
            details=details,
        )
        dossier.sections.setdefault(section, []).append(claim)
        counts[section]["added"] += 1
        changed_claims[section].append(claim)

    if not dry_run:
        dossier = await save_dossier(db, dossier)

    for section in sorted(touched_sections):
        section_counts = counts[section]
        print(
            f"{section}: replaced {section_counts['replaced']}, "
            f"added {section_counts['added']}, skipped {section_counts['skipped']}"
        )
        for claim in changed_claims[section]:
            print(f"  [{claim.tag}] {claim.claim}")
    if dry_run:
        print("dry run: dossier was not saved")
    return dossier


async def _main(project_id: str, corrections_file: Path, dry_run: bool) -> None:
    with corrections_file.open(encoding="utf-8") as file:
        corrections = json.load(file)
    if not isinstance(corrections, dict):
        raise ValueError("corrections file must contain a JSON object")
    await apply_corrections(project_id, get_db(), corrections, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(args.project_id, args.file, args.dry_run))
