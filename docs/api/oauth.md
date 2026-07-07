# OAuth2 / OIDC Authentication (SSO)

Zeeb ships a generic OAuth2/OpenID Connect layer with ready-made presets for
**Azure AD (Microsoft Entra ID)**, **Google** and **GitHub**. Users sign in at
the identity provider; Zeeb validates the result, links (or provisions) a
local user and issues its own JWT access/refresh tokens, so the rest of your
API keeps working unchanged.

## Installation

The OAuth layer is an optional extra (it needs `httpx` and PyJWT's crypto
backend):

```bash
pip install zeebpy[oauth]
```

Plain `import zeeb_api` never requires `httpx` — all OAuth names are exported
lazily.

## Quick Start

Settings-driven (recommended):

```python
# settings.py
OAUTH_PROVIDERS = {
    "azure": {
        "tenant": "common",                      # or your tenant GUID
        "client_id": "YOUR-APP-CLIENT-ID",
        "client_secret": "YOUR-CLIENT-SECRET",   # omit for public PKCE clients
    },
    "google": {
        "client_id": "....apps.googleusercontent.com",
        "client_secret": "...",
    },
}
```

```python
# app setup
from zeeb_api import create_app
from zeeb_api.auth.oauth import create_oauth_router

app = create_app()
app.include_router(create_oauth_router())   # uses OAUTH_PROVIDERS
```

Or fully programmatic:

```python
from zeeb_api.auth.oauth import AzureADProvider, create_oauth_router

provider = AzureADProvider(
    tenant="common",
    client_id="YOUR-APP-CLIENT-ID",
    client_secret="YOUR-CLIENT-SECRET",
)
app.include_router(create_oauth_router(providers={"azure": provider}))
```

