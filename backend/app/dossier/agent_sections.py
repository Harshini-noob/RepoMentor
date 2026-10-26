"""Agent-written dossier sections.

Run from the backend folder:
  python -m app.dossier.agent_sections <project_id> [--only components,features,decisions]
         [--commits 02a5c7b,9cac962] [--max-tokens 45000] [--force] [--reparse]

--reparse re-reads the answers of earlier saved runs (agent_traces) and parses them again.
It makes no LLM calls and costs no tokens.
"""

import argparse
import asyncio
import re

from app.agent.evidence import CITE_RE, find_unsupported_citations
from app.agent.loop import run_agent
from app.core.db import get_db
from app.dossier.builder import build_static_dossier, save_dossier
from app.dossier.schema import Claim, Dossier

FORMAT = (
    "Answer format: 2 to 8 lines, ONE claim per line, each line exactly like "
    "'[Fact] claim text [file:main.py#L31]'. Tags: [Fact] a tool result shows it, "
    "[Inferred] reasoned from observations, [Uncertain] not supported. Use plain ASCII square "
    "brackets for evidence IDs and write file IDs as file:<path>#L<start>-<end>. "
    "No headings, no code blocks, no extra text."
)

SECTION_GOALS = {
    "components": (
        "Describe the main components of this project. For the 6 to 8 most important source "
        "files or folders, say what each is responsible for and what it depends on. Start with "
        "get_overview and get_facts, then read only the files you need. Do not only list imports."
    ),
    "features": (
        "List the main features this project offers to its users. Use the README and the code. "
        "Each feature must name the file(s) that implement it."
    ),
}

DECISION_GOAL = (
    'Commit {sha7} ({date}, +{add}/-{dele}) has the message: "{message}".\n'
    "Find what this commit changed and what it replaced. Call get_commit with sha {sha7} first "
    "WITHOUT path to see the changed files, then call get_commit WITH path for the 1 or 2 most "
    "important source files (ignore lockfiles and tests). In a diff, lines starting with '-' were "
    "removed, so they show what was replaced.\n"
    "Write 2 to 4 lines: [Fact] what changed (only what the message or diff shows); "
    "[Fact] what it replaced, only if removed lines show it, otherwise [Uncertain]; "
    "[Inferred] the likely reason, unless the message states it (then use [Fact]). "
    "Never put a purpose ('to prevent', 'to avoid', 'because', 'so that') inside a [Fact] line."
)

LINE_RE = re.compile(r"^\s*(?:[-*\u2022]\s*)?\[(fact|inferred|uncertain)\]\s*(.+?)\s*$", re.IGNORECASE)
PURPOSE_RE = re.compile(
    r"\b(to prevent|to avoid|in order to|so that|because|to ensure|likely)\b", re.IGNORECASE
)
STRUCTURAL = ("migrat", "replac", "refactor", "rewrite", "switch")

# Models sometimes use full-width brackets and typographic dashes; map them to ASCII.
CHAR_MAP = str.maketrans({
    "\u3010": "[", "\u3011": "]", "\u3014": "[", "\u3015": "]", "\uff3b": "[", "\uff3d": "]",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
})
BARE_REF_RE = re.compile(
    r"(?<![\w:/.\-])([A-Za-z0-9_][\w./\-]*\.[A-Za-z0-9]{1,5})#L?(\d+)(?:-L?(\d+))?"
)


def normalize(text: str) -> str:
    """ASCII brackets and dashes; turn bare 'file.py#L10-20' into 'file:file.py#L10-20'."""
    text = text.translate(CHAR_MAP)

    def to_id(m: re.Match) -> str:
        return f"file:{m.group(1)}#L{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")

    return BARE_REF_RE.sub(to_id, text)


def _first_line(commit: dict) -> str:
    return ((commit.get("message") or "").splitlines() or [""])[0]


async def candidate_commits(db, project_id: str, n: int = 7) -> list[dict]:
    commits = await db.commits.find({"project_id": project_id}).to_list(length=None)

    def size(c: dict) -> int:
        return (c.get("additions") or 0) + (c.get("deletions") or 0)

    chosen: dict[str, dict] = {}
    for c in commits:  # commits whose message names a structural change
        if any(k in _first_line(c).lower() for k in STRUCTURAL):
            chosen[c["sha"]] = c
    for c in sorted(commits, key=size, reverse=True):  # plus the largest ones
        if len(chosen) >= n:
            break
        chosen.setdefault(c["sha"], c)
    return sorted(chosen.values(), key=lambda c: str(c.get("date")))


