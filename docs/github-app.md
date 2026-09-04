# GitHub App for Check Runs

Contributors need to see Linux build logs on pull requests. That uses GitHub
**Check Runs** named `environ`. Creating Check Runs requires a **GitHub App** —
personal access tokens (classic or fine-grained) **cannot** do it.

This guide is for a conference admin who owns or administers the **definitions**
repository (the repo with `environment-*.yml` / `requirements-*.txt`). That repo
usually lives under a GitHub **organization**.

There are **two separate GitHub integrations**. Do not mix them up:

| Integration | Purpose | Where you configure it |
| --- | --- | --- |
| **Repository webhook** | Notify the server of pushes and PRs | Definitions repo → Settings → Webhooks |
| **GitHub App** (this page) | Post the **environ** Check on commits | Org → Settings → GitHub Apps |

The App does **not** replace the webhook. Keep the repo webhook for **Pushes**
and **Pull requests** pointing at `https://your-server/.../hooks/github`.

Without the App, rebuilds and PR validation still run on the server; contributors
simply will not see a Check on the PR.

---

## Prerequisites

- You are an **owner** or **admin** of the GitHub organization that owns the
  definitions repository (enough to create org GitHub Apps and install them).
- The definitions repo already exists on `github.com`.
- `repo.url` in `/opt/tljh/config/environ.toml` is a github.com URL, for example:

  ```toml
  [repo]
  url = "https://github.com/YOUR_ORG/your-definitions-repo.git"
  # or: url = "git@github.com:YOUR_ORG/your-definitions-repo.git"
  ```

- SSH access to the TLJH server as someone who can write under
  `/etc/brightcon-environ/` and restart `brightcon-environ`.

---

## Step 1 — Open the “New GitHub App” form for your org

1. Sign in to GitHub as an org owner/admin.
2. Open:

   `https://github.com/organizations/YOUR_ORG/settings/apps/new`

   Replace `YOUR_ORG` with the organization slug (the name in the org URL).

   Or navigate: organization home → **Settings** → **Developer settings** →
   **GitHub Apps** → **New GitHub App**.

```{note}
Create the App **under the organization**, not under your personal account.
Then installation on the org’s definitions repo is straightforward and the App
is owned by the conference, not by one person’s user settings.
```

---

## Step 2 — Identifying fields

| Field | What to enter |
| --- | --- |
| **GitHub App name** | Something unique on all of github.com, e.g. `brightcon-environ` or `brightcon-environ-YOUR_ORG`. If the name is taken, add a suffix. |
| **Homepage URL** | Required. Use the org URL, conference site, or the definitions repo URL. |
| **Description** | Optional. e.g. “Posts environ Check Runs for brightcon-environ.” |
| **Callback URL** / **Setup URL** | Leave blank. This App is not an OAuth login app. |
| **Expire user authorization tokens** | Leave **checked**. Unused for installation tokens; safer default if you never use user OAuth. |
| **Request user authorization (OAuth) during installation** | Leave **unchecked**. |
| **Enable Device Flow** | Leave **unchecked**. |

---

## Step 3 — Webhook: turn it off

Under **Webhook**:

1. **Uncheck Active.**
2. Leave **Webhook URL** empty.
3. Do **not** subscribe the App to any events.

The App only calls the GitHub API. Event delivery stays on the **repository
webhook** you already configured (or will configure) on the definitions repo.

```{warning}
If you leave the App webhook Active, GitHub will try to POST events to a URL
you must host and authenticate separately. brightcon-environ does not use that
path. Keep Active **off**.
```

---

## Step 4 — Repository permissions

Open **Repository permissions** and set **only**:

| Permission | Access |
| --- | --- |
| **Checks** | **Read and write** |
| **Metadata** | **Read-only** (required; usually pre-selected) |

Leave every other repository permission at **No access**.

Leave **Organization permissions** and **Account permissions** at No access
unless GitHub forces something mandatory (Metadata is enough).

---

## Step 5 — Who can install the App

Under **Where can this GitHub App be installed?** choose:

**Only on this account**

That limits installation to your organization.

Then click **Create GitHub App**.

---

## Step 6 — Note the App ID

On the App’s settings page (right after creation, or later under
Org → Settings → Developer settings → GitHub Apps → your App):

1. Find **App ID** — a number such as `123456`.
2. Copy it. You will put it in `/etc/brightcon-environ.env` as `GITHUB_APP_ID`.

This is **not** the Client ID (a string like `Iv1.…`). Use the numeric **App ID**.

---

## Step 7 — Generate and store the private key

Still on the App settings page:

1. Scroll to **Private keys**.
2. Click **Generate a private key**.
3. GitHub downloads a `.pem` file once (e.g.
   `brightcon-environ.2026-09-04.private-key.pem`). Keep it secret.

