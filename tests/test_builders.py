from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from brightcon_environ.builders import (
    BuildError,
    BuildResult,
    build,
    destroy,
    env_path,
    list_environments,
)
from brightcon_environ.config import Config
from brightcon_environ.discovery import DiscoveryError, spec_from_file
from brightcon_environ.jobs import EnvRecord, Job, JobLog, StateStore, run_job

from .conftest import commit_all, git


def make_env(config: Config, name: str) -> Path:
    target = config.paths.env_root / name
    (target / "bin").mkdir(parents=True, exist_ok=True)
    (target / "bin" / "python").touch()
    return target


def make_kernel(config: Config, name: str) -> Path:
    target = config.paths.kernel_dir / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "kernel.json").write_text("{}", encoding="utf-8")
    return target


def test_env_path_is_a_direct_child_of_the_root(config: Config):
    assert env_path(config, "demo") == config.paths.env_root / "demo"


@pytest.mark.parametrize("name", ["../outside", "nested/demo", "..", "Demo"])
def test_env_path_refuses_names_that_could_escape(config: Config, name: str):
    with pytest.raises((BuildError, DiscoveryError)):
        env_path(config, name)


def test_env_path_refuses_a_symlink_pointing_out_of_the_root(
    config: Config, tmp_path: Path
):
    outside = tmp_path / "precious"
    outside.mkdir()
    (config.paths.env_root / "demo").symlink_to(outside)
    with pytest.raises(BuildError, match="not a direct child"):
        env_path(config, "demo")


def test_destroy_removes_the_environment_and_its_kernel(config: Config):
    make_env(config, "demo")
    make_kernel(config, "demo")

    assert destroy(config, "demo") is True
    assert not (config.paths.env_root / "demo").exists()
    assert not (config.paths.kernel_dir / "demo").exists()


def test_destroy_is_a_no_op_when_nothing_exists(config: Config):
    assert destroy(config, "demo") is False


def test_destroy_removes_a_kernel_whose_environment_is_gone(config: Config):
    make_kernel(config, "orphan")
    assert destroy(config, "orphan") is True


def test_list_environments_only_counts_real_ones(config: Config):
    make_env(config, "real")
    (config.paths.env_root / "empty").mkdir()
    assert list_environments(config) == ["real"]


def test_a_failed_build_leaves_nothing_behind(config: Config, source_repo: Path):
    broken = dataclasses.replace(
        config, tools=dataclasses.replace(config.tools, uv="/nonexistent/uv")
    )
    spec = spec_from_file(source_repo, "requirements-demo.txt", broken.defaults)
    with pytest.raises((BuildError, FileNotFoundError)):
        build(broken, spec, source_repo)
    assert not (broken.paths.env_root / "demo").exists()


def test_run_job_skips_an_unchanged_environment(
    config: Config, source_repo: Path, monkeypatch
):
    """The second run must not rebuild when the definition's blob is unchanged."""
    calls: list[str] = []

    def fake_build(cfg, spec, repo_root, *, log=None):
        calls.append(spec.name)
        make_env(cfg, spec.name)
        make_kernel(cfg, spec.name)
        return BuildResult(
            name=spec.name,
            backend=spec.backend,
            definition=spec.path,
            env_path=cfg.paths.env_root / spec.name,
            kernel_path=cfg.paths.kernel_dir / spec.name,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("brightcon_environ.jobs.build", fake_build)
    monkeypatch.setattr(
        "brightcon_environ.jobs.GitRepo.ensure_clone", lambda self: None
    )
    monkeypatch.setattr("brightcon_environ.jobs.GitRepo.fetch", lambda self: None)
    monkeypatch.setattr(
        "brightcon_environ.jobs.GitRepo.checkout",
        lambda self, sha=None: self.head_sha(),
    )
    config.repo.path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_repo, config.repo.path)

    log = JobLog(None)
    first = run_job(config, Job(trigger="cli"), log)
    assert sorted(first.built) == ["course", "demo"]
    assert first.errors == []

    second = run_job(config, Job(trigger="cli"), JobLog(None))
    assert second.built == []
    assert sorted(second.skipped) == ["course", "demo"]
    assert calls == ["course", "demo"] or calls == ["demo", "course"]

    # Touching one definition rebuilds only that environment.
    (config.repo.path / "requirements-demo.txt").write_text("rich\n", encoding="utf-8")
    commit_all(config.repo.path, "change demo")
    third = run_job(config, Job(trigger="cli"), JobLog(None))
    assert third.errors == []
    assert third.built == ["demo"]
    assert third.skipped == ["course"]


def test_run_job_removes_a_deleted_definition(
    config: Config, source_repo: Path, monkeypatch
):
    monkeypatch.setattr(
        "brightcon_environ.jobs.GitRepo.ensure_clone", lambda self: None
    )
    monkeypatch.setattr("brightcon_environ.jobs.GitRepo.fetch", lambda self: None)
    monkeypatch.setattr(
        "brightcon_environ.jobs.GitRepo.checkout",
        lambda self, sha=None: self.head_sha(),
    )
    config.repo.path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_repo, config.repo.path)

    state = StateStore(config.paths.state_file)
    state.put(
        EnvRecord(
            name="demo",
            backend="venv",
            definition="requirements-demo.txt",
            display_name="Demo",
        )
    )
    state.save()
    make_env(config, "demo")
    make_kernel(config, "demo")

    head_before = git(config.repo.path, "rev-parse", "HEAD")
    (config.repo.path / "requirements-demo.txt").unlink()
    commit_all(config.repo.path, "drop demo")

    job = run_job(config, Job(trigger="cli", before=head_before), JobLog(None))
    assert job.removed == ["demo"]
    assert not (config.paths.env_root / "demo").exists()
    assert not (config.paths.kernel_dir / "demo").exists()


@pytest.mark.slow
def test_building_a_real_venv_registers_a_kernel(config: Config, tmp_path: Path):
    """End to end against the real uv: create a venv and register its kernel."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")

    repo = tmp_path / "definitions"
    repo.mkdir()
    (repo / "requirements-smoke.txt").write_text(
        "# python: 3.12\n# display-name: Smoke Test\n", encoding="utf-8"
    )

    spec = spec_from_file(repo, "requirements-smoke.txt", config.defaults)
    result = build(config, spec, repo)

    assert (result.env_path / "bin" / "python").exists()
    assert (result.kernel_path / "kernel.json").is_file()
    assert list_environments(config) == ["smoke"]

    destroy(config, "smoke")
    assert list_environments(config) == []
