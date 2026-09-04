# Configuration

Configuration is a TOML file, read from `--config`, then `$ENVIRON_CONFIG`,
then `/opt/tljh/config/environ.toml`. When none of those exist the built-in
defaults below are used.

Secrets are never stored in the file; they come from the environment.

## Secrets

| Variable | Purpose |
| --- | --- |
| `GITHUB_WEBHOOK_SECRET` | shared secret for the `X-Hub-Signature-256` check |
| `ENVIRON_ADMIN_TOKEN` | bearer token for `POST /rebuild` |
| `GITHUB_CHECKS_TOKEN` | optional fine-grained PAT with **Checks: Read and write** on the definitions repo; used to post Check Runs |
| `ENVIRON_CONFIG` | path to the configuration file |

`GITHUB_WEBHOOK_SECRET` is mandatory. Without it the webhook endpoint fails
closed with `503` and refuses every delivery, rather than accepting
unauthenticated builds.

`GITHUB_CHECKS_TOKEN` is optional. Without it, jobs still run; contributors
just will not see an **environ** check on the PR. The token is never stored in
the TOML file.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `repo.url` | `""` | clone URL of the watched repository |
| `repo.branch` | `"main"` | watched branch; `push` to other refs and PRs targeting other bases are ignored |
| `repo.path` | `/opt/tljh/environ/repo` | where the clone lives |
| `repo.ssh_key` | unset | private deploy key for a private repository; store under `/etc/brightcon-environ/`, not under `/opt/tljh` |
| `paths.env_root` | `/opt/tljh/user/envs` | parent directory of every environment |
| `paths.kernel_prefix` | `/opt/tljh/user` | prefix passed to `ipykernel install` |
| `paths.state_dir` | `/opt/tljh/environ/state` | holds `environments.json` |
| `paths.log_dir` | `/opt/tljh/environ/logs` | one log file per job |
| `tools.git` | `git` | git binary |
| `tools.conda` | `/opt/tljh/user/bin/mamba` | mamba or conda binary |
| `tools.uv` | `/opt/tljh/user/bin/uv` | uv binary |
| `server.host` | `127.0.0.1` | listen address |
| `server.port` | `8787` | listen port |
| `defaults.python` | `"3.12"` | python version when a definition does not state one |
| `defaults.installer` | `"uv"` | `uv`, or `pip` for `python -m venv` plus pip |
| `defaults.conda_channels` | `["conda-forge"]` | channels for conda environments |
| `defaults.search_roots` | `[]` | directories to scan; empty means the whole repo |
| `defaults.timeout_seconds` | `3600` | per-command timeout |

## Example

```toml
[repo]
url = "https://github.com/CHANGE-ME/course-environments.git"
branch = "main"
path = "/opt/tljh/environ/repo"
# ssh_key = "/etc/brightcon-environ/deploy_key"

[paths]
env_root = "/opt/tljh/user/envs"
kernel_prefix = "/opt/tljh/user"
state_dir = "/opt/tljh/environ/state"
log_dir = "/opt/tljh/environ/logs"

[tools]
git = "/usr/bin/git"
conda = "/opt/tljh/user/bin/mamba"
uv = "/opt/tljh/user/bin/uv"

[server]
host = "127.0.0.1"
port = 8787

[defaults]
python = "3.12"
installer = "uv"
conda_channels = ["conda-forge"]
timeout_seconds = 3600
# search_roots = ["environments"]
```

Ready-made files for both deployments are in `deploy/`: `config.local.toml`
and `config.tljh.toml`.

## Path layout

The layout is identical on a laptop and on a real TLJH box, so nothing but the
tool paths changes between them:

```
/opt/tljh/user/envs/<name>              the environment itself
/opt/tljh/user/share/jupyter/kernels/   kernelspecs, shared by all hub users
/opt/tljh/environ/repo/                 the clone of the watched repository
/opt/tljh/environ/venv/                 dedicated venv for the service itself
/opt/tljh/environ/cache/                tool caches (uv, conda)
/opt/tljh/environ/state/environments.json
/opt/tljh/environ/logs/<timestamp>-<job>.log
/opt/tljh/config/environ.toml           configuration
/etc/brightcon-environ.env              secrets (mode 0600)
```

See {doc}`deployment` for a step-by-step install guide and {doc}`troubleshooting`
for common problems.

Environments are always created with an explicit prefix, never with
`conda create -n`, so the result never depends on `envs_dirs` or `.condarc`.
Kernels are registered the way TLJH documents it:

```bash
/opt/tljh/user/envs/<name>/bin/python -m ipykernel install \
    --prefix /opt/tljh/user --name <name> --display-name "..."
```

uv is not part of a stock TLJH install; add it with
`sudo -E /opt/tljh/user/bin/conda install -c conda-forge uv`.