On the TLJH server:

```bash
sudo mkdir -p /etc/brightcon-environ
sudo install -m 600 /path/to/downloaded.pem \
    /etc/brightcon-environ/github-app.pem
sudo chown root:root /etc/brightcon-environ/github-app.pem
```

- Store the PEM under `/etc/brightcon-environ/`, **not** under `/opt/tljh`
  (that tree is world-readable for hub users).
- Never paste the PEM contents into `environ.toml` or a world-readable file.
- Only the **path** goes in the env file (`GITHUB_APP_PRIVATE_KEY_FILE`).

If you lose the download, generate a **new** private key on the App page and
replace the file on the server (old keys can be deleted in the App UI).

---

## Step 8 — Install the App on the definitions repository

1. On the App settings page, open **Install App** (left sidebar or top).
2. Choose your **organization**.
3. Select **Only select repositories**.
4. Pick the **definitions** repository (the one with environment definition
   files — not the brightcon-environ source repo, unless they are the same).
5. Click **Install**.

You need permission to install apps on that org/repo (owner/admin).

---

## Step 9 — Note the Installation ID

After install, GitHub shows a URL like:

```text
https://github.com/organizations/YOUR_ORG/settings/installations/12345678
```

The trailing number (`12345678`) is the **Installation ID**.

You can also find it later:

1. `https://github.com/organizations/YOUR_ORG/settings/installations`
2. Click the App.
3. Read the number from the browser address bar.

Copy it for `GITHUB_APP_INSTALLATION_ID`.

```{note}
**App ID** and **Installation ID** are different integers. The App ID
identifies the App registration; the Installation ID identifies “this App
installed on these repos.”
```

---

## Step 10 — Configure the server

Edit `/etc/brightcon-environ.env` (mode `0600`) and set:

```bash
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=12345678
GITHUB_APP_PRIVATE_KEY_FILE=/etc/brightcon-environ/github-app.pem
```

Replace the numbers with your values. Keep existing
`GITHUB_WEBHOOK_SECRET` and `ENVIRON_ADMIN_TOKEN` lines.

Restart the service:

```bash
sudo systemctl restart brightcon-environ
sudo systemctl status brightcon-environ
sudo journalctl -u brightcon-environ -n 50 --no-pager
```

There should be no warnings about a missing private key. On the first Check
Run, the service mints an installation token (cached ~1 hour); that is normal.

Confirm `repo.url` is still a `github.com` remote — otherwise Check posting is
disabled even if the App env vars are set.

---

## Step 11 — Confirm the repository webhook still exists

On the **definitions** repo → **Settings** → **Webhooks**:

| Setting | Value |
| --- | --- |
| Payload URL | `https://your-server/.../hooks/github` |
| Content type | `application/json` |
| Secret | same as `GITHUB_WEBHOOK_SECRET` |
| Events | **Pushes** and **Pull requests** |

This is independent of the GitHub App. The App does not subscribe to events.

---

## Step 12 — Smoke-test

1. Open a small PR against `main` (or your watched branch) that touches a
   definition file, or push a commit to an open PR.
2. On the PR, open the **Checks** tab (or the status rollup near the bottom).
3. You should see a check named **environ**: pending/in progress, then
   success or failure, with a summary and log text when you open it.
4. After merge to `main`, another **environ** check appears on the merge
   commit when the live apply job finishes.

If the job runs on the server (`journalctl` / `GET /jobs`) but no Check
appears:

- App ID / installation ID / PEM path wrong or PEM unreadable
- App not installed on that repository
- Checks permission not **Read and write**
- `repo.url` not a github.com URL
- Look for `GitHub API` / `GitHub Checks` warnings in the journal

---

## Checklist

- [ ] App created under the **organization** (not only a personal account)
- [ ] App webhook **Active** is unchecked; no App event subscriptions
- [ ] **Checks: Read and write**; Metadata Read-only; nothing else
- [ ] App installed on the **definitions** repo only
- [ ] `GITHUB_APP_ID` = numeric App ID
- [ ] `GITHUB_APP_INSTALLATION_ID` = numeric Installation ID from the install URL
- [ ] PEM at `/etc/brightcon-environ/github-app.pem` mode `0600`
- [ ] `GITHUB_APP_PRIVATE_KEY_FILE` points at that path
- [ ] `systemctl restart brightcon-environ` after editing the env file
- [ ] Repo webhook still delivers Pushes + Pull requests
- [ ] Test PR shows the **environ** check

---

## Security notes

- Treat the private key like a deploy key or root password. Anyone with it can
  act as the App on installed repos (create Checks, etc., within granted
  permissions).
- Prefer rotating the key (generate new PEM, update the file, delete old key in
  the App UI) if a laptop copy might have leaked.
- Do not commit the `.pem` to any git repository.
