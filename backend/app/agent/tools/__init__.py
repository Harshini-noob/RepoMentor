from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.tools.base import (
    MAX_OUTPUT_CHARS,
    Tool,
    ToolOutput,
    make_commit_evidence,
    make_dep_evidence,
    make_fact_evidence,
    make_file_evidence,
    make_pr_evidence,
)
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.get_overview import OverviewTool
from app.agent.tools.list_tree import ListTreeTool
from app.agent.tools.read_file import ReadFileTool
from app.agent.tools.search_code import SearchCodeTool
from app.agent.tools.get_dependencies import DependenciesTool
from app.agent.tools.get_facts import FactsTool
from app.agent.tools.get_dossier import DossierTool
from app.agent.tools.get_timeline import TimelineTool
from app.agent.tools.search_commits import SearchCommitsTool
from app.agent.tools.file_history import FileHistoryTool
from app.agent.tools.get_commit import GetCommitTool
from app.agent.tools.search_prs import SearchPRsTool
from app.agent.tools.get_pr import GetPRTool
from app.github.client import GitHubClient


def build_registry(
    db: AsyncIOMotorDatabase | None = None,
    github_client: GitHubClient | None = None,
) -> ToolRegistry:
    """
    Construct a new ToolRegistry instance and register all implemented tools.
    Allows injecting a database client and/or github_client (e.g. for testing).
    """
    registry = ToolRegistry()
    registry.register(OverviewTool(db=db))
    registry.register(ListTreeTool(db=db))
    registry.register(ReadFileTool(db=db))
    registry.register(SearchCodeTool(db=db))
    registry.register(DependenciesTool(db=db))
    registry.register(FactsTool(db=db))
    registry.register(DossierTool(db=db))
    registry.register(TimelineTool(db=db))
    registry.register(SearchCommitsTool(db=db))
    registry.register(FileHistoryTool(db=db))
    registry.register(GetCommitTool(db=db, github_client=github_client))
    registry.register(SearchPRsTool(db=db))
    registry.register(GetPRTool(db=db))
    return registry


__all__ = [
    "MAX_OUTPUT_CHARS",
    "Tool",
    "ToolOutput",
    "ToolRegistry",
    "build_registry",
    "OverviewTool",
    "ListTreeTool",
    "ReadFileTool",
    "SearchCodeTool",
    "DependenciesTool",
    "FactsTool",
    "DossierTool",
    "TimelineTool",
    "SearchCommitsTool",
    "FileHistoryTool",
    "GetCommitTool",
    "SearchPRsTool",
    "GetPRTool",
    "make_commit_evidence",
    "make_dep_evidence",
    "make_fact_evidence",
    "make_file_evidence",
    "make_pr_evidence",
]
