import re

# Matches ids as tools emit them, plus the "#L10-L20" variant models like to write.
CITE_RE = re.compile(
    r"(?:file:[^\s\]\)#,]+#L\d+(?:-L?\d+)?"
    r"|commit:[0-9a-fA-F]{7,40}:[^\s\]\)#,]+"
    r"|commit:[0-9a-fA-F]{7,40}"
    r"|pr:\d+"
    r"|dep:[^\s\]\),]+"
    r"|fact:[a-z_]+:[^\s\]\),]+"
    r"|overview:[^\s\]\),]+)"
    r"(?![^\s\]\)#,.;:])"
)
EVIDENCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:file|commit|pr|dep|fact):[^\s\]\),]*"
)
FILE_RE = re.compile(r"file:(?P<path>[^#]+)#L(?P<s>\d+)(?:-L?(?P<e>\d+))?")
COMMIT_RE = re.compile(
    r"commit:(?P<sha>[0-9a-fA-F]{7,40})(?::(?P<path>[^\s\]\)#,]+))?"
)
QUOTE_RE = re.compile(r"`([^`]+)`|\"((?:\\.|[^\"\\])*)\"")


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def count_tags(answer: str) -> dict[str, int]:
    """Count claim tags in an answer, ignoring tag-name case."""
    counts = {"Fact": 0, "Inferred": 0, "Uncertain": 0}
    for tag in re.findall(r"\[(Fact|Inferred|Uncertain)\]", answer, re.IGNORECASE):
        counts[tag.capitalize()] += 1
    return counts


def _covered(evidence_ids):
    lines: dict[str, set[int]] = {}
    others: set[str] = set()
    commits: set[tuple[str, str | None]] = set()
    for eid in evidence_ids:
        m = FILE_RE.fullmatch(eid)
        if m:
            s = int(m["s"])
            e = int(m["e"] or s)
            lines.setdefault(m["path"], set()).update(range(s, e + 1))
        else:
            commit_match = COMMIT_RE.fullmatch(eid)
            if commit_match:
                commits.add((commit_match["sha"][:7].lower(), commit_match["path"]))
            else:
                others.add(eid.lower())
    return lines, others, commits


def find_unsupported_citations(answer: str, evidence_ids) -> list[str]:
    """Return citations in the answer that no tool result in this run backs up."""
    lines, others, commits = _covered(evidence_ids)
    bad: list[str] = []
    for raw in dict.fromkeys(CITE_RE.findall(answer)):
        cite = raw.rstrip(".;:")
        if not CITE_RE.fullmatch(cite):
            continue
        m = FILE_RE.fullmatch(cite)
        if m:
            s = int(m["s"])
            e = int(m["e"] or s)
            have = lines.get(m["path"], set())
            if not all(n in have for n in range(s, e + 1)):
                bad.append(cite)
        else:
            commit_match = COMMIT_RE.fullmatch(cite)
            if commit_match:
                cited_sha = commit_match["sha"][:7].lower()
                cited_path = commit_match["path"]
                if cited_path is None:
                    backed = any(sha7 == cited_sha for sha7, _ in commits)
                else:
                    backed = (cited_sha, cited_path) in commits
                if not backed:
                    bad.append(cite)
            elif cite.lower() not in others:
                bad.append(cite)
    for raw in dict.fromkeys(EVIDENCE_TOKEN_RE.findall(answer)):
        token = raw.rstrip(".;")
        if not CITE_RE.fullmatch(token):
            malformed = f"malformed citation: {token}"
            if malformed not in bad:
                bad.append(malformed)
    return bad


def find_unverified_quotes(answer: str, tool_texts) -> list[str]:
    """Return quoted spans of at least 12 characters not found in tool output."""
    normalised_results = [
        _normalise_whitespace(text)
        for text in tool_texts
    ]
    unverified: list[str] = []
    for match in QUOTE_RE.finditer(answer):
        quote = match.group(1) if match.group(1) is not None else match.group(2)
        if quote is None:
            continue
        quote = quote.replace('\\"', '"').replace("\\\\", "\\")
        if len(quote) < 12:
            continue
        normalised_quote = _normalise_whitespace(quote)
        if CITE_RE.fullmatch(normalised_quote):
            continue
        if not any(normalised_quote in result for result in normalised_results):
            if normalised_quote not in unverified:
                unverified.append(normalised_quote)
    return unverified