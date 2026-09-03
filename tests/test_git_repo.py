from __future__ import annotations

from pathlib import Path

from brightcon_environ.config import Config
from brightcon_environ.git_repo import NULL_SHA, GitRepo, parse_name_status

from .conftest import commit_all, git


def test_parse_name_status_extracts_paths():
    output = "M\trequirements-demo.txt\nA\tnested/environment-course.yml\nD\told.txt\n"
    assert parse_name_status(output) == [
        "requirements-demo.txt",
        "nested/environment-course.yml",
        "old.txt",
    ]


def test_parse_name_status_ignores_noise():
    assert parse_name_status("\n  \nM\n") == []


def _repo(config: Config, source: Path) -> GitRepo:
    repo = GitRepo(config.repo, config.tools)
    repo.path = source
    return repo


def test_changed_paths_between_two_commits(config: Config, source_repo: Path):
    repo = _repo(config, source_repo)
    before = repo.head_sha()
    (source_repo / "requirements-extra.txt").write_text("rich\n", encoding="utf-8")
    (source_repo / "environment-course.yml").unlink()
    after = commit_all(source_repo, "add one, drop one")

    assert sorted(repo.changed_paths(before, after)) == [
        "environment-course.yml",
        "requirements-extra.txt",
    ]


def test_changed_paths_is_none_for_an_unknown_range(config: Config, source_repo: Path):
    repo = _repo(config, source_repo)
    head = repo.head_sha()
    assert repo.changed_paths(NULL_SHA, head) is None
    assert repo.changed_paths("f" * 40, head) is None
    assert repo.changed_paths(None, head) is None


def test_blob_sha_tracks_content(config: Config, source_repo: Path):
    repo = _repo(config, source_repo)
    first = repo.blob_sha("requirements-demo.txt")
    assert first is not None
    assert repo.blob_sha("does-not-exist.txt") is None

    (source_repo / "requirements-demo.txt").write_text(
        "packaging\nrich\n", encoding="utf-8"
    )
    commit_all(source_repo, "add a dependency")
    assert repo.blob_sha("requirements-demo.txt") != first


def test_checkout_resets_local_edits(config: Config, source_repo: Path):
    repo = _repo(config, source_repo)
    head = repo.head_sha()
    (source_repo / "requirements-demo.txt").write_text("tampered\n", encoding="utf-8")
    repo.checkout(head)
    assert "tampered" not in (source_repo / "requirements-demo.txt").read_text(
        encoding="utf-8"
    )


def test_clone_on_first_use(config: Config, source_repo: Path, tmp_path: Path):
    target = tmp_path / "clone"
    repo = GitRepo(
        type(config.repo)(url=str(source_repo), branch="main", path=target),
        config.tools,
    )
    assert not repo.exists
    repo.ensure_clone()
    assert repo.exists
    assert (target / "requirements-demo.txt").is_file()
    assert git(target, "rev-parse", "HEAD")
