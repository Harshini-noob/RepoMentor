import re

ANSWER_CHAR_MAP = str.maketrans({
    "\u3010": "[",
    "\u3011": "]",
    "\u3014": "[",
    "\u3015": "]",
    "\uff3b": "[",
    "\uff3d": "]",
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
})

# Matches ids as tools emit them, plus the "#L10-L20" variant models like to write.
CITE_RE = re.compile(
    r"(?:file:[^\s\]\)\u3011#,]+#L\d+(?:-L?\d+)?"
    r"|commit:[0-9a-fA-F]{7,40}:[^\s\]\)\u3011#,]+"
    r"|commit:[0-9a-fA-F]{7,40}"
    r"|pr:\d+"
    r"|dep:[^\s\]\)\u3011,]+"
    r"|fact:[a-z_]+:[^\s\]\)\u3011,]+"
    r"|overview:[^\s\]\)\u3011,]+)"
    r"(?![^\s\]\)\u3011#,.;:])"
)
EVIDENCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:file|commit|pr|dep|fact):[^\s\]\),]*"
)
FILE_RE = re.compile(r"file:(?P<path>[^#]+)#L(?P<s>\d+)(?:-L?(?P<e>\d+))?")
COMMIT_RE = re.compile(
    r"commit:(?P<sha>[0-9a-fA-F]{7,40})(?::(?P<path>[^\s\]\)#,]+))?"
)
FENCED_CODE_RE = re.compile(r"```[^\r\n]*\r?\n(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\r\n]+)`(?!`)")
DOUBLE_QUOTED_RE = re.compile(r'"((?:\\.|[^"\\])*)"')


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def count_tags(answer: str) -> dict[str, int]:
    """Count claim tags in an answer, ignoring tag-name case."""
    counts = {"Fact": 0, "Inferred": 0, "Uncertain": 0, "General": 0}
    for tag in re.findall(r"\[(Fact|Inferred|Uncertain|General)\]", answer, re.IGNORECASE):
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
    answer = answer.translate(ANSWER_CHAR_MAP)
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


def find_uncited_facts(answer: str) -> list[str]:
    """Find fact-tagged paragraphs or bullets without an evidence ID."""
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in answer.splitlines():
        stripped = line.strip()
        is_bullet = bool(re.match(r"^(?:[-*+]\s+)", stripped))
        if not stripped:
            flush()
        elif is_bullet:
            flush()
            current.append(stripped)
        else:
            current.append(line)
    flush()

    uncited: list[str] = []
    for block in blocks:
        if not re.match(r"^(?:[-*+]\s+)?\[Fact\]", block, re.IGNORECASE):
            continue
        has_evidence_id = any(
            CITE_RE.fullmatch(token.rstrip(".;:"))
            for token in EVIDENCE_TOKEN_RE.findall(block)
        )
        if not has_evidence_id:
            uncited.append(block)
    return uncited


def find_unverified_quotes(answer: str, tool_texts, evidence_ids=()) -> list[str]:
    """Return code lines and quoted spans not found in tool output."""
    answer = answer.translate(ANSWER_CHAR_MAP)
    normalised_results = []
    for text in tool_texts:
        cleaned_lines = []
        for line in text.splitlines():
            if line.startswith(("+", "-", " ")):
                line = line[1:]
            cleaned_lines.append(line)
        normalised_results.append(_normalise_whitespace("\n".join(cleaned_lines)))
    normalised_results.extend(
        _normalise_whitespace(str(evidence_id)) for evidence_id in evidence_ids
    )

    unverified: list[str] = []
    code_failure_count = 0

    def record_if_missing(value: str, minimum_length: int, code_line: bool = False) -> None:
        nonlocal code_failure_count
        value = value.replace('\\"', '"').replace("\\\\", "\\")
        if len(value) < minimum_length:
            return
        normalised = _normalise_whitespace(value)
        if not normalised or CITE_RE.fullmatch(normalised):
            return
        if any(normalised in result for result in normalised_results):
            return
        if code_line and code_failure_count >= 5:
            return
        item = (
            f"code lines not found in any tool result: {normalised}"
            if code_line
            else normalised
        )
        if item not in unverified:
            unverified.append(item)
            if code_line:
                code_failure_count += 1

    prose_segments: list[str] = []
    cursor = 0
    for match in FENCED_CODE_RE.finditer(answer):
        prose_segments.append(answer[cursor:match.start()])
        cursor = match.end()
        for line in match.group(1).splitlines():
            if line.strip():
                record_if_missing(line, 1, code_line=True)
    prose_segments.append(answer[cursor:])

    for prose in prose_segments:
        inline_spans = list(INLINE_CODE_RE.finditer(prose))
        for match in inline_spans:
            record_if_missing(match.group(1), 12)

        prose_without_inline = INLINE_CODE_RE.sub(" ", prose)
        for match in DOUBLE_QUOTED_RE.finditer(prose_without_inline):
            record_if_missing(match.group(1), 20)

    return unverified


def remove_unverified_code_blocks(answer: str, tool_texts, evidence_ids=()) -> str:
    """Replace fenced blocks containing lines not found in tool output."""
    def replace_block(match: re.Match[str]) -> str:
        issues = find_unverified_quotes(
            match.group(0), tool_texts, evidence_ids=evidence_ids
        )
        if any(issue.startswith("code lines not found in any tool result:") for issue in issues):
            return "[code block removed: lines not found in any tool result]"
        return match.group(0)

    return FENCED_CODE_RE.sub(replace_block, answer)