from __future__ import annotations

from pathlib import Path

import pytest

from brightcon_environ.config import DefaultsConfig
from brightcon_environ.discovery import (
    Backend,
    DiscoveryError,
    classify,
    discover_all,
    is_definition_file,
    name_from_path,
    parse_headers,
    resolve_changes,
    spec_from_file,
    validate_name,
)


@pytest.mark.parametrize(
    ("path", "backend", "name"),
    [
        ("environment-course.yml", Backend.CONDA, "course"),
        ("nested/dir/environment-course.yaml", Backend.CONDA, "course"),
        ("environment.yml", Backend.CONDA, None),
        ("requirements-demo.txt", Backend.VENV, "demo"),
        ("requirements-demo.lock", Backend.VENV, "demo"),
        ("pyproject-tools.toml", Backend.UV_PROJECT, "tools"),
    ],
)
def test_classify_recognises_the_convention(path, backend, name):
    match = classify(path)
    assert match is not None
    assert match.backend is backend
    assert match.name == name


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",  # this project's own metadata must never be picked up
        "requirements.txt",
        "README.md",
        "environment.json",
        "src/requirements-demo.txt.bak",
    ],
)
def test_classify_ignores_everything_else(path):
    assert classify(path) is None
    assert not is_definition_file(path)


@pytest.mark.parametrize(
    "name", ["Demo", "../escape", "demo/../x", "", "-lead", "a" * 65]
)
def test_invalid_names_are_rejected(name):
    with pytest.raises(DiscoveryError):
        validate_name(name, origin="test")


@pytest.mark.parametrize("name", ["user", "hub", "base", "python3"])
def test_reserved_names_are_rejected(name):
    with pytest.raises(DiscoveryError, match="reserved"):
        validate_name(name, origin="test")


def test_name_from_path_survives_deletion():
    assert name_from_path("requirements-demo.txt") == "demo"
    assert name_from_path("environment.yml") is None
    assert name_from_path("requirements-Bad Name.txt") is None


def test_parse_headers_reads_only_the_leading_block():
    text = "# python: 3.11\n# display-name: My Env\n\nnumpy\n# python: 3.9\n"
    assert parse_headers(text) == {"python": "3.11", "display-name": "My Env"}


def test_requirements_spec_uses_filename_and_headers(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-demo.txt").write_text(
        "# python: 3.11\n# display-name: Demo Environment\nnumpy\n", encoding="utf-8"
    )
    spec = spec_from_file(tmp_path, "requirements-demo.txt", defaults)
    assert (spec.name, spec.backend, spec.python) == ("demo", Backend.VENV, "3.11")
    assert spec.kernel_display_name == "Demo Environment"
    assert spec.lock_path is None


