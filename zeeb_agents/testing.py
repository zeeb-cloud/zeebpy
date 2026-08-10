"""Agent functions for running the project's test suite."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function

_COUNT_RES = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) error"),
    "skipped": re.compile(r"(\d+) skipped"),
}


#: pytest's short-summary lines ("FAILED tests/test_blog.py::test_create - ...").
#: Emitted by default and under ``-q``, so the failing node ids are available
#: without re-running the suite verbosely.
_FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)

#: Cap on reported node ids — a suite failing wholesale must not push a
#: thousand-line list through the result envelope.
_MAX_FAILED_TESTS = 20


def _parse_failed_tests(output: str) -> list[str]:
    """Return the node ids pytest listed as FAILED/ERROR, in order, deduped."""
    seen: dict[str, None] = {}
    for node in _FAILED_LINE_RE.findall(output):
        seen.setdefault(node, None)
        if len(seen) >= _MAX_FAILED_TESTS:
            break
    return list(seen)


def _parse_pytest_output(output: str) -> dict:
    """Extract pass/fail/error/skipped counts from pytest's summary line.

    Each count is matched independently: pytest orders them by outcome
    ("2 failed, 1 passed") and omits absent ones entirely ("3 failed in
    1.20s") — the old passed-first pattern silently reported all-failing
    runs as zero failures.
    """
    for line in reversed(output.splitlines()):
        if not any(token in line for token in ("passed", "failed", "error", "skipped")):
            continue
        return {
            key: int(match.group(1)) if (match := rx.search(line)) else 0
            for key, rx in _COUNT_RES.items()
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
        failed_tests (list[str]): node ids of the failing/erroring tests (up to
            20), so a caller can name them without re-reading the full output.
        no_tests (bool): pytest collected nothing (exit code 5).
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
        data={
            **counts,
            "failed_tests": _parse_failed_tests(output),
            "output": output,
            "returncode": returncode,
            "no_tests": no_tests,
        },
    )
