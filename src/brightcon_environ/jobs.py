"""The rebuild pipeline, its persisted state, and the worker that serialises it.

Rebuilds are queued and executed one at a time by a single background thread,
so two pushes landing together can never fight over the same environment root.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from queue import Queue

from .builders import BuildError, BuildResult, build, destroy, env_path
from .config import Config
from .discovery import DiscoveryProblem, EnvSpec, discover_all, resolve_changes
from .git_repo import GitError, GitRepo
from .kernels import kernel_dir
from .runner import CommandError

MAX_JOB_HISTORY = 100
LOG_TAIL_LINES = 400


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
    before: str | None = None
    after: str | None = None
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

    def summary(self) -> dict:
        return {
            "id": self.id,
            "trigger": self.trigger,
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


def run_job(config: Config, job: Job, log: JobLog) -> Job:
    """Execute one rebuild request end to end."""
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    state = StateStore(config.paths.state_file)

    try:
        repo = GitRepo(config.repo, config.tools, log=log.write)
        repo.ensure_clone()
        repo.fetch()
        job.commit = repo.checkout(job.after)
        log.write(f"repository at {job.commit}")
    except (GitError, CommandError) as exc:
        log.write(f"! {exc}")
        job.errors.append(str(exc))
        job.status = JobStatus.FAILED
        job.finished_at = _now()
        return job

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
        removals: list[str] = []
    elif job.before is None:
        specs, removals = all_specs, []
        log.write(f"full scan: {len(specs)} definition file(s)")
    else:
        changed = repo.changed_paths(job.before, job.commit or "HEAD")
        if changed is None:
            log.write(
                f"cannot diff {job.before}..{job.commit}; falling back to a full scan"
            )
            specs, removals = all_specs, []
        else:
            log.write(f"{len(changed)} path(s) changed in this push")
            specs, removals, change_problems = resolve_changes(
                repo.path,
                changed,
                config.defaults,
                known=state.definition_to_name(),
            )
            _report_problems(change_problems, job, log)

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


# -------------------------------------------------------------------------- queue


class JobQueue:
    """Serialises rebuilds onto a single worker thread."""

    def __init__(self, config: Config) -> None:
        self.config = config
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
                log.close()
                self._queue.task_done()
