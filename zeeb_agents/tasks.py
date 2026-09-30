"""Agent functions for background task scaffolding."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    escape_docstring,
    remove_route_function,
    skip_result,
    validate_if_exists,
    write_source,
)
from zeeb_agents._utils.errors import AgentError, close_matches, did_you_mean, fail
from zeeb_agents._utils.project import get_app_path
from zeeb_agents._utils.validation import ensure_identifier

_TASKS_HEADER = '''\
"""Background tasks for the {app} app.

Tasks can be triggered manually, via a scheduler (e.g. APScheduler),
or via a Celery-like worker.

Usage example (APScheduler)::

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apps.{app}.tasks import {example_task}

    scheduler = AsyncIOScheduler()
    scheduler.add_job({example_task}, "cron", hour=0)
    scheduler.start()
"""

from __future__ import annotations

import asyncio
'''

_TASK_BLOCK = '''\

async def {function_name}() -> None:
    """TODO: implement {function_name}.

    Schedule: {schedule_comment}
    """
    pass
'''


def _tasks_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "tasks.py"


def _extract_task_names(source: str) -> list[str]:
    """Return all async def function names in the source."""
    return re.findall(r"^async def (\w+)\s*\(", source, re.MULTILINE)


@agent_function
async def create_task(
    app: str,
    function_name: str,
    schedule: str | None = None,
    project_root: Path | None = None,
    if_exists: str = "error",
) -> AgentResult:
    """Scaffold an async task function in ``apps/{app}/tasks.py``.

    Creates ``tasks.py`` with a standard header if it does not yet exist.

    Args:
        app: App directory name.
        function_name: Snake-case function name for the task.
        schedule: Optional cron expression (e.g. ``"0 * * * *"`` for every hour)
            or human-readable description.  Used only as a comment in the stub.
        project_id: The host-assigned project id (required).
        if_exists: ``"error"`` (default) or ``"skip"`` (succeed and change
            nothing if it already exists — makes retries idempotent; the
            result then carries ``skipped=True``).

    Example::

        await create_task("billing", "send_monthly_invoices", schedule="0 9 1 * *")

    Returns data (on success):
        app (str): the app directory name
        function_name (str): the task function name
        schedule (str | None): the ``schedule`` argument as passed (may be
            ``None``)
        path (str): ``tasks.py`` path relative to the project root
        file_created (bool): ``True`` if ``tasks.py`` was newly created, ``False``
            if it already existed and was appended to
        skipped (bool): present and ``True`` when the task existed and
            ``if_exists="skip"``

    Notes:
        - A task with the same name fails with ``error_code="already_exists"``
          unless ``if_exists="skip"``.
    """
    ensure_identifier(function_name, "function name")
    validate_if_exists(if_exists)
    if schedule is not None and not isinstance(schedule, str):
        return fail(f"schedule must be a string, got {schedule!r}", code="invalid_input")
    root = project_root
    tasks_path = _tasks_file(app, root)

    def _write() -> bool:
        created = False
        if not tasks_path.exists():
            header = _TASKS_HEADER.format(app=app, example_task=function_name)
            write_source(tasks_path, header)
            created = True

        content = tasks_path.read_text(encoding="utf-8")
        if re.search(rf"^async def {re.escape(function_name)}\s*\(", content, re.MULTILINE):
            raise AgentError(
                f"Task '{function_name}' already exists in tasks.py.",
                code="already_exists",
                function=function_name,
            )

        # The schedule is free text that lands in the task's docstring.
        schedule_comment = escape_docstring(schedule or "manual / call directly")
        block = _TASK_BLOCK.format(
            function_name=function_name,
            schedule_comment=schedule_comment,
        )
        write_source(tasks_path, content.rstrip("\n") + "\n" + block)
        return created

    try:
        created = await asyncio.to_thread(_write)
    except AgentError as exc:
        if if_exists == "skip" and (exc.result.data or {}).get("error_code") == "already_exists":
            return skip_result(
                f"Task '{function_name}' already exists in apps/{app}/tasks.py; skipped",
                app=app,
                function_name=function_name,
                schedule=schedule,
                path=str(tasks_path.relative_to(root)),
                file_created=False,
            )
        raise
    rel = str(tasks_path.relative_to(root))
    action = "created" if created else "updated"
    return AgentResult(
        success=True,
        message=f"Task '{function_name}' added — {rel} {action}.",
        data={
            "app": app,
            "function_name": function_name,
            "schedule": schedule,
            "path": rel,
            "file_created": created,
        },
    )


@agent_function
async def list_tasks(
    app: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return all async task functions defined in ``apps/{app}/tasks.py``.

    Args:
        app: App directory name.
        project_id: The host-assigned project id (required).

    Returns data (always):
        app (str): the app directory name (omitted when ``tasks.py`` is missing)
        tasks (list[str]): async ``def`` function names found in ``tasks.py``;
            empty list when no ``tasks.py`` exists
        count (int): len(tasks)

    Notes:
        - When ``tasks.py`` does not exist this still returns ``success=True``
          with ``data={"tasks": [], "count": 0}`` (no ``app`` key).
    """
    tasks_path = _tasks_file(app, project_root)

    if not tasks_path.exists():
        return AgentResult(
            success=True,
            message=f"No tasks.py found for app '{app}'.",
            data={"tasks": [], "count": 0},
        )

    def _read() -> list[str]:
        source = tasks_path.read_text(encoding="utf-8")
        return _extract_task_names(source)

    task_names = await asyncio.to_thread(_read)
    return AgentResult(
        success=True,
        message=f"Found {len(task_names)} task(s) in apps/{app}/tasks.py.",
        data={"app": app, "tasks": task_names, "count": len(task_names)},
    )


@agent_function
async def delete_task(
    app: str,
    function_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove an async task function from ``apps/{app}/tasks.py``.

    Removes the entire ``async def <function_name>`` block — located through
    the AST, decorators included — and nothing after it.

    Args:
        app: App directory name.
        function_name: Name of the task function to remove.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name
        function_name (str): the removed task function name

    Notes:
        - On failure (missing ``tasks.py``, or the task is not found) ``data``
          is ``None``.
    """
    tasks_path = _tasks_file(app, project_root)

    if not tasks_path.exists():
        return fail(
            f"tasks.py not found for app '{app}'.", code="file_not_found", missing="tasks.py"
        )

    def _remove() -> None:
        source = tasks_path.read_text(encoding="utf-8")
        # AST-located: the old "up to the next top-level async def" span ran on
        # over any sync function, class or statement in between, deleting it.
        new_source = remove_route_function(source, function_name)
        if new_source is None:
            names = re.findall(r"async def (\w+)", source)
            hint = did_you_mean(function_name, names)
            if not hint:
                hint = f" Tasks present: {', '.join(names) or '(none)'}."
            raise AgentError(
                f"Task '{function_name}' not found in tasks.py.{hint}",
                code="function_not_found",
                suggestions=close_matches(function_name, names),
                tasks=names,
            )
        write_source(tasks_path, new_source)

    await asyncio.to_thread(_remove)
    return AgentResult(
        success=True,
        message=f"Task '{function_name}' removed from apps/{app}/tasks.py.",
        data={"app": app, "function_name": function_name},
    )
