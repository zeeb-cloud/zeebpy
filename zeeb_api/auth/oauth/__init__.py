"""
OAuth2/OIDC authentication layer (requires the "oauth" extra: httpx).

Quick start:
    from zeeb_api.auth.oauth import AzureADProvider, create_oauth_router

    provider = AzureADProvider(tenant="common", client_id="...", client_secret="...")
    app.include_router(create_oauth_router(providers={"azure": provider}))

All public names are exported lazily (PEP 562):
    - ``ExternalIdentity`` registers a database table with zeeb_orm on
      import; lazy export keeps it out of migration autodetection for
      projects that don't use OAuth.
    - httpx is only required once the client is actually used.
"""

from __future__ import annotations

from typing import Any

# name -> submodule (all exports resolved lazily via __getattr__)
_EXPORTS = {
    # client
    "OAuth2Client": "client",
    "OAuthTokenSet": "client",
    "OIDCMetadata": "client",
    "OAuthError": "client",
    "OAuthExchangeError": "client",
    "OAuthValidationError": "client",
    # jwks
    "JWKSCache": "jwks",
    "validate_id_token": "jwks",
    # provider
    "OAuthProvider": "provider",
    "ExternalClaims": "provider",
    # presets
    "AzureADProvider": "presets",
    "GoogleProvider": "presets",
    "GitHubProvider": "presets",
    # state
    "create_state_token": "state",
    "decode_state_token": "state",
    "generate_pkce_pair": "state",
    "generate_nonce": "state",
    "pkce_cookie_name": "state",
    # models (table registration! keep lazy)
    "ExternalIdentity": "models",
    "get_or_create_user_for_identity": "models",
    # router
    "create_oauth_router": "router",
    # registry
    "build_providers_from_settings": "registry",
    "get_oauth_provider": "registry",
    "clear_provider_cache": "registry",
    # bearer
    "ExternalTokenValidator": "bearer",
    "ExternalAuthenticatedUser": "bearer",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module = importlib.import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