def test_requirements_spec_falls_back_to_the_default_python(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-demo.txt").write_text("numpy\n", encoding="utf-8")
    spec = spec_from_file(tmp_path, "requirements-demo.txt", defaults)
    assert spec.python == defaults.python
    assert spec.kernel_display_name == "demo"


def test_a_lock_file_is_picked_up_when_present(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-demo.txt").write_text("numpy\n", encoding="utf-8")
    (tmp_path / "requirements-demo.lock").write_text("numpy==2.0.0\n", encoding="utf-8")
    spec = spec_from_file(tmp_path, "requirements-demo.txt", defaults)
    assert spec.lock_path == "requirements-demo.lock"


def test_a_lock_file_resolves_to_its_requirements_file(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-demo.txt").write_text("numpy\n", encoding="utf-8")
    (tmp_path / "requirements-demo.lock").write_text("numpy==2.0.0\n", encoding="utf-8")
    spec = spec_from_file(tmp_path, "requirements-demo.lock", defaults)
    assert spec.path == "requirements-demo.txt"


def test_bad_python_header_is_an_error(tmp_path: Path, defaults: DefaultsConfig):
    (tmp_path / "requirements-demo.txt").write_text(
        "# python: latest\n", encoding="utf-8"
    )
    with pytest.raises(DiscoveryError, match="not a version"):
        spec_from_file(tmp_path, "requirements-demo.txt", defaults)


def test_conda_inner_name_wins_over_the_filename(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "environment-onfile.yml").write_text(
        "name: inside\ndependencies:\n  - python=3.12\n", encoding="utf-8"
    )
    spec = spec_from_file(tmp_path, "environment-onfile.yml", defaults)
    assert spec.name == "inside"
    assert spec.backend is Backend.CONDA


def test_conda_filename_is_used_when_the_yaml_has_no_name(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "environment-course.yml").write_text(
        "dependencies:\n  - python=3.12\n", encoding="utf-8"
    )
    assert spec_from_file(tmp_path, "environment-course.yml", defaults).name == "course"


def test_bare_environment_yml_needs_an_inner_name(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "environment.yml").write_text(
        "dependencies:\n  - python=3.12\n", encoding="utf-8"
    )
    with pytest.raises(DiscoveryError, match="must carry a 'name:' key"):
        spec_from_file(tmp_path, "environment.yml", defaults)


def test_pyproject_takes_python_from_requires_python(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "pyproject-tools.toml").write_text(
        '[project]\nname = "whatever"\nrequires-python = ">=3.13,<3.14"\n'
        'dependencies = ["packaging"]\n'
        '\n[tool.environ]\ndisplay-name = "Tooling"\n',
        encoding="utf-8",
    )
    spec = spec_from_file(tmp_path, "pyproject-tools.toml", defaults)
    assert (spec.name, spec.python, spec.kernel_display_name) == (
        "tools",
        "3.13",
        "Tooling",
    )


def test_discover_all_reports_duplicate_names(tmp_path: Path, defaults: DefaultsConfig):
    (tmp_path / "requirements-demo.txt").write_text("numpy\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "environment-demo.yml").write_text(
        "dependencies: []\n", encoding="utf-8"
    )
    specs, problems = discover_all(tmp_path, defaults)
    assert len(specs) == 1
    assert any("already defined" in problem.reason for problem in problems)


def test_discover_all_honours_search_roots(tmp_path: Path):
    (tmp_path / "envs").mkdir()
    (tmp_path / "envs" / "requirements-inside.txt").write_text(
        "numpy\n", encoding="utf-8"
    )
    (tmp_path / "requirements-outside.txt").write_text("numpy\n", encoding="utf-8")
    specs, _ = discover_all(tmp_path, DefaultsConfig(search_roots=("envs",)))
    assert [spec.name for spec in specs] == ["inside"]


def test_resolve_changes_splits_rebuilds_from_removals(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-kept.txt").write_text("numpy\n", encoding="utf-8")
    specs, removals, problems = resolve_changes(
        tmp_path,
        ["requirements-kept.txt", "requirements-gone.txt", "README.md"],
        defaults,
    )
    assert [spec.name for spec in specs] == ["kept"]
    assert removals == ["gone"]
    assert problems == []


def test_resolve_changes_uses_state_for_a_renamed_conda_environment(
    tmp_path: Path, defaults: DefaultsConfig
):
    # The file is gone, so its inner name: is unreadable; state remembers it.
    specs, removals, problems = resolve_changes(
        tmp_path,
        ["environment.yml"],
        defaults,
        known={"environment.yml": "from-state"},
    )
    assert specs == []
    assert removals == ["from-state"]
    assert problems == []


def test_resolve_changes_reports_an_unresolvable_deletion(
    tmp_path: Path, defaults: DefaultsConfig
):
    specs, removals, problems = resolve_changes(tmp_path, ["environment.yml"], defaults)
    assert (specs, removals) == ([], [])
    assert "name is unknown" in problems[0].reason


def test_a_lock_change_rebuilds_its_environment_once(
    tmp_path: Path, defaults: DefaultsConfig
):
    (tmp_path / "requirements-demo.txt").write_text("numpy\n", encoding="utf-8")
    (tmp_path / "requirements-demo.lock").write_text("numpy==2.0.0\n", encoding="utf-8")
    specs, removals, _ = resolve_changes(
        tmp_path, ["requirements-demo.lock", "requirements-demo.txt"], defaults
    )
    assert [spec.name for spec in specs] == ["demo"]
    assert removals == []
