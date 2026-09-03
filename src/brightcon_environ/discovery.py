"""Map environment definition files to environment names.

The naming convention is entirely filename driven, so that pip and uv
requirement lists -- which have no place to record a name -- get one anyway:

    environment-<name>.yml / .yaml   mamba/conda   (an inner ``name:`` key wins)
    requirements-<name>.txt          venv built with uv (or pip)
    requirements-<name>.lock         optional pin set for the same <name>
    pyproject-<name>.toml            uv, installed from the project metadata

Two optional header comments, inert to both pip and uv, carry what a filename
cannot::

    # python: 3.12
    # display-name: Brightcon 2026 Basic
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import yaml

from .config import DefaultsConfig

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PYTHON_RE = re.compile(r"^\d+(\.\d+){0,2}$")
_VERSION_IN_SPECIFIER_RE = re.compile(r"(\d+\.\d+)")
_HEADER_RE = re.compile(
    r"^#\s*(?P<key>[a-z][a-z-]*)\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE
)

# Names that would collide with the TLJH user environment itself or with the
# kernelspec that ships in it.
RESERVED_NAMES = frozenset({"user", "hub", "base", "root", "python3", "envs", "share"})

_CONDA_NAMED_RE = re.compile(r"^environment-(?P<name>.+)\.ya?ml$")
_CONDA_BARE_RE = re.compile(r"^environment\.ya?ml$")
_REQUIREMENTS_RE = re.compile(r"^requirements-(?P<name>.+)\.txt$")
_LOCK_RE = re.compile(r"^requirements-(?P<name>.+)\.lock$")
_PYPROJECT_RE = re.compile(r"^pyproject-(?P<name>.+)\.toml$")


class DiscoveryError(Exception):
    """Raised when a file looks like a definition but cannot be used."""


class Backend(StrEnum):
    CONDA = "conda"
    VENV = "venv"
    UV_PROJECT = "uv-project"


@dataclass(frozen=True)
class EnvSpec:
    """A single environment to build, resolved from one definition file."""

    name: str
    backend: Backend
    path: str
    """Definition file, relative to the repository root."""
    python: str | None = None
    display_name: str | None = None
    lock_path: str | None = None

    @property
    def kernel_display_name(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class DiscoveryProblem:
    path: str
    reason: str


@dataclass(frozen=True)
class _Match:
    backend: Backend
    name: str | None
    """``None`` for a bare ``environment.yml``, whose name must come from inside."""
    is_lock: bool = False


def validate_name(name: str, *, origin: str) -> str:
    """Reject anything that could escape the environment root or shadow TLJH."""
    if not NAME_RE.fullmatch(name):
        raise DiscoveryError(
            f"{origin}: {name!r} must match {NAME_RE.pattern} "
            "(lowercase letters, digits, dot, dash, underscore)"
        )
    if name in RESERVED_NAMES:
        raise DiscoveryError(f"{origin}: {name!r} is a reserved environment name")
    return name


def classify(relpath: str) -> _Match | None:
    """Match a repository path against the naming convention. Pure, no I/O."""
    filename = Path(relpath).name

    if match := _CONDA_NAMED_RE.fullmatch(filename):
        return _Match(Backend.CONDA, match.group("name"))
    if _CONDA_BARE_RE.fullmatch(filename):
        return _Match(Backend.CONDA, None)
    if match := _REQUIREMENTS_RE.fullmatch(filename):
        return _Match(Backend.VENV, match.group("name"))
    if match := _LOCK_RE.fullmatch(filename):
        return _Match(Backend.VENV, match.group("name"), is_lock=True)
    if match := _PYPROJECT_RE.fullmatch(filename):
        return _Match(Backend.UV_PROJECT, match.group("name"))
    return None


def is_definition_file(relpath: str) -> bool:
    return classify(relpath) is not None


def definition_path_for_lock(relpath: str) -> str:
    """The ``requirements-<name>.txt`` a ``requirements-<name>.lock`` belongs to."""
    path = Path(relpath)
    return str(path.with_name(path.name.removesuffix(".lock") + ".txt"))


def parse_headers(text: str) -> dict[str, str]:
    """Read ``# key: value`` pairs from the leading comment block of a file."""
    headers: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        if match := _HEADER_RE.fullmatch(stripped):
            headers[match.group("key").lower()] = match.group("value")
    return headers


