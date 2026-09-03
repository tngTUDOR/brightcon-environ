"""The local working copy of the watched repository."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .config import RepoConfig, ToolsConfig
from .runner import CommandError, LogFn, run

NULL_SHA = "0" * 40


class GitError(Exception):
    """Raised when a git operation cannot be completed."""


def parse_name_status(output: str) -> list[str]:
    """Extract paths from ``git diff --name-status --no-renames`` output."""
    paths: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        # Renames are disabled, so the path is always the last field.
        path = fields[-1]
        if path not in paths:
            paths.append(path)
    return paths


class GitRepo:
    """A checkout kept in lockstep with the branch we watch.

    The clone is treated as disposable: every sync hard-resets it, so local
    modifications are never preserved.
    """

    def __init__(
        self,
        repo: RepoConfig,
        tools: ToolsConfig,
        *,
        log: LogFn | None = None,
        timeout: float | None = 600,
    ) -> None:
        self.config = repo
        self.git = tools.git
        self.path = repo.path
        self.log = log
        self.timeout = timeout

    def _env(self) -> Mapping[str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if self.config.ssh_key:
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {self.config.ssh_key} -o IdentitiesOnly=yes -o BatchMode=yes"
            )
        return env

    def _git(self, *args: str, check: bool = True, cwd: Path | None = None):
        return run(
            [self.git, *args],
            log=self.log,
            cwd=cwd if cwd is not None else self.path,
            env=self._env(),
            timeout=self.timeout,
            check=check,
        )

    @property
    def exists(self) -> bool:
        return (self.path / ".git").is_dir()

    def ensure_clone(self) -> None:
        """Clone on first use; afterwards this is a no-op."""
        if self.exists:
            return
        if not self.config.url:
            raise GitError(
                f"no clone at {self.path} and repo.url is not configured; "
                "set repo.url or clone it manually"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run(
                [
                    self.git,
                    "clone",
                    "--branch",
                    self.config.branch,
                    self.config.url,
                    str(self.path),
                ],
                log=self.log,
                cwd=self.path.parent,
                env=self._env(),
                timeout=self.timeout,
            )
        except CommandError as exc:
            raise GitError(f"cloning {self.config.url} failed: {exc}") from exc

    def fetch(self) -> None:
        try:
            self._git("fetch", "--prune", "origin", self.config.branch)
        except CommandError as exc:
            raise GitError(f"fetching {self.config.branch} failed: {exc}") from exc

    def has_commit(self, sha: str) -> bool:
        if not sha or sha == NULL_SHA:
            return False
        result = self._git("cat-file", "-e", f"{sha}^{{commit}}", check=False)
        return result.returncode == 0

    def checkout(self, sha: str | None = None) -> str:
        """Hard-reset the working copy to ``sha`` (default: the fetched branch tip)."""
        target = sha if sha and sha != NULL_SHA else f"origin/{self.config.branch}"
        try:
            self._git("reset", "--hard", target)
            self._git("clean", "-ffdx")
        except CommandError as exc:
            raise GitError(f"checking out {target} failed: {exc}") from exc
        return self.head_sha()

    def head_sha(self) -> str:
        return self._git("rev-parse", "HEAD").output.strip()

    def changed_paths(self, before: str | None, after: str) -> list[str] | None:
        """Paths touched between two commits.

        Returns ``None`` when the range cannot be computed -- a brand new branch,
        a force push, or a first run against a fresh clone -- which the caller
        should treat as "consider every definition file".
        """
        if not before or not self.has_commit(before) or not self.has_commit(after):
            return None
        result = self._git("diff", "--name-status", "--no-renames", before, after)
        return parse_name_status(result.output)

    def blob_sha(self, relpath: str, ref: str = "HEAD") -> str | None:
        """Content hash of a tracked file, used to skip unchanged rebuilds."""
        result = self._git("rev-parse", f"{ref}:{relpath}", check=False)
        if result.returncode != 0:
            return None
        return result.output.strip() or None
