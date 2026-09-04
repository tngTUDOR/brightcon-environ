# brightcon-environ

A small REST service that listens for GitHub push webhooks and rebuilds the
Python environments offered by a JupyterHub. When a commit lands on the watched
branch, every environment whose definition file changed is torn down, created
again from scratch, and its Jupyter kernelspec is re-registered.

Three definition formats are supported: mamba/conda `environment` YAML, pip
requirement lists, and uv project metadata.

```{tip}
**Conference repo contributor?** You probably want
[For contributors](for-contributors.md) — how to add `environment-*.yml` or
`requirements-*.txt`, open a PR, and read the **environ** Check for Linux logs.

**Server admin or maintainer?** Continue below, then see [Deployment](deployment.md)
and [Configuration](configuration.md).
```

## What happens on a push

1. GitHub posts the push to `POST /hooks/github`.
2. The `X-Hub-Signature-256` header is verified against the shared secret, and
   the payload's ref is compared with the watched branch.
3. The job is queued and GitHub is answered immediately with `202 Accepted` and
   a job id, because a rebuild takes far longer than a webhook delivery timeout.
4. A single worker thread fetches and hard-resets the clone to the pushed
   commit, diffs `before..after`, and maps the changed files to environments.
5. Each affected environment is removed together with its kernelspec, built
   again, and the kernelspec is registered.

Jobs run one at a time, so two pushes landing together cannot fight over the
same directory. A build that fails is cleaned up rather than left
half-finished, so a broken kernel never appears in the launcher.

An environment is skipped when the git blob hash of its definition is unchanged
since the last successful build and both the environment and its kernel are
still on disk. If the `before` commit is unknown -- a first run, a force push,
a new branch -- the service falls back to scanning every definition file.

```{toctree}
:maxdepth: 2

for-contributors
environments
configuration
deployment
operating
troubleshooting
development
changelog
```
