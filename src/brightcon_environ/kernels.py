"""Kernelspec registration.

Follows the TLJH convention: the kernelspec is installed from inside the target
environment into the shared user prefix, so every JupyterHub user sees it::

    <env>/bin/python -m ipykernel install --prefix /opt/tljh/user --name <name>
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import PathsConfig
from .runner import LogFn, run

KERNEL_TIMEOUT = 300


def kernel_dir(paths: PathsConfig, name: str) -> Path:
    return paths.kernel_dir / name


def install_kernel(
    env_path: Path,
    name: str,
    display_name: str,
    paths: PathsConfig,
    *,
    log: LogFn | None = None,
) -> Path:
    """Register ``env_path`` as a kernel named ``name`` in the shared prefix."""
    python = env_path / "bin" / "python"
    if not python.exists():
        raise FileNotFoundError(f"no interpreter at {python}")

    paths.kernel_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            python,
            "-m",
            "ipykernel",
            "install",
            "--prefix",
            str(paths.kernel_prefix),
            "--name",
            name,
            "--display-name",
            display_name,
        ],
        log=log,
        timeout=KERNEL_TIMEOUT,
    )

    target = kernel_dir(paths, name)
    # Users' single-user servers run as unprivileged accounts and must be able
    # to read the spec we just wrote as root.
    _make_world_readable(target)
    return target


def remove_kernel(name: str, paths: PathsConfig, *, log: LogFn | None = None) -> bool:
    """Delete a kernelspec. Returns whether anything was removed."""
    target = kernel_dir(paths, name)
    if not target.exists():
        return False
    if log:
        log(f"removing kernelspec {target}")
    shutil.rmtree(target)
    return True


def list_kernels(paths: PathsConfig) -> list[str]:
    if not paths.kernel_dir.is_dir():
        return []
    return sorted(child.name for child in paths.kernel_dir.iterdir() if child.is_dir())


def _make_world_readable(path: Path) -> None:
    if not path.exists():
        return
    path.chmod(0o755)
    for child in path.rglob("*"):
        child.chmod(0o755 if child.is_dir() else 0o644)
