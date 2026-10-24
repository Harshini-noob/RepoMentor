from app.agent.evidence import find_unsupported_citations as check


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
