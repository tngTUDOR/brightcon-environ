# Defining environments

```{note}
**Adding a kernel to the conference repo?** Start with
[For contributors](for-contributors.md) — copy-paste templates and a checklist.
This page is the full specification for admins and edge cases.
```

Definition files may live anywhere in the watched repository, at any depth. Set
`defaults.search_roots` to restrict the scan to particular directories.

## Naming

Conda YAML files can carry a `name:` key, but a `requirements.txt` has nowhere
to record one. The convention is therefore filename driven, so all three
formats name their environment the same way.

| File | Backend | Environment name |
| --- | --- | --- |
| `environment-<name>.yml` / `.yaml` | mamba/conda | the inner `name:` if present, otherwise `<name>` |
| `environment.yml` / `.yaml` | mamba/conda | the inner `name:`, which is then required |
| `requirements-<name>.txt` | uv (or pip) into a venv | `<name>` |
| `requirements-<name>.lock` | pinned input for the same environment | `<name>` |
| `pyproject-<name>.toml` | uv, from the project metadata | `<name>` |

Conda YAML files must be portable to Linux: hand-write them, or export with
`--from-history` — see {ref}`exporting-from-history`.

Names must match `^[a-z0-9][a-z0-9._-]{0,63}$` and must not be one of `user`,
`hub`, `base`, `root`, `python3`, `envs` or `share`. Anything else is refused
before a single command runs; this is what keeps the teardown step from being
able to touch a path outside the environment root.

## Headers

A filename can only carry a name, so two header comments fill the gaps. Both
are ordinary comments that pip and uv ignore, and only the leading comment
block of the file is read:

```
# python: 3.12
# display-name: Brightcon 2026 Basic

pandas
matplotlib
```

Without `# python:` the version from `defaults.python` is used. Without
`# display-name:` the kernel is labelled with the environment name.

For `pyproject-<name>.toml` the equivalents live in the TOML itself:

```toml
[project]
requires-python = ">=3.12"
dependencies = ["pandas", "matplotlib"]

[tool.environ]
display-name = "Brightcon 2026 Basic"
```

## Lock files

A `requirements-<name>.lock` beside a `requirements-<name>.txt` wins: the
environment is then built with `uv pip sync`, giving exactly the pinned set.

## Deletions

Deleting a definition file removes the environment and its kernel. The name of
a deleted conda file is recovered from the state file, since its `name:` key is
no longer readable.
