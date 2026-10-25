"""Models a framework built on the ORM contributes to every project.

A project's own models are found through ``INSTALLED_APPS``. A layer above the
ORM (``zeeb_api``'s auth tables, say) may own models that every project using it
has, whose registration depends on project settings — which user model is
active, which table a foreign key points at. Such a layer registers a
:class:`ModelProvider`, and the schema tooling
(:func:`zeeb_orm.migrations.cli._register_models`) calls it around the app
scan; the ORM itself never imports the layer.

Providers are found two ways: the ``zeeb_orm.model_providers`` entry-point
group (so a provider is active whenever its package is installed, before
anything has imported it), and :func:`register_model_provider` (which the
package also calls on import, for an environment whose package metadata is
stale). A provider registered under the same name either way is used once.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Protocol

logger = logging.getLogger("zeeb_orm.models.providers")

ENTRY_POINT_GROUP = "zeeb_orm.model_providers"


class ModelProvider(Protocol):
    """Registers a layer's models with the ORM around the project's app scan."""

    #: ``INSTALLED_APPS`` entries that stand for this provider rather than an
    #: importable app (the scan skips them).
    app_labels: frozenset[str]

    def before_apps(self, project_root: Path, settings: ModuleType | None) -> None:
        """Register models nothing in the project's apps depends on being first."""

    def after_apps(self, project_root: Path, settings: ModuleType | None) -> None:
        """Register models that point at the project's (e.g. a swappable user model)."""


_registered: dict[str, ModelProvider] = {}


def register_model_provider(name: str, provider: ModelProvider) -> None:
    """Make *provider* part of every model registration, under *name*."""
    _registered[name] = provider


def model_providers() -> list[ModelProvider]:
    """Every provider: entry points first loaded, then those registered on import.

    An entry point that fails to load is an error, not a skip: the tables it
    owns would be missing from the model state, and a migration generated
    from that state drops them.
    """
    from importlib.metadata import entry_points

    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.name in _registered:
            continue
        provider = entry_point.load()
        _registered[entry_point.name] = provider
    return list(_registered.values())


def provider_app_labels() -> frozenset[str]:
    """``INSTALLED_APPS`` entries that name a provider, not an app."""
    labels: set[str] = set()
    for provider in model_providers():
        labels.update(getattr(provider, "app_labels", ()))
    return frozenset(labels)
