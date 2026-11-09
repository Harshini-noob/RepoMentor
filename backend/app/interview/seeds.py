"""
Seed selection for interview question generation.

No LLM calls happen here — this is pure dossier analysis.
"""

from __future__ import annotations

import random
from typing import Any

from .concepts import ROUNDS


def _claim_as_dict(claim: Any) -> dict:
    """Normalise a claim from either Pydantic model or plain dict."""
    if isinstance(claim, dict):
        return claim
    return {
        "claim": getattr(claim, "claim", ""),
        "tag": getattr(claim, "tag", "uncertain"),
        "evidence": list(getattr(claim, "evidence", []) or []),
        "details": dict(getattr(claim, "details", {}) or {}),
    }


def _get_sections(dossier: Any) -> dict[str, list[dict]]:
    """Extract sections dict from a Dossier model or plain dict."""
    if isinstance(dossier, dict):
        raw = dossier.get("sections") or {}
    else:
        raw = getattr(dossier, "sections", {}) or {}
    result: dict[str, list[dict]] = {}
    for name, claims in raw.items():
        result[name] = [_claim_as_dict(c) for c in (claims or [])]
    return result


def pick_seed(
    dossier: Any,
    round_name: str,
    asked: list[dict],
    commit: str | None = None,
) -> dict:
    """
    Choose a seed for a new interview question from the dossier.

    Parameters
    ----------
    dossier:    Dossier model or plain dict from MongoDB.
    round_name: One of "history", "tech", "architecture".
    asked:      List of previously saved InterviewQuestion dicts for this project
                (from db.interview_questions).
    commit:     If given, force that commit to be used (history round only).

    Returns
    -------
    {"section": str, "claims": [{"claim", "tag", "evidence"}], "commit": sha7|None}
    """
    sections = _get_sections(dossier)
    round_sections = ROUNDS.get(round_name, [])

    if round_name == "history":
        return _pick_history_seed(sections, asked, commit)
    else:
        return _pick_claim_seed(sections, round_sections, asked)


# ---------------------------------------------------------------------------
# History round
# ---------------------------------------------------------------------------

def _pick_history_seed(
    sections: dict[str, list[dict]],
    asked: list[dict],
    commit: str | None,
) -> dict:
    """Group decisions claims by commit, score, exclude already-asked commits."""
    decisions_claims = sections.get("decisions") or []
    if not decisions_claims:
        raise RuntimeError(
            "No decisions claims in dossier. run the dossier builder first."
        )

    # Group by commit sha7 from details["commit"]
    by_commit: dict[str, list[dict]] = {}
    for claim in decisions_claims:
        sha = (claim.get("details") or {}).get("commit")
        if sha:
            by_commit.setdefault(sha, []).append(claim)

    # Also handle claims whose evidence includes commit:<sha7>:<path>
    for claim in decisions_claims:
        for eid in claim.get("evidence") or []:
            if isinstance(eid, str) and eid.startswith("commit:"):
                parts = eid.split(":")
                if len(parts) >= 3:
                    sha = parts[1]
                    by_commit.setdefault(sha, [])
                    if claim not in by_commit[sha]:
                        by_commit[sha].append(claim)

    if commit is not None:
        # Honour explicit commit
        sha7 = commit[:7]
        matched = by_commit.get(sha7) or by_commit.get(commit)
        if not matched:
            raise ValueError(
                f"Commit {commit!r} has no decisions claims in the dossier. "
                "run the dossier builder first."
            )
        return _build_history_result(sha7, matched)

    # Score each commit
    already_asked_commits = {
        (d.get("seed") or {}).get("commit", "")
        for d in asked
        if (d.get("seed") or {}).get("commit")
    }

    def score_commit(sha: str, claims: list[dict]) -> int:
        s = 0
        for c in claims:
            if str(c.get("tag", "")).lower() == "fact":
                # +1 per fact claim that has a commit:<sha7>:<path> evidence
                has_commit_ev = any(
                    isinstance(e, str) and e.startswith("commit:")
                    for e in (c.get("evidence") or [])
                )
                s += 2 if has_commit_ev else 1
        return s

    scored = [
        (sha, claims, score_commit(sha, claims))
        for sha, claims in by_commit.items()
        if any(str(c.get("tag", "")).lower() == "fact" for c in claims)
    ]
    if not scored:
        raise RuntimeError(
            "No commits with fact-tagged decisions claims in dossier. "
            "run the dossier builder first."
        )

    scored.sort(key=lambda x: -x[2])

    # Try candidates not already asked
    not_asked = [x for x in scored if x[0] not in already_asked_commits]
    pool = not_asked[:3] if not_asked else scored[:3]

    chosen_sha, chosen_claims, _ = random.choice(pool)
    return _build_history_result(chosen_sha, chosen_claims)


