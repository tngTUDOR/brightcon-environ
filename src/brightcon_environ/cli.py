"""Command line entry point.

Everything the webhook can trigger is also reachable here, so the whole
pipeline can be exercised on a laptop without GitHub in the loop.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .builders import BuildError, destroy, list_environments
from .config import Config, ConfigError, load_config
from .discovery import discover_all
from .jobs import Job, JobLog, StateStore, run_job
from .kernels import list_kernels


def _console_log(config: Config, job: Job) -> JobLog:
    return JobLog(
        Path(config.paths.log_dir) / f"cli-{job.id}.log",
        echo=lambda line: print(line, flush=True),
    )


def _cmd_serve(config: Config, args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    host = args.host or config.server.host
    port = args.port or config.server.port
    if not config.webhook_secret:
        print(
            "warning: GITHUB_WEBHOOK_SECRET is unset;"
            " /hooks/github will reject every delivery",
            file=sys.stderr,
        )
    uvicorn.run(create_app(config), host=host, port=port, log_level=args.log_level)
    return 0


def _cmd_sync(config: Config, args: argparse.Namespace) -> int:
    os.umask(0o022)
    job = Job(
        trigger="cli",
        names=None if args.all else args.names,
        force=args.force,
        before=args.before,
        after=args.after,
    )
    if not args.all and not args.names:
        print("nothing to do: pass environment names or --all", file=sys.stderr)
        return 2

    log = _console_log(config, job)
    try:
        run_job(config, job, log)
    finally:
        log.close()

    print(
        f"\nbuilt={len(job.built)} removed={len(job.removed)} "
        f"skipped={len(job.skipped)} errors={len(job.errors)}"
    )
    return 1 if job.errors else 0


def _cmd_list(config: Config, _args: argparse.Namespace) -> int:
    state = StateStore(config.paths.state_file)
    on_disk = set(list_environments(config))
    kernels = set(list_kernels(config.paths))
    names = sorted(set(state.environments) | on_disk)

    if not names:
        print(f"no environments under {config.paths.env_root}")
        return 0

    width = max(len(name) for name in names)
    for name in names:
        record = state.get(name)
        flags = "".join(
            [
                "e" if name in on_disk else "-",
                "k" if name in kernels else "-",
                "t" if record else "-",
            ]
        )
        detail = f"{record.backend:<10} {record.definition}" if record else "untracked"
        print(f"{name:<{width}}  [{flags}]  {detail}")
    print("\nflags: e=environment on disk, k=kernelspec registered, t=tracked in state")
    return 0


def _cmd_remove(config: Config, args: argparse.Namespace) -> int:
    state = StateStore(config.paths.state_file)
    failures = 0
    for name in args.names:
        try:
            removed = destroy(config, name, log=lambda line: print(line, flush=True))
        except (BuildError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
            continue
        state.drop(name)
        print(f"{name}: {'removed' if removed else 'nothing to remove'}")
    state.save()
    return 1 if failures else 0


def _cmd_plan(config: Config, args: argparse.Namespace) -> int:
    repo_root = Path(args.repo or config.repo.path)
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2

    specs, problems = discover_all(repo_root, config.defaults)
    state = StateStore(config.paths.state_file)

    print(f"scanning {repo_root}")
    for spec in sorted(specs, key=lambda item: item.name):
        record = state.get(spec.name)
        status = "rebuild" if record is None else "known"
        python = f" python={spec.python}" if spec.python else ""
        lock = f" lock={spec.lock_path}" if spec.lock_path else ""
        print(
            f"  {spec.name:<24} {spec.backend:<10} {status:<8} "
            f"{spec.path}{python}{lock} -> {config.paths.env_root / spec.name}"
        )
    for problem in problems:
        print(f"  ! {problem.path}: {problem.reason}", file=sys.stderr)

    print(f"\n{len(specs)} environment(s), {len(problems)} problem(s)")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="environ",
        description="Rebuild JupyterHub environments from a git repository",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help=(
            "path to environ.toml"
            " (default: $ENVIRON_CONFIG or /opt/tljh/config/environ.toml)"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the webhook API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=_cmd_serve)

    sync = sub.add_parser("sync", help="rebuild environments now")
    sync.add_argument("names", nargs="*", help="environments to rebuild")
    sync.add_argument(
        "--all", action="store_true", help="rebuild every definition found"
    )
    sync.add_argument("--force", action="store_true", help="rebuild even if unchanged")
    sync.add_argument("--before", default=None, help="diff against this commit instead")
    sync.add_argument(
        "--after", default=None, help="check out this commit before building"
    )
    sync.set_defaults(func=_cmd_sync)

    listing = sub.add_parser("list", help="show known environments")
    listing.set_defaults(func=_cmd_list)

    remove = sub.add_parser("remove", help="delete environments and their kernels")
    remove.add_argument("names", nargs="+")
    remove.set_defaults(func=_cmd_remove)

    plan = sub.add_parser("plan", help="dry run: show what would be built")
    plan.add_argument(
        "--repo", default=None, help="scan this directory instead of the clone"
    )
    plan.set_defaults(func=_cmd_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return args.func(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