def parse_claims(
    answer: str,
    collected: set[str],
    extra: dict | None = None,
    fallback: list[str] | None = None,
) -> tuple[list[Claim], dict]:
    claims: list[Claim] = []
    stats = {"parsed": 0, "downgraded": 0, "dropped_ids": 0}
    for line in normalize(answer).splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        orig_tag = tag = m.group(1).lower()
        body = m.group(2)
        cited = list(dict.fromkeys(x.group(0).rstrip(".;:") for x in CITE_RE.finditer(body)))
        bad = set(find_unsupported_citations(body, collected))
        good = [c for c in cited if c not in bad]
        stats["dropped_ids"] += len(bad)

        text = body
        for c in cited:
            text = text.replace(c, "")
        text = re.sub(r"\[\s*evidence[s]?\s*:?\s*[,;\s]*\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[[\s,;:]*\]", "", text)
        text = re.sub(r"\(\s*[,;]*\s*\)", "", text)
        text = re.sub(r"\s+([,.;])", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,;")
        if not text:
            continue

        confidence = {"fact": 1.0, "inferred": 0.7, "uncertain": 0.3}[tag]
        details: dict = dict(extra or {})
        if bad:
            details["dropped_ids"] = sorted(bad)
        if tag == "fact" and PURPOSE_RE.search(text):
            tag, confidence = "inferred", 0.6
            details["downgraded"] = "purpose clause inside a fact"
            stats["downgraded"] += 1
        if tag in ("fact", "inferred") and not good and fallback:
            good = list(fallback)
            details["evidence_level"] = "commit only (no line-level citation survived)"
        if tag == "fact" and not good:
            tag, confidence = "inferred", 0.5
            details["downgraded"] = "fact had no supported evidence"
            stats["downgraded"] += 1
        if orig_tag == "inferred" and not good and claims:
            good = list(claims[-1].evidence)
            details["based_on_previous_claim"] = True
        if tag == "inferred" and not good:
            tag, confidence = "uncertain", 0.3
            details["no_basis"] = True
        claims.append(
            Claim(claim=text, tag=tag, evidence=good, confidence=confidence,
                  source="agent", details=details)
        )
        stats["parsed"] += 1
    return claims, stats


async def load_or_build(project_id: str, db) -> Dossier:
    doc = await db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
    if doc:
        return Dossier.model_validate(doc)
    return await build_static_dossier(project_id, db)


async def latest_answer(db, project_id: str, goal_prefix: str):
    """Final answer and collected evidence of the newest saved run whose goal starts with the prefix."""
    doc = await db.agent_traces.find_one(
        {"project_id": project_id, "stop_reason": "answered",
         "goal": {"$regex": "^" + re.escape(goal_prefix)}},
        sort=[("_id", -1)],
    )
    if not doc:
        return None, set()
    collected = {eid for s in doc.get("steps", []) for eid in (s.get("evidence_ids") or [])}
    return doc.get("final_answer") or "", collected


async def investigate(goal: str, project_id: str, db, max_steps: int):
    return await run_agent(
        goal=goal + "\n\n" + FORMAT,
        project_id=project_id,
        max_steps=max_steps,
        db=db,
        on_step=lambda s: print(f"  [{s.step}] {s.tool} {str(s.args)[:90]}", flush=True),
        on_event=lambda t: print(f"  ... {t}", flush=True),
    )


def report(claims: list[Claim], stats: dict, tokens: int) -> None:
    print(f"  saved {stats['parsed']} claims (downgraded {stats['downgraded']}, "
          f"unsupported ids dropped {stats['dropped_ids']}, tokens {tokens})")
    for c in claims:
        print(f"   [{c.tag}] {c.claim}")
        print(f"       evidence: {', '.join(c.evidence) or '-'}")
        if c.details.get("dropped_ids"):
            print(f"       rejected ids: {', '.join(c.details['dropped_ids'])}")


async def build_simple_section(dossier: Dossier, db, project_id: str, name: str, reparse: bool) -> Dossier:
    if reparse:
        answer, collected = await latest_answer(db, project_id, SECTION_GOALS[name][:60])
        if answer is None:
            print("  no stored run for this commit, skipped")
            return dossier
        tokens = 0
    else:
        run = await investigate(SECTION_GOALS[name], project_id, db, 10)
        if run.stop_reason != "answered":
            print(f"  skipped: stop_reason={run.stop_reason}: {(run.final_answer or '')[:200]}")
            return dossier
        answer = run.final_answer or ""
        collected = {eid for step in run.steps for eid in step.evidence_ids}
        tokens = run.total_tokens
    claims, stats = parse_claims(answer, collected)
    if not claims:
        print("  skipped: no '[Tag] claim' lines found in the answer")
        return dossier
    dossier.sections[name] = [c for c in dossier.sections.get(name, []) if c.source != "agent"] + claims
    dossier = await save_dossier(db, dossier)
    report(claims, stats, tokens)
    return dossier


async def build_decisions(dossier: Dossier, db, project_id: str, commits_arg: str,
                          max_tokens: int, force: bool, reparse: bool) -> Dossier:
    if commits_arg:
        commits = []
        for prefix in [p.strip() for p in commits_arg.split(",") if p.strip()]:
            doc = await db.commits.find_one(
                {"project_id": project_id, "sha": {"$regex": "^" + re.escape(prefix)}}
            )
            if doc:
                commits.append(doc)
            else:
                print(f"  commit {prefix} not found, skipped")
    else:
        commits = await candidate_commits(db, project_id)
    print("  commits to investigate:\n    " + "\n    ".join(
        f"{c['sha'][:7]} ({str(c.get('date'))[:10]}): {_first_line(c)[:70]}" for c in commits), flush=True)

    done = {c.details.get("commit") for c in dossier.sections.get("decisions", []) if c.source == "agent"}
    spent = 0
    for i, c in enumerate(commits):
        sha7 = c["sha"][:7]
        if not reparse and sha7 in done and not force:
            print(f"\n--- {sha7}: already in the dossier, skipped (use --force to redo)")
            continue
        if not reparse and spent >= max_tokens:
            print(f"\nToken cap reached ({spent}). Run the same command again to continue.")
            break
        print(f"\n--- commit {sha7}: {_first_line(c)[:70]}", flush=True)
        if reparse:
            answer, collected = await latest_answer(db, project_id, f"Commit {sha7} (")
            if answer is None:
                print("  no stored run for this commit, skipped")
                continue
            tokens = 0
        else:
            goal = DECISION_GOAL.format(
                sha7=sha7, date=str(c.get("date"))[:10], add=c.get("additions") or 0,
                dele=c.get("deletions") or 0, message=_first_line(c)[:160],
            )
            run = await investigate(goal, project_id, db, 7)
            spent += run.total_tokens
            answer = run.final_answer or ""
            if run.stop_reason != "answered":
                print(f"  skipped: stop_reason={run.stop_reason}: {answer[:200]}")
                if "daily" in answer.lower():
                    print("  Daily limit reached. Try again later or change LLM_MODEL.")
                    break
                continue
            collected = {eid for step in run.steps for eid in step.evidence_ids}
            tokens = run.total_tokens
        fallback = [f"commit:{sha7}"] if f"commit:{sha7}" in collected else None
        claims, stats = parse_claims(answer, collected, {"commit": sha7}, fallback=fallback)
        if not claims:
            print("  skipped: no '[Tag] claim' lines found in the answer")
            continue
        # replace this commit's claims and drop old decision claims that have no commit attached
        dossier.sections["decisions"] = [
            x for x in dossier.sections.get("decisions", [])
            if not (x.source == "agent" and x.details.get("commit") in (sha7, None))
        ] + claims
        dossier = await save_dossier(db, dossier)
        report(claims, stats, tokens)
        if not reparse and i < len(commits) - 1:
            await asyncio.sleep(25)  # let the per-minute token window recover
    return dossier


async def main_async(project_id: str, db, only: list[str], commits_arg: str,
                     max_tokens: int, force: bool, reparse: bool) -> None:
    dossier = await load_or_build(project_id, db)
    for name in only:
        print(f"\n=== {name} ===", flush=True)
        if name == "decisions":
            dossier = await build_decisions(dossier, db, project_id, commits_arg, max_tokens, force, reparse)
        else:
            dossier = await build_simple_section(dossier, db, project_id, name, reparse)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--only", default="decisions")
    parser.add_argument("--commits", default="")
    parser.add_argument("--max-tokens", type=int, default=45000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reparse", action="store_true")
    args = parser.parse_args()
    names = [n.strip() for n in args.only.split(",") if n.strip() in (*SECTION_GOALS, "decisions")]
    if not names:
        raise SystemExit("--only must contain some of: components, features, decisions")
    asyncio.run(main_async(args.project_id, get_db(), names, args.commits, args.max_tokens,
                           args.force, args.reparse))