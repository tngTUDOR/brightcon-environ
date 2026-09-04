# Deploying on a TLJH server

This guide covers a fresh deployment on an Ubuntu server running The Littlest
JupyterHub. The service runs in its own venv, separate from `/opt/tljh/user`,
so it does not pollute the environment shared by hub users.

## Two repositories

There are **two different repositories** involved:

| Repository | Role | Location on server |
| --- | --- | --- |
| **brightcon-environ** (this repo) | The webhook service source code | e.g. `/usr/local/share/brightcon-environ` |
| **Your definitions repo** | `requirements-*.txt`, `environment-*.yml`, etc. | Auto-cloned to `/opt/tljh/environ/repo` |

You configure the definitions repo URL in `environ.toml` as `repo.url`; the
service clones it on first use and keeps it in sync on every push. You do not
need to clone the definitions repo manually.

## Prerequisites

uv is not part of a stock TLJH install. Add it to the user environment so the
service can use it to create venvs:

```bash
sudo -E /opt/tljh/user/bin/conda install -c conda-forge uv
```

## Step 1: Clone the service source

Pick any location readable by root. `/usr/local/share` is a good choice:

```bash
sudo git clone https://github.com/YOUR-ORG/brightcon-environ.git \
    /usr/local/share/brightcon-environ
```

## Step 2: Create the service venv and install

This project requires Python >= 3.14. Use uv to pull the right interpreter
into a dedicated venv (not the TLJH user environment):

```bash
sudo mkdir -p /opt/tljh/environ
sudo /opt/tljh/user/bin/uv venv --python 3.14 /opt/tljh/environ/venv
sudo /opt/tljh/user/bin/uv pip install \
    --python /opt/tljh/environ/venv/bin/python \
    /usr/local/share/brightcon-environ
```

Verify:

```bash
/opt/tljh/environ/venv/bin/environ --version
```

## Step 3: Create the directory layout

```bash
sudo mkdir -p \
    /opt/tljh/user/envs \
    /opt/tljh/user/share/jupyter/kernels \
    /opt/tljh/config \
    /opt/tljh/environ/repo \
    /opt/tljh/environ/state \
    /opt/tljh/environ/logs \
    /opt/tljh/environ/cache/uv \
    /opt/tljh/environ/cache/conda/pkgs
sudo chmod -R a+rX /opt/tljh
```

On a real TLJH box most of this tree already exists. The key additions are
`/opt/tljh/environ/` (for the service) and the `cache/` subtree.

## Step 4: Configuration

```bash
sudo cp /usr/local/share/brightcon-environ/deploy/config.tljh.toml \
        /opt/tljh/config/environ.toml
sudo $EDITOR /opt/tljh/config/environ.toml
```

At minimum, set `repo.url` to your definitions repository. For public repos use
an HTTPS URL; for private repos see {ref}`private-repos` below.

```toml
[repo]
url = "https://github.com/YOUR-ORG/your-definitions-repo.git"
```

See {doc}`configuration` for every setting.

## Step 5: Secrets

Secrets live in `/etc/brightcon-environ.env`, **not** in `environ.toml`. On a
TLJH server every hub user is a real system user with terminal access, and the
`/opt/tljh` tree is world-readable. Putting secrets there would expose them to
every student.

```bash
sudo install -m 600 /dev/null /etc/brightcon-environ.env
```

Edit the file and set the secrets:

```
GITHUB_WEBHOOK_SECRET=<paste the same secret you configure on the GitHub webhook>
ENVIRON_ADMIN_TOKEN=<a token of your choice for manual POST /rebuild calls>
GITHUB_APP_ID=<App ID integer>
GITHUB_APP_INSTALLATION_ID=<installation ID integer>
GITHUB_APP_PRIVATE_KEY_FILE=/etc/brightcon-environ/github-app.pem
```

Generate strong random values for the webhook secret and admin token with:

```bash
openssl rand -hex 32
```

Check Runs need a **GitHub App** on the definitions repository (PATs cannot
create them). Follow the full walkthrough in {doc}`github-app` (create under
the org, disable the App webhook, Checks read/write, install on the
definitions repo, copy App ID / Installation ID / PEM onto the server).

Without the three `GITHUB_APP_*` values, rebuilds still run but contributors
will not see an **environ** check on their PR.

| Variable | Purpose |
| --- | --- |
| `GITHUB_WEBHOOK_SECRET` | Shared secret for `X-Hub-Signature-256` verification. Must match the secret in GitHub webhook settings. |
| `ENVIRON_ADMIN_TOKEN` | Bearer token for the `POST /rebuild` endpoint. Only needed if you want to trigger manual rebuilds via the API. |
| `GITHUB_APP_ID` | Optional. GitHub App ID used to mint installation tokens for Check Runs. |
| `GITHUB_APP_INSTALLATION_ID` | Optional. Installation ID of that App on the definitions repo. |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Optional. Path to the App private key PEM. |

