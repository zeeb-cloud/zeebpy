"""
Settings-driven OAuth provider registry.

Reads ``settings.OAUTH_PROVIDERS`` and instantiates providers. The provider
class is inferred for the well-known names ``azure`` / ``google`` / ``github``
and can be overridden (or supplied for custom names) via a ``"class"`` dotted
path.
"""

from __future__ import annotations

from typing import Any

from zeeb_api.auth.oauth.provider import OAuthProvider
from zeeb_api.exceptions import ImproperlyConfigured

# Class inference for well-known provider names.
KNOWN_PROVIDER_CLASSES = {
    "azure": "zeeb_api.auth.oauth.presets.AzureADProvider",
    "google": "zeeb_api.auth.oauth.presets.GoogleProvider",
    "github": "zeeb_api.auth.oauth.presets.GitHubProvider",
}

_provider_cache: dict[str, OAuthProvider] | None = None


def build_providers_from_settings(settings: Any = None) -> dict[str, OAuthProvider]:
    """
    Build the provider registry from ``settings.OAUTH_PROVIDERS``.

    Example settings:
        OAUTH_PROVIDERS = {
            "azure": {"tenant": "common", "client_id": "...", "client_secret": "..."},
            "corp": {"class": "myapp.oauth.CorpProvider", "client_id": "..."},
        }
    """
    from zeeb_api.middleware.loader import import_string

    if settings is None:
        from zeeb_api.conf import settings as settings_obj
        settings = settings_obj

    config: dict[str, dict[str, Any]] = getattr(settings, "OAUTH_PROVIDERS", {}) or {}
    providers: dict[str, OAuthProvider] = {}

    for name, options in config.items():
        options = dict(options)
        class_path = options.pop("class", None) or KNOWN_PROVIDER_CLASSES.get(name)
        if class_path is None:
            raise ImproperlyConfigured(
                f"OAUTH_PROVIDERS[{name!r}] needs a 'class' dotted path "
                f"(class inference only works for {sorted(KNOWN_PROVIDER_CLASSES)})"
            )
        provider_class = import_string(class_path)
        options.setdefault("name", name)
        providers[name] = provider_class(**options)

    return providers


def get_oauth_provider(name: str) -> OAuthProvider:
    """
    Get a provider from the settings-driven registry (built once, cached).

    Raises:
        KeyError: When the provider is not configured.
    """
    global _provider_cache
    if _provider_cache is None:
        _provider_cache = build_providers_from_settings()
    return _provider_cache[name]


def get_registered_providers() -> dict[str, OAuthProvider]:
    """Return the cached settings-driven registry (building it if needed)."""
    global _provider_cache
    if _provider_cache is None:
        _provider_cache = build_providers_from_settings()
    return _provider_cache


def clear_provider_cache() -> None:
    """Clear the cached registry (useful in tests / after settings changes)."""
    global _provider_cache
    _provider_cache = None
