# For contributors: add a Jupyter kernel in 3 steps 🧪

You are editing the **definitions repository** — the repo whose URL the conference
server watches. You are **not** editing brightcon-environ itself.

When you open a pull request against the watched branch (usually `main`), the
server **validates** the environment on Linux and posts an **environ** Check Run
on your commit — with the log tail if it fails. Live JupyterHub kernels are
**not** updated until the PR is merged. No admin hand-waving required. ✨

---

## Step 1 — Pick a format

| You already use… | Add this file | Backend |
| --- | --- | --- |
| **mamba / conda** *(recommended if that is your world)* | `environment-<name>.yml` | conda-forge via mamba |
| **pip** *(plain list of packages)* | `requirements-<name>.txt` | uv on the server *(you do not need uv locally)* |
| **pyproject.toml** *(rare; only if you already have one)* | `pyproject-<name>.toml` | uv on the server |

Put the file **anywhere** in the repo (`workshops/foo/`, `conference/day/talks/talk46`, etc. ), **but** preferably, put it under a folder that makes sense (`conference/friday/tgif/` for example).
Only filenames matching the patterns above are picked up.

> **Do not** commit a bare `requirements.txt` or `pyproject.toml` — those names are
> ignored on purpose so the repo's own tooling is never mistaken for a kernel.

---

## Step 2 — Name the file correctly 🏷️

The **filename** picks the kernel name. This is the rule everyone trips over.

Replace `<name>` with a short, lowercase slug — what users will see internally:

| File you add | Kernel name |
| --- | --- |
| `environment-intro.yml` | `intro` |
| `requirements-ml.txt` | `ml` |
| `pyproject-tools.toml` | `tools` |

**Allowed:** lowercase letters, digits, `.`, `-`, `_` — max 64 characters.  
**Forbidden names:** `user`, `hub`, `base`, `root`, `python3`, `envs`, `share`.

Examples that **will be rejected:** `environment-Intro.yml`, `requirements-My Course.txt`.

Full rules: [Defining environments — Naming](environments.md#naming).

---

## Step 3 — Fill in your packages

### Option A — conda / mamba *(most familiar)* 🐍

```yaml
# display-name: Intro to Python 2026
# (optional comment headers — see below)

name: intro          # optional; filename wins if omitted
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
  - matplotlib
  - pandas
```

Save as e.g. `environment-intro.yml` and open a PR. That is it.

`ipykernel` is installed automatically — **do not** list it unless you really want to
pin a version.

(exporting-from-history)=
### Exporting from an existing local env ⚠️

The conference server is **Linux**. A plain `conda env export` / `mamba env export`
dumps every transitive dependency with **platform-specific build strings**
(`osx-arm64`, `win-64`, …). That file often fails to solve on Linux.

**Do not** commit a full dump. Either hand-write the YAML (as above), or export
only what you explicitly installed:

```bash
conda env export --from-history > environment-intro.yml
# or
mamba env export --from-history > environment-intro.yml
```

Then:

1. Rename to `environment-<name>.yml` if needed.
2. Delete any trailing `prefix:` line (it is local to your machine).
3. Prefer `channels: [conda-forge]` if the export lists something else.

`--no-builds` alone is **not** enough — it still lists OS-specific transitive
packages. See conda's
[exporting across platforms](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html#exporting-an-environment-file-across-platforms).

Pins with `--from-history` are only as strong as what you typed at install time
(`numpy` vs `numpy=2.0`). That is fine for conference kernels.

### Option B — pip requirements *(no uv knowledge needed)* 📦

```text
# python: 3.12
# display-name: Machine Learning Lab

numpy
scikit-learn
matplotlib
```

Save as `requirements-ml.txt`.

The server creates a virtualenv with **uv** and runs `uv pip install`. You never
touch uv — treat the file like a normal `requirements.txt`.

### Option C — pyproject *(only if you already use this)* ⚙️

```toml
[project]
name = "whatever"          # ignored for naming; filename decides
requires-python = ">=3.12"
dependencies = ["numpy", "pandas"]

[tool.environ]
display-name = "Advanced Tools"
```

Save as `pyproject-tools.toml`.

---

## Optional headers (nice labels & Python version) 💬

Two comment lines at the **top** of `.txt` / `.yml` files control extras pip and
conda ignore anyway:

```text
# python: 3.12
# display-name: Brightcon 2026 — Intro Workshop
```

| Header | Effect if omitted |
| --- | --- |
| `# python:` | Server default (often `3.12`; ask the organisers) |
| `# display-name:` | Kernel shows the `<name>` slug |

For `pyproject-<name>.toml`, use `[tool.environ]` instead — see
[Headers](environments.md#headers).

---

## What happens when you open a PR 🚀

```text
open / update PR  →  Linux validate in staging  →  Check "environ" on your commit
merge to main     →  live env rebuilt            →  kernel appears in JupyterHub
```

Watch the **environ** check on the PR. Click it for the log tail if it is red.
A green check means “this definition builds on the conference Linux server”;
it does **not** yet change the hub.

After merge:

- **Edit** a definition → that kernel is rebuilt from scratch.
- **Delete** a definition → that kernel is removed from the hub.
- **Rename** a file → treated as delete old + create new.
- Unchanged files are skipped on apply.

Participants may need to **restart their single-user server** once from the Hub
control panel before a new kernel shows up.

---

## Copy-paste checklist ✅

Before you open the PR, scan this list:

- [ ] Filename matches `environment-<name>.yml`, `requirements-<name>.txt`, or
      `pyproject-<name>.toml`
- [ ] `<name>` is lowercase and not a [reserved name](environments.md#naming)
- [ ] No duplicate `<name>` — two files must not define the same kernel
- [ ] Python version set (`python=3.12` in conda, or `# python: 3.12` in txt)
- [ ] Packages you need for the notebook actually listed
- [ ] Conda YAML is hand-written or from `--from-history` (no `osx-*` / `win-*` build strings)
- [ ] *(optional)* `# display-name:` set so the launcher looks friendly
- [ ] Open a PR and wait for the **environ** check (not a direct push to `main`)

---

## Pinning versions *(optional, for reproducibility)* 📌

Conda: pin in the YAML as you normally would (`numpy=2.0`, etc.).

Pip: add a sibling lock file:

```text
requirements-ml.txt      ← human-edited list (what you commit)
requirements-ml.lock     ← exact pins; server runs uv pip sync when present
```

Generate the lock locally **only if** you already use uv:

```bash
uv pip compile requirements-ml.txt -o requirements-ml.lock
```

No uv? Skip the lock — unpinned `requirements-ml.txt` works fine; the server
installs current compatible versions.

Details: [Lock files](environments.md#lock-files).

---

## When things go wrong 🔧

| Symptom | Likely cause |
| --- | --- |
| Kernel never appears | Wrong filename pattern; check [Step 2](#step-2--name-the-file-correctly-) |
| Build fails on server | Invalid YAML/TOML, bad `# python:` value, conda solve error |
| Missing `osx-*` / `win-*` packages on Linux | Full `env export` dump — re-export with {ref}`--from-history <exporting-from-history>` |
| Duplicate-name warning | Two files map to the same `<name>` — rename or remove one |
| Deleted kernel still listed | Old env cached; admins can force a rebuild |

Open the **environ** check on the PR for the Linux log. If Checks are not
configured on the server, ping the organisers with your PR link — they can
read the job log.

---

## Full specification 📖

This page is the short path. For every edge case (bare `environment.yml`, inner
`name:` vs filename, search roots, deletions):

→ **[Defining environments](environments.md)** — the complete, normative spec.