`ENVIRON_CONFIG` is **not** a secret -- it is a plain path to the configuration
file and is set in the systemd unit, not in the env file.

## Step 6: systemd

```bash
sudo cp /usr/local/share/brightcon-environ/deploy/brightcon-environ.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now brightcon-environ
```

Check that it started:

```bash
sudo systemctl status brightcon-environ
sudo journalctl -u brightcon-environ -n 20
```

The unit runs as root because creating environments under `/opt/tljh/user` and
writing shared kernelspecs are root operations -- the same ones a TLJH admin
performs with `sudo` from a notebook terminal. Tool caches are redirected under
`/opt/tljh/environ/cache/` so `ProtectHome=read-only` does not block uv or
conda.

## Step 7: Configure the GitHub webhook

In your **definitions** repository (not brightcon-environ), go to Settings ->
Webhooks and add a webhook:

- **Payload URL**: `https://your-server/path/to/hooks/github`
  (the route is `/hooks/github`, **not** `/`)
- **Content type**: **`application/json`** (not `application/x-www-form-urlencoded`)
- **Secret**: the same value as `GITHUB_WEBHOOK_SECRET` in `/etc/brightcon-environ.env`
- **Events**: **Pushes** and **Pull requests** (not “Just the push event”)

Pull requests targeting the watched branch are **validated** in a staging
directory (no live kernels). Merges still arrive as pushes to `main` and
**apply** to the hub. Other PR actions and other base branches are ignored.

## Step 8: Verify

Push a commit to `main` in your definitions repo (or redeliver an existing
webhook from the GitHub UI), then check:

```bash
# Job status (use the job id from the webhook response or the journal)
curl -s https://your-server/path/to/jobs | python3 -m json.tool

# List built environments
curl -s https://your-server/path/to/environments | python3 -m json.tool

# Or from the CLI on the server
sudo ENVIRON_CONFIG=/opt/tljh/config/environ.toml \
    /opt/tljh/environ/venv/bin/environ list
```

After a successful build, restart your single-user server from the JupyterHub
control panel. The new kernels appear in the launcher. The hub itself does not
need restarting.

(private-repos)=

## Private repositories and SSH deploy keys

For a private definitions repo, use an SSH deploy key. Keep the private key
alongside the other secrets, **not** under `/opt/tljh` (which is world-readable):

```bash
sudo mkdir -p /etc/brightcon-environ
sudo ssh-keygen -t ed25519 -f /etc/brightcon-environ/deploy_key -N "" -C "brightcon-environ"
sudo chmod 600 /etc/brightcon-environ/deploy_key
```

Add the public key to your definitions repo on GitHub (Settings -> Deploy keys,
read access is sufficient):

```bash
sudo cat /etc/brightcon-environ/deploy_key.pub
```

Then set both the URL and key in `/opt/tljh/config/environ.toml`:

```toml
[repo]
url = "git@github.com:YOUR-ORG/your-definitions-repo.git"
ssh_key = "/etc/brightcon-environ/deploy_key"
```

Restart the service after changing the config:

```bash
sudo systemctl restart brightcon-environ
```

## Updating the service

After pulling new code into the source clone:

```bash
cd /usr/local/share/brightcon-environ
sudo git pull
sudo /opt/tljh/user/bin/uv pip install \
    --python /opt/tljh/environ/venv/bin/python \
    /usr/local/share/brightcon-environ
sudo systemctl restart brightcon-environ
```

If the systemd unit file changed, also re-copy it:

```bash
sudo cp /usr/local/share/brightcon-environ/deploy/brightcon-environ.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart brightcon-environ
```

## Local development setup

A plain JupyterHub is not TLJH, so it does not look inside
`/opt/tljh/user/share/jupyter` for kernels. It does look in
`/usr/local/share/jupyter`, so `scripts/bootstrap-local.sh` links the two
together. On a real TLJH install no bridge is needed, because the single-user
server's `sys.prefix` already is `/opt/tljh/user`.

```bash
sudo scripts/bootstrap-local.sh
sudo cp deploy/config.local.toml /opt/tljh/config/environ.toml
sudo $EDITOR /opt/tljh/config/environ.toml       # set repo.url

uv sync
export ENVIRON_CONFIG=/opt/tljh/config/environ.toml
export GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32)
export ENVIRON_ADMIN_TOKEN=$(openssl rand -hex 32)
```

See {doc}`operating` for the CLI, API endpoints, and testing without GitHub.
