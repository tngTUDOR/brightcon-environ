from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from brightcon_environ.config import (
    Config,
    DefaultsConfig,
    PathsConfig,
    RepoConfig,
    ServerConfig,
    ToolsConfig,
)


@pytest.fixture
def defaults() -> DefaultsConfig:
    return DefaultsConfig()


@pytest.fixture
def tljh_root(tmp_path: Path) -> Path:
    """A throwaway copy of the /opt/tljh layout."""
    root = tmp_path / "tljh"
    (root / "user" / "envs").mkdir(parents=True)
    (root / "user" / "share" / "jupyter" / "kernels").mkdir(parents=True)
    (root / "environ" / "state").mkdir(parents=True)
    (root / "environ" / "logs").mkdir(parents=True)
    return root


@pytest.fixture
def config(tljh_root: Path, tmp_path: Path) -> Config:
    return Config(
        repo=RepoConfig(url="", branch="main", path=tmp_path / "repo"),
        paths=PathsConfig(
            env_root=tljh_root / "user" / "envs",
            kernel_prefix=tljh_root / "user",
            state_dir=tljh_root / "environ" / "state",
            log_dir=tljh_root / "environ" / "logs",
        ),
        tools=ToolsConfig(
            git=shutil.which("git") or "git",
            conda=shutil.which("mamba") or shutil.which("conda") or "mamba",
            uv=shutil.which("uv") or "uv",
        ),
        server=ServerConfig(host="127.0.0.1", port=0),
        defaults=DefaultsConfig(),
    )


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--initial-branch", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    # The developer's global config may require signed commits.
    git(path, "config", "commit.gpgsign", "false")
    git(path, "commit", "--allow-empty", "-m", "root")
    return path


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """A git repository holding environment definitions."""
    repo = init_repo(tmp_path / "source")
    (repo / "requirements-demo.txt").write_text(
        "# python: 3.12\n# display-name: Demo Environment\n\npackaging\n",
        encoding="utf-8",
    )
    (repo / "environment-course.yml").write_text(
        "name: course\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.12\n",
        encoding="utf-8",
    )
    commit_all(repo, "add definitions")
    return repo
