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

## In a generated project

`zeeb startproject` already wires OAuth — it just stays dormant. The project
`settings.py` builds providers from the environment:

```python
OAUTH_PROVIDERS = env_oauth_providers()   # google / github / azure
```

and the project `urls.py` mounts the routes only while that is non-empty, so
half-configured endpoints never reach the schema. Setting a client id is all it
takes:

```bash
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Two details worth knowing:

- The `auth_external_identities` table ships in the **initial migration**
  whether or not a provider is configured. `apps/accounts/models.py` imports
  `ExternalIdentity` for exactly that reason: gating it on the environment
  would make the generated schema differ between developers, and the next
  `makemigrations` without credentials would emit a `DeleteModel` against a
  schema already committed to git. Enabling a provider therefore needs no
  migration.
- Install the extra first: `pip install "zeebpy[oauth]"`.

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
    allowed_tenants=["YOUR-TENANT-GUID"],   # required for common/organizations
)
app.include_router(create_oauth_router(providers={"azure": provider}))
```

This registers (default prefix `/auth`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/{provider}/authorize/` | GET | Redirect the browser to the IdP (sets state + PKCE cookie) |
| `/auth/{provider}/callback/` | GET, POST | Complete the browser login (POST supports Azure's `form_post`) |
| `/auth/{provider}/state/` | GET | SPA flow: issue a signed state + nonce for an IdP URL the SPA builds itself |
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
        "allowed_tenants": ["22222222-...."],   # multi-tenant: who may sign in
    },
}
```

### Tenant modes

The `tenant` value selects the authority
`https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`:

| `tenant` | Who can sign in |
|----------|-----------------|
| a tenant GUID (or verified domain) | Only users of that tenant |
| `common` | Work/school or personal Microsoft accounts from `allowed_tenants` |
| `organizations` | Work/school accounts from `allowed_tenants` |
| `consumers` | Personal Microsoft accounts (tenant `9188040d-6c67-4c5b-b112-36a304b66dad`) |

### Multi-tenant Azure AD

With `common`/`organizations`/`consumers` *any* Azure AD tenant can obtain a
validly signed token for your app — including one an attacker creates, whose
admin can assign themselves any of your app roles. So a multi-tenant provider
must say which tenants it accepts, and **refuses every token until it does**:

| Option | Effect |
|---|---|
| `allowed_tenants=["<tenant GUID>", ...]` | Accept tokens only from these tenants (the `tid`, carried in `iss`) |
| `allow_any_tenant=True` | Accept every tenant — a true multi-tenant SaaS. Such tokens never carry roles or staff flags for an unlinked user (see below) |

Without either, sign-in and external bearer tokens fail with
`OAuthValidationError` naming the two options. In a generated project set
`AZURE_ALLOWED_TENANTS` (comma-separated GUIDs) or `AZURE_ALLOW_ANY_TENANT=true`.

The ID token's `iss` contains the concrete tenant GUID
(`https://login.microsoftonline.com/<tid>/v2.0`); the provider takes the
tenant from it and checks it against `allowed_tenants`. A concrete tenant GUID
is an exact issuer match, and a verified domain matches the issuer its
discovery document names. `allowed_tenants` must be GUIDs
(`ImproperlyConfigured` otherwise); when given with a single-tenant provider
it narrows that too.

**Upgrading:** a provider on `common`/`organizations`/`consumers` used to
accept every tenant. To keep that behaviour pass `allow_any_tenant=True`, but
prefer `allowed_tenants` — and note that roles in those tokens are no longer
honoured for users without a linked account.

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
user via `api.github.com/user`. The external subject is the numeric GitHub
user id.

`/user` exposes no verification flag, so `/user/emails` is always queried as
well — both to fill in a private primary address and to learn whether the
address is verified. That call needs the `user:email` scope, which the preset
requests by default. Without it `email_verified` stays unknown, and with
`OAUTH_REQUIRE_VERIFIED_EMAIL` at its default `True` the first login for that
identity is rejected with 401 `AUTH_OAUTH_EMAIL_UNVERIFIED`.

The login routes (`authorize`, `callback`, `state`, `token`) are rate limited
per client like `/auth/login`: `create_oauth_router(throttle_rate=...)`,
defaulting to `AUTH_LOGIN_THROTTLE_RATE` (`None` disables).

## SPA Flow (POST /token/)

Single-page apps drive the redirect themselves (e.g. with MSAL or a plain
OAuth client) and exchange the code at the API:

```text
1. SPA -> GET /auth/azure/authorize/        (the API builds the IdP URL)
   or SPA -> GET /auth/azure/state/?redirect_uri=...  -> {"state", "nonce"}
          and builds the IdP URL itself with that state and nonce
2. Browser -> IdP login -> redirect back to the SPA with ?code=...&state=...
3. SPA -> POST /auth/azure/token/
       {"code": "...", "redirect_uri": "...", "code_verifier": "...", "state": "..."}
4. API responds with a JSON TokenResponse (Zeeb-issued JWTs)
```

`state` is **required** in step 3 (a request without it is a 422): it must be a
state token this API signed for the same provider, and the ID token's `nonce`
is checked against the one it carries. Without that check an authorization
code or ID token obtained for any other login could be replayed at `/token/`.
An SPA that used to build the IdP URL without asking the API for a state must
now call `GET /{provider}/state/` first.

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

## Provider Classes

| Class | Use |
|---|---|
| `OAuthProvider` | The generic OAuth2/OIDC provider — PKCE, discovery, async JWKS validation. Subclass or instantiate it for an IdP with no preset. |
| `AzureADProvider` | Microsoft Entra ID preset (tenant modes, issuer validation) |
| `GoogleProvider` | Google Sign-In preset (pure OIDC via discovery) |
| `GitHubProvider` | GitHub preset (plain OAuth2 + `/user/emails`) |

Supporting types you will meet when writing hooks or a custom provider:

| Type | What it is |
|---|---|
| `OAuth2Client` | Thin async HTTP client for the authorize/token/userinfo calls |
| `OAuthTokenSet` | What the IdP's token endpoint returned |
| `ExternalClaims` | Identity claims normalised across providers — this is what `get_or_create_user` receives |
| `UserInfo` | The user payload `/auth/me` returns |
| `RefreshRequest` | Body model for the refresh endpoint |

The settings-driven registry is built by `build_providers_from_settings()` and
read through `get_oauth_provider(name)`, which caches after the first call:

```python
from zeeb_api.auth.oauth import get_oauth_provider

provider = get_oauth_provider("azure")
```

`get_oauth_provider()` raises `KeyError` for a name that is not in
`OAUTH_PROVIDERS`, and `build_providers_from_settings()` raises
`ImproperlyConfigured` for an entry whose name is not `azure`/`google`/`github`
and that has no `"class"` dotted path.

The registry is built on first use and then cached for the process — each
provider instance also caches its discovery document and JWKS.
`clear_provider_cache()` (no arguments) drops the cached registry so the next
lookup rebuilds it from the current `OAUTH_PROVIDERS`: call it in tests that
override that setting, or after changing it at runtime.

```python
from zeeb_api.auth.oauth import clear_provider_cache, get_oauth_provider
from zeeb_api.conf import settings


def test_uses_the_overridden_provider():
    settings.OAUTH_PROVIDERS = {"google": {"client_id": "test-id"}}
    clear_provider_cache()
    assert get_oauth_provider("google").client_id == "test-id"
```

Clearing the cache affects code that looks providers up afterwards — the
router built by `create_oauth_router()` without `providers=`, and
`OAuth2BearerAuthentication`. A running `JWTAuthMiddleware` resolves its
external validators once, on its first request, and keeps them.

Every provider constructor also takes `require_verified_email=` to override
`OAUTH_REQUIRE_VERIFIED_EMAIL` for that provider alone.

## User Linking and Provisioning

Logins are tied to local users through the `ExternalIdentity` model
(table `auth_external_identities`, unique per `(provider, subject)`):

1. An existing identity wins — its user is logged in.
2. **The email must be verified.** With `OAUTH_REQUIRE_VERIFIED_EMAIL = True`
   (default), a provider reporting the address as unverified — or not reporting
   a flag at all — stops here with `401 AUTH_OAUTH_EMAIL_UNVERIFIED`. This
   gate precedes both of the next two steps, so it blocks auto-provisioning as
   well as linking.
3. If a local user already has that email, it is linked only when
   `OAUTH_LINK_BY_EMAIL = True` (default `False`) **and** that local account is
   known to own the address — the user model has an `email_verified` attribute
   that is `True` (a column your own verification flow sets), or the user
   already has an identity from a provider that verified the same address.
   Otherwise the login fails with `401 AUTH_OAUTH_ACCOUNT_EXISTS`; a second
   account with the same email is never created beside it.
4. Otherwise, with `OAUTH_AUTO_CREATE_USERS = True` (default), a new user is
   created **without a usable password** (they can only log in via SSO until
   they set one).
5. Otherwise the login fails with `401 AUTH_OAUTH_USER_NOT_PROVISIONED`.

A deactivated local account never logs in through SSO: an existing identity or
an email link that resolves to it fails with `401 AUTH_ACCOUNT_DISABLED`.

### Why linking by email is off by default

`POST /auth/register` does not verify the address it is given. With email
linking on and no check of the local side, an attacker could register
`victim@example.com` first; the victim's later Google or GitHub login would be
linked to the attacker's account, and the attacker keeps its password
(pre-registration account hijack). Hence the default and the rule in step 3.

**Upgrading:** `OAUTH_LINK_BY_EMAIL` used to default to `True` and linked any
local account. If you rely on linking, set it to `True` explicitly and give
your user model an `email_verified` field that your verification flow sets —
accounts without it are refused with `AUTH_OAUTH_ACCOUNT_EXISTS` instead of
being linked.

Both behaviours can also be set per provider
(`auto_create_user=` / `link_by_email=` on the provider). To replace the whole
upsert step, pass `get_or_create_user=async fn(provider_name, claims)` to
`create_oauth_router`; a post-login hook `on_login(user, identity, created)`
is also available.

### get_or_create_user_for_identity()

The default upsert step, used by the router and by `ExternalTokenValidator`,
is a public coroutine you can call yourself — typically from a custom
`get_or_create_user` that adds a rule and then defers to the standard
behaviour:

```python
async def get_or_create_user_for_identity(
    provider: str,
    claims: ExternalClaims,
    *,
    auto_create: bool,
    link_by_email: bool,
    require_verified_email: bool = True,
) -> tuple[user, ExternalIdentity, bool]: ...
```

`provider` is the provider name stored on the `ExternalIdentity` row and
`claims` the normalised `ExternalClaims`. The keyword flags are taken
literally — unlike the router, the function does not read
`OAUTH_AUTO_CREATE_USERS` / `OAUTH_LINK_BY_EMAIL` /
`OAUTH_REQUIRE_VERIFIED_EMAIL`, so pass them explicitly. It runs exactly the
five steps listed above and returns `(user, identity, created)`:

- an existing `(provider, subject)` identity → its user, with the identity's
  `last_login_at`, `email` and `extra_data` (the raw claims) refreshed;
  `created` is `False`;
- an email link or a newly provisioned user → a new identity row;
  `created` is `True`. A provisioned user gets `email` from the claims,
  `first_name`/`last_name` split from the `name` claim (when the user model
  has those fields), no usable password, and `date_joined`/`last_login` set
  to now.

Every refusal is an `AuthenticationException` (401) with the code from the
steps above: `AUTH_ACCOUNT_DISABLED`, `AUTH_OAUTH_EMAIL_UNVERIFIED`,
`AUTH_OAUTH_ACCOUNT_EXISTS` or `AUTH_OAUTH_USER_NOT_PROVISIONED` (the last also
when auto-creation is on but the claims carry no email address).

```python
from zeeb_api.auth.oauth import create_oauth_router, get_or_create_user_for_identity
from zeeb_api.exceptions import AuthenticationException, ErrorCode


async def corporate_users_only(provider_name, claims):
    if not (claims.email or "").endswith("@example.com"):
        raise AuthenticationException(
            code=ErrorCode.AUTH_OAUTH_USER_NOT_PROVISIONED,
            message="Only example.com accounts may sign in",
        )
    return await get_or_create_user_for_identity(
        provider_name, claims, auto_create=True, link_by_email=False
    )


app.include_router(create_oauth_router(get_or_create_user=corporate_users_only))
```

Like `ExternalIdentity`, it lives in the module that registers the
`auth_external_identities` table, so importing it has the same effect on
migrations (see the next section).

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
resolved to the linked database user via `ExternalIdentity`. Only a token with
**no** linked identity falls back to a lightweight `ExternalAuthenticatedUser`
built from the token claims (without roles, unless you opt in — see below).
Invalid tokens never raise —
`request.state.user` simply stays `None`. There is zero overhead when nothing is
configured.

The resolution fails closed: a token whose linked account is deleted or
deactivated authenticates nobody, and neither does one whose identity lookup
errors (database down, or the `auth_external_identities` table never
migrated) — an error is logged and the request proceeds unauthenticated
rather than as a claims-only user.

### Roles from external tokens

A linked user (an `ExternalIdentity` points at a local account) gets its
roles, `is_staff` and `is_superuser` from the database alone, exactly like a
local login. An `ExternalAuthenticatedUser` — a valid IdP token with **no**
linked account — is authenticated but by default has **no roles and no staff
flags**: the token's `roles`, `is_staff` and `is_superuser` claims say
whatever the IdP's admins put there, so `require_auth(roles=[...])`,
`IsAdminUser` and friends ignore them.

Opt in when every principal the IdP can issue a token for is one you would let
choose those roles (e.g. a single-tenant Azure app whose app roles your own
admins assign):

```python
OAUTH_TRUST_TOKEN_ROLES = True                 # every provider
# or per provider
AzureADProvider(tenant="<tenant GUID>", client_id="...", trust_token_roles=True)
```

The opt-in never applies to a multi-tenant `AzureADProvider` accepting any
tenant (`allow_any_tenant=True`), and with `allowed_tenants` only to tokens
from those tenants. Subclasses can narrow it further by overriding
`OAuthProvider.trusts_token_roles(claims)`; `ExternalAuthenticatedUser`
exposes the outcome as `trusts_token_roles` and the trusted roles as `roles`.

**Upgrading:** an unlinked external user used to receive the token's
`roles`/`is_staff`/`is_superuser` unconditionally. If you relied on that, set
`OAUTH_TRUST_TOKEN_ROLES = True` (and, for multi-tenant Azure,
`allowed_tenants`), or link the identities to local accounts and manage their
roles there.

### JWKS refetch limits

The signing key is looked up by the token's `kid` header *before* the
signature can be checked, so an unknown `kid` must not cost an outbound
request every time. An unknown `kid` forces at most one JWKS refetch per
`OAUTH_JWKS_REFETCH_COOLDOWN_SECONDS` (default `60`; per provider
`jwks_refetch_cooldown_seconds=`), concurrent fetches share one request, and a
`kid` a fetch confirmed absent is remembered for five minutes. Real key
rotation still works: the first token with the new `kid` after the cooldown
triggers the refetch.

To accept IdP tokens on some viewsets only, leave
`OAUTH_ACCEPT_EXTERNAL_TOKENS` empty and list `OAuth2BearerAuthentication` in
those viewsets' `authentication_classes` — see
[Per-viewset Authentication](authentication.md#per-viewset-authentication).

## Building Blocks

The router and the provider classes are assembled from the pieces below. They
are public so a custom provider, a custom flow or a test can reuse them. All of
them are importable from `zeeb_api.auth.oauth`; the error classes,
`OIDCMetadata`, `JWKSCache` and `get_or_create_user_for_identity` are also
re-exported (lazily) from `zeeb_api.auth`.

### Errors

The OAuth client layer raises its own exceptions rather than HTTP errors:

| Exception | Raised when |
|---|---|
| `OAuthError` | Base class of the two below — catch it to handle any OAuth client failure |
| `OAuthExchangeError` | An HTTP call to the IdP failed: network error, a non-2xx status, a non-JSON body, a token response without `access_token`, or a JWKS document without `keys` |
| `OAuthValidationError` | A token or its configuration failed validation: bad signature, audience, expiry, issuer or nonce; a forbidden algorithm; an unknown `kid`; a provider with no `jwks_uri`, or with neither `server_metadata_url` nor manual endpoints; a subject claim missing from the token |

At the router boundary, while completing a login, `OAuthValidationError`
becomes `401 AUTH_OAUTH_ID_TOKEN_INVALID` and any other `OAuthError`
`401 AUTH_OAUTH_EXCHANGE_FAILED`. `ExternalTokenValidator` swallows them and
treats the token as unauthenticated.

```python
from zeeb_api.auth.oauth import OAuthError, OAuthValidationError, get_oauth_provider

provider = get_oauth_provider("azure")
try:
    claims = await provider.validate_external_token(token)
except OAuthValidationError:
    claims = None   # signature, audience, issuer, expiry, ...
except OAuthError:
    raise           # the IdP could not be reached
```

### OIDCMetadata

`OIDCMetadata` is the Pydantic model for an OpenID Connect discovery document
(`/.well-known/openid-configuration`). `OAuthProvider.get_metadata()` returns
one — fetched from `server_metadata_url`, or assembled from the manual
endpoint arguments — and caches it on the provider. Manually configured
endpoints always override discovered ones.

| Field | Type | Default |
|---|---|---|
| `issuer` | `str` | required |
| `authorization_endpoint` | `str` | required |
| `token_endpoint` | `str` | required |
| `userinfo_endpoint` | `str \| None` | `None` |
| `jwks_uri` | `str \| None` | `None` |
| `id_token_signing_alg_values_supported` | `list[str]` | `["RS256"]` |

Any other keys in the document are kept as extra attributes. The
`id_token_signing_alg_values_supported` list is what a provider passes as
`allowed_algs` when validating tokens.

```python
metadata = await get_oauth_provider("google").get_metadata()
metadata.jwks_uri                              # as published by the IdP
metadata.id_token_signing_alg_values_supported  # e.g. ["RS256"]
```

### JWKSCache and validate_id_token()

`JWKSCache(client, jwks_uri, ttl_seconds=3600, *, refetch_cooldown_seconds=60.0,
unknown_kid_ttl_seconds=300.0, max_unknown_kids=1024)` caches an IdP's JSON Web Key
Set, fetched asynchronously through an `OAuth2Client`. Its one method,
`await cache.get_signing_key(kid)`, returns the `jwt.PyJWK` for a key id:

- the key set is fetched on first use and refetched once it is older than
  `ttl_seconds`; concurrent fetches are coalesced into one;
- an unknown `kid` forces **one** extra refetch (the usual key rollover case)
  before failing with `OAuthValidationError` — but at most one forced refetch
  per `refetch_cooldown_seconds` (default `60`): inside the cooldown an unknown
  `kid` fails without fetching;
- a `kid` a fetch confirmed absent fails without fetching for
  `unknown_kid_ttl_seconds` (default `300`), in a negative cache bounded to
  `max_unknown_kids` (default `1024`) entries;
- a JWKS document PyJWT cannot parse raises `OAuthValidationError`; a failed
  fetch raises `OAuthExchangeError`.

Each provider creates one lazily from its metadata's `jwks_uri`, with the
cooldown from `jwks_refetch_cooldown_seconds=` or
`OAUTH_JWKS_REFETCH_COOLDOWN_SECONDS`.

`validate_id_token()` verifies a JWT against such a cache and returns its
claims as a `dict`:

```python
async def validate_id_token(
    id_token: str,
    *,
    jwks: JWKSCache,
    client_id: str,
    issuer: str | None = None,
    issuer_validator: Callable[[str], bool] | None = None,
    nonce: str | None = None,
    allowed_algs: list[str] | None = None,
) -> dict[str, Any]: ...
```

| Parameter | Meaning |
|---|---|
| `jwks` | The key cache used to find the signing key named by the token's `kid` header |
| `client_id` | The required audience (`aud`) |
| `issuer` | Exact expected `iss`; ignored when `issuer_validator` is given; with neither, any issuer is accepted |
| `issuer_validator` | A callable receiving `iss` and returning `True` to accept it (how `AzureADProvider` handles multi-tenant issuers) |
| `nonce` | When not `None`, the token's `nonce` claim must equal it |
| `allowed_algs` | Accepted signature algorithms, default `["RS256"]` |

`none` and every `HS*` algorithm are refused even if listed — the list itself
is rejected. The token must carry a `kid` header and the `exp`, `iat`, `aud`
and `iss` claims; signature, audience and expiry are verified by PyJWT. Every
failure raises `OAuthValidationError`.

```python
from zeeb_api.auth.oauth import JWKSCache, OAuth2Client, validate_id_token

jwks = JWKSCache(OAuth2Client(), "https://login.example.com/keys", ttl_seconds=900)
claims = await validate_id_token(
    id_token,
    jwks=jwks,
    client_id="my-client-id",
    issuer="https://login.example.com",
    nonce=expected_nonce,
)
```

### State, nonce and PKCE helpers

These are the functions the browser and SPA flows use to protect the
authorization-code round trip. Use them when building a flow of your own.

| Function | Returns |
|---|---|
| `generate_nonce()` | A random URL-safe string (32 bytes of entropy) for the OIDC `nonce` |
| `generate_pkce_pair()` | `(code_verifier, code_challenge)` — an RFC 7636 S256 pair; the verifier is 86 characters, the challenge its unpadded base64url SHA-256 |
| `pkce_cookie_name(provider)` | The name of the cookie holding the PKCE verifier: `zeeb_oauth_pkce_{provider}` |
| `create_state_token(provider, *, nonce, redirect_uri, next_url=None)` | A signed `state` string |
| `decode_state_token(token, provider)` | The state's claims: `{"nonce", "redirect_uri", "next"}` |

`create_state_token()` signs a short-lived JWT of type `oauth_state` with the
application's JWT secret and algorithm. Its subject is the provider name, it
lives for `OAUTH_STATE_TTL_SECONDS` (default 600) and it carries the nonce, the
redirect URI and the optional post-login `next` URL — so no server-side session
is needed. The insecure-secret guard applies: with `DEBUG` off and a weak
secret it raises `InsecureSecretError`.

`decode_state_token()` verifies the signature, expiry and token type, and that
the state was issued for `provider`. Any failure — expired, tampered, a
different provider, a different token type — raises
`AuthenticationException` with `AUTH_OAUTH_STATE_INVALID` (401).

The PKCE verifier is never put in the state, because the state travels through
the IdP. The built-in authorize endpoint stores it in an `HttpOnly`, `Secure`,
`SameSite=Lax` cookie named by `pkce_cookie_name()`, and the callback reads and
deletes it:

```python
from zeeb_api.auth.oauth import (
    create_state_token,
    decode_state_token,
    generate_nonce,
    generate_pkce_pair,
    pkce_cookie_name,
)

nonce = generate_nonce()
verifier, challenge = generate_pkce_pair()
state = create_state_token("azure", nonce=nonce, redirect_uri=redirect_uri)
url = await provider.build_authorization_url(
    redirect_uri=redirect_uri, state=state, nonce=nonce, code_challenge=challenge
)
response.set_cookie(pkce_cookie_name("azure"), verifier, httponly=True, secure=True)

# ... later, in the callback
claims = decode_state_token(request.query_params["state"], "azure")
verifier = request.cookies.get(pkce_cookie_name("azure"))
tokens = await provider.exchange_code(
    code=code, redirect_uri=claims["redirect_uri"], code_verifier=verifier
)
identity_claims = await provider.get_claims(tokens, nonce=claims["nonce"])
```

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `OAUTH_PROVIDERS` | `{}` | Provider registry (`class` inferred for azure/google/github) |
| `OAUTH_AUTO_CREATE_USERS` | `True` | Provision users on first login |
| `OAUTH_LINK_BY_EMAIL` | `False` | Attach identities to existing users by email — only to a local account that verified the address |
| `OAUTH_STATE_TTL_SECONDS` | `600` | Lifetime of the signed state token (and PKCE cookie) |
| `OAUTH_REDIRECT_URI` | `None` | Fixed redirect URI (default: derived from the callback route) |
| `OAUTH_SUCCESS_REDIRECT` | `None` | Browser-flow redirect target (tokens in URL fragment) |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | `[]` | Hosts allowed as absolute `next` redirect targets (relative paths are always allowed) |
| `OAUTH_ACCEPT_EXTERNAL_TOKENS` | `[]` | Provider names whose IdP-issued JWTs are accepted as Bearer tokens |
| `OAUTH_REQUIRE_VERIFIED_EMAIL` | `True` | Refuse to link or provision when the provider reports the email as unverified (or reports no flag). Override per provider with `require_verified_email=` |
| `OAUTH_TRUST_TOKEN_ROLES` | `False` | Let an external bearer token with no linked account carry its `roles`/`is_staff`/`is_superuser` claims. Override per provider with `trust_token_roles=`; never for a multi-tenant Azure provider without `allowed_tenants` |
| `OAUTH_JWKS_REFETCH_COOLDOWN_SECONDS` | `60` | Minimum interval between JWKS refetches forced by an unknown `kid`. Override per provider with `jwks_refetch_cooldown_seconds=` |

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
  email claim. `OAUTH_REQUIRE_VERIFIED_EMAIL` (default `True`) mitigates this
  by refusing to link *or* provision on an unverified address, so an IdP that
  does not verify email ownership cannot be used to take over a matching local
  account. If you turn that gate off, keep `OAUTH_LINK_BY_EMAIL` enabled only
  for providers that verify emails (Azure AD and Google do; be careful with
  custom IdPs), or disable it per provider with `link_by_email=False`.
  `email_verified` is read strictly: `true`/`false` booleans or strings; any
  other value counts as unverified.
- **Multi-tenant Azure AD**: a provider on `common`/`organizations`/`consumers`
  refuses every token until you set `allowed_tenants` or
  `allow_any_tenant=True` — see [Multi-tenant Azure AD](#multi-tenant-azure-ad).
- **IdP-asserted roles**: an external token without a linked account carries
  no roles or staff flags unless `OAUTH_TRUST_TOKEN_ROLES` / `trust_token_roles`
  is on — see [Roles from external tokens](#roles-from-external-tokens).
- **JWKS amplification**: unknown `kid` values trigger at most one refetch per
  cooldown — see [JWKS refetch limits](#jwks-refetch-limits).
- **Pre-registration hijack**: the local side must prove ownership too — see
  [Why linking by email is off by default](#why-linking-by-email-is-off-by-default).
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
| `AUTH_OAUTH_EMAIL_UNVERIFIED` | 401 | `OAUTH_REQUIRE_VERIFIED_EMAIL` is on and the provider reported the address as unverified (or reported no flag) — blocks linking *and* provisioning |
| `AUTH_OAUTH_ACCOUNT_EXISTS` | 401 | A local account already uses the email and may not be linked (linking off, or the local account never verified its address) |
| `AUTH_ACCOUNT_DISABLED` | 401 | The identity (or email link) resolves to a deactivated or deleted local account |

## Next Steps

- [Authentication](authentication.md) — local users, passwords, JWT sessions
- [Permissions](permissions.md) — protecting endpoints