This registers (default prefix `/auth`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/{provider}/authorize/` | GET | Redirect the browser to the IdP (sets state + PKCE cookie) |
| `/auth/{provider}/callback/` | GET, POST | Complete the browser login (POST supports Azure's `form_post`) |
| `/auth/{provider}/token/` | POST | SPA flow: exchange an authorization code for Zeeb JWTs |
| `/auth/providers/` | GET | List configured providers and their authorize URLs |

A successful login returns the standard `TokenResponse`
(`access_token`, `refresh_token`, `token_type`, `expires_in`) — the same shape
as `/auth/login`.

## Azure AD (Microsoft Entra ID) Walkthrough

1. **App registration** (Azure Portal → Microsoft Entra ID → App
   registrations → New registration):
   - Supported account types: pick a tenant mode (see below).
   - Redirect URI (type *Web*): `https://your-api.example.com/auth/azure/callback/`
     — this must match the callback URL of your deployed API exactly
     (or set `OAUTH_REDIRECT_URI` and register that).
   - Create a client secret under *Certificates & secrets* (server-side flow),
     or configure the app as a public client for PKCE-only SPAs.

2. **Configure Zeeb**:

```python
OAUTH_PROVIDERS = {
    "azure": {
        "tenant": "common",          # tenant mode, see below
        "client_id": "11111111-....",
        "client_secret": "...",
    },
}
```

### Tenant modes

The `tenant` value selects the authority
`https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`:

| `tenant` | Who can sign in |
|----------|-----------------|
| a tenant GUID (or verified domain) | Only users of that tenant |
| `common` | Any work/school or personal Microsoft account |
| `organizations` | Any work/school account |
| `consumers` | Personal Microsoft accounts only |

### Multi-tenant issuer note

With `common`/`organizations`/`consumers` the ID token's `iss` claim contains
the *concrete* tenant GUID of the signing tenant, so simple issuer equality
cannot work. `AzureADProvider` therefore validates the issuer against
`^https://login\.microsoftonline\.com/[0-9a-f-]{36}/v2\.0$` in multi-tenant
mode and uses an exact match when you configure a concrete tenant GUID. If you
need to restrict a multi-tenant app to an allow-list of tenants, subclass
`AzureADProvider` and override `_issuer_validator`.

Azure claims are mapped as: subject = `oid` (stable object id; falls back to
`sub`), email = `email` (falls back to `preferred_username`).

Default scopes: `openid profile email offline_access` (`offline_access` gives
you IdP refresh tokens).

## Google

```python
OAUTH_PROVIDERS = {
    "google": {"client_id": "...apps.googleusercontent.com", "client_secret": "..."},
}
```

Pure OIDC via the `accounts.google.com` discovery document; nothing else to
configure. Register the callback URL in the Google Cloud Console OAuth client.

## GitHub

```python
OAUTH_PROVIDERS = {
    "github": {"client_id": "...", "client_secret": "..."},
}
```

GitHub does not implement OIDC, so the preset uses manual endpoints
(`github.com/login/oauth/...`), disables ID token validation and resolves the
user via `api.github.com/user`. When the user's email is private, it falls
back to `/user/emails` (the default `user:email` scope covers this). The
external subject is the numeric GitHub user id.

## SPA Flow (POST /token/)

Single-page apps drive the redirect themselves (e.g. with MSAL or a plain
OAuth client) and exchange the code at the API:

```text
1. SPA -> GET /auth/azure/authorize/        (or builds the IdP URL itself)
2. Browser -> IdP login -> redirect back to the SPA with ?code=...&state=...
3. SPA -> POST /auth/azure/token/
       {"code": "...", "redirect_uri": "...", "code_verifier": "...", "state": "..."}
4. API responds with a JSON TokenResponse (Zeeb-issued JWTs)
```

`state` is optional in step 3 but recommended: when supplied, the ID token's
`nonce` is verified against the one embedded in the signed state token.

## Browser Flow

`GET /auth/{provider}/authorize/?next=/dashboard` redirects (307) to the IdP
with a signed state token and, when PKCE is enabled (default), an S256 code
challenge. The callback returns a JSON `TokenResponse` by default; configure
`OAUTH_SUCCESS_REDIRECT` (or `create_oauth_router(success_redirect=...)`, or
the `next` query parameter) to redirect instead — tokens are then carried in
the **URL fragment** (`#access_token=...`), which browsers never send to
servers.

The user-supplied `next` is validated before use: relative paths are always
allowed, but an absolute URL is only honored when its host is listed in
`OAUTH_ALLOWED_REDIRECT_HOSTS`. An unlisted/unsafe `next` is ignored and the
configured success redirect (or JSON response) is used instead, so tokens can
never be redirected to an attacker-controlled origin.

Azure's `response_mode=form_post` is supported: the callback accepts POST with
a form body as well as the standard GET.

## User Linking and Provisioning

Logins are tied to local users through the `ExternalIdentity` model
(table `auth_external_identities`, unique per `(provider, subject)`):

1. An existing identity wins — its user is logged in.
2. Otherwise, with `OAUTH_LINK_BY_EMAIL = True` (default), an existing user
   with the same email gets the identity attached.
3. Otherwise, with `OAUTH_AUTO_CREATE_USERS = True` (default), a new user is
   created **without a usable password** (they can only log in via SSO until
   they set one).
4. Otherwise the login fails with `401 AUTH_OAUTH_USER_NOT_PROVISIONED`.

Both behaviours can also be set per provider
(`auto_create_user=` / `link_by_email=` on the provider). To replace the whole
upsert step, pass `get_or_create_user=async fn(provider_name, claims)` to
`create_oauth_router`; a post-login hook `on_login(user, identity, created)`
is also available.

### Migration note

`ExternalIdentity` is imported **lazily**: it only registers its table with
the ORM (and thus with migration autodetection) once you import something from
`zeeb_api.auth.oauth`. If you use OAuth, make sure the OAuth layer is imported
in your project (e.g. in `settings.py`/`urls.py` via the router) before
running `zeeb-manage makemigrations` / `migrate`, so the
`auth_external_identities` table is created. Projects that don't use OAuth
never see the table.

## External Bearer Mode (accept IdP tokens directly)

Instead of (or in addition to) exchanging codes, the API can accept JWTs
issued directly by the IdP — e.g. an Azure AD token acquired by MSAL in a
SPA — as `Authorization: Bearer` tokens:

```python
# settings.py — zero code, uses the OAUTH_PROVIDERS registry
OAUTH_ACCEPT_EXTERNAL_TOKENS = ["azure"]
```

```python
# or explicitly
from zeeb_api.auth import JWTAuthMiddleware
from zeeb_api.auth.oauth import ExternalTokenValidator

app.add_middleware(
    JWTAuthMiddleware,
    external_validators=[ExternalTokenValidator(provider)],
)
```

When a Bearer token is not a locally-issued JWT, the middleware tries each
validator (first non-None wins): the token is validated against the
provider's JWKS (signature, `aud` = your client id, issuer, expiry), then
resolved to the linked database user via `ExternalIdentity`, falling back to a
lightweight `ExternalAuthenticatedUser` built from the token claims. Invalid
tokens never raise — `request.state.user` simply stays `None`. There is zero
overhead when nothing is configured.

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `OAUTH_PROVIDERS` | `{}` | Provider registry (`class` inferred for azure/google/github) |
| `OAUTH_AUTO_CREATE_USERS` | `True` | Provision users on first login |
| `OAUTH_LINK_BY_EMAIL` | `True` | Attach identities to existing users by email |
| `OAUTH_STATE_TTL_SECONDS` | `600` | Lifetime of the signed state token (and PKCE cookie) |
| `OAUTH_REDIRECT_URI` | `None` | Fixed redirect URI (default: derived from the callback route) |
| `OAUTH_SUCCESS_REDIRECT` | `None` | Browser-flow redirect target (tokens in URL fragment) |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | `[]` | Hosts allowed as absolute `next` redirect targets (relative paths are always allowed) |
| `OAUTH_ACCEPT_EXTERNAL_TOKENS` | `[]` | Provider names whose IdP-issued JWTs are accepted as Bearer tokens |

## Security Notes

- **State**: a short-lived JWT signed with your `SECRET_KEY` (the
  insecure-default-secret guard applies). Tampered, expired or
  cross-provider state fails with `401 AUTH_OAUTH_STATE_INVALID`.
- **PKCE**: enabled by default (S256). The code verifier is stored in an
  `HttpOnly`, `Secure`, `SameSite=Lax` cookie (`zeeb_oauth_pkce_{provider}`)
  and never inside the state token; the callback deletes it.
- **ID tokens**: validated against the provider JWKS with pinned asymmetric
  algorithms — `none` and HMAC algorithms are rejected unconditionally;
  audience, expiry, issuer and nonce are checked.
- **Email linking / account takeover**: `OAUTH_LINK_BY_EMAIL` trusts the IdP's
  email claim. If an IdP does not verify email ownership, anyone who registers
  that email there could take over the matching local account. Keep it enabled
  only for providers that verify emails (Azure AD, Google do; be careful with
  custom IdPs), or disable it per provider with `link_by_email=False`.
- **Tokens in fragments**: the browser success redirect carries tokens in the
  URL fragment, which is not sent to servers or logged; still, prefer the SPA
  `POST /token/` flow where possible.
- **Open-redirect / token theft**: the user-supplied `next` parameter is never
  redirected to verbatim. Absolute URLs must match `OAUTH_ALLOWED_REDIRECT_HOSTS`
  (relative paths are always allowed); anything else falls back to the configured
  redirect. This prevents an attacker from crafting an authorize link that leaks
  the victim's tokens (in the fragment) to a foreign origin after a legitimate
  login.
- Remember to install the extra: `pip install zeebpy[oauth]`.

## Error Codes

| Code | Status | Meaning |
|------|--------|---------|
| `AUTH_OAUTH_STATE_INVALID` | 401 | State token missing/expired/tampered/wrong provider |
| `AUTH_OAUTH_EXCHANGE_FAILED` | 401 | IdP rejected the code exchange (or returned an error) |
| `AUTH_OAUTH_ID_TOKEN_INVALID` | 401 | ID token failed signature/claims validation |
| `AUTH_OAUTH_PROVIDER_NOT_FOUND` | 404 | Unknown provider name in the URL |
| `AUTH_OAUTH_USER_NOT_PROVISIONED` | 401 | No linked user and auto-provisioning disabled |

## Next Steps

- [Authentication](authentication.md) — local users, passwords, JWT sessions
- [Permissions](permissions.md) — protecting endpoints
