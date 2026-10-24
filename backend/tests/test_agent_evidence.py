from app.agent.evidence import (
    count_tags,
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
        '`file:src/main.py#L1-2` "the   configured timeout is 30 seconds" '
        '`this fabricated phrase has no evidence`'
    )
    tool_texts = ["the configured\ntimeout is 30 seconds."]

    assert find_unverified_quotes(answer, tool_texts) == [
        "this fabricated phrase has no evidence"
    ]


def test_find_unverified_quotes_is_case_sensitive_and_requires_twelve_chars() -> None:
    assert find_unverified_quotes(
        '"Long enough phrase" "short" "a          b"',
        ["long enough phrase"],
    ) == ["Long enough phrase", "a b"]
