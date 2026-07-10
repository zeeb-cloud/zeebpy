"""Agent functions for running the project's test suite."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function

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


@agent_function
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
        project_id: The host-assigned project id (required).

    Returns data (always):
        passed (int), failed (int), errors (int), skipped (int): counts parsed
            from pytest's summary line.
        output (str): full combined pytest stdout + stderr.
        returncode (int): pytest's exit code.

    Notes:
        - ``success`` mirrors ``returncode == 0``.
        - Counts are best-effort, scraped from pytest's summary line; if pytest
          fails before producing a summary they default to ``0``.
    """
    root = project_root

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
    # The test RUN completing is the tool's success, not whether tests passed.
    # pytest exit codes: 0=all passed, 1=some failed, 5=no tests collected — all
    # are valid, reported results. 2/3/4 (interrupted / internal / usage error)
    # mean pytest could not run properly, so those stay failures.
    no_tests = returncode == 5
    success = returncode in (0, 1, 5)
    if no_tests:
        parts = ["no tests collected"]
    else:
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
        data={**counts, "output": output, "returncode": returncode, "no_tests": no_tests},
    )
