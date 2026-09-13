"""Pytest configuration."""

import os
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def _isolate_environment():
    """Restore ``os.environ`` and drop the loaded ``.env`` after every test.

    Many tests scaffold a project into ``tmp_path`` and execute its
    ``settings.py``, which loads that project's ``.env``. Without this, one
    test's ``SECRET_KEY``/``DATABASE_URL`` would still be in effect for the
    next — and a leaked ``DATABASE_URL`` means two tests silently share a
    SQLite file. ``load_env`` never writes to ``os.environ`` by design; this is
    the belt-and-braces half of that guarantee.
    """
    from zeeb_api.conf.env import clear_env_cache

    snapshot = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
        clear_env_cache()


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore the model registry, metadata and scaffolded imports.

    ``make_migrations`` and the migration CLI import a scaffolded project's
    models into zeeb_orm's flat, process-global registry. Every scaffolded
    project now ships an ``accounts.User``, so without cleanup one test's user
    model shadows the framework's own — the registry is keyed by bare class
    name — and unrelated auth and ORM tests fail depending on collection order.

    Previously each module that scaffolded projects carried its own copy of
    this fixture (``test_agent_seamless.py``, ``test_model_ref_resolution.py``);
    applying it suite-wide protects the modules that don't.
    """
    from zeeb_api.conf import settings as api_settings
    from zeeb_orm.models.base import _model_registry, metadata

    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    # create_app("<project>.settings") repoints the settings singleton at that
    # project and there is no reset for it, so the next test would read a
    # scaffolded project's configuration. Underscore attributes bypass the
    # wrapper's __setattr__, so this is a plain snapshot/restore.
    settings_before = (
        api_settings._wrapped,
        api_settings._configured,
        set(api_settings._explicit_settings),
    )
    try:
        yield
    finally:
        (
            api_settings._wrapped,
            api_settings._configured,
            api_settings._explicit_settings,
        ) = settings_before[0], settings_before[1], set(settings_before[2])

        # Fully restore the registry rather than deleting new keys: scaffolded
        # models reuse common names (User, Post, …) and *overwrite* entries a
        # delete-only cleanup would leave broken.
        _model_registry.clear()
        _model_registry.update(registry_before)
        for name in set(metadata.tables) - tables_before:
            metadata.remove(metadata.tables[name])
        for name in set(sys.modules) - modules_before:
            if name == "apps" or name.startswith("apps.") or name == "settings":
                del sys.modules[name]
        sys.path[:] = path_before

        # _register_models points auth's settings discovery at the project it
        # is migrating and never resets it. Left set, the next test resolves
        # AUTH_USER_MODEL out of a deleted tmp project, and UserPermission's
        # user foreign key stays bound to that project's accounts_user table.
        try:
            from zeeb_api.auth.backends import set_project_root

            set_project_root(None)
        except ImportError:  # pragma: no cover - zeeb_api always present here
            pass