def _python_from_headers(headers: dict[str, str], origin: str) -> str | None:
    value = headers.get("python")
    if value is None:
        return None
    if not PYTHON_RE.fullmatch(value):
        raise DiscoveryError(
            f"{origin}: '# python: {value}' is not a version like 3.12"
        )
    return value


def _python_from_specifier(specifier: str) -> str | None:
    """Best effort minor version out of a ``requires-python`` specifier."""
    match = _VERSION_IN_SPECIFIER_RE.search(specifier)
    return match.group(1) if match else None


def _read(repo_root: Path, relpath: str) -> str:
    try:
        return (repo_root / relpath).read_text(encoding="utf-8")
    except OSError as exc:
        raise DiscoveryError(f"{relpath}: cannot be read ({exc})") from exc


def _conda_spec(repo_root: Path, relpath: str, match: _Match) -> EnvSpec:
    text = _read(repo_root, relpath)
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DiscoveryError(f"{relpath}: invalid YAML ({exc})") from exc
    if not isinstance(document, dict):
        raise DiscoveryError(f"{relpath}: expected a YAML mapping at the top level")

    inner_name = document.get("name")
    if inner_name is not None and not isinstance(inner_name, str):
        raise DiscoveryError(f"{relpath}: 'name:' must be a string")

    name = inner_name or match.name
    if not name:
        raise DiscoveryError(
            f"{relpath}: a bare environment.yml must carry a 'name:' key, "
            "or be renamed to environment-<name>.yml"
        )

    headers = parse_headers(text)
    return EnvSpec(
        name=validate_name(name, origin=relpath),
        backend=Backend.CONDA,
        path=relpath,
        display_name=headers.get("display-name"),
    )


def _venv_spec(
    repo_root: Path, relpath: str, match: _Match, defaults: DefaultsConfig
) -> EnvSpec:
    assert match.name is not None
    name = validate_name(match.name, origin=relpath)
    text = _read(repo_root, relpath)
    headers = parse_headers(text)

    lock_relpath = str(Path(relpath).with_name(f"requirements-{match.name}.lock"))
    has_lock = (repo_root / lock_relpath).is_file()

    return EnvSpec(
        name=name,
        backend=Backend.VENV,
        path=relpath,
        python=_python_from_headers(headers, relpath) or defaults.python,
        display_name=headers.get("display-name"),
        lock_path=lock_relpath if has_lock else None,
    )


def _uv_project_spec(
    repo_root: Path, relpath: str, match: _Match, defaults: DefaultsConfig
) -> EnvSpec:
    assert match.name is not None
    name = validate_name(match.name, origin=relpath)
    text = _read(repo_root, relpath)
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DiscoveryError(f"{relpath}: invalid TOML ({exc})") from exc

    tool_section = document.get("tool", {}).get("environ", {})
    if not isinstance(tool_section, dict):
        raise DiscoveryError(f"{relpath}: [tool.environ] must be a table")

    python = tool_section.get("python")
    if python is None:
        requires_python = document.get("project", {}).get("requires-python")
        if isinstance(requires_python, str):
            python = _python_from_specifier(requires_python)
    if python is not None and not PYTHON_RE.fullmatch(str(python)):
        raise DiscoveryError(f"{relpath}: python {python!r} is not a version like 3.12")

    return EnvSpec(
        name=name,
        backend=Backend.UV_PROJECT,
        path=relpath,
        python=str(python) if python else defaults.python,
        display_name=tool_section.get("display-name"),
    )


