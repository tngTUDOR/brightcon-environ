"""The rebuild pipeline, its persisted state, and the worker that serialises it.

Rebuilds are queued and executed one at a time by a single background thread,
so two pushes landing together can never fight over the same environment root.

Jobs run in one of two modes:

* ``apply`` -- tear down and recreate live environments under ``paths.env_root``
  and register shared kernels (push to main, manual rebuild).
* ``validate`` -- build into a disposable staging tree for a pull request head;
  never touch the live env root or shared kernelspecs.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from queue import Queue

from .builders import BuildError, BuildResult, build, destroy, env_path
from .config import Config
from .discovery import DiscoveryProblem, EnvSpec, discover_all, resolve_changes
from .git_repo import NULL_SHA, GitError, GitRepo
from .github_checks import ChecksClient, checks_client_from_config
from .kernels import kernel_dir
from .runner import CommandError

MAX_JOB_HISTORY = 100
LOG_TAIL_LINES = 400

logger = logging.getLogger("brightcon_environ.jobs")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- state


@dataclass
class EnvRecord:
    """What we last built for one environment."""

    name: str
    backend: str
    definition: str
    display_name: str
    blob_sha: str | None = None
    commit: str | None = None
    built_at: str = ""
    duration_seconds: float = 0.0


class StateStore:
    """A small JSON file recording the last successful build per environment."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.environments: dict[str, EnvRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return
        entries = raw.get("environments", {})
        if not isinstance(entries, dict):
            return
        known = {f.name for f in EnvRecord.__dataclass_fields__.values()}
        self.environments = {
            name: EnvRecord(**{k: v for k, v in entry.items() if k in known})
            for name, entry in entries.items()
            if isinstance(entry, dict)
        }

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": _now(),
                "environments": {
                    name: asdict(record)
                    for name, record in sorted(self.environments.items())
                },
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, name: str) -> EnvRecord | None:
        return self.environments.get(name)

    def put(self, record: EnvRecord) -> None:
        self.environments[record.name] = record

    def drop(self, name: str) -> None:
        self.environments.pop(name, None)

    def definition_to_name(self) -> dict[str, str]:
        """Reverse lookup so a deleted file still resolves to its environment."""
        return {record.definition: name for name, record in self.environments.items()}


# ----------------------------------------------------------------------------- job


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Job:
    """One rebuild request and its outcome."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    trigger: str = "manual"
    mode: str = "apply"
    """``apply`` mutates live kernels; ``validate`` builds in staging only."""
    before: str | None = None
    after: str | None = None
    head_sha: str | None = None
    """Commit SHA used for GitHub Check Runs (push ``after`` or PR head)."""
    pr_number: int | None = None
    names: list[str] | None = None
    force: bool = False
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    commit: str | None = None
    built: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_path: str | None = None
    check_run_id: int | None = None

    def summary(self) -> dict:
        return {
            "id": self.id,
            "trigger": self.trigger,
            "mode": self.mode,
            "status": str(self.status),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "commit": self.commit,
            "built": self.built,
            "removed": self.removed,
            "skipped": self.skipped,
            "errors": self.errors,
            "log_path": self.log_path,
            "check_run_id": self.check_run_id,
            "pr_number": self.pr_number,
        }


class JobLog:
    """Streams a job's output to a file while keeping a tail in memory."""

    def __init__(
        self, path: Path | None, echo: Callable[[str], None] | None = None
    ) -> None:
        self.path = path
        self.echo = echo
        self.lines: deque[str] = deque(maxlen=LOG_TAIL_LINES)
        self._handle = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8")

    def write(self, line: str) -> None:
        self.lines.append(line)
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()
        if self.echo is not None:
            self.echo(line)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def tail(self, count: int = LOG_TAIL_LINES) -> list[str]:
        return list(self.lines)[-count:]


# ------------------------------------------------------------------------ pipeline


def _blob_fingerprint(repo: GitRepo, spec: EnvSpec) -> str | None:
    parts = [repo.blob_sha(spec.path) or "?"]
    if spec.lock_path:
        parts.append(repo.blob_sha(spec.lock_path) or "?")
    return ":".join(parts)


def _is_intact(config: Config, name: str) -> bool:
    """Whether a previously recorded environment is still usable on disk."""
    try:
        target = env_path(config, name)
    except BuildError:
        return False
    return (target / "bin" / "python").exists() and kernel_dir(
        config.paths, name
    ).is_dir()


def _report_problems(
    problems: Iterable[DiscoveryProblem], job: Job, log: JobLog
) -> None:
    for problem in problems:
        message = f"{problem.path}: {problem.reason}"
        log.write(f"! {message}")
        job.errors.append(message)


def _staging_config(config: Config, job_id: str) -> tuple[Config, Path]:
    """Build config rooted under ``state_dir/validate/<job_id>``."""
    staging_root = config.paths.state_dir / "validate" / job_id
    staging_paths = replace(
        config.paths,
        env_root=staging_root / "envs",
        kernel_prefix=staging_root / "prefix",
        state_dir=staging_root / "state",
    )
    return replace(config, paths=staging_paths), staging_root


