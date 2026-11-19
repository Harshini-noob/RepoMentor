"""
Seed selection for interview question generation.

No LLM calls happen here — this is pure dossier analysis.
"""

from __future__ import annotations

import random
import re
from typing import Any

from .concepts import ROUNDS, candidate_concepts


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

    # Also handle claims whose evidence includes commit:<sha7> or commit:<sha7>:<path>
    for claim in decisions_claims:
        for eid in claim.get("evidence") or []:
            if isinstance(eid, str) and eid.startswith("commit:"):
                parts = eid.split(":")
                if len(parts) >= 2 and parts[1]:
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


def _has_path_evidence(claim: dict) -> bool:
    for eid in claim.get("evidence") or []:
        if isinstance(eid, str) and eid.startswith("commit:"):
            parts = eid.split(":")
            if len(parts) >= 3 and parts[2]:
                return True
    return False


def _claim_priority(claim: dict) -> int:
    tag = str(claim.get("tag", "")).lower()
    if tag == "fact":
        if _has_path_evidence(claim):
            return 0  # fact with commit:<sha7>:<path> evidence
        return 1      # other fact
    if tag == "inferred":
        return 2      # inferred
    return 3          # other (e.g. uncertain)


def _order_and_filter_history_claims(claims: list[dict], max_claims: int = 7) -> list[dict]:
    # 1. Order claims: fact with path evidence, other fact, inferred, others
    ordered = sorted(claims, key=_claim_priority)

    # 2. Filter: drop an inferred claim whose text restates the commit message
    # (more than 60% of its words, 4+ letters, also appear in an earlier claim of the seed)
    selected: list[dict] = []
    for c in ordered:
        tag = str(c.get("tag", "")).lower()
        if tag == "inferred":
            claim_text = c.get("claim") or ""
            words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", claim_text)]
            if words:
                restate = False
                for prev in selected:
                    prev_text = prev.get("claim") or ""
                    prev_words = set(w.lower() for w in re.findall(r"[a-zA-Z]{4,}", prev_text))
                    matching_tokens = sum(1 for w in words if w in prev_words)
                    token_ratio = matching_tokens / len(words)
                    unique_words = set(words)
                    unique_ratio = len(unique_words & prev_words) / len(unique_words)
                    if token_ratio > 0.6 or unique_ratio > 0.6:
                        restate = True
                        break
                if restate:
                    continue
        selected.append(c)
        if len(selected) >= max_claims:
            break
    return selected


def _build_history_result(sha7: str, claims: list[dict]) -> dict:
    top = _order_and_filter_history_claims(claims, max_claims=7)
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

_FILE_REGEX = re.compile(r"\b[\w/.-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b", re.IGNORECASE)
_BACKTICK_REGEX = re.compile(r"`([^`]+)`")
_UNDERSCORE_REGEX = re.compile(r"\b[A-Za-z0-9]+_[A-Za-z0-9_]*\b")


def _extract_entity_tokens(claim: dict) -> set[str]:
    """Extract entity tokens from claim text and evidence ids."""
    text = (claim.get("claim") or "") + " " + " ".join(claim.get("evidence") or [])
    tokens: set[str] = set()

    # File names and stem without extension
    for m in _FILE_REGEX.finditer(text):
        fname = m.group(0)
        tokens.add(fname)
        base = fname.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        if stem:
            tokens.add(stem)

    # Identifiers in backticks
    for m in _BACKTICK_REGEX.finditer(text):
        tokens.add(m.group(1))

    # Identifiers with an underscore
    for m in _UNDERSCORE_REGEX.finditer(text):
        tokens.add(m.group(0))

    # Name at the end of fact:<kind>:<name> ids
    for eid in claim.get("evidence") or []:
        if isinstance(eid, str) and eid.startswith("fact:"):
            parts = eid.split(":")
            if len(parts) >= 3 and parts[-1]:
                tokens.add(parts[-1])

    # Lowercase them; drop tokens shorter than 4 characters
    return {t.lower() for t in tokens if len(t) >= 4}


