"""Shared decorator that gives every agent function uniform error handling.

All public ``zeeb_agents`` functions return :class:`AgentResult` and must
never raise.  Instead of repeating the same ``try/except`` boilerplate in
every function, decorate them with :func:`agent_function`.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ParamSpec, overload

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.project import require_project_root

logger = logging.getLogger("zeeb_agents")

P = ParamSpec("P")

_AgentFunc = Callable[..., Awaitable[AgentResult]]


@overload
def agent_function(func: _AgentFunc) -> _AgentFunc: ...


@overload
def agent_function(
    func: None = None, *, resolve_project_root: bool = True
) -> Callable[[_AgentFunc], _AgentFunc]: ...


def agent_function(
    func: _AgentFunc | None = None,
    *,
    resolve_project_root: bool = True,
) -> Any:
    """Wrap an async agent function with uniform error handling.

    - If *resolve_project_root* is true and the wrapped function's signature
      has a ``project_root`` parameter, its value is replaced with
      ``require_project_root(value)`` before the call.  The function body may
      then assume an already-resolved :class:`~pathlib.Path`, while the public
      signature stays ``Path | str | None = None``.
    - :class:`AgentError` (an expected, targeted failure) is logged at info
      level and its prebuilt failure result is returned — the ``data`` dict
      carries ``error_code`` and optional ``suggestions``.
    - ``FileNotFoundError`` / ``PermissionError`` keep the
      ``'<ErrorType>: <details>'`` message format but gain ``error_code``
      ``file_not_found`` / ``permission_denied`` in ``data``.
    - Any other :class:`Exception` is logged via
      ``logger.exception('%s failed', func.__qualname__)`` (logger name:
      ``"zeeb_agents"``) and converted to
      ``AgentResult(success=False, message=f'{type(exc).__name__}: {exc}')``.
    - :class:`AgentResult` return values pass through unchanged.

    Usable both bare (``@agent_function``) and with keyword arguments
    (``@agent_function(resolve_project_root=False)``).
    """

    def decorate(fn: _AgentFunc) -> _AgentFunc:
        signature = inspect.signature(fn)
        needs_root = resolve_project_root and "project_root" in signature.parameters

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> AgentResult:
            try:
                if needs_root:
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    given = bound.arguments.get("project_root")
                    if isinstance(given, str):
                        given = Path(given)
                    try:
                        bound.arguments["project_root"] = require_project_root(given)
                    except RuntimeError as exc:
                        logger.info("%s failed: %s", fn.__qualname__, exc)
                        return fail(str(exc), code="no_project_root")
                    args, kwargs = bound.args, bound.kwargs
                return await fn(*args, **kwargs)
            except AgentError as exc:
                # Expected, targeted failure — no traceback needed.
                logger.info("%s failed: %s", fn.__qualname__, exc)
                return exc.result
            except FileNotFoundError as exc:
                logger.exception("%s failed", fn.__qualname__)
                return fail(f"FileNotFoundError: {exc}", code="file_not_found")
            except PermissionError as exc:
                logger.exception("%s failed", fn.__qualname__)
                return fail(f"PermissionError: {exc}", code="permission_denied")
            except Exception as exc:
                logger.exception("%s failed", fn.__qualname__)
                return AgentResult(
                    success=False, message=f"{type(exc).__name__}: {exc}"
                )

        return wrapper

    if func is not None:
        return decorate(func)
    return decorate
