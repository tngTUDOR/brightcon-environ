# Troubleshooting

## Viewing logs

```bash
sudo journalctl -u brightcon-environ -f          # live tail
sudo journalctl -u brightcon-environ -n 100       # last 100 lines
sudo systemctl status brightcon-environ           # quick status + recent output
```

Per-job logs are also written to `/opt/tljh/environ/logs/` and are returned by
`GET /jobs/{id}`.

Use the job id from the webhook response (`{"job": "..."}`) or from the journal
line `queued job ... for delivery ...`.

## `404 Not Found` on webhook delivery

GitHub is posting to the wrong path. The webhook route is `/hooks/github`, not
`/`. Update the **Payload URL** in the GitHub webhook settings to include the
full path, e.g. `https://your-server/hooks/github`.

## `invalid JSON` error on webhook delivery

The webhook content type is wrong. In the GitHub webhook settings, change
**Content type** from `application/x-www-form-urlencoded` to
**`application/json`**.

## `Failed at step NAMESPACE` / systemd refuses to start

```
Failed to set up mount namespacing: /some/path: No such file or directory
```

The systemd unit lists paths in `ReadWritePaths=` that do not exist yet.
Create the missing directories before starting the service:

```bash
sudo mkdir -p /opt/tljh/environ/cache/uv /opt/tljh/environ/cache/conda/pkgs
sudo systemctl daemon-reload
sudo systemctl restart brightcon-environ
```

## `Read-only file system` from uv or conda

```
Could not create temporary file
Caused by: Read-only file system (os error 30) at path "/root/.cache/uv/..."
```

The systemd unit has `ProtectHome=read-only`, which makes `/root` read-only.
Tool caches must be redirected elsewhere. The current unit file sets
`UV_CACHE_DIR`, `CONDA_PKGS_DIRS` and `XDG_CACHE_HOME` to directories under
`/opt/tljh/environ/cache/`. If you are using an older version of the unit file,
update it from `deploy/brightcon-environ.service` and re-copy.

## `dubious ownership` from git

```
fatal: detected dubious ownership in repository at '/opt/tljh/environ/repo'
```

The clone was created by a different user than the one running the service
(root). Fix the ownership and mark the directory as safe:

```bash
sudo chown -R root:root /opt/tljh/environ/repo
sudo git -C /opt/tljh/environ/repo config --local safe.directory /opt/tljh/environ/repo
```

Prefer fixing ownership over a global `safe.directory` entry in root's git
config.

## `Permission denied (publickey)` on fetch

The service cannot authenticate to GitHub. For a public repo, use an HTTPS URL
in `repo.url`. For a private repo, set up a deploy key (see {doc}`deployment`)
and make sure `repo.ssh_key` points at the private key file under
`/etc/brightcon-environ/`, not under `/opt/tljh`.

## Environments build but kernels do not appear in JupyterHub

Restart your single-user server from the JupyterHub control panel (Hub ->
Control Panel -> Stop My Server, then Start My Server). The hub itself does not
need restarting. Kernels are installed into `/opt/tljh/user/share/jupyter/kernels/`,
which is on the TLJH kernel search path.

On a non-TLJH JupyterHub the path may not be searched. In that case either
symlink `/usr/local/share/jupyter` to `/opt/tljh/user/share/jupyter`, or add
`JUPYTER_PATH=/opt/tljh/user/share/jupyter` to the spawner environment in
`jupyterhub_config.py`.

## Checking what would be built (dry run)

```bash
sudo ENVIRON_CONFIG=/opt/tljh/config/environ.toml \
    /opt/tljh/environ/venv/bin/environ plan --repo /opt/tljh/environ/repo
```

## Forcing a rebuild of everything

```bash
sudo ENVIRON_CONFIG=/opt/tljh/config/environ.toml \
    /opt/tljh/environ/venv/bin/environ sync --all --force
```

Or via the API:

```bash
curl -X POST https://your-server/path/to/rebuild \
    -H "Authorization: Bearer $ENVIRON_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"force": true}'
```

## Webhook returns `503`

`GITHUB_WEBHOOK_SECRET` is not set in `/etc/brightcon-environ.env`, or the
service was not restarted after editing it. The webhook endpoint fails closed
without a secret rather than accepting unauthenticated builds.

## Webhook returns `401`

The signature does not match. Confirm that the secret in GitHub webhook
settings is identical to `GITHUB_WEBHOOK_SECRET` in `/etc/brightcon-environ.env`,
then redeliver from the GitHub UI.