def run_job(config: Config, job: Job, log: JobLog) -> Job:
    """Execute one rebuild request end to end."""
    job.status = JobStatus.RUNNING
    job.started_at = _now()

    if job.mode == "validate":
        return _run_validate(config, job, log)
    return _run_apply(config, job, log)


def _prepare_repo(config: Config, job: Job, log: JobLog) -> GitRepo | None:
    try:
        repo = GitRepo(config.repo, config.tools, log=log.write)
        repo.ensure_clone()
        repo.fetch()
        if job.mode == "validate" and job.pr_number is not None:
            fetched = repo.fetch_pull(job.pr_number)
            target = job.after or fetched
            job.commit = repo.checkout(target)
        else:
            job.commit = repo.checkout(job.after)
        log.write(f"repository at {job.commit}")
        return repo
    except (GitError, CommandError) as exc:
        log.write(f"! {exc}")
        job.errors.append(str(exc))
        job.status = JobStatus.FAILED
        job.finished_at = _now()
        return None


def _select_specs(
    config: Config,
    job: Job,
    repo: GitRepo,
    state: StateStore,
    log: JobLog,
) -> tuple[list[EnvSpec], list[str]]:
    all_specs, problems = discover_all(repo.path, config.defaults)
    _report_problems(problems, job, log)

    if job.names is not None:
        wanted = set(job.names)
        specs = [spec for spec in all_specs if spec.name in wanted]
        missing = wanted - {spec.name for spec in specs}
        for name in sorted(missing):
            message = f"no definition file found for environment {name!r}"
            log.write(f"! {message}")
            job.errors.append(message)
        return specs, []

    if job.before is None or job.before == NULL_SHA:
        log.write(f"full scan: {len(all_specs)} definition file(s)")
        return all_specs, []

    changed = repo.changed_paths(job.before, job.commit or "HEAD")
    if changed is None:
        log.write(
            f"cannot diff {job.before}..{job.commit}; falling back to a full scan"
        )
        return all_specs, []

    log.write(f"{len(changed)} path(s) changed in this push")
    specs, removals, change_problems = resolve_changes(
        repo.path,
        changed,
        config.defaults,
        known=state.definition_to_name(),
    )
    _report_problems(change_problems, job, log)
    return specs, removals


def _run_apply(config: Config, job: Job, log: JobLog) -> Job:
    state = StateStore(config.paths.state_file)
    repo = _prepare_repo(config, job, log)
    if repo is None:
        return job

    specs, removals = _select_specs(config, job, repo, state, log)

    for name in removals:
        try:
            if destroy(config, name, log=log.write):
                job.removed.append(name)
            state.drop(name)
        except (BuildError, OSError) as exc:
            log.write(f"! {exc}")
            job.errors.append(str(exc))

    for spec in specs:
        fingerprint = _blob_fingerprint(repo, spec)
        record = state.get(spec.name)
        if (
            not job.force
            and record is not None
            and record.blob_sha == fingerprint
            and record.definition == spec.path
            and _is_intact(config, spec.name)
        ):
            log.write(f"= {spec.name} unchanged, skipping")
            job.skipped.append(spec.name)
            continue

        try:
            result: BuildResult = build(config, spec, repo.path, log=log.write)
        except (BuildError, OSError) as exc:
            log.write(f"! {exc}")
            job.errors.append(str(exc))
            state.drop(spec.name)
            continue

        log.write(
            f"+ {result.name} built in {result.duration_seconds}s -> {result.env_path}"
        )
        job.built.append(result.name)
        state.put(
            EnvRecord(
                name=result.name,
                backend=str(result.backend),
                definition=result.definition,
                display_name=spec.kernel_display_name,
                blob_sha=fingerprint,
                commit=job.commit,
                built_at=_now(),
                duration_seconds=result.duration_seconds,
            )
        )

    state.save()
    job.status = JobStatus.FAILED if job.errors else JobStatus.SUCCEEDED
    job.finished_at = _now()
    log.write(
        f"done: {len(job.built)} built, {len(job.removed)} removed, "
        f"{len(job.skipped)} skipped, {len(job.errors)} error(s)"
    )
    return job


def _run_validate(config: Config, job: Job, log: JobLog) -> Job:
    """Build changed definitions under a staging prefix; never touch live envs."""
    live_state = StateStore(config.paths.state_file)
    staging_config, staging_root = _staging_config(config, job.id)
    staging_root.mkdir(parents=True, exist_ok=True)
    log.write(f"validate mode: staging under {staging_root}")

    try:
        repo = _prepare_repo(config, job, log)
        if repo is None:
            return job

        # Always rebuild candidates for a PR; fingerprint skip uses live state.
        job.force = True
        specs, removals = _select_specs(config, job, repo, live_state, log)

        for name in removals:
            log.write(f"~ {name} would be removed on merge (not applied in validate)")
            job.removed.append(name)

        for spec in specs:
            try:
                result = build(staging_config, spec, repo.path, log=log.write)
            except (BuildError, OSError) as exc:
                log.write(f"! {exc}")
                job.errors.append(str(exc))
                continue
            log.write(
                f"+ {result.name} validated in {result.duration_seconds}s "
                f"-> {result.env_path}"
            )
            job.built.append(result.name)

        job.status = JobStatus.FAILED if job.errors else JobStatus.SUCCEEDED
        job.finished_at = _now()
        log.write(
            f"done (validate): {len(job.built)} built, "
            f"{len(job.removed)} would-remove, {len(job.errors)} error(s)"
        )
        return job
    finally:
        if staging_root.exists():
            log.write(f"removing staging tree {staging_root}")
            shutil.rmtree(staging_root, ignore_errors=True)


