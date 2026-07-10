"""Structured failure helpers for agent functions.

Two entry points, one shape:

- :func:`fail` builds an ``AgentResult`` failure for early returns.
- :class:`AgentError` raises the same failure from deep inside a function
  body (including code running in ``asyncio.to_thread``); the
  ``@agent_function`` decorator converts it back into the prebuilt result.

Failure ``data`` carries ``error_code`` (from the closed :data:`ERROR_CODES`
vocabulary) and, when available, ``suggestions`` (close-match candidates for
the invalid input) so MCP clients can react programmatically.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from zeeb_agents._utils import AgentResult

# Closed vocabulary of machine-readable failure codes.  Documented in
# agent_docs/principles.md; a drift-guard test keeps the two in sync.
ERROR_CODES = frozenset(
    {
        "app_not_found",
        "model_not_found",
        "table_not_found",
        "user_not_found",
        "log_file_not_found",
        "field_not_found",
        "file_not_found",
        "function_not_found",
        "setting_not_found",
        "env_key_not_found",
        "already_exists",
        "invalid_identifier",
        "invalid_field_type",
        "invalid_field_spec",
        "invalid_meta",
        "invalid_permission",
        "invalid_authentication",
        "invalid_input",
        "invalid_sql",
        "invalid_regex",
        "outside_project_root",
        "no_user_table",
        "server_not_reachable",
        "dependency_missing",
        "permission_denied",
        "no_project_id",
        "project_not_found",
        "runtime_not_configured",
    }
)


def fail(
    message: str,
    *,
    code: str,
    suggestions: list[str] | None = None,
    **data: Any,
) -> AgentResult:
    """Build a failure ``AgentResult`` with a machine-readable ``error_code``."""
    if code not in ERROR_CODES:
        raise ValueError(f"Unknown error code '{code}'")
    payload: dict[str, Any] = {k: v for k, v in data.items() if v is not None}
    payload["error_code"] = code
    if suggestions:
        payload["suggestions"] = suggestions
    return AgentResult(success=False, message=message, data=payload)


class AgentError(Exception):
    """Expected, targeted failure raised anywhere inside an agent function.

    Carries a prebuilt :class:`AgentResult`; the ``@agent_function`` decorator
    returns it as-is (logged at info level, no traceback).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        suggestions: list[str] | None = None,
        **data: Any,
    ) -> None:
        super().__init__(message)
        self.result = fail(message, code=code, suggestions=suggestions, **data)


def close_matches(value: str, candidates: list[str], n: int = 3) -> list[str]:
    """Return up to *n* close matches for *value* among *candidates*."""
    return get_close_matches(value, candidates, n=n, cutoff=0.6)


def did_you_mean(value: str, candidates: list[str]) -> str:
    """Return a ``" Did you mean: a, b?"`` suffix, or ``""`` if no close match."""
    matches = close_matches(value, candidates)
    if not matches:
        return ""
    return f" Did you mean: {', '.join(matches)}?"
