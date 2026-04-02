"""Git operations via GitPython."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from git import Actor, InvalidGitRepositoryError, Repo
from git.exc import GitCommandError

from app.core.config import Settings, get_settings
from app.models.request_models import GitRequest, GitResponse


class GitManager:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    def run(self, req: GitRequest) -> GitResponse:
        path = Path(req.repo_path).expanduser().resolve()
        try:
            repo = Repo(path, search_parent_directories=True)
        except InvalidGitRepositoryError:
            return GitResponse(
                success=False,
                operation=req.operation,
                message="Not a valid git repository",
            )

        author = Actor(
            self._settings.git_author_name,
            self._settings.git_author_email,
        )

        try:
            if req.operation == "commit":
                return self._commit(repo, req, author)
            if req.operation == "branch":
                return self._branch(repo, req)
            if req.operation == "diff":
                return self._diff(repo, req)
            if req.operation == "log":
                return self._log(repo, req)
        except GitCommandError as e:
            return GitResponse(
                success=False,
                operation=req.operation,
                message=str(e),
            )

        return GitResponse(success=False, operation=req.operation, message="Unknown operation")

    def _commit(self, repo: Repo, req: GitRequest, author: Actor) -> GitResponse:
        if not req.message:
            return GitResponse(
                success=False,
                operation="commit",
                message="commit requires 'message'",
            )
        repo.git.add("--all")
        commit = repo.index.commit(req.message, author=author)
        return GitResponse(
            success=True,
            operation="commit",
            data={"hexsha": commit.hexsha, "summary": commit.summary},
        )

    def _branch(self, repo: Repo, req: GitRequest) -> GitResponse:
        if req.branch_name:
            head = repo.active_branch
            if req.branch_name in [b.name for b in repo.branches]:
                repo.git.checkout(req.branch_name)
            else:
                repo.git.checkout("-b", req.branch_name)
            return GitResponse(
                success=True,
                operation="branch",
                data={"checked_out": req.branch_name, "previous": head.name},
            )

        branches = [b.name for b in repo.branches]
        return GitResponse(
            success=True,
            operation="branch",
            data={"branches": branches, "active": repo.active_branch.name},
        )

    def _diff(self, repo: Repo, req: GitRequest) -> GitResponse:
        diff = repo.git.diff()
        return GitResponse(
            success=True,
            operation="diff",
            data={"diff": diff},
        )

    def _log(self, repo: Repo, req: GitRequest) -> GitResponse:
        entries = []
        for commit in repo.iter_commits(max_count=req.max_log_entries):
            entries.append(
                {
                    "hexsha": commit.hexsha,
                    "summary": commit.summary,
                    "author": f"{commit.author.name} <{commit.author.email}>",
                    "committed_datetime": commit.committed_datetime.isoformat(),
                }
            )
        return GitResponse(success=True, operation="log", data={"commits": entries})
