from app.agent.evidence import count_tags, find_unsupported_citations as check


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
