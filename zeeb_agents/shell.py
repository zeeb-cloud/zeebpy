"""Agent functions for running project management commands."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.process import run_captured, validate_timeout

#: Default ``run_management_command`` deadline. A management command that
#: needs longer (a large data migration) can pass its own; ``None`` waits.
DEFAULT_COMMAND_TIMEOUT = 300.0


@agent_function
async def run_management_command(
    command: str,
    args: list[str] | None = None,
    project_root: Path | None = None,
    timeout: float | None = DEFAULT_COMMAND_TIMEOUT,
) -> AgentResult:
    """Run a ``manage.py`` management command and return its output.

    Equivalent to calling ``python manage.py <command> [args...]`` from the
    project root.

    Args:
        command: Management command name (e.g. ``"migrate"``, ``"shell"``,
                 ``"createsuperuser"``).
        args: Optional list of additional arguments/flags.
        project_id: The host-assigned project id (required).
        timeout: Seconds before the command is killed (its whole process
            group). Default 300; ``None`` waits indefinitely.

    Returns data (always, once the command was started):
        command (str): the command that was run.
        args (list[str]): the extra arguments passed.
        returncode (int | None): the subprocess exit code (``None`` when it
            was killed at *timeout*).
        output (str): combined stdout + stderr.
        timed_out (bool): the command was killed at *timeout*.

    Notes:
        - ``success`` is ``True`` only when ``returncode == 0``.
        - This spawns a real subprocess with stdin closed, so a command that
          prompts (``createsuperuser`` without ``--noinput``) fails instead of
          hanging; anything else that never ends is killed at *timeout*.
    """
    root = project_root
    deadline = validate_timeout(timeout)
    manage_py = root / "manage.py"
    if not manage_py.exists():
        return AgentResult(
            success=False,
            message=f"manage.py not found at {root}",
        )

    cmd = [sys.executable, str(manage_py), command] + (args or [])
    outcome = await asyncio.to_thread(run_captured, cmd, root, deadline)
    returncode = outcome.returncode
    success = returncode == 0
    if outcome.timed_out:
        message = f"Command '{command}' timed out after {deadline:g}s and was killed"
    elif success:
        message = f"Command '{command}' completed successfully"
    else:
        message = f"Command '{command}' exited with code {returncode}"
    return AgentResult(
        success=success,
        message=message,
        data={
            "command": command,
            "args": args or [],
            "returncode": returncode,
            "output": outcome.output,
            "timed_out": outcome.timed_out,
        },
    )
