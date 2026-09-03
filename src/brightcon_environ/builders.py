"""Create and destroy environments under a single environment root.

All three backends produce the same shape -- a directory with ``bin/python`` at
``<env_root>/<name>`` -- so kernel registration is identical for every one of
them. Environments are always built with an explicit prefix, never with
``conda create -n``, so the result never depends on ``envs_dirs`` or ``.condarc``.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .discovery import Backend, DiscoveryError, EnvSpec, validate_name
from .kernels import install_kernel, remove_kernel
from .runner import CommandError, LogFn, run


class BuildError(Exception):
    """Raised when an environment cannot be created or removed."""


@dataclass(frozen=True)
class BuildResult:
    name: str
    backend: Backend
    definition: str
    env_path: Path
    kernel_path: Path
    duration_seconds: float


def env_path(config: Config, name: str) -> Path:
    """Resolve ``<env_root>/<name>``, refusing anything outside the root.

    This is the guard that makes the later ``rmtree`` safe: the name is
    validated, the result must be a direct child of the real environment root,
    and the entry itself must not be a symlink pointing elsewhere.
    """
    try:
        validate_name(name, origin="environment")
    except DiscoveryError as exc:
        raise BuildError(str(exc)) from exc

    root = Path(os.path.realpath(config.paths.env_root))
    candidate = root / name
    resolved = Path(os.path.realpath(candidate))

    if resolved.parent != root or resolved.name != name:
        raise BuildError(
            f"refusing to touch {candidate}: it resolves to {resolved}, "
            f"which is not a direct child of {root}"
        )
    return candidate


def _force_writable(func, path, _exc) -> None:
    """rmtree error handler: clear read-only bits and retry once."""
    os.chmod(
        path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )
    func(path)


def destroy(config: Config, name: str, *, log: LogFn | None = None) -> bool:
    """Remove an environment and its kernelspec. Returns whether either existed."""
    target = env_path(config, name)
    removed = False

    if target.is_symlink():
        raise BuildError(f"refusing to remove {target}: it is a symlink")

    if target.exists():
        if log:
            log(f"removing environment {target}")
        shutil.rmtree(target, onexc=_force_writable)
        removed = True

    if remove_kernel(name, config.paths, log=log):
        removed = True

    return removed


def _conda_create(
    config: Config, spec: EnvSpec, repo_root: Path, target: Path, log: LogFn | None
):
    run(
        [
            config.tools.conda,
            "env",
            "create",
            "--yes",
            "--file",
            str(repo_root / spec.path),
            "--prefix",
            str(target),
        ],
        log=log,
        timeout=config.defaults.timeout_seconds,
    )
    # The definition may not list ipykernel, but a kernel is the whole point.
    run(
        [
            config.tools.conda,
            "install",
            "--yes",
            "--prefix",
            str(target),
            *_channel_args(config),
            "ipykernel",
        ],
        log=log,
        timeout=config.defaults.timeout_seconds,
    )


def _channel_args(config: Config) -> list[str]:
    args: list[str] = []
    for channel in config.defaults.conda_channels:
        args += ["--channel", channel]
    return args


def _uv_venv(
    config: Config, python: str | None, target: Path, log: LogFn | None
) -> None:
    # --no-project keeps uv from adopting a pyproject.toml it finds by walking up
    # from the working directory, whose requires-python would fight with ours.
    args = [config.tools.uv, "venv", "--no-project"]
    if python:
        args += ["--python", python]
    args.append(str(target))
    run(
        args,
        log=log,
        cwd=config.paths.env_root,
        timeout=config.defaults.timeout_seconds,
    )


def _uv_install(
    config: Config,
    target: Path,
    args: list[str],
    log: LogFn | None,
    *,
    sync: bool = False,
) -> None:
    run(
        [
            config.tools.uv,
            "pip",
            "sync" if sync else "install",
            "--python",
            str(target / "bin" / "python"),
            *args,
        ],
        log=log,
        cwd=config.paths.env_root,
        timeout=config.defaults.timeout_seconds,
    )


def _pip_venv(
    config: Config, python: str | None, target: Path, log: LogFn | None
) -> None:
    interpreter = shutil.which(f"python{python}") if python else None
    interpreter = interpreter or shutil.which("python3")
    if interpreter is None:
        raise BuildError(f"no python{python or '3'} interpreter on PATH for {target}")
    run(
        [interpreter, "-m", "venv", str(target)],
        log=log,
        timeout=config.defaults.timeout_seconds,
    )


def _pip_install(
    config: Config, target: Path, args: list[str], log: LogFn | None
) -> None:
    run(
        [str(target / "bin" / "python"), "-m", "pip", "install", *args],
        log=log,
        timeout=config.defaults.timeout_seconds,
    )


def _venv_create(
    config: Config, spec: EnvSpec, repo_root: Path, target: Path, log: LogFn | None
):
    use_uv = config.defaults.installer == "uv"
    source = repo_root / (spec.lock_path or spec.path)

    if use_uv:
        _uv_venv(config, spec.python, target, log)
        _uv_install(
            config,
            target,
            ["--requirements", str(source)],
            log,
            sync=spec.lock_path is not None,
        )
        _uv_install(config, target, ["ipykernel"], log)
    else:
        _pip_venv(config, spec.python, target, log)
        _pip_install(config, target, ["--requirement", str(source)], log)
        _pip_install(config, target, ["ipykernel"], log)


def _uv_project_create(
    config: Config, spec: EnvSpec, repo_root: Path, target: Path, log: LogFn | None
):
    _uv_venv(config, spec.python, target, log)
    # uv recognises project metadata by filename, so hand it a real pyproject.toml.
    with tempfile.TemporaryDirectory(prefix="environ-pyproject-") as tmp:
        staged = Path(tmp) / "pyproject.toml"
        shutil.copyfile(repo_root / spec.path, staged)
        _uv_install(config, target, ["--requirements", str(staged)], log)
    _uv_install(config, target, ["ipykernel"], log)


def build(
    config: Config, spec: EnvSpec, repo_root: Path, *, log: LogFn | None = None
) -> BuildResult:
    """Tear down any existing environment for ``spec`` and create it afresh."""
    started = time.monotonic()
    target = env_path(config, spec.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    if log:
        log(f"=== {spec.name} ({spec.backend}) from {spec.path}")

    destroy(config, spec.name, log=log)

    try:
        match spec.backend:
            case Backend.CONDA:
                _conda_create(config, spec, repo_root, target, log)
            case Backend.VENV:
                _venv_create(config, spec, repo_root, target, log)
            case Backend.UV_PROJECT:
                _uv_project_create(config, spec, repo_root, target, log)
    except CommandError as exc:
        # A half-built environment is worse than none: it would register a
        # broken kernel on the next run.
        destroy(config, spec.name, log=log)
        raise BuildError(f"building {spec.name} failed: {exc}") from exc

    try:
        kernel_path = install_kernel(
            target, spec.name, spec.kernel_display_name, config.paths, log=log
        )
    except (CommandError, OSError) as exc:
        destroy(config, spec.name, log=log)
        raise BuildError(
            f"registering the kernel for {spec.name} failed: {exc}"
        ) from exc

    return BuildResult(
        name=spec.name,
        backend=spec.backend,
        definition=spec.path,
        env_path=target,
        kernel_path=kernel_path,
        duration_seconds=round(time.monotonic() - started, 1),
    )


def list_environments(config: Config) -> list[str]:
    root = config.paths.env_root
    if not root.is_dir():
        return []
    return sorted(
        child.name for child in root.iterdir() if (child / "bin" / "python").exists()
    )
