"""Agent functions for running the project's test suite."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import require_project_root

_RESULT_RE = re.compile(
    r"(?P<passed>\d+) passed"
    r"(?:, (?P<failed>\d+) failed)?"
    r"(?:, (?P<error>\d+) error)?"
    r"(?:, (?P<skipped>\d+) skipped)?",
    re.IGNORECASE,
)


def _parse_pytest_output(output: str) -> dict:
    """Extract pass/fail/error/skipped counts from pytest output."""
    for line in reversed(output.splitlines()):
        m = _RESULT_RE.search(line)
        if m:
            return {
                "passed": int(m.group("passed") or 0),
                "failed": int(m.group("failed") or 0),
                "errors": int(m.group("error") or 0),
                "skipped": int(m.group("skipped") or 0),
            }
    return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}


async def run_tests(
    path: str | None = None,
    verbose: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """Run the project test suite via pytest.

    Args:
        path: Path to a specific test file or directory.  Runs all tests
              in the project if ``None``.
        verbose: Pass ``-v`` to pytest for detailed output.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _run() -> tuple[int, str, str]:
            cmd = [sys.executable, "-m", "pytest"]
            if verbose:
                cmd.append("-v")
            else:
                cmd.append("-q")
            if path:
                cmd.append(path)
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
            )
            return proc.returncode, proc.stdout, proc.stderr

        returncode, stdout, stderr = await asyncio.to_thread(_run)
        output = stdout + (f"\n{stderr}" if stderr.strip() else "")
        counts = _parse_pytest_output(output)
        success = returncode == 0
        parts = [f"{counts['passed']} passed"]
        if counts["failed"]:
            parts.append(f"{counts['failed']} failed")
        if counts["errors"]:
            parts.append(f"{counts['errors']} error(s)")
        if counts["skipped"]:
            parts.append(f"{counts['skipped']} skipped")
        return AgentResult(
            success=success,
            message=", ".join(parts),
            data={**counts, "output": output, "returncode": returncode},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
