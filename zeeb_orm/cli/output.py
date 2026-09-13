"""One result envelope for every management command.

An agent working on a generated project without the MCP layer drives it through
``python manage.py <command>``, and until now it had to read prose and guess
from an exit code. These helpers give the CLI the same shape the agent tools
already return — ``success`` / ``message`` / ``data`` with an ``error_code`` —
so a caller that knows one interface knows both:

    {"success": false,
     "message": "No migrations found.",
     "data": {"error_code": "file_not_found", "recoverable": true,
              "state_changed": false,
              "next_command": "python manage.py makemigrations",
              "searched_in": "/abs/path/migrations"}}

``next_command`` is required on every failure. That is deliberate: "each error
path names the exact next step" is only true if it cannot be forgotten, so the
signature enforces it rather than a convention document.

The vocabulary is a subset of ``zeeb_agents._utils.errors.ERROR_CODES`` —
:class:`zeeb_orm.scaffold.errors.ScaffoldError` already raises with these codes,
so a scaffold failure translates straight through::

    except ScaffoldError as exc:
        return fail(str(exc), code=exc.code, next_command=..., **exc.data)
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: Codes a management command can report. A closed set, checked at call time:
#: a typo'd code is worse than no code, because a caller branches on it.
CLI_ERROR_CODES = frozenset(
    {
        "project_not_found",
        "file_not_found",
        "app_not_found",
        "model_not_found",
        "invalid_identifier",
        "invalid_input",
        "already_exists",
        "setting_not_found",
        "dependency_missing",
        "permission_denied",
        "partial_failure",
    }
)


def _emit(payload: dict[str, Any], *, json_output: bool, lines: tuple[str, ...]) -> None:
    """Write either the JSON object or the human lines — never both.

    In JSON mode stdout carries exactly one object, so a caller can parse it
    without stripping banners; anything else a command wants to say goes to
    stderr.
    """
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    for line in lines:
        print(line)


def ok(
    message: str,
    *,
    json_output: bool = False,
    lines: tuple[str, ...] = (),
    **data: Any,
) -> int:
    """Report success and return the process exit code (``0``).

    Args:
        message: One-line summary, used verbatim in JSON mode.
        json_output: Emit the envelope instead of prose.
        lines: The human-readable output. Defaults to just *message*.
        **data: Everything the caller learned, for the ``data`` object.

    No ``state_changed`` key here on purpose: a success says what the command
    did, so the flag would carry no information — and on a read-only command
    like ``check`` or ``showmigrations`` it would actively mislead. It appears
    only on failures, where "did anything land before this broke?" is a real
    question.
    """
    payload = {"success": True, "message": message, "data": dict(data)}
    _emit(payload, json_output=json_output, lines=lines or (message,))
    return 0


def fail(
    message: str,
    *,
    code: str,
    next_command: str,
    json_output: bool = False,
    suggestions: list[str] | None = None,
    recoverable: bool = True,
    state_changed: bool = False,
    **data: Any,
) -> int:
    """Report failure and return the process exit code (``1``).

    Args:
        message: What went wrong, in one line.
        code: A member of :data:`CLI_ERROR_CODES`.
        next_command: The exact command that addresses this failure. Required,
            and by convention spelled ``python manage.py …`` — the ``zeeb``
            entry point may not be on PATH inside a project's virtualenv.
        suggestions: Close matches for a mistyped name, when the caller has any.
        recoverable: ``False`` only when re-running cannot possibly help.
        state_changed: ``True`` when the command already wrote something before
            failing, so the caller knows a re-run is not starting from scratch.
    """
    if code not in CLI_ERROR_CODES:
        raise ValueError(f"Unknown CLI error code: {code!r}")

    payload = {
        "success": False,
        "message": message,
        "data": {
            **data,
            "error_code": code,
            "recoverable": recoverable,
            "state_changed": state_changed,
            "next_command": next_command,
            **({"suggestions": suggestions} if suggestions else {}),
        },
    }
    if json_output:
        _emit(payload, json_output=True, lines=())
        return 1

    print(f"Error: {message}", file=sys.stderr)
    for suggestion in suggestions or ():
        print(f"  Did you mean: {suggestion}", file=sys.stderr)
    print(f"Next: {next_command}", file=sys.stderr)
    return 1


def no_project(command: str, *, json_output: bool = False) -> int:
    """The shared "you are not inside a project" failure.

    Every command that needs a project root reports it identically, so an agent
    that has learned to recover from it once has learned it for all of them.
    """
    return fail(
        "Not inside a Zeeb project (no manage.py found in this directory or above).",
        code="project_not_found",
        next_command=f"cd <your project> && python manage.py {command}",
        json_output=json_output,
        recoverable=False,
    )
