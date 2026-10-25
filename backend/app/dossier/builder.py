"""Static dossier: claims built only from stored data, no LLM.

Run:  python -m app.dossier.builder <project_id>   (from the backend folder)
"""

import asyncio
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.db import get_db
from app.dossier.schema import Claim, Dossier


def _fact(claim: str, evidence: list[str], details: dict[str, Any] | None = None) -> Claim:
    return Claim(claim=claim, tag="fact", evidence=evidence, source="static", details=details or {})


def _naive_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _range(fact: dict) -> str:
    start = fact.get("line_start") or 1
    end = fact.get("line_end") or start
    return f"file:{fact['file_path']}#L{start}-{end}"


def _short(items: list[str], limit: int = 5) -> str:
    text = ", ".join(items[:limit])
    return text + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _group(facts: list[dict], kind: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for fact in facts:
        if fact.get("kind") == kind:
            groups[fact["name"]].append(fact)
    for items in groups.values():
        items.sort(key=lambda f: (f["file_path"], f.get("line_start") or 0))
    return groups


async def build_static_dossier(project_id: str, db) -> Dossier:
    project = await db.projects.find_one(
        {"$or": [{"project_id": project_id}, {"_id": project_id}]}
    )
    project = project or {}
    repo = project.get("repo_full_name") or project_id
    repo_ev = [f"repo:{repo}"]

    files = await db.files.find({"project_id": project_id}).to_list(length=None)
    deps = await db.dependencies.find({"project_id": project_id}).to_list(length=None)
    facts = await db.derived_facts.find({"project_id": project_id}).to_list(length=None)
    commits = await db.commits.find({"project_id": project_id}).to_list(length=None)
    code_facts = [f for f in facts if not f.get("in_test")]

    dossier = Dossier(project_id=project_id, generated_from_sha=project.get("head_sha"))
    s = dossier.sections

    # ---- identity
    branch = project.get("default_branch")
    s["identity"].append(
        _fact(f"Repository {repo}" + (f", default branch {branch}" if branch else ""), repo_ev)
    )
    langs = Counter((f.get("language") or "Other") for f in files)
    if langs:
        text = ", ".join(f"{name} ({count})" for name, count in langs.most_common(6))
        s["identity"].append(
            _fact(f"Files by language: {text}", repo_ev, {"languages": langs.most_common()})
        )
    for name, items in sorted(_group(code_facts, "entry_point").items()):
        s["identity"].append(
            _fact(
                f"Entry point: {name} in {items[0]['file_path']}",
                [f"fact:entry_point:{name}", _range(items[0])],
            )
        )

    # ---- technologies
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for d in deps:
        key = (d["name"], d.get("kind") or "runtime")
        entry = grouped.setdefault(key, {"sources": set(), "version": d.get("version_spec")})
        entry["sources"].add(d.get("source_file") or "unknown")
    for (name, kind), entry in sorted(grouped.items()):
        sources = sorted(entry["sources"])
        if kind == "base_image":
            version = entry["version"] or ""
            s["technologies"].append(
                _fact(f"Docker base image {name}:{version}", [f"dep:{name}"], {"source_files": sources})
            )
        else:
            s["technologies"].append(
                _fact(
                    f"{name} is a {kind} dependency (declared in {', '.join(sources)})",
                    [f"dep:{name}"],
                    {"source_files": sources, "version_spec": entry["version"]},
                )
            )
    for kind, label in [("llm_provider", "LLM provider SDK"), ("ml", "ML library"), ("ui_app", "UI framework")]:
        for name, items in sorted(_group(code_facts, kind).items()):
            paths = sorted({i["file_path"] for i in items})
            s["technologies"].append(
                _fact(
                    f"{label} {name} is used in {len(paths)} file(s): {_short(paths)}",
                    [f"fact:{kind}:{name}"] + [_range(i) for i in items[:3]],
                    {"files": paths},
                )
            )

    # ---- apis
    for fact in sorted(_flat(_group(code_facts, "route")), key=lambda f: (f["file_path"], f.get("line_start") or 0)):
        details = fact.get("details") or {}
        handler = details.get("handler")
        s["apis"].append(
            _fact(
                f"{fact['name']} is defined in {fact['file_path']} at line {fact.get('line_start')}"
                + (f" (handler {handler})" if handler else ""),
                [f"fact:route:{fact['name']}", _range(fact)],
                details,
            )
        )

    # ---- data
    for name, items in sorted(_group(code_facts, "database").items()):
        paths = sorted({i["file_path"] for i in items})
        s["data"].append(
            _fact(
                f"Database client {name} is used in {len(paths)} file(s): {_short(paths)}",
                [f"fact:database:{name}"] + [_range(i) for i in items[:3]],
                {"files": paths},
            )
        )
    env_names = sorted(_group(code_facts, "env_var"))
    if env_names:
        s["data"].append(
            _fact(
                f"Configuration is read from environment variables (names only): {_short(env_names, 8)}",
                [f"fact:env_var:{n}" for n in env_names[:8]],
            )
        )

    # ---- timeline
    dated = [(dt, c) for c in commits if (dt := _naive_utc(c.get("date")))]
    if dated:
        dated.sort(key=lambda pair: pair[0])
        first, last = dated[0], dated[-1]
        s["timeline"].append(
            _fact(
                f"{len(dated)} commits are stored, from {first[0]:%Y-%m-%d} to {last[0]:%Y-%m-%d}",
                [f"commit:{first[1]['sha'][:7]}", f"commit:{last[1]['sha'][:7]}"],
            )
        )
        weeks: dict[Any, list[dict]] = defaultdict(list)
        for dt, c in dated:
            weeks[(dt - timedelta(days=dt.weekday())).date()].append(c)
        week, week_commits = max(weeks.items(), key=lambda kv: len(kv[1]))
        s["timeline"].append(
            _fact(
                f"Busiest week starts {week}: {len(week_commits)} commits",
                [f"commit:{c['sha'][:7]}" for c in week_commits[:3]],
            )
        )
        big = max((c for _, c in dated), key=lambda c: (c.get("additions") or 0) + (c.get("deletions") or 0))
        message = ((big.get("message") or "").splitlines() or [""])[0][:80]
        s["timeline"].append(
            _fact(
                f"Largest stored commit is {big['sha'][:7]} (+{big.get('additions') or 0}/-{big.get('deletions') or 0}): {message}",
                [f"commit:{big['sha'][:7]}"],
            )
        )

    # ---- quality
    tests = [f for f in facts if f.get("kind") == "test"]
    if tests:
        in_root = [t for t in tests if "/" not in t["file_path"].replace("\\", "/")]
        s["quality"].append(
            _fact(
                f"{len(tests)} test files found; {len(in_root)} sit in the repository root instead of a tests folder",
                [f"fact:test:{t['name']}" for t in tests[:3]],
            )
        )
    else:
        s["quality"].append(
            _fact("No test files were detected by file-name patterns (test_*.py, *_test.py, *.test.js)", repo_ev)
        )
    n_prs = await db.prs.count_documents({"project_id": project_id})
    n_issues = await db.issues.count_documents({"project_id": project_id})
    s["quality"].append(
        _fact(f"{n_prs} pull requests and {n_issues} issues were found when the repository was ingested", repo_ev)
    )
    readme = next((f for f in files if str(f.get("path", "")).lower() == "readme.md"), None)
    s["quality"].append(
        _fact("README.md is present", [f"file:{readme['path']}#L1-1"])
        if readme
        else _fact("No README.md was found in the repository root", repo_ev)
    )

    return dossier


def _flat(groups: dict[str, list[dict]]) -> list[dict]:
    return [item for items in groups.values() for item in items]


async def save_dossier(db, dossier: Dossier) -> Dossier:
    existing = await db.dossiers.find_one({"project_id": dossier.project_id}, {"version": 1})
    dossier.version = (existing["version"] + 1) if existing else 1
    await db.dossiers.replace_one(
        {"project_id": dossier.project_id}, dossier.model_dump(mode="json"), upsert=True
    )
    return dossier


async def _main(project_id: str) -> None:
    db = get_db()
    dossier = await save_dossier(db, await build_static_dossier(project_id, db))
    print(f"Dossier for {project_id}  (version {dossier.version}, head {dossier.generated_from_sha})")
    for name, claims in dossier.sections.items():
        print(f"\n== {name} ({len(claims)} claims)")
        for claim in claims[:3]:
            print(f"  [{claim.tag}] {claim.claim}")
            print(f"      evidence: {', '.join(claim.evidence[:4])}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.dossier.builder <project_id>")
        sys.exit(1)
    asyncio.run(_main(sys.argv[1]))