# Development

```bash
uv sync
uv run pytest -m "not slow"    # fast: no network, no real environments
uv run pytest                  # also builds a real venv with uv
```

Formatting and linting are handled by ruff, wired up through pre-commit.
Install the git hook once after cloning:

```bash
uv run pre-commit install
uv run pre-commit run --all-files   # optional: check everything now
```

Or run ruff directly:

```bash
uv run ruff format .
uv run ruff check --fix .
```

## Building these docs

```bash
uv run --group docs sphinx-build -b html docs docs/_build/html
```

Published builds run on [Read the Docs](https://readthedocs.org/) from
[`.readthedocs.yaml`](../.readthedocs.yaml): Python 3.14, `uv sync --group docs`,
and Sphinx with `fail_on_warning`. Import the GitHub repository on Read the Docs
(project slug `brightcon-environ`) and enable builds for `main` (and optionally
`develop`).

## Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | TOML configuration; secrets come from the environment |
| `security.py` | HMAC signature and bearer token checks |
| `git_repo.py` | clone, fetch, hard reset, diff, blob hashes, PR head fetch |
| `github_checks.py` | GitHub Check Runs for contributor-visible job logs |
| `discovery.py` | filenames and headers to `EnvSpec` |
| `builders.py` | create and destroy environments, with path confinement |
| `kernels.py` | kernelspec install and removal |
| `jobs.py` | queue, worker, per-job logs, persisted state |
| `app.py` | FastAPI routes |
| `cli.py` | the `environ` command |
| `runner.py` | subprocess wrapper; argument lists only, never a shell |

## Local setup

A plain JupyterHub is not TLJH, so it does not look inside
`/opt/tljh/user/share/jupyter` for kernels. It does look in
`/usr/local/share/jupyter`, so the bootstrap script links the two together. On
a real TLJH install no bridge is needed, because the single-user server's
`sys.prefix` already is `/opt/tljh/user`.

```bash
sudo scripts/bootstrap-local.sh
sudo cp deploy/config.local.toml /opt/tljh/config/environ.toml
sudo $EDITOR /opt/tljh/config/environ.toml       # set repo.url

uv sync
export ENVIRON_CONFIG=/opt/tljh/config/environ.toml
export GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32)
export ENVIRON_ADMIN_TOKEN=$(openssl rand -hex 32)
```

Restart your single-user server from the JupyterHub control panel and the new
kernels appear in the launcher. The hub itself does not need restarting.
