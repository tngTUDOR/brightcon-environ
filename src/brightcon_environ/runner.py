"""Subprocess helper.

Every external command goes through :func:`run`: argument lists only, never a
shell, with output streamed line by line into the job log.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

LogFn = Callable[[str], None]


class CommandError(Exception):
    """A command exited non-zero or did not finish in time."""

    def __init__(
        self, args: Sequence[str], returncode: int | None, output: str
    ) -> None:
        self.args_list = list(args)
        self.returncode = returncode
        self.output = output
        rendered = shlex.join(str(arg) for arg in args)
        if returncode is None:
            super().__init__(f"command timed out: {rendered}")
        else:
            super().__init__(f"command failed with exit code {returncode}: {rendered}")


@dataclass(frozen=True)
class RunResult:
    args: list[str]
    returncode: int
    output: str

    @property
    def stdout(self) -> str:
        return self.output


def run(
    args: Sequence[str | Path],
    *,
    log: LogFn | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> RunResult:
    """Run a command, streaming its combined output to ``log``."""
    argv = [str(arg) for arg in args]
    if log:
        log(f"$ {shlex.join(argv)}")

    process = subprocess.Popen(  # noqa: S603 - argv is a list, shell is never used
        argv,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        errors="replace",
    )

    collected: list[str] = []

    def drain() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            collected.append(line)
            if log:
                log(line)

    # Read in a thread so that a silent, hung process still hits the timeout.
    reader = threading.Thread(target=drain, name="run-reader", daemon=True)
    reader.start()

    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=5)
        output = "\n".join(collected)
        if log:
            log(f"! timed out after {timeout}s")
        raise CommandError(argv, None, output) from None

    reader.join(timeout=30)
    output = "\n".join(collected)

    if check and returncode != 0:
        raise CommandError(argv, returncode, output)

    return RunResult(args=argv, returncode=returncode, output=output)
