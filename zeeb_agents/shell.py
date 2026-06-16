"""Agent functions for running project management commands."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function


@agent_function
async def run_management_command(
    command: str,
    args: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Run a ``manage.py`` management command and return its output.

    Equivalent to calling ``python manage.py <command> [args...]`` from the
    project root.

    Args:
        command: Management command name (e.g. ``"migrate"``, ``"shell"``,
                 ``"createsuperuser"``).
        args: Optional list of additional arguments/flags.
        project_root: Auto-detected if ``None``.

    Returns data (always):
        command (str): the command that was run.
        args (list[str]): the extra arguments passed.
        returncode (int): the subprocess exit code.
        output (str): combined stdout + stderr.

    Notes:
        - ``success`` is ``True`` only when ``returncode == 0``.
        - This spawns a real subprocess; avoid interactive commands that block
          on stdin.
    """
    root = project_root
    manage_py = root / "manage.py"
    if not manage_py.exists():
        return AgentResult(
            success=False,
            message=f"manage.py not found at {root}",
        )

    cmd = [sys.executable, str(manage_py), command] + (args or [])

    def _run() -> tuple[int, str, str]:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr

    returncode, stdout, stderr = await asyncio.to_thread(_run)
    output = stdout + (f"\n{stderr}" if stderr.strip() else "")
    success = returncode == 0
    return AgentResult(
        success=success,
        message=(
            f"Command '{command}' completed successfully"
            if success
            else f"Command '{command}' exited with code {returncode}"
        ),
        data={
            "command": command,
            "args": args or [],
            "returncode": returncode,
            "output": output,
        },
    )