def _build_history_result(sha7: str, claims: list[dict]) -> dict:
    top = claims[:6]
    return {
        "section": "decisions",
        "claims": [
            {
                "claim": c.get("claim", ""),
                "tag": c.get("tag", "uncertain"),
                "evidence": c.get("evidence") or [],
            }
            for c in top
        ],
        "commit": sha7,
    }


# ---------------------------------------------------------------------------
# Tech / Architecture rounds
# ---------------------------------------------------------------------------

_TECH_EVIDENCE_PREFIXES = (
    "fact:database:",
    "fact:llm_provider:",
    "fact:ml:",
    "fact:ui_app:",
)


def _pick_claim_seed(
    sections: dict[str, list[dict]],
    round_sections: list[str],
    asked: list[dict],
) -> dict:
    """Pick a seed claim from tech or architecture sections."""
    is_tech = set(round_sections) & {"technologies", "data"}

    # Collect candidate claims
    candidates: list[tuple[str, dict]] = []  # (section_name, claim_dict)
    for sec in round_sections:
        for claim in sections.get(sec) or []:
            if str(claim.get("tag", "")).lower() != "fact":
                continue
            if is_tech:
                # Must have an evidence id starting with one of the prefixes
                evidence = claim.get("evidence") or []
                if not any(
                    isinstance(e, str) and e.startswith(_TECH_EVIDENCE_PREFIXES)
                    for e in evidence
                ):
                    continue
            else:
                # Architecture: must have at least one file:... evidence id
                evidence = claim.get("evidence") or []
                if not any(
                    isinstance(e, str) and e.startswith("file:")
                    for e in evidence
                ):
                    continue
            candidates.append((sec, claim))

    if not candidates:
        raise RuntimeError(
            f"No suitable fact claims found in sections {round_sections}. "
            "run the dossier builder first."
        )

    # Exclude already-asked claims by text
    asked_texts = {
        c.get("claim", "")
        for d in asked
        for c in (d.get("seed") or {}).get("claims", [])
    }
    not_asked = [(s, c) for s, c in candidates if c.get("claim") not in asked_texts]
    pool = not_asked if not_asked else candidates

    chosen_sec, chosen_claim = random.choice(pool)
    chosen_evidence = set(chosen_claim.get("evidence") or [])
    chosen_commit = (chosen_claim.get("details") or {}).get("commit")

    # Add up to 3 related claims from any section
    related: list[dict] = []
    all_claims: list[tuple[str, dict]] = []
    for sec_name, sec_claims in sections.items():
        for c in sec_claims:
            all_claims.append((sec_name, c))

    for _, c in all_claims:
        if c is chosen_claim or c.get("claim") == chosen_claim.get("claim"):
            continue
        c_evidence = set(c.get("evidence") or [])
        c_commit = (c.get("details") or {}).get("commit")
        shares_evidence = bool(chosen_evidence & c_evidence)
        shares_commit = chosen_commit and c_commit and chosen_commit == c_commit
        if shares_evidence or shares_commit:
            related.append(c)
        if len(related) >= 3:
            break

    seed_claims = [chosen_claim] + related
    return {
        "section": chosen_sec,
        "claims": [
            {
                "claim": c.get("claim", ""),
                "tag": c.get("tag", "uncertain"),
                "evidence": c.get("evidence") or [],
            }
            for c in seed_claims[:4]
        ],
        "commit": chosen_commit or None,
    }