# -------------------------------------------------------------------------- queue


def _report_check_start(client: ChecksClient, job: Job) -> None:
    head_sha = job.head_sha
    if not head_sha or head_sha == NULL_SHA:
        return
    title = (
        "Validating environments…"
        if job.mode == "validate"
        else "Rebuilding environments…"
    )
    check_id = client.create(
        head_sha=head_sha,
        job_id=job.id,
        title=title,
        summary=f"Job `{job.id}` ({job.mode})",
    )
    if check_id is not None:
        job.check_run_id = check_id


def _report_check_finish(client: ChecksClient, job: Job, log: JobLog) -> None:
    if job.check_run_id is None:
        return
    failed = bool(job.errors) or job.status is JobStatus.FAILED
    conclusion = "failure" if failed else "success"
    title = (
        f"{len(job.errors)} error(s)"
        if failed
        else (
            f"{len(job.built)} built, {len(job.removed)} removed, "
            f"{len(job.skipped)} skipped"
        )
    )
    summary_parts = [
        f"**Job** `{job.id}` ({job.mode})",
        f"**Status** {job.status}",
    ]
    if job.commit:
        summary_parts.append(f"**Commit** `{job.commit}`")
    if job.built:
        summary_parts.append("**Built:** " + ", ".join(f"`{n}`" for n in job.built))
    if job.removed:
        label = "Would remove" if job.mode == "validate" else "Removed"
        names = ", ".join(f"`{n}`" for n in job.removed)
        summary_parts.append(f"**{label}:** {names}")
    if job.skipped:
        summary_parts.append("**Skipped:** " + ", ".join(f"`{n}`" for n in job.skipped))
    if job.errors:
        summary_parts.append("**Errors:**")
        summary_parts.extend(f"- {error}" for error in job.errors)
    text = "\n".join(log.tail()) or "(no log output)"
    try:
        client.complete(
            job.check_run_id,
            conclusion=conclusion,
            title=title,
            summary="\n\n".join(summary_parts),
            text=text,
        )
    except Exception:  # noqa: BLE001 - never fail the job on reporting
        logger.exception("failed to complete GitHub Check Run %s", job.check_run_id)


class JobQueue:
    """Serialises rebuilds onto a single worker thread."""

    def __init__(
        self,
        config: Config,
        *,
        checks: ChecksClient | None = None,
    ) -> None:
        self.config = config
        if checks is None:
            checks = checks_client_from_config(config)
        self.checks = checks
        self._queue: Queue[Job | None] = Queue()
        self._jobs: dict[str, Job] = {}
        self._logs: dict[str, JobLog] = {}
        self._order: deque[str] = deque()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # Environments and kernelspecs must be readable by every hub user.
        os.umask(0o022)
        self._thread = threading.Thread(
            target=self._work, name="environ-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=timeout)
        self._thread = None

    def submit(self, job: Job) -> Job:
        log_path = (
            self.config.paths.log_dir
            / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{job.id}.log"
        )
        job.log_path = str(log_path)
        if job.head_sha is None and job.after and job.after != NULL_SHA:
            job.head_sha = job.after
        try:
            _report_check_start(self.checks, job)
        except Exception:  # noqa: BLE001
            logger.exception("failed to create GitHub Check Run for job %s", job.id)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > MAX_JOB_HISTORY:
                stale = self._order.popleft()
                self._jobs.pop(stale, None)
                self._logs.pop(stale, None)
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def tail(self, job_id: str, count: int = LOG_TAIL_LINES) -> list[str]:
        with self._lock:
            log = self._logs.get(job_id)
        return log.tail(count) if log else []

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            ids = list(self._order)[-limit:]
            return [
                self._jobs[job_id] for job_id in reversed(ids) if job_id in self._jobs
            ]

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            log = JobLog(Path(job.log_path) if job.log_path else None)
            with self._lock:
                self._logs[job.id] = log
            try:
                run_job(self.config, job, log)
            except Exception as exc:  # noqa: BLE001 - the worker must never die
                job.status = JobStatus.FAILED
                job.finished_at = _now()
                job.errors.append(f"unexpected failure: {exc!r}")
                log.write(f"! unexpected failure: {exc!r}")
            finally:
                _report_check_finish(self.checks, job, log)
                log.close()
                self._queue.task_done()
