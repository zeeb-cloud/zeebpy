"""Hand-off from project-level settings (zeeb_api) to the ORM layer (zeeb_orm).

Settings layering in zeebpy:

1. **zeeb_api.conf.settings (LazySettings)** — the canonical in-process reader
   of a project's ``settings.py`` (Django-style). ``create_app()`` consumes it.
2. **zeeb_orm.conf.settings** — the low-level library sink (``configure()`` /
   ``get_settings()``); normally fed from layer 1 via
   :func:`apply_orm_settings`, or directly by library users without a project.
3. **zeeb_agents** — reads a *target* project's settings off disk by path
   (``load_project_settings``); independent of the current process by design.
"""

from __future__ import annotations

from typing import Any

# Keys of DATABASE that map onto zeeb_orm.DatabaseConfig / Database kwargs.
_DATABASE_KEYS = (
    "echo",
    "pool_size",
    "max_overflow",
    "pool_timeout",
    "pool_recycle",
    "pool_pre_ping",
    "connect_args",
)


def database_kwargs(database_config: dict[str, Any]) -> dict[str, Any]:
    """Extract the non-URL connection kwargs from a DATABASE settings dict."""
    return {k: database_config[k] for k in _DATABASE_KEYS if k in database_config}


def apply_orm_settings(settings: Any | None = None) -> None:
    """Configure zeeb_orm from the project settings.

    Called by ``create_app()``'s default lifespan; safe to call manually when
    using zeeb_orm alongside a project without ``create_app``.
    """
    if settings is None:
        from zeeb_api.conf import settings as _settings

        settings = _settings

    try:
        from zeeb_orm import configure
    except ImportError:  # zeeb_orm not installed
        return

    kwargs: dict[str, Any] = {}
    database = getattr(settings, "DATABASE", None)
    if isinstance(database, dict) and database.get("url"):
        kwargs["database"] = database
    migrations_dir = getattr(settings, "MIGRATIONS_DIR", None)
    if migrations_dir:
        kwargs["migrations_dir"] = migrations_dir
    if kwargs:
        configure(**kwargs)


__all__ = ["apply_orm_settings", "database_kwargs"]
