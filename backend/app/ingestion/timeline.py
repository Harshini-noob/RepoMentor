import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.db import get_db

logger = logging.getLogger("repomentor.ingestion.timeline")


def _iso_to_dt(date_str: str | None) -> datetime | None:
    """Parse an ISO-8601 string into an aware datetime, return None on failure."""
    if not date_str:
        return None
    try:
        # Python 3.11+ handles Z natively; strip trailing Z for 3.10 compat
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _week_start(dt: datetime) -> datetime:
    """Return midnight Monday of the ISO week containing dt (UTC)."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _manifest_filenames() -> frozenset[str]:
    """Return the set of manifest file basenames that parsers recognise."""
    return frozenset(
        {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        }
    )


async def build_timeline(
    project_id: str,
    db: AsyncIOMotorDatabase | None = None,
    ingest_cap: int = 500,
) -> dict[str, Any]:
    """
    Compute a timeline document from the commits collection for project_id.

    Produces:
      - weekly_buckets   : list of {week_start, commit_count, additions, deletions, top_files}
      - biggest_commits  : 10 commits sorted by additions + deletions descending
      - dependency_first_seen : {manifest_basename: earliest_commit_sha, date}
      - dir_first_seen   : {top_level_dir: earliest_commit_sha, date}
      - ingest_cap       : cap used during ingestion
      - history_complete : True only if the total commits ingested < ingest_cap
    """
    if db is None:
        db = get_db()

    logger.info("[%s] Building timeline", project_id)

    commits: list[dict[str, Any]] = await db.commits.find(
        {"project_id": project_id}
    ).to_list(length=None)

    total_commits = len(commits)
    history_complete = total_commits < ingest_cap

    if not commits:
        doc: dict[str, Any] = {
            "project_id": project_id,
            "weekly_buckets": [],
            "biggest_commits": [],
            "dependency_first_seen": {},
            "dir_first_seen": {},
            "ingest_cap": ingest_cap,
            "history_complete": history_complete,
        }
        await db.timeline.replace_one({"project_id": project_id}, doc, upsert=True)
        return doc

    # -----------------------------------------------------------------------
    # Weekly buckets
    # -----------------------------------------------------------------------
    # bucket key -> {commit_count, additions, deletions, file_counts}
    weekly: dict[str, dict[str, Any]] = {}

    for commit in commits:
        dt = _iso_to_dt(commit.get("date"))
        if not dt:
            continue
        wk = _week_start(dt)
        key = wk.isoformat()

        if key not in weekly:
            weekly[key] = {
                "week_start": key,
                "commit_count": 0,
                "additions": 0,
                "deletions": 0,
                "_file_counts": defaultdict(int),  # filename -> touches
            }

        bucket = weekly[key]
        bucket["commit_count"] += 1
        bucket["additions"] += commit.get("additions", 0) or 0
        bucket["deletions"] += commit.get("deletions", 0) or 0

        for fc in commit.get("files_changed", []) or []:
            fname = fc.get("filename") if isinstance(fc, dict) else None
            if fname:
                bucket["_file_counts"][fname] += 1

    weekly_buckets = []
    for key in sorted(weekly):
        bucket = weekly[key]
        top_files = sorted(
            bucket["_file_counts"].items(), key=lambda x: x[1], reverse=True
        )[:5]
        weekly_buckets.append(
            {
                "week_start": bucket["week_start"],
                "commit_count": bucket["commit_count"],
                "additions": bucket["additions"],
                "deletions": bucket["deletions"],
                "top_files": [{"file": f, "touches": c} for f, c in top_files],
            }
        )

    # -----------------------------------------------------------------------
    # 10 biggest commits by churn (additions + deletions)
    # -----------------------------------------------------------------------
    biggest = sorted(
        commits,
        key=lambda c: (c.get("additions", 0) or 0) + (c.get("deletions", 0) or 0),
        reverse=True,
    )[:10]
    biggest_commits = [
        {
            "sha": c["sha"],
            "date": c.get("date"),
            "author": c.get("author"),
            "message": (c.get("message") or "")[:120],
            "additions": c.get("additions", 0),
            "deletions": c.get("deletions", 0),
        }
        for c in biggest
    ]

    # -----------------------------------------------------------------------
    # First-seen per manifest file and per top-level directory
    # -----------------------------------------------------------------------
    manifests = _manifest_filenames()
    dep_first: dict[str, dict[str, str]] = {}  # basename -> {sha, date}
    dir_first: dict[str, dict[str, str]] = {}  # dir -> {sha, date}

    # Sort chronologically (oldest first) so the first match wins
    commits_by_date = sorted(
        commits, key=lambda c: _iso_to_dt(c.get("date")) or datetime.min.replace(tzinfo=timezone.utc)
    )

    for commit in commits_by_date:
        sha = commit.get("sha", "")
        date = commit.get("date", "")
        for fc in commit.get("files_changed", []) or []:
            filepath = fc.get("filename") if isinstance(fc, dict) else None
            if not filepath:
                continue

            # Dependency first-seen
            basename = filepath.split("/")[-1].lower()
            if basename in manifests and basename not in dep_first:
                dep_first[basename] = {"sha": sha, "date": date}

            # Top-level directory first-seen
            parts = filepath.split("/")
            top_dir = parts[0] if len(parts) > 1 else "."
            if top_dir not in dir_first:
                dir_first[top_dir] = {"sha": sha, "date": date}

    doc = {
        "project_id": project_id,
        "weekly_buckets": weekly_buckets,
        "biggest_commits": biggest_commits,
        "dependency_first_seen": dep_first,
        "dir_first_seen": dir_first,
        "ingest_cap": ingest_cap,
        # history_complete=True means we received fewer commits than the cap,
        # so we likely saw all history. False means older commits may be missing.
        "history_complete": history_complete,
    }

    await db.timeline.replace_one({"project_id": project_id}, doc, upsert=True)
    logger.info(
        "[%s] Timeline stored: %d weeks, %d biggest commits, history_complete=%s",
        project_id,
        len(weekly_buckets),
        len(biggest_commits),
        history_complete,
    )
    return doc
