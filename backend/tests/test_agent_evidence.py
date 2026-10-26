from app.agent.evidence import (
    count_tags,
    find_uncited_facts,
    find_unverified_quotes,
    find_unsupported_citations as check,
)


def test_range_inside_read_is_ok() -> None:
    assert check("see [file:logger.py#L37-L66]", {"file:logger.py#L1-68"}) == []


def test_file_never_read_is_flagged() -> None:
    assert check("see file:other.py#L1-5", {"file:logger.py#L1-68"}) == [
        "file:other.py#L1-5"
    ]


def test_invented_overview_id_is_flagged() -> None:
    assert check("[overview:dependencies]", {"dep:supabase"}) == [
        "overview:dependencies"
    ]


def test_trailing_period_is_ignored() -> None:
    assert check("it uses dep:supabase.", {"dep:supabase"}) == []


def test_malformed_evidence_tokens_are_flagged() -> None:
    assert check(
        "file:logger.py commit:xyz pr:abc pr:12x dep: fact:route:",
        set(),
    ) == [
        "malformed citation: file:logger.py",
        "malformed citation: commit:xyz",
        "malformed citation: pr:abc",
        "malformed citation: pr:12x",
        "malformed citation: dep:",
        "malformed citation: fact:route:",
    ]


def test_malformed_token_substrings_are_not_citations() -> None:
    assert check("profile:logger.py", set()) == []


def test_commit_path_citation_is_validated_against_collected_ids() -> None:
    citation = "commit:a1b2c3d:src/main.py"
    assert check(f"Changed [Fact] in {citation}.", {citation}) == []
    assert check(f"Changed in {citation}.", {"commit:a1b2c3d"}) == [citation]


def test_full_width_commit_path_citation_is_normalized() -> None:
    citation = "commit:abc1234:main.py"
    assert check("【commit:abc1234:main.py】", {citation}) == []
    assert check("【commit:abc1234:main.py】", set()) == [citation]


def test_count_tags_is_case_insensitive() -> None:
    assert count_tags("[fact] [INFERRED] [Uncertain] [Fact]") == {
        "Fact": 2,
        "Inferred": 1,
        "Uncertain": 1,
    }


def test_commit_citations_match_sha_prefixes_with_or_without_paths() -> None:
    evidence = {
        "commit:a1b2c3d",
        "commit:1234567:src/main.py",
    }
    assert check("See commit:a1b2c3d4e5f6789.", evidence) == []
    assert check("See commit:1234567890:src/main.py.", evidence) == []
    assert check("See commit:1234567890:src/other.py.", evidence) == [
        "commit:1234567890:src/other.py"
    ]
    assert check("See commit:7654321.", evidence) == ["commit:7654321"]


def test_find_unverified_quotes_normalises_whitespace_and_skips_evidence_ids() -> None:
    answer = (
        '`file:src/main.py#L1-2` `short` "the   configured timeout is 30 seconds" '
        '`this fabricated phrase has no evidence`'
    )
    tool_texts = ["the configured\ntimeout is 30 seconds."]

    assert find_unverified_quotes(answer, tool_texts) == [
        "this fabricated phrase has no evidence"
    ]


def test_find_unverified_quotes_is_case_sensitive_and_requires_twelve_chars() -> None:
    assert find_unverified_quotes(
        '`Long enough phrase` "A sufficiently long quoted prose phrase" "short"',
        ["long enough phrase"],
    ) == ["Long enough phrase", "A sufficiently long quoted prose phrase"]


def test_reconstructed_old_function_lines_match_diff_markers() -> None:
    code = """```python
def old_function(value):
    if value:
        return value
    return None
```"""
    diff = """-def old_function(value):
     if value:
-        return value
     return None"""

    assert find_unverified_quotes(code, [diff]) == []


def test_fenced_code_reports_invented_line_and_ignores_language_label() -> None:
    code = """```python
def existing():
    return "invented value"
```"""

    assert find_unverified_quotes(code, ["def existing():"]) == [
        'code lines not found in any tool result: return "invented value"'
    ]


def test_prose_between_two_fenced_blocks_is_not_paired_as_inline_code() -> None:
    answer = (
        "Prose starts with a tick ` before the first fence.\n"
        "```python\nprint('first block')\n```\n"
        "Prose between fences should not pair with a tick after the second fence `.\n"
        "```text\nsome other block\n```"
    )

    assert find_unverified_quotes(answer, []) == [
        "code lines not found in any tool result: print('first block')",
        "code lines not found in any tool result: some other block",
    ]


def test_uncited_fact_paragraphs_and_bullets_are_detected() -> None:
    answer = (
        "[Fact] The service uses a cache.\n\n"
        "- [Fact] The cache is in memory.\n"
        "- [Fact] The code includes a file citation [file:cache.py#L1-2].\n\n"
        "[Inferred] This may improve latency."
    )

    assert find_uncited_facts(answer) == [
        "[Fact] The service uses a cache.",
        "- [Fact] The cache is in memory.",
    ]


def test_path_spans_present_in_evidence_ids_are_not_flagged() -> None:
    assert find_unverified_quotes(
        "The provider is `providers/groq_provider.py`.",
        [],
        evidence_ids={"file:providers/groq_provider.py#L1-5"},
    ) == []
