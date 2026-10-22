import asyncio
import base64
import re
import time
from typing import Any, AsyncIterator
import httpx

from app.config import get_settings
from app.github.models import (
    CommitInfo,
    FileContent,
    IssueInfo,
    PullComment,
    PullFile,
    PullRequestInfo,
    RepoInfo,
    TreeItem,
)


class GitHubError(Exception):
    """Base exception for GitHub API errors."""
    pass


class RateLimitExceededError(GitHubError):
    """Raised when GitHub rate limit is exceeded and reset wait exceeds maximum allowed."""
    pass


def _extract_next_url(link_header: str | None) -> str | None:
    """Extract URL marked with rel='next' from RFC 5988 Link header."""
    if not link_header:
        return None
    for url, rel in re.findall(r'<([^>]+)>;\s*rel="([^"]+)"', link_header):
        if rel == "next":
            return url
    return None


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        max_rate_limit_wait: int = 60,
    ) -> None:
        settings = get_settings()
        self.token = token if token is not None else settings.github_token
        self.base_url = base_url.rstrip("/")
        self.max_rate_limit_wait = max_rate_limit_wait

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=10.0,
        )
        # In-memory ETag cache mapping request url -> (etag, response_body)
        self._cache: dict[str, tuple[str, Any]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def _handle_rate_limit(self, response: httpx.Response) -> bool:
        """
        Inspect rate limit headers.
        If remaining is 0, sleep if within cap and return True to signal retry;
        otherwise raise RateLimitExceededError.
        """
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            reset_str = response.headers.get("X-RateLimit-Reset")
            reset_timestamp = int(reset_str) if reset_str else int(time.time())
            wait_seconds = max(0, int(reset_timestamp - time.time()))

            if wait_seconds > self.max_rate_limit_wait:
                raise RateLimitExceededError(
                    f"Rate limit exceeded. Reset wait of {wait_seconds}s exceeds maximum of {self.max_rate_limit_wait}s."
                )
            await asyncio.sleep(wait_seconds)
            return True
        return False

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, httpx.Headers]:
        """
        Execute an HTTP request with ETag caching and rate limit handling.
        Returns a tuple of (data, response_headers).
        """
        # Resolve full URL string to use as cache key
        built_req = self._client.build_request(method, url, params=params)
        cache_key = str(built_req.url)

        req_headers: dict[str, str] = {}
        cached_entry = self._cache.get(cache_key)
        if cached_entry:
            req_headers["If-None-Match"] = cached_entry[0]

        while True:
            response = await self._client.request(
                method,
                url,
                params=params,
                headers=req_headers,
            )

            # Check rate limiting
            should_retry = await self._handle_rate_limit(response)
            if should_retry:
                continue

            # Check 304 Not Modified
            if response.status_code == 304 and cached_entry:
                return cached_entry[1], response.headers

            if response.is_error:
                raise GitHubError(
                    f"GitHub API error {response.status_code} for {url}: {response.text}"
                )

            data = response.json()
            etag = response.headers.get("ETag")
            if etag:
                self._cache[cache_key] = (etag, data)

            return data, response.headers

    async def paginate(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Generic paginated GET that follows RFC 5988 Link headers and yields items.
        """
        next_url: str | None = url
        current_params = params

        while next_url:
            data, headers = await self._request("GET", next_url, params=current_params)
            if isinstance(data, list):
                for item in data:
                    yield item
            else:
                yield data

            # Subsequent pagination URLs already include query params
            current_params = None
            next_url = _extract_next_url(headers.get("Link"))

    async def get_repo(self, owner: str, repo: str) -> RepoInfo:
        data, _ = await self._request("GET", f"/repos/{owner}/{repo}")
        return RepoInfo(
            name=data["name"],
            full_name=data["full_name"],
            owner=data["owner"]["login"] if isinstance(data.get("owner"), dict) else str(data.get("owner")),
            default_branch=data.get("default_branch", "main"),
            description=data.get("description"),
            raw=data,
        )

    async def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        data, _ = await self._request("GET", f"/repos/{owner}/{repo}/languages")
        return data

    async def get_tree(
        self,
        owner: str,
        repo: str,
        sha: str,
        recursive: bool = True,
    ) -> list[TreeItem]:
        params = {"recursive": "1"} if recursive else None
        data, _ = await self._request("GET", f"/repos/{owner}/{repo}/git/trees/{sha}", params=params)
        tree_items = data.get("tree", [])
        return [
            TreeItem(
                path=item["path"],
                mode=item["mode"],
                type=item["type"],
                sha=item["sha"],
                size=item.get("size"),
                raw=item,
            )
            for item in tree_items
        ]

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> FileContent:
        params = {"ref": ref} if ref else None
        data, _ = await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)

        decoded_text: str | None = None
        if data.get("encoding") == "base64" and data.get("content"):
            try:
                decoded_text = base64.b64decode(data["content"]).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                decoded_text = None

        return FileContent(
            path=data["path"],
            sha=data["sha"],
            size=data["size"],
            content=data.get("content"),
            encoding=data.get("encoding"),
            decoded_text=decoded_text,
            raw=data,
        )

    async def list_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[CommitInfo]:
        params: dict[str, Any] = {}
        if since:
            params["since"] = since

        commits: list[CommitInfo] = []
        async for item in self.paginate(f"/repos/{owner}/{repo}/commits", params=params):
            commit_data = item.get("commit", {})
            author_data = commit_data.get("author", {})
            commits.append(
                CommitInfo(
                    sha=item["sha"],
                    message=commit_data.get("message", ""),
                    author_name=author_data.get("name"),
                    author_email=author_data.get("email"),
                    date=author_data.get("date"),
                    raw=item,
                )
            )
            if limit and len(commits) >= limit:
                break
        return commits

    async def get_commit(self, owner: str, repo: str, sha: str) -> CommitInfo:
        data, _ = await self._request("GET", f"/repos/{owner}/{repo}/commits/{sha}")
        commit_data = data.get("commit", {})
        author_data = commit_data.get("author", {})
        return CommitInfo(
            sha=data["sha"],
            message=commit_data.get("message", ""),
            author_name=author_data.get("name"),
            author_email=author_data.get("email"),
            date=author_data.get("date"),
            raw=data,
        )

    async def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        limit: int | None = None,
    ) -> list[PullRequestInfo]:
        pulls: list[PullRequestInfo] = []
        async for item in self.paginate(f"/repos/{owner}/{repo}/pulls", params={"state": state}):
            user = item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else None
            pulls.append(
                PullRequestInfo(
                    number=item["number"],
                    title=item["title"],
                    state=item["state"],
                    user=user,
                    created_at=item.get("created_at"),
                    merged_at=item.get("merged_at"),
                    raw=item,
                )
            )
            if limit and len(pulls) >= limit:
                break
        return pulls

    async def get_pull_files(self, owner: str, repo: str, n: int) -> list[PullFile]:
        files: list[PullFile] = []
        async for item in self.paginate(f"/repos/{owner}/{repo}/pulls/{n}/files"):
            files.append(
                PullFile(
                    filename=item["filename"],
                    status=item["status"],
                    additions=item.get("additions", 0),
                    deletions=item.get("deletions", 0),
                    changes=item.get("changes", 0),
                    raw=item,
                )
            )
        return files

    async def get_pull_comments(self, owner: str, repo: str, n: int) -> list[PullComment]:
        comments: list[PullComment] = []
        async for item in self.paginate(f"/repos/{owner}/{repo}/pulls/{n}/comments"):
            user = item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else None
            comments.append(
                PullComment(
                    id=item["id"],
                    user=user,
                    body=item.get("body"),
                    created_at=item.get("created_at"),
                    raw=item,
                )
            )
        return comments

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        limit: int | None = None,
    ) -> list[IssueInfo]:
        """
        Fetch repository issues, filtering out pull requests.
        GitHub includes pull requests in GET /repos/{owner}/{repo}/issues.
        """
        issues: list[IssueInfo] = []
        async for item in self.paginate(f"/repos/{owner}/{repo}/issues", params={"state": state}):
            # Filter out items that contain the pull_request key
            if "pull_request" in item:
                continue

            user = item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else None
            issues.append(
                IssueInfo(
                    number=item["number"],
                    title=item["title"],
                    state=item["state"],
                    user=user,
                    created_at=item.get("created_at"),
                    body=item.get("body"),
                    raw=item,
                )
            )
            if limit and len(issues) >= limit:
                break
        return issues