def _score_and_find_related_claims(
    main_claim: dict,
    sections: dict[str, list[dict]],
) -> list[dict]:
    """Search claims of ALL dossier sections and return top related claims."""
    entity_tokens = _extract_entity_tokens(main_claim)
    if not entity_tokens:
        return []

    candidate_related: list[tuple[dict, int]] = []
    main_text = main_claim.get("claim", "")

    for sec_name, sec_claims in sections.items():
        for c in sec_claims:
            if c is main_claim or c.get("claim") == main_text:
                continue
            tag = str(c.get("tag", "")).lower()
            if tag not in ("fact", "inferred"):
                continue

            c_text = (c.get("claim") or "") + " " + " ".join(c.get("evidence") or [])

            # Number of distinct entity tokens it contains (whole-word, case-insensitive)
            matched_count = 0
            for tok in entity_tokens:
                if re.search(rf"\b{re.escape(tok)}\b", c_text, re.IGNORECASE):
                    matched_count += 1

            if matched_count == 0:
                continue

            score = matched_count
            details = c.get("details") or {}
            if details.get("commit"):
                score += 2
            if c.get("source") == "user":
                score += 1

            if score >= 1:
                candidate_related.append((c, score))

    # Sort by score descending
    candidate_related.sort(key=lambda x: -x[1])

    # Take top 4 with score >= 1, at most 2 per commit
    selected: list[dict] = []
    commit_counts: dict[str, int] = {}

    for c, sc in candidate_related:
        details = c.get("details") or {}
        commit_sha = details.get("commit")
        if not commit_sha:
            # Check commit from evidence id
            for eid in c.get("evidence") or []:
                if isinstance(eid, str) and eid.startswith("commit:"):
                    parts = eid.split(":")
                    if len(parts) >= 2 and parts[1]:
                        commit_sha = parts[1]
                        break

        if commit_sha:
            if commit_counts.get(commit_sha, 0) >= 2:
                continue
            commit_counts[commit_sha] = commit_counts.get(commit_sha, 0) + 1

        selected.append(c)
        if len(selected) >= 4:
            break

    return selected


def _pick_claim_seed(
    sections: dict[str, list[dict]],
    round_sections: list[str],
    asked: list[dict],
) -> dict:
    """Pick a seed claim from tech or architecture sections with related claims."""
    is_tech = set(round_sections) & {"technologies", "data"}

    # Collect candidate claims
    candidates: list[tuple[str, dict]] = []  # (section_name, claim_dict)
    for sec in round_sections:
        for claim in sections.get(sec) or []:
            if str(claim.get("tag", "")).lower() != "fact":
                continue
            if is_tech:
                evidence = claim.get("evidence") or []
                if not any(
                    isinstance(e, str) and e.startswith(_TECH_EVIDENCE_PREFIXES)
                    for e in evidence
                ):
                    continue
            else:
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

    # Compute related claims for each candidate in pool
    pool_with_rel: list[tuple[str, dict, list[dict]]] = [
        (s, c, _score_and_find_related_claims(c, sections))
        for s, c in pool
    ]

    # Prefer candidates with at least 2 related claims
    with_at_least_2 = [item for item in pool_with_rel if len(item[2]) >= 2]
    eligible = with_at_least_2 if with_at_least_2 else pool_with_rel

    # Choose randomly among top 3 by number of related claims
    eligible.sort(key=lambda x: -len(x[2]))
    top_pool = eligible[:3]

    chosen_sec, chosen_claim, chosen_related = random.choice(top_pool)
    chosen_commit = (chosen_claim.get("details") or {}).get("commit")

    seed_claims = [chosen_claim] + chosen_related
    return {
        "section": chosen_sec,
        "claims": [
            {
                "claim": c.get("claim", ""),
                "tag": c.get("tag", "uncertain"),
                "evidence": c.get("evidence") or [],
            }
            for c in seed_claims[:5]
        ],
        "commit": chosen_commit or None,
    }


