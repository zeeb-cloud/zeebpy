"""Run a project subprocess with a deadline, and kill all of it when it passes.

``subprocess.run(timeout=...)`` kills only the direct child, and then waits for
its pipes to close — which never happens while a grandchild (a pytest worker,
a server a management command started) still holds them. So the child gets its
own session and the whole process group is killed on timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from zeeb_agents._utils.errors import AgentError


@dataclass
class ProcessOutcome:
    """What a finished (or killed) subprocess left behind."""

    returncode: int | None  # None when it was killed at the deadline
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def output(self) -> str:
        """stdout plus stderr (when non-blank), the shape tools report."""
        return self.stdout + (f"\n{self.stderr}" if self.stderr.strip() else "")


def validate_timeout(timeout: object) -> float | None:
    """A positive number of seconds, or ``None`` for no deadline."""
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise AgentError(
            f"timeout must be a positive number of seconds (or None), got {timeout!r}",
            code="invalid_input",
        )
    return float(timeout)


def _kill_group(proc: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run_captured(cmd: list[str], cwd: Path, timeout: float | None) -> ProcessOutcome:
    """Run *cmd* in *cwd* with stdin closed; kill its process group after *timeout*.

    stdin is ``/dev/null`` so an interactive prompt fails instead of blocking
    until the deadline.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return ProcessOutcome(proc.returncode, stdout, stderr, False)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return ProcessOutcome(None, stdout or "", stderr or "", True)
