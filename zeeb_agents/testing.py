"""Agent functions for running the project's test suite."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.paths import confine_path
from zeeb_agents._utils.process import run_captured, validate_timeout

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


#: Default ``run_tests`` deadline: long enough for a generated suite, short
#: enough that a hung test (an awaited server, a deadlock) does not hold the
#: tool — and the MCP call behind it — forever.
DEFAULT_TEST_TIMEOUT = 600.0


def _checked_test_path(root: Path, path: str) -> str:
    """Validate a ``run_tests`` target; return it unchanged.

    It is handed to pytest as an argument, so a value starting with ``-`` would
    be an *option* (``--basetemp=<dir>`` makes pytest delete that directory).
    It must name something inside the project; a node id's ``::`` suffix is
    allowed and not a path.
    """
    if not isinstance(path, str) or not path.strip():
        raise AgentError("path must be a non-empty string", code="invalid_input")
    if path.lstrip().startswith("-"):
        raise AgentError(
            f"path {path!r} looks like a pytest option; pass a test file, directory "
            "or node id inside the project",
            code="invalid_input",
        )
    confine_path(root, path.split("::", 1)[0], kind="path")
    return path


@agent_function
async def run_tests(
    path: str | None = None,
    verbose: bool = False,
    project_root: Path | None = None,
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
) -> AgentResult:
    """Run the project test suite via pytest.

    Args:
        path: A test file, directory or node id (``tests/test_blog.py::test_x``)
              inside the project. Runs all tests if ``None``.
        verbose: Pass ``-v`` to pytest for detailed output.
        project_id: The host-assigned project id (required).
        timeout: Seconds before the run is killed (its whole process group).
            Default 600; ``None`` waits indefinitely.

    Returns data (always, once pytest was started):
        passed (int), failed (int), errors (int), skipped (int): counts parsed
            from pytest's summary line.
        failed_tests (list[str]): node ids of the failing/erroring tests (up to
            20), so a caller can name them without re-reading the full output.
        no_tests (bool): pytest collected nothing (exit code 5).
        all_passed (bool): the verdict — ``returncode == 0``.
        timed_out (bool): the run was killed at *timeout*.
        output (str): full combined pytest stdout + stderr.
        returncode (int | None): pytest's exit code (``None`` when timed out).

    Notes:
        - ``success`` means *the run completed and reported a result*, not
          that every test passed: exit codes 0 (all passed), 1 (some tests
          failed) and 5 (no tests collected) are all ``success=True``. Exit
          codes 2/3/4 (interrupted, internal error, usage error) and a timeout
          are ``success=False``. Read ``all_passed`` (or ``failed`` /
          ``errors``) for the verdict on the tests themselves.
        - *path* must stay inside the project (``outside_project_root``) and
          must not start with ``-`` (``invalid_input``); it is passed after
          ``--`` so pytest never reads it as an option.
        - Counts are best-effort, scraped from pytest's summary line; if pytest
          fails before producing a summary they default to ``0``.
    """
    root = project_root
    deadline = validate_timeout(timeout)
    cmd = [sys.executable, "-m", "pytest", "-v" if verbose else "-q"]
    if path:
        cmd += ["--", _checked_test_path(root, path)]

    outcome = await asyncio.to_thread(run_captured, cmd, root, deadline)
    output = outcome.output
    returncode = outcome.returncode
    counts = _parse_pytest_output(output)
    # The test RUN completing is the tool's success, not whether tests passed.
    # pytest exit codes: 0=all passed, 1=some failed, 5=no tests collected — all
    # are valid, reported results. 2/3/4 (interrupted / internal / usage error)
    # mean pytest could not run properly, so those stay failures.
    no_tests = returncode == 5
    success = returncode in (0, 1, 5)
    if outcome.timed_out:
        parts = [f"timed out after {deadline:g}s"]
    elif no_tests:
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
            "all_passed": returncode == 0,
            "timed_out": outcome.timed_out,
        },
    )
