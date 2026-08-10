"""Project and app scaffolding: the templates and the wiring that serves them.

This package is the single source of truth for what ``zeeb startproject`` and
``zeeb startapp`` write, and for the two edits that make a scaffolded app
actually run — registering it in ``INSTALLED_APPS`` and including its router in
the project ``urls.py``.

It lives in ``zeeb_orm`` rather than ``zeeb_agents`` so that the CLI and the
agent layer share one implementation instead of drifting apart: the agent
wiring helpers in ``zeeb_agents._utils.wiring`` are a thin adapter over
:mod:`zeeb_orm.scaffold.wiring` that translates :class:`ScaffoldError` into the
agent-facing ``AgentError``.
"""

from zeeb_orm.scaffold.errors import ScaffoldError
from zeeb_orm.scaffold.naming import (
    find_project_root,
    pluralize,
    singularize,
    to_class_name,
    to_title,
)
from zeeb_orm.scaffold.wiring import (
    STANDARD_URLS_TEMPLATE,
    append_router_include,
    ensure_app_urls_included,
    ensure_installed_app,
    find_project_package,
)

__all__ = [
    "STANDARD_URLS_TEMPLATE",
    "ScaffoldError",
    "append_router_include",
    "ensure_app_urls_included",
    "ensure_installed_app",
    "find_project_package",
    "find_project_root",
    "pluralize",
    "singularize",
    "to_class_name",
    "to_title",
]
