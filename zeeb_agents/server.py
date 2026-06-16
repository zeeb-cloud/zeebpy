"""Agent functions for dev-server lifecycle management."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function

_PID_FILENAME = ".zeeb_server.pid"


def _pid_file(root: Path) -> Path:
    return root / _PID_FILENAME


@agent_function
async def start_server(
    addrport: str = "127.0.0.1:9000",
    project_root: Path | None = None,
) -> AgentResult:
    """Start the Zeeb development server as a background process.

    The server PID is stored in ``.zeeb_server.pid`` inside the project root so
    it can be stopped later with :func:`stop_server`.

    Args:
        addrport: ``"host:port"`` string (default ``"127.0.0.1:9000"``).
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        pid (int): PID of the spawned server process
        addrport (str): the ``"host:port"`` the server was started on

    Notes:
        - If a server is already running, returns ``success=False`` with
          ``data={"pid": <existing pid>}``.
        - If the process exits within ~1.5s of launch, returns
          ``success=False`` with ``data=None`` (stale PID file removed).
    """
    root = project_root
    pid_path = _pid_file(root)

    if pid_path.exists():
        pid = int(pid_path.read_text().strip())
        try:
            os.kill(pid, 0)
            return AgentResult(
                success=False,
                message=f"Server already running (PID {pid}). Stop it first with stop_server().",
                data={"pid": pid},
            )
        except ProcessLookupError:
            pid_path.unlink(missing_ok=True)

    manage_py = root / "manage.py"
    cmd = [sys.executable, str(manage_py), "runserver", addrport, "--no-reload"]
    proc = await asyncio.to_thread(
        subprocess.Popen,
        cmd,
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid))
    # Brief pause to let the server start (or fail fast)
    await asyncio.sleep(1.5)
    if proc.poll() is not None:
        pid_path.unlink(missing_ok=True)
        return AgentResult(success=False, message="Server process exited immediately")

    return AgentResult(
        success=True,
        message=f"Server started at http://{addrport} (PID {proc.pid})",
        data={"pid": proc.pid, "addrport": addrport},
    )


@agent_function
async def stop_server(project_root: Path | None = None) -> AgentResult:
    """Stop the development server started by :func:`start_server`.

    Returns data (on success):
        pid (int): PID of the process that was signalled/stopped

    Notes:
        - When no PID file exists, returns ``success=False`` with ``data=None``.
        - Sends ``SIGTERM`` then escalates to ``SIGKILL`` if still alive; the
          PID file is always removed.
    """
    pid_path = _pid_file(project_root)

    if not pid_path.exists():
        return AgentResult(success=False, message="No server PID file found. Is the server running?")

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        await asyncio.sleep(0.5)
        # Escalate if still alive
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    finally:
        pid_path.unlink(missing_ok=True)

    return AgentResult(
        success=True,
        message=f"Server stopped (PID {pid})",
        data={"pid": pid},
    )


@agent_function
async def get_server_status(project_root: Path | None = None) -> AgentResult:
    """Check whether the development server is currently running.

    Returns data (always):
        running (bool): whether a live server process was found
        pid (int): PID of the running process; present only when
            ``running`` is ``True``

    Notes:
        - Always returns ``success=True``.
        - A PID file pointing at a dead process is treated as not running and
          removed (``data={"running": False}``).
    """
    pid_path = _pid_file(project_root)

    if not pid_path.exists():
        return AgentResult(
            success=True,
            message="Server is not running",
            data={"running": False},
        )

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, 0)
        return AgentResult(
            success=True,
            message=f"Server is running (PID {pid})",
            data={"running": True, "pid": pid},
        )
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return AgentResult(
            success=True,
            message="Server is not running (stale PID file removed)",
            data={"running": False},
        )
