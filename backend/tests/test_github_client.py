import time
import httpx
import pytest
import respx

from app.github.client import GitHubClient, RateLimitExceededError


@pytest.mark.asyncio
@respx.mock
async def test_pagination_and_pull_request_filtering() -> None:
    # Page 1 matches the initial request with state=all
    page1_route = respx.get(
        "https://api.github.com/repos/octocat/Hello-World/issues",
        params={"state": "all"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 1, "title": "Issue 1", "state": "open", "user": {"login": "alice"}},
                {
                    "number": 2,
                    "title": "PR 2",
                    "state": "open",
                    "user": {"login": "bob"},
                    "pull_request": {"url": "https://api.github.com/..."},
                },
            ],
            headers={
                "Link": '<https://api.github.com/repos/octocat/Hello-World/issues?page=2>; rel="next"'
            },
        )
    )

    # Page 2 matches the next page URL
    page2_route = respx.get(
        "https://api.github.com/repos/octocat/Hello-World/issues",
        params={"page": "2"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 3, "title": "Issue 3", "state": "closed", "user": {"login": "carol"}},
            ],
        )
    )

    async with GitHubClient(token="fake-token") as client:
        issues = await client.list_issues("octocat", "Hello-World")

    assert page1_route.called
    assert page2_route.called
    # The pull request item (number 2) should be filtered out
    assert len(issues) == 2
    assert [issue.number for issue in issues] == [1, 3]


@pytest.mark.asyncio
@respx.mock
async def test_etag_304_caching() -> None:
    repo_url = "https://api.github.com/repos/octocat/Hello-World"
    etag_value = '"abc123xyz"'
    payload = {
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
        "owner": {"login": "octocat"},
        "default_branch": "main",
    }

    # First request returns 200 with ETag; second returns 304 Not Modified
    route = respx.get(repo_url)
    route.side_effect = [
        httpx.Response(200, json=payload, headers={"ETag": etag_value}),
        httpx.Response(304, headers={"ETag": etag_value}),
    ]

    async with GitHubClient(token="fake-token") as client:
        repo1 = await client.get_repo("octocat", "Hello-World")
        assert repo1.name == "Hello-World"

        repo2 = await client.get_repo("octocat", "Hello-World")
        assert repo2.name == "Hello-World"

    assert route.call_count == 2
    second_request = route.calls[1].request
    assert second_request.headers.get("if-none-match") == etag_value


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_exceeded_raises_error() -> None:
    repo_url = "https://api.github.com/repos/octocat/Hello-World"

    # Reset timestamp is 120s in future; client max wait is 10s
    future_reset = str(int(time.time()) + 120)
    respx.get(repo_url).mock(
        return_value=httpx.Response(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": future_reset,
            },
            json={"message": "API rate limit exceeded"},
        )
    )

    async with GitHubClient(token="fake-token", max_rate_limit_wait=10) as client:
        with pytest.raises(RateLimitExceededError) as exc_info:
            await client.get_repo("octocat", "Hello-World")

    assert "exceeds maximum" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_retries_when_wait_is_short() -> None:
    repo_url = "https://api.github.com/repos/octocat/Hello-World"
    now_reset = str(int(time.time()))

    payload = {
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
        "owner": {"login": "octocat"},
        "default_branch": "main",
    }

    route = respx.get(repo_url)
    route.side_effect = [
        httpx.Response(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": now_reset,
            },
            json={"message": "rate limit"},
        ),
        httpx.Response(200, json=payload),
    ]

    async with GitHubClient(token="fake-token", max_rate_limit_wait=5) as client:
        repo = await client.get_repo("octocat", "Hello-World")
        assert repo.name == "Hello-World"

    assert route.call_count == 2
