"""Idempotent project-wiring helpers: register apps and include their routers.

These perform the two edits that make a scaffolded app actually *served*:

- :func:`ensure_installed_app` appends ``"apps.<app>"`` to ``INSTALLED_APPS`` in
  the project ``settings.py`` — without it ``make_migrations`` never sees the
  app's models (``_register_models`` walks ``INSTALLED_APPS`` only).
- :func:`ensure_app_urls_included` imports the app router and includes it in the
  project ``urls.py`` — without it every ``router.register(...)`` an app makes is
  never routed and every endpoint 404s.

The app router is included with **no prefix**: ``register_route`` already mounts
each ViewSet under its own URL segment (the pluralized lowercase model name by
default, or an explicit ``url_prefix``), and :meth:`DefaultRouter.include`
*nests* prefixes — so adding a prefix here would double it (``/posts/posts/``).

Both functions are safe to re-run (grep-before-write, mirroring the
``_ensure_middleware`` pattern in ``auth_scaffold``) and return ``True`` only
when they actually changed the file.

The implementation lives in :mod:`zeeb_orm.scaffold.wiring` so the CLI
(``zeeb startapp``) and the agent layer share one code path. This module is the
adapter that re-raises the scaffolding layer's :class:`ScaffoldError` as an
:class:`AgentError` with the identical ``code`` and payload.
"""

from __future__ import annotations

import functools

from zeeb_agents._utils.errors import AgentError
from zeeb_orm.scaffold import wiring as _wiring
from zeeb_orm.scaffold.errors import ScaffoldError

__all__ = [
    "append_router_include",
    "ensure_app_urls_included",
    "ensure_installed_app",
    "find_project_package",
]


def _agent_facing(fn):
    """Re-raise :class:`ScaffoldError` as :class:`AgentError`, code intact."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScaffoldError as exc:
            raise AgentError(str(exc), code=exc.code, **exc.data) from exc

    return wrapper


find_project_package = _agent_facing(_wiring.find_project_package)
ensure_installed_app = _agent_facing(_wiring.ensure_installed_app)
append_router_include = _agent_facing(_wiring.append_router_include)
ensure_app_urls_included = _agent_facing(_wiring.ensure_app_urls_included)

# Private helpers a few callers and tests still reach for.
_installed_apps_body_span = _agent_facing(_wiring._installed_apps_body_span)
_code_before_comment = _wiring._code_before_comment
_STANDARD_URLS_TEMPLATE = _wiring.STANDARD_URLS_TEMPLATE
