"""Shared decorator that gives every agent function uniform error handling.

All public ``zeeb_agents`` functions return :class:`AgentResult` and must
never raise.  Instead of repeating the same ``try/except`` boilerplate in
every function, decorate them with :func:`agent_function`.

The decorator also implements the **public ``project_id`` seam**: function
bodies are written against a resolved ``project_root: Path``, while the public
tool signature exposes ``project_id: str`` (owned by the hosting MCP server).
The decorator renames the parameter in the reported signature and resolves the
id to a path via :func:`~zeeb_agents._utils.resolver.resolve_project_id` before
the body runs — so no function body needs to know about ``project_id``.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, overload

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.resolver import resolve_project_id

logger = logging.getLogger("zeeb_agents")

_AgentFunc = Callable[..., Awaitable[AgentResult]]


@overload
def agent_function(func: _AgentFunc) -> _AgentFunc: ...


@overload
def agent_function(
    func: None = None,
    *,
    resolve_project: bool = True,
    optional_project: bool = False,
    resolve_project_root: bool | None = None,
    aliases: dict[str, str] | None = None,
) -> Callable[[_AgentFunc], _AgentFunc]: ...


def agent_function(
    func: _AgentFunc | None = None,
    *,
    resolve_project: bool = True,
    optional_project: bool = False,
    resolve_project_root: bool | None = None,
    aliases: dict[str, str] | None = None,
) -> Any:
    """Wrap an async agent function with uniform error handling + the id seam.

    - When *resolve_project* is true and the wrapped function has a
      ``project_root`` parameter, the **public** signature renames it to
      ``project_id: str | None`` and the decorator resolves that id to a
      ``Path`` (via :func:`resolve_project_id`) before calling the body, which
      still receives a ``project_root: Path``.  A ``Path`` passed straight
      through (internal forwarding between tools) is left as-is.
    - ``project_root`` remains accepted as a caller keyword even though the
      advertised (MCP) signature only names ``project_id``.  This is the bridge
      for the framework-neutral codegen engine and other path-first callers,
      which pass a resolved ``project_root=<Path>``: it is treated exactly like
      a ``Path`` handed to ``project_id`` (passthrough).  An explicit
      ``project_id`` wins if both are supplied.
    - When *optional_project* is true (doc / live-context tools), a missing or
      unresolvable id yields ``project_root=None`` instead of failing — the body
      decides whether to append live context.  Otherwise a missing id fails with
      ``no_project_id`` and an unknown id with ``project_not_found``.
    - :class:`AgentError` (an expected, targeted failure) is logged at info
      level and its prebuilt failure result is returned.
    - ``FileNotFoundError`` / ``PermissionError`` gain ``error_code``
      ``file_not_found`` / ``permission_denied``.
    - Any other :class:`Exception` is logged and converted to a failure
      ``AgentResult``.  :class:`AgentResult` return values pass through unchanged.

    - ``aliases`` maps an accepted-but-hidden keyword to the real parameter name
      (e.g. ``{"app": "name"}`` lets a caller pass ``app=`` where the body names
      the parameter ``name``). Like ``project_root``, aliases are honored as a
      forgiving input convenience but are **not** added to the advertised
      signature; an explicit real-name argument wins over its alias.

    Usable bare (``@agent_function``) or with keywords
    (``@agent_function(resolve_project=False)``).  ``resolve_project_root`` is a
    deprecated alias for ``resolve_project`` kept for callers written against the
    pre-seam signature (notably ``django_agents``, which reuses this decorator).
    """
    if resolve_project_root is not None:
        resolve_project = resolve_project_root
    alias_map = aliases or {}

    def decorate(fn: _AgentFunc) -> _AgentFunc:
        signature = inspect.signature(fn)
        do_resolve = resolve_project and "project_root" in signature.parameters

        public_sig: inspect.Signature | None = None
        if do_resolve:
            new_params = [
                (
                    p.replace(name="project_id", annotation="str | None", default=None)
                    if name == "project_root"
                    else p
                )
                for name, p in signature.parameters.items()
            ]
            public_sig = signature.replace(parameters=new_params)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> AgentResult:
            try:
                # Map accepted-but-hidden aliases to their real parameter names;
                # an explicit real-name argument always wins over its alias.
                for alias, real in alias_map.items():
                    if alias in kwargs:
                        value = kwargs.pop(alias)
                        if real not in kwargs:
                            kwargs[real] = value
                if do_resolve:
                    assert public_sig is not None
                    # Accept a caller-supplied ``project_root`` (the codegen
                    # engine / internal forwarding pass a resolved Path this
                    # way) as an alias for ``project_id``; an explicit
                    # ``project_id`` takes precedence.
                    if "project_root" in kwargs:
                        alias_root = kwargs.pop("project_root")
                        kwargs.setdefault("project_id", alias_root)
                    bound = public_sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    call_kwargs = dict(bound.arguments)
                    project_id = call_kwargs.pop("project_id", None)
                    if optional_project:
                        try:
                            root = (
                                resolve_project_id(project_id)
                                if project_id is not None
                                else None
                            )
                        except AgentError:
                            root = None
                    else:
                        root = resolve_project_id(project_id)
                    call_kwargs["project_root"] = root
                    return await fn(**call_kwargs)
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
                # An unanticipated crash is the one case where the caller
                # genuinely cannot know whether anything was written, so say so
                # instead of guessing: state_changed None means "unknown", and
                # re-running is the documented recovery (writes are idempotent).
                # Carrying no data at all left the worst failure mode with the
                # least machine-readable information.
                return AgentResult(
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                    data={
                        "error_type": type(exc).__name__,
                        "recoverable": True,
                        "state_changed": None,
                        "next_actions": [
                            "Re-run the same call — completed steps are skipped.",
                            "If it fails the same way, inspect the logs (read_logs).",
                        ],
                    },
                )

        if public_sig is not None:
            wrapper.__signature__ = public_sig  # type: ignore[attr-defined]
        # Expose accepted-but-hidden aliases for introspection (docs drift guard,
        # forgiving-input discovery).
        wrapper.__agent_aliases__ = dict(alias_map)  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        return decorate(func)
    return decorate