def find_seed_for_concept(
    dossier: Any,
    concept: str,
    asked: list[dict],
    commit: str | None = None,
) -> tuple[dict, str] | None:
    """Find a candidate seed from any round that matches the given concept."""
    sections = _get_sections(dossier)
    if not sections:
        return None

    candidates: list[tuple[dict, str]] = []

    # 1. History seeds (from decisions claims grouped by commit)
    decisions_claims = sections.get("decisions") or []
    by_commit: dict[str, list[dict]] = {}
    for claim in decisions_claims:
        sha = (claim.get("details") or {}).get("commit")
        if sha:
            by_commit.setdefault(sha, []).append(claim)
    for claim in decisions_claims:
        for eid in claim.get("evidence") or []:
            if isinstance(eid, str) and eid.startswith("commit:"):
                parts = eid.split(":")
                if len(parts) >= 2 and parts[1]:
                    sha = parts[1]
                    by_commit.setdefault(sha, [])
                    if claim not in by_commit[sha]:
                        by_commit[sha].append(claim)

    for sha, c_list in by_commit.items():
        if commit is not None and sha != commit and sha[:7] != commit[:7]:
            continue
        if any(str(c.get("tag", "")).lower() == "fact" for c in c_list):
            hist_seed = _build_history_result(sha, c_list)
            candidates.append((hist_seed, "history"))

    # 2. Tech seeds (from technologies and data)
    tech_sections = ["technologies", "data"]
    for sec in tech_sections:
        for claim in sections.get(sec) or []:
            if str(claim.get("tag", "")).lower() != "fact":
                continue
            evidence = claim.get("evidence") or []
            if not any(
                isinstance(e, str) and e.startswith(_TECH_EVIDENCE_PREFIXES)
                for e in evidence
            ):
                continue
            related = _score_and_find_related_claims(claim, sections)
            seed_claims = [claim] + related
            seed = {
                "section": sec,
                "claims": [
                    {
                        "claim": c.get("claim", ""),
                        "tag": c.get("tag", "uncertain"),
                        "evidence": c.get("evidence") or [],
                    }
                    for c in seed_claims[:5]
                ],
                "commit": (claim.get("details") or {}).get("commit") or None,
            }
            candidates.append((seed, "tech"))

    # 3. Architecture seeds (from components, apis, features)
    arch_sections = ["components", "apis", "features"]
    for sec in arch_sections:
        for claim in sections.get(sec) or []:
            if str(claim.get("tag", "")).lower() != "fact":
                continue
            evidence = claim.get("evidence") or []
            if not any(
                isinstance(e, str) and e.startswith("file:")
                for e in evidence
            ):
                continue
            related = _score_and_find_related_claims(claim, sections)
            seed_claims = [claim] + related
            seed = {
                "section": sec,
                "claims": [
                    {
                        "claim": c.get("claim", ""),
                        "tag": c.get("tag", "uncertain"),
                        "evidence": c.get("evidence") or [],
                    }
                    for c in seed_claims[:5]
                ],
                "commit": (claim.get("details") or {}).get("commit") or None,
            }
            candidates.append((seed, "architecture"))

    if not candidates:
        return None

    # Track already asked
    already_asked_commits = {
        (d.get("seed") or {}).get("commit", "")
        for d in asked
        if (d.get("seed") or {}).get("commit")
    }
    asked_texts = {
        c.get("claim", "")
        for d in asked
        for c in (d.get("seed") or {}).get("claims", [])
    }

    matching_candidates: list[tuple[int, int, dict, str]] = []

    for seed, round_name in candidates:
        seed_text = " ".join(
            c.get("claim", "") + " " + " ".join(c.get("evidence") or [])
            for c in seed.get("claims", [])
        ).lower()
        cands = candidate_concepts(seed_text, k=3)
        if concept in cands:
            rank = cands.index(concept)
            is_asked = bool(
                (seed.get("commit") and seed["commit"] in already_asked_commits)
                or (seed.get("claims") and seed["claims"][0].get("claim") in asked_texts)
            )
            asked_priority = 1 if is_asked else 0
            matching_candidates.append((rank, asked_priority, seed, round_name))

    if not matching_candidates:
        return None

    # Prefer highest rank (lowest rank index), then not asked before (asked_priority 0)
    best_key = min(matching_candidates, key=lambda x: (x[0], x[1]))[:2]
    tied_pool = [
        (seed, round_name)
        for rank, asked_prio, seed, round_name in matching_candidates
        if (rank, asked_prio) == best_key
    ]

    return random.choice(tied_pool)
