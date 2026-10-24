"""zeeb_api's auth tables, contributed to every project's model state.

Registered with the ORM as a model provider (see
:mod:`zeeb_orm.models.providers`), through the ``zeeb_orm.model_providers``
entry point and on import of :mod:`zeeb_api.auth`, so the ORM's schema tooling
includes these tables without knowing about this package.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from zeeb_orm.models.providers import register_model_provider


class AuthModels:
    """``Permission`` and ``User`` before the apps, ``UserPermission`` after them."""

    #: The pseudo-app a project's ``INSTALLED_APPS`` lists for these tables.
    app_labels = frozenset({"zeeb_auth"})

    def before_apps(self, project_root: Path, settings: ModuleType | None) -> None:
        """Register ``Permission``, and the built-in ``User`` unless ``AUTH_USER_MODEL`` is set."""
        from zeeb_api.auth.backends import set_project_root
        from zeeb_api.auth.models import Permission

        # AUTH_USER_MODEL resolves against the target project even when the
        # process CWD is elsewhere (the agent layer works on projects by path).
        set_project_root(project_root)
        Permission._get_table()
        if not getattr(settings, "AUTH_USER_MODEL", None):
            from zeeb_api.auth.models import User

            User._get_table()

    def after_apps(self, project_root: Path, settings: ModuleType | None) -> None:
        """Register ``UserPermission``, whose foreign key targets the active user model."""
        from zeeb_api.auth.backends import clear_user_model_cache
        from zeeb_api.auth.models import UserPermission

        # Re-resolve the user model now that every app (a custom user model
        # among them) is imported: UserPermission's foreign key points at it.
        clear_user_model_cache()
        UserPermission._get_table()


provider = AuthModels()
register_model_provider("zeeb_api", provider)
