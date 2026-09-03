# Operating the service

For a full TLJH deployment guide see {doc}`deployment`. For common problems
see {doc}`troubleshooting`.

## Command line

Everything the webhook can trigger is also reachable from the `environ`
command, so the whole pipeline can be exercised without GitHub in the loop.

| Command | Purpose |
| --- | --- |
| `environ serve` | run the webhook API |
| `environ plan` | dry run: show what would be built |
| `environ sync` | rebuild environments now |
| `environ list` | show known environments |
| `environ remove <name>...` | delete environments and their kernels |

`serve` accepts `--host`, `--port` and `--log-level`. `plan` accepts `--repo`
to scan a directory other than the clone. `sync` takes environment names or
`--all`, plus `--force` to rebuild even when nothing changed, and
`--before` / `--after` to pick the commit range by hand.

A typical first run, checking before building:

```bash
uv run environ plan --repo /path/to/a/definitions/repo
sudo -E uv run environ sync --all
uv run environ list
```

`environ list` marks each environment with three flags: `e` when the
environment is on disk, `k` when the kernelspec is registered, and `t` when it
is tracked in the state file.

## HTTP endpoints

```bash
uv run environ serve            # http://127.0.0.1:8787
```

| Endpoint | Purpose |
| --- | --- |
| `POST /hooks/github` | webhook receiver; requires a valid signature |
| `POST /rebuild` | manual trigger; requires `Authorization: Bearer $ENVIRON_ADMIN_TOKEN` |
| `GET /healthz` | liveness and current configuration summary |
| `GET /jobs`, `GET /jobs/{id}` | job history, status and log tail |
| `GET /environments` | what is built, and whether it is still on disk |

`POST /rebuild` takes an optional JSON body with `names` (omit to rebuild
everything) and `force`.

## As a systemd unit

See {doc}`deployment` for the complete install sequence. In short:

```bash
sudo cp deploy/brightcon-environ.service /etc/systemd/system/
sudo cp deploy/brightcon-environ.env.example /etc/brightcon-environ.env
sudo chmod 600 /etc/brightcon-environ.env
sudo $EDITOR /etc/brightcon-environ.env         # set both secrets
sudo systemctl enable --now brightcon-environ
journalctl -u brightcon-environ -f
```

The unit runs as root because creating environments under `/opt/tljh/user` and
writing shared kernelspecs are root operations -- the same ones a TLJH admin
performs with `sudo` from a notebook terminal.

## Testing without GitHub

`scripts/fake-webhook.py` signs a push payload with your secret and posts it,
so the signature check, the ref filter, the diff and the rebuild all run for
real:

```bash
scripts/fake-webhook.py --repo /opt/tljh/environ/repo --watch
scripts/fake-webhook.py --first-push --watch    # null before-SHA: full scan
scripts/fake-webhook.py --bad-signature         # expect HTTP 401
scripts/fake-webhook.py --event ping            # expect {"pong": true}
```

## Receiving real deliveries

A laptop is not reachable from GitHub, so point the webhook at a tunnel:

```bash
# smee.io
npx smee-client --url https://smee.io/<channel> --target http://127.0.0.1:8787/hooks/github

# or cloudflared
cloudflared tunnel --url http://127.0.0.1:8787
```

In the repository settings, add a webhook with the tunnel URL, content type
`application/json`, the same secret as `GITHUB_WEBHOOK_SECRET`, and the *Just
the push event* trigger. Merged pull requests arrive as pushes to the watched
branch, so no separate `pull_request` subscription is needed.

For a private repository, generate a deploy key, add the public half to the
repository with read access, and point `repo.ssh_key` at the private half.
