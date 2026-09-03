# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything below is the initial, not yet released feature set of the service.

### Added

- Webhook receiver at `POST /hooks/github`. Deliveries are authenticated with
  `X-Hub-Signature-256`, filtered by ref, answered with `202 Accepted` and a job
  id, and rebuilt asynchronously so GitHub never waits for a build.
- Manual trigger at `POST /rebuild`, guarded by a bearer token, with a `force`
  flag that ignores the up-to-date check.
- Read-only endpoints: `GET /healthz` for liveness and a configuration summary,
  `GET /jobs` and `GET /jobs/{id}` for job history, status and a log tail, and
  `GET /environments` for what is built and whether it is still on disk.
- Environment discovery from three definition formats: mamba/conda
  `environment` YAML, pip requirement lists, and uv project metadata. Definition
  files may live at any depth in the repository, and `defaults.search_roots`
  restricts the scan to particular directories.
- Filename-driven environment naming, so all three formats name an environment
  the same way, with a conda file's inner `name:` key taking precedence.
  A `requirements-<name>.lock` beside its `.txt` is built with `uv pip sync` to
  give exactly the pinned set.
- Optional `# python:` and `# display-name:` header comments for the formats
  that have nowhere else to record them, with `requires-python` and
  `[tool.environ] display-name` as the `pyproject-<name>.toml` equivalents.
- Name validation against `^[a-z0-9][a-z0-9._-]{0,63}$` and a reserved-name
  list, rejected before any command runs. Together with the prefix confinement
  in the builders this keeps teardown from reaching outside the environment
  root.
- A single worker thread with a job queue, so two pushes landing together cannot
  fight over the same directory.
- Incremental rebuilds: the pushed range is diffed, changed files are mapped to
  environments, and an environment is skipped when its definition's git blob
  hash is unchanged and both the environment and its kernel are still present.
  An unknown `before` commit falls back to scanning every definition file.
- Teardown of environments whose definition file was deleted, recovering the
  name of a deleted conda file from the state file.
- Kernelspec registration and removal through `ipykernel install --prefix`, the
  way TLJH documents it.
- Cleanup of failed builds, so a broken kernel never appears in the launcher.
- Persistent state in `environments.json` and a per-job log file.
- The `environ` command line tool, with `serve`, `plan`, `sync`, `list` and
  `remove` subcommands.
- TOML configuration with secrets read from the environment. The webhook
  endpoint fails closed with `503` when `GITHUB_WEBHOOK_SECRET` is unset rather
  than accepting unauthenticated builds.
- Deployment support: a systemd unit with an environment file example, a
  `scripts/bootstrap-local.sh` that bridges kernel search paths on a plain
  JupyterHub, and a `scripts/fake-webhook.py` that signs and posts push payloads
  so the whole path can be exercised without GitHub.
- Test suite covering signature checks, discovery, the git layer, the builders
  and the API, with a `slow` marker for the tests that build a real environment.
- ruff for formatting and linting, enforced by pre-commit hooks that also keep
  `uv.lock` in sync with `pyproject.toml`.
- Sphinx documentation under `docs/`, written in MyST markdown and built with
  the `docs` dependency group.