def spec_from_file(repo_root: Path, relpath: str, defaults: DefaultsConfig) -> EnvSpec:
    """Build the spec for one definition file that exists on disk."""
    match = classify(relpath)
    if match is None:
        raise DiscoveryError(f"{relpath}: does not match any definition file pattern")
    if match.is_lock:
        return spec_from_file(repo_root, definition_path_for_lock(relpath), defaults)

    match match.backend:
        case Backend.CONDA:
            return _conda_spec(repo_root, relpath, match)
        case Backend.VENV:
            return _venv_spec(repo_root, relpath, match, defaults)
        case Backend.UV_PROJECT:
            return _uv_project_spec(repo_root, relpath, match, defaults)


def name_from_path(relpath: str) -> str | None:
    """Filename-derived name, usable when the file is already deleted."""
    match = classify(relpath)
    if match is None or match.name is None:
        return None
    try:
        return validate_name(match.name, origin=relpath)
    except DiscoveryError:
        return None


def _candidate_paths(repo_root: Path, defaults: DefaultsConfig) -> list[str]:
    roots = [repo_root / root for root in defaults.search_roots] or [repo_root]
    candidates: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            relpath = str(path.relative_to(repo_root))
            if is_definition_file(relpath):
                candidates.append(relpath)
    return candidates


def discover_all(
    repo_root: Path, defaults: DefaultsConfig
) -> tuple[list[EnvSpec], list[DiscoveryProblem]]:
    """Scan the whole repository for definition files."""
    specs: dict[str, EnvSpec] = {}
    problems: list[DiscoveryProblem] = []

    for relpath in _candidate_paths(repo_root, defaults):
        match = classify(relpath)
        if match is not None and match.is_lock:
            continue
        try:
            spec = spec_from_file(repo_root, relpath, defaults)
        except DiscoveryError as exc:
            problems.append(DiscoveryProblem(path=relpath, reason=str(exc)))
            continue
        if (existing := specs.get(spec.name)) is not None:
            problems.append(
                DiscoveryProblem(
                    path=relpath,
                    reason=(
                        f"environment {spec.name!r} is already defined by "
                        f"{existing.path}; ignoring this file"
                    ),
                )
            )
            continue
        specs[spec.name] = spec

    return list(specs.values()), problems


def resolve_changes(
    repo_root: Path,
    changed: list[str],
    defaults: DefaultsConfig,
    *,
    known: dict[str, str] | None = None,
) -> tuple[list[EnvSpec], list[str], list[DiscoveryProblem]]:
    """Turn a list of changed repository paths into rebuilds and removals.

    ``known`` maps a previously built definition path to its environment name so
    that a deleted file can still be resolved -- its content is gone, so a conda
    ``name:`` key that differed from the filename is no longer readable.

    Returns the specs to rebuild, the names to remove, and any problems found.
    """
    known = known or {}
    rebuild: dict[str, EnvSpec] = {}
    remove: dict[str, None] = {}
    problems: list[DiscoveryProblem] = []

    interesting: list[str] = []
    for relpath in changed:
        match = classify(relpath)
        if match is None:
            continue
        target = definition_path_for_lock(relpath) if match.is_lock else relpath
        if target not in interesting:
            interesting.append(target)

    for relpath in interesting:
        if (repo_root / relpath).is_file():
            try:
                spec = spec_from_file(repo_root, relpath, defaults)
            except DiscoveryError as exc:
                problems.append(DiscoveryProblem(path=relpath, reason=str(exc)))
                continue
            rebuild[spec.name] = spec
            continue

        name = known.get(relpath) or name_from_path(relpath)
        if name is None:
            problems.append(
                DiscoveryProblem(
                    path=relpath,
                    reason="file was deleted and its environment name is unknown",
                )
            )
            continue
        remove[name] = None

    # A file that was both removed and re-added under a different name must not
    # be torn down after it has just been rebuilt.
    for name in rebuild:
        remove.pop(name, None)

    return list(rebuild.values()), list(remove), problems


def with_defaults(spec: EnvSpec, defaults: DefaultsConfig) -> EnvSpec:
    """Fill in a python version for backends that need one."""
    if spec.backend is Backend.CONDA or spec.python:
        return spec
    return replace(spec, python=defaults.python)
