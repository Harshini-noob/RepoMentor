import re

# Matches ids as tools emit them, plus the "#L10-L20" variant models like to write.
CITE_RE = re.compile(
    r"(?:file:[^\s\]\)#,]+#L\d+(?:-L?\d+)?"
    r"|commit:[0-9a-fA-F]{7,40}"
    r"|pr:\d+"
    r"|dep:[^\s\]\),]+"
    r"|fact:[a-z_]+:[^\s\]\),]+"
    r"|overview:[^\s\]\),]+)"
    r"(?![^\s\]\)#,])"
)
EVIDENCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:file|commit|pr|dep|fact):[^\s\]\),]*"
)
FILE_RE = re.compile(r"file:(?P<path>[^#]+)#L(?P<s>\d+)(?:-L?(?P<e>\d+))?")


def _covered(evidence_ids):
    lines: dict[str, set[int]] = {}
    others: set[str] = set()
    for eid in evidence_ids:
        m = FILE_RE.fullmatch(eid)
        if m:
            s = int(m["s"])
            e = int(m["e"] or s)
            lines.setdefault(m["path"], set()).update(range(s, e + 1))
        else:
            others.add(eid.lower())
    return lines, others


def find_unsupported_citations(answer: str, evidence_ids) -> list[str]:
    """Return citations in the answer that no tool result in this run backs up."""
    lines, others = _covered(evidence_ids)
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
        elif cite.lower() not in others:
            bad.append(cite)
    for raw in dict.fromkeys(EVIDENCE_TOKEN_RE.findall(answer)):
        token = raw.rstrip(".;")
        if not CITE_RE.fullmatch(token):
            malformed = f"malformed citation: {token}"
            if malformed not in bad:
                bad.append(malformed)
    return bad