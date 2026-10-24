# Authentication

Zeeb provides a flexible authentication system with swappable user models.

## What a generated project already has

`zeeb startproject` wires authentication end to end, so there is nothing to set
up before the first login:

| Endpoint | Purpose |
|----------|---------|
| `POST {API_PREFIX}/auth/register` | Create an account |
| `POST {API_PREFIX}/auth/login` | Exchange credentials for an access + refresh token |
| `POST {API_PREFIX}/auth/refresh` | Rotate the refresh token, get a new access token |
| `POST {API_PREFIX}/auth/logout` | Invoke the `on_logout` hook; see the note below |
| `GET {API_PREFIX}/auth/me` | The current user's claims |

Three pieces make that work, and all three are in the scaffold:

1. `"zeeb_api.middleware.JWTAuthMiddleware"` in `MIDDLEWARE`, which resolves the
   Bearer token into `request.state.user` — every permission class reads it.
2. `router.include(create_auth_router(...))` in the project `urls.py`;
   `create_app()` does not mount it for you.
3. A `SECRET_KEY` of at least 32 bytes that is not one of the framework's
   known-insecure defaults. `startproject` generates one into `.env`; without
   it `create_app()` refuses to start once `DEBUG` is off.

`/login`, `/register` and `/refresh` are rate limited per client — they are
the routes an attacker can drive without an access token. The limit comes from
`AUTH_LOGIN_THROTTLE_RATE` (default `10/min`, empty turns it off) and is passed
through `create_auth_router(login_throttle=...)`; `/refresh` uses the same rate
in its own bucket (override with `refresh_throttle=`, `None` disables), so
refreshing never eats into the login budget. The OAuth routes are throttled at
the same rate (`create_oauth_router(throttle_rate=...)`, defaulting to
`AUTH_LOGIN_THROTTLE_RATE`). `/logout` and `/me` need a valid access token and
stay unthrottled.

Related env variables: `AUTH_URL_PREFIX`, `AUTH_ENABLE_REGISTRATION`,
`AUTH_LOGIN_THROTTLE_RATE`, `JWT_*`. See
[Settings](../configuration/settings.md).

> **`/logout` revokes the refresh token you send it.** With
> `{"refresh_token": "..."}` in the body, that token and every token rotated
> from the same login are revoked in the refresh-token store (see
> [Refresh Token Rotation](#refresh-token-rotation)). The access token is
> stateless and stays valid until it expires: the endpoint hands its `jti` to
> the optional `on_logout(jti)` hook you pass to `create_auth_router()`, which
> is where a denylist would go. Clients should discard both tokens locally.

## User Model

A generated project owns its user model from the first migration: `startproject`
writes `apps/accounts/models.py` with

```python
from zeeb_api.auth.models import AbstractUser


class User(AbstractUser):
    class Meta:
        table_name = "accounts_user"
```

and sets `AUTH_USER_MODEL = "accounts.User"`. Add your own fields there. The
built-in `auth_users` table is then never created — everything resolves to your
class, which is why the model is generated up front rather than swapped in
later against an existing schema.

Reference it as `ForeignKey("accounts.User")`, not the bare `"User"`: the
framework's own user class shares that class name and the registry is keyed by
bare name, so the short form depends on import order.

### Default User

Zeeb provides a default `User` model:

```python
from zeeb_api.auth import User

# Create user
user = await User.objects.create(
    username="john",
    email="john@example.com",
)
user.set_password("secret123")
await user.save()

# Authenticate
from zeeb_api.auth import authenticate

user = await authenticate(username="john", password="secret123")
if user:
    print(f"Authenticated: {user.username}")
```

### User Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `username` | CharField | Unique username |
| `email` | EmailField | Unique email |
| `password` | CharField | Hashed password |
| `first_name` | CharField | First name |
| `last_name` | CharField | Last name |
| `is_active` | BooleanField | Account active |
| `is_staff` | BooleanField | Staff access |
| `is_superuser` | BooleanField | All permissions |
| `date_joined` | DateTimeField | Registration date |
| `last_login` | DateTimeField | Last login |

### User Methods

```python
# Password
user.set_password("newpassword")
if user.check_password("guess"):
    print("Correct!")

# Name
full_name = user.get_full_name()  # "John Doe"
short_name = user.get_short_name()  # "John"

# Status
user.is_authenticated  # True for real users
user.is_anonymous  # False for real users

# Permissions
has_perm = await user.has_perm_async("add_article")
has_perm_cached = user.has_perm("add_article")  # sync, uses already-loaded perms

# JWT claims
claims = user.get_claims()
# {"user_id": "uuid", "username": "john", "email": "...", ...}
```

## Custom User Model

### Creating Custom User

```python
# apps/accounts/models.py
from zeeb_api.auth.models import AbstractUser
from zeeb_orm import fields


class CustomUser(AbstractUser):
    """Custom user with additional fields."""
    phone = fields.CharField(max_length=20, null=True)
    avatar_url = fields.URLField(null=True)
    company = fields.CharField(max_length=100, null=True)
    
    class Meta:
        table_name = "users"
```

Two base classes are available:

| Base | Gives you |
|---|---|
| `AbstractUser` | The full model — `email`, `username`, `is_active`, `is_staff`, `is_superuser`, `date_joined`, plus the password and permission methods. Start here. |
| `AbstractBaseUser` | Password handling only (`set_password`, `check_password`, `is_authenticated`). Use it when you want to define every other field yourself. |

Choosing `AbstractBaseUser` means the permission helpers (`has_perm`,
`has_perm_async`) and the flags that `IsAdminUser` / `ModelPermissions` read are
yours to provide.

### Configure in Settings

```python
# settings.py
AUTH_USER_MODEL = "apps.accounts.CustomUser"
```

### Access Custom User

```python
from zeeb_api.auth import get_user_model

User = get_user_model()

# Now User is your CustomUser
user = await User.objects.create(
    username="john",
    email="john@example.com",
    phone="+1234567890",
    company="Acme Corp",
)
```

## Password Management

### Password Hashing

Passwords are automatically hashed:

```python
user = await User.objects.create(username="john", email="john@example.com")
user.set_password("secret123")  # Hashes password
await user.save()

# Verify
user.check_password("secret123")  # True
user.check_password("wrong")      # False
```

### The Hasher

Hashing is **bcrypt**, with no pluggable hasher registry — there is no
`PASSWORD_HASHERS` setting. `set_password()` and `check_password()` on the user
model delegate to three helpers you can also call directly:

```python
from zeeb_api import make_password
from zeeb_api.auth.hashers import check_password, is_password_usable

hashed = make_password("secret123")
check_password("secret123", hashed)   # True
is_password_usable(hashed)            # False for an unset/unusable password
```

bcrypt reads at most **72 bytes** of a password (UTF-8 encoded — `"ä"` is two
bytes), and bcrypt 5 raises instead of silently truncating. The limit is
enforced up front: `/auth/register` and `/auth/login` answer `422` with a
`FIELD_TOO_LONG` detail on `password`, and `make_password()`/`set_password()`
raise `zeeb_api.exceptions.PasswordTooLongError` — a 400 field error in a
request, and still a `ValueError` for code that sets passwords elsewhere.
`check_password()` simply returns `False` for such input.

`check_password()` against an empty or unusable hash (an account created by
OAuth sign-in has no password) still runs one bcrypt verification against a
throwaway hash before returning `False`, as `authenticate()` does for an
unknown email — so a failed login costs the same whether the account is
missing, passwordless or given the wrong password, and its timing does not
reveal which.

### Password Validation

The framework ships no password-strength validators and has no
`AUTH_PASSWORD_VALIDATORS` setting. Enforce your own rules in the serializer
that accepts the password — see
[Serializers](serializers.md) for field-level `validators`:

```python
def validate_password_strength(value: str) -> str:
    if len(value) < 12:
        raise ValueError("Password must be at least 12 characters.")
    return value


class RegisterSerializer(Serializer):
    password = fields.CharField(
        write_only=True,
        validators=[validate_password_strength],
    )
```

## Authentication Backend

### authenticate()

```python
from zeeb_api.auth import authenticate

# By username
user = await authenticate(username="john", password="secret123")

# By email
user = await authenticate(email="john@example.com", password="secret123")

# Returns None if failed (wrong password, unknown user or inactive account)
if user is None:
    print("Invalid credentials")
else:
    print(f"Welcome, {user.username}!")
```

`authenticate()` performs exactly one bcrypt verification whatever the
outcome — against a throwaway hash when the email is unknown, and before the
`is_active` check for an inactive account — so response times do not reveal
which addresses have accounts.

### Custom Authentication

There is no pluggable backend registry and no `AUTHENTICATION_BACKENDS`
setting. `authenticate()` already accepts either `email` or `username`; when you
need different logic, write a plain async function and call it from your own
login route:

```python
# apps/accounts/auth.py
from zeeb_api.auth import get_user_model


async def authenticate_by_email(email: str, password: str):
    """Authenticate by email only."""
    User = get_user_model()

    try:
        user = await User.objects.get(email=email)
    except User.DoesNotExist:
        return None

    if not user.check_password(password):
        return None

    return user
```

`set_password()` and `check_password()` on the user model are **sync** — only
the database calls around them are awaited.

To point the framework at a different user model programmatically, use
`configure_auth()`:

```python
from zeeb_api import configure_auth
from apps.accounts.models import User

configure_auth(user_model=User)
```

In a scaffolded project this is driven by the `AUTH_USER_MODEL` setting
instead, and you do not need to call `configure_auth()` yourself.

#### clear_user_model_cache()

`get_user_model()` resolves `AUTH_USER_MODEL` once and caches the class for
the life of the process; `configure_auth(user_model=...)` simply writes that
same cache. `clear_user_model_cache()` (no arguments, returns `None`) empties
it, so the next `get_user_model()` call resolves `AUTH_USER_MODEL` again — or
falls back to the built-in `User` when the setting is unset. It also undoes an
earlier `configure_auth()`.

You only need it when the user model changes inside one process, which in
practice means tests:

```python
import pytest

from zeeb_api.auth import clear_user_model_cache, configure_auth, get_user_model


@pytest.fixture
def custom_user_model():
    from apps.accounts.models import CustomUser

    configure_auth(user_model=CustomUser)
    yield get_user_model()
    clear_user_model_cache()   # the next test resolves AUTH_USER_MODEL afresh
```

A model path that cannot be imported is never cached (a warning is issued and
the built-in `User` is returned for that call only), so there is nothing to
clear after fixing a typo in `AUTH_USER_MODEL`.

## Permissions

Permissions work with both the default `User` and custom user models.
When `AUTH_USER_MODEL` is set, `UserPermission` automatically references
the configured user model.

### Permission Model

```python
from zeeb_api.auth.models import Permission, UserPermission

# Create permission
perm = await Permission.objects.create(
    codename="publish_article",
    name="Can publish articles",
)

# Assign to user (works with default or custom user model)
await UserPermission.objects.create(user_id=user.id, permission_id=perm.id)

# Check permission
if await user.has_perm_async("publish_article"):
    # User can publish
    ...
```

### Permission Checking

Permissions are matched on the bare `Permission.codename` — there is no
`app_label.` prefix.

```python
# Query the database for the permission (async)
await user.has_perm_async("add_article")

# Check against permissions already loaded on the instance (sync)
user.has_perm("add_article")
user.has_perms(["add_article", "change_article"])   # AND

# Superusers have all permissions
if user.is_superuser:
    # Has all permissions automatically
    ...
```

> `has_perm()` and `has_perms()` are **synchronous** and only see permissions
> already loaded onto the user object. Only `has_perm_async()` hits the
> database. Awaiting `has_perm()` is an error.

### Object-level Permissions

```python
class Article(Model):
    author = fields.ForeignKey(User)
    
    def user_can_edit(self, user):
        """Check if user can edit this article."""
        return user == self.author or user.is_staff
```

## Creating Users

### create_user()

```python
from zeeb_api.auth import get_user_model

User = get_user_model()

# Regular user
user = await User.objects.create_user(
    username="john",
    email="john@example.com",
    password="secret123",
)
```

### create_superuser()

```python
# Superuser (has all permissions)
admin = await User.objects.create_superuser(
    username="admin",
    email="admin@example.com",
    password="adminpass",
)
# is_staff=True, is_superuser=True automatically
```

### CLI: createsuperuser

```bash
python manage.py createsuperuser
```

Interactive prompts for username, email, and password.

## Session/Token Authentication

### JWT Tokens

```python
from zeeb_api.auth.jwt import create_access_token, create_token_pair, decode_token

# Create a token — the first argument is the user *id*, not the user object
token = create_access_token(user.id)
# "eyJ0eXAiOiJKV1QiLC..."

# Attach extra claims
token = create_access_token(user.id, {"is_staff": user.is_staff})

# Decode / verify
payload = decode_token(token)
# {"sub": "uuid", "type": "access", "exp": ..., "jti": ...}

# Both tokens at once — a (access, refresh) tuple, not a dict
access_token, refresh_token = create_token_pair(user.id, {"is_staff": user.is_staff})
```

The `{"access_token", "refresh_token", "token_type", "expires_in"}` JSON object
is what the `/auth/login` **endpoint** returns; `create_token_pair()` itself
returns the two encoded strings.

`decode_token()` raises `TokenExpiredError` or `TokenInvalidError` (both
subclasses of `TokenError`) rather than returning `None`. When `JWT_ISSUER` or
`JWT_AUDIENCE` is configured, the corresponding claim is **required** and
verified — tokens minted before you set them will be rejected.

> `create_refresh_token()` takes `claims` as its **second** argument
> (`user_id, claims=None, config=None`). An older call written as
> `create_refresh_token(user_id, config)` now passes the config as claims —
> pass it by keyword: `create_refresh_token(user_id, config=cfg)`.

### Configuring JWT in code

Settings drive this in a scaffolded project, but `configure_jwt()` sets it
programmatically and returns the resulting `JWTConfig`:

```python
from zeeb_api import configure_jwt, get_jwt_config

configure_jwt(
    secret_key=...,
    algorithm="HS256",
    access_token_expire_minutes=60,
    refresh_token_expire_days=7,
    issuer="https://api.example.com",
    audience="myapp",
)

config = get_jwt_config()   # JWTConfig currently in force
```

`decode_token()` returns a `TokenPayload` whose fields are `sub` (the user id),
`type` (`"access"` or `"refresh"`), `exp`, `iat`, `jti`, optional `iss`/`aud`,
and `claims`.

### Route Dependencies

For plain FastAPI routes — outside a viewset, where `permission_classes` does
not apply — use the dependencies:

```python
from fastapi import Depends
from zeeb_api import get_current_user, get_current_user_optional, require_auth


@app.get("/me/dashboard")
async def dashboard(user=Depends(get_current_user)):
    """401s when unauthenticated."""
    return {"user_id": str(user.id)}


@app.get("/articles")
async def articles(user=Depends(get_current_user_optional)):
    """Returns None instead of raising when unauthenticated."""
    return {"personalised": user is not None}


@app.get("/admin/stats", dependencies=[Depends(require_auth(roles=["admin"]))])
async def stats():
    """Requires every listed role; pass any_role=True to require just one."""
    return {...}
```

The dependencies accept exactly what `JWTAuthMiddleware` accepts. When the
middleware has run, its verdict stands: a token whose account was deleted or
deactivated since it was issued is anonymous, so `get_current_user` answers
`401 AUTH_TOKEN_INVALID` (and `get_current_user_optional` returns `None`) —
the dependency does not decode the token again behind the middleware's back.
Without the middleware the dependencies resolve the token themselves the same
way: with `AUTH_LOAD_USER_FROM_DB` on (the default) the account is loaded and
must be active; a claims-only `AuthenticatedUser` is built only when that
setting is off.

`require_auth` answers `401` when nobody is authenticated and
`403 PERM_INSUFFICIENT_ROLE` when the user is authenticated but lacks a role.
A user's roles (`zeeb_api.auth.middleware.get_user_roles`) are the union of:

| Source | Roles |
|---|---|
| `user.get_roles()` | whatever it returns — override it on a custom user model (groups, a column, …) |
| `user.roles` | as listed |
| the token's `roles` claim | **token-only users only** (`AuthenticatedUser`, i.e. `AUTH_LOAD_USER_FROM_DB = False`) |
| account flags | `"staff"` for `is_staff`, `"superuser"` for `is_superuser`, `"admin"` for anyone `IsAdminUser` admits |

A user loaded from the database gets its roles from the database alone: the
`roles` claim written into its token at login is ignored, so demoting an
account (`is_staff = False`) takes effect on its next request rather than when
the token expires — `require_auth` and `IsAdminUser` agree. An external IdP
token with no linked account (`ExternalAuthenticatedUser`) carries the IdP's
`roles` (Azure AD app roles) and staff flags only when its provider trusts
them — off by default, see
[Roles from external tokens](oauth.md#roles-from-external-tokens).

`AbstractUser.get_roles()` returns the flag-derived roles and
`get_claims()` emits them as the `roles` claim, so a token-only
`AuthenticatedUser` (`AUTH_LOAD_USER_FROM_DB = False`) sees the same set.

**Upgrading:** roles used to be the union of the database and the token's
`roles` claim for every user. If a custom user model relied on extra roles
arriving only in the token, return them from `get_roles()` instead.

#### bearer_scheme

`zeeb_api.auth.middleware.bearer_scheme` is the FastAPI security scheme the
dependencies above declare — `HTTPBearer(auto_error=False)`. Declaring it is
what puts the *Authorize* (bearer token) button into `/docs` and the
`HTTPBearer` security requirement into `/openapi.json`. Because `auto_error` is
off, a request without an `Authorization: Bearer` header is not rejected by the
scheme itself: the dependency receives `None` and decides (401 from
`get_current_user`, `None` from `get_current_user_optional`).

Reuse it in a dependency of your own when you need the raw credentials and
want the route documented as bearer-protected:

```python
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials

from zeeb_api.auth.middleware import bearer_scheme


@app.get("/debug/token")
async def raw_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    return {"has_token": credentials is not None}
```

### Refresh Token Rotation

`POST /auth/refresh` **rotates**: it issues a fresh pair and records the
presented token's `jti` as consumed, so the same refresh token cannot be
redeemed twice. Replaying one — the signature that a token was stolen — is
rejected:

```json
{
  "success": false,
  "error": {"code": "AUTH_TOKEN_INVALID", "message": "Refresh token has already been used"}
}
```

Clients must therefore store the new refresh token from every `/refresh`
response and discard the old one. A client that keeps reusing its original
refresh token will work exactly once.

Redeeming is one atomic step (`consume_once`), so two requests replaying the
same token at the same moment cannot both be issued a pair. And a replay
revokes the token's whole **family** — every refresh token rotated from the
same login, tracked by the `fam` claim — because after a replay the owner's
copy and the thief's are indistinguishable: both must log in again. Other
logins (other families) are untouched. The one cost: two browser tabs racing
to refresh the same token count as a replay, so a client should serialise its
refreshes (one in flight at a time).

`POST /auth/logout` revokes the session's refresh token (and its family) when
it is sent in the body, so a logged-out refresh token cannot mint new access
tokens:

```http
POST /auth/logout
Authorization: Bearer <access token>

{"refresh_token": "<refresh token>"}
```

The body is optional; a token that is invalid, expired or belongs to another
user is ignored.

Refresh tokens also carry the user's claims, so a refresh does not require a
database round-trip. When `AUTH_LOAD_USER_FROM_DB` is on, claims are reloaded
and a deleted or inactive user is rejected with `AUTH_TOKEN_INVALID`.

#### Swapping the store

The default `InMemoryRefreshTokenStore` is **per process**: state is lost on
restart, and behind multiple uvicorn/gunicorn workers each process detects
reuse independently. That is best-effort, not a guarantee.

For a hard cross-process guarantee, implement `BaseRefreshTokenStore` over a
shared backend and install it at startup:

```python
from zeeb_api.auth.refresh_store import (
    BaseRefreshTokenStore,
    set_refresh_token_store,
)


class RedisRefreshTokenStore(BaseRefreshTokenStore):
    def __init__(self, redis):
        self._redis = redis

    async def is_consumed(self, jti: str) -> bool:
        return await self._redis.exists(f"rt:{jti}") == 1

    async def consume(self, jti: str, ttl_seconds: float) -> None:
        await self._redis.set(f"rt:{jti}", "1", ex=max(int(ttl_seconds), 1))

    async def consume_once(self, jti: str, ttl_seconds: float) -> bool:
        # SET NX is the atomic check-and-consume: only the first caller wins.
        return bool(
            await self._redis.set(f"rt:{jti}", "1", ex=max(int(ttl_seconds), 1), nx=True)
        )


set_refresh_token_store(RedisRefreshTokenStore(redis))
```

| Method | Must implement? | Default |
|---|---|---|
| `is_consumed(jti)` | yes | — |
| `consume(jti, ttl_seconds)` | yes | — |
| `consume_once(jti, ttl_seconds)` | **override with an atomic primitive** | `is_consumed` then `consume` — two concurrent requests can both pass |
| `revoke_family(family, ttl_seconds)` / `is_family_revoked(family)` | no | stored through `consume`/`is_consumed` under the key `family:<id>` |

`consume()` receives the token's **remaining** lifetime as `ttl_seconds`, so
entries can expire rather than accumulating forever (a family is revoked for
the full `JWT_REFRESH_TOKEN_EXPIRE_DAYS`, the longest any descendant can live).
`get_refresh_token_store()` returns the active store; call
`set_refresh_token_store(None)` to restore a fresh default, which is also how
tests reset between cases. A store written against the older two-method
interface keeps working unchanged.

These names live in `zeeb_api.auth.refresh_store` and are not re-exported from
`zeeb_api.auth`.

### JWT Secret Hardening

Zeeb refuses to sign or verify JWTs with an insecure secret: an **empty** one
(`SECRET_KEY=` in `.env` loads as the empty string), a known insecure default
(e.g. `"change-me-in-production"` or the startproject template default), or one
**shorter than 32 bytes** (RFC 7518 §3.2: an HS256 key at least as long as the
hash; a shorter one can be brute-forced offline from a single token):

- **`DEBUG = False`** (the default): `create_access_token()`,
  `create_refresh_token()` and `decode_token()` raise
  `zeeb_api.exceptions.InsecureSecretError`.
- **`DEBUG = True`**: the calls work, but a warning is logged once per
  process reminding you to set a real secret.
- **`create_app()`** fails fast with
  `zeeb_api.exceptions.ImproperlyConfigured` when `DEBUG` is off and
  `SECRET_KEY`/`JWT_SECRET_KEY` is empty, an insecure default or too short.

`zeeb_api.auth.jwt.secret_key_problem(key)` returns the reason a key is refused
(or `None`). Upgrading: a deployment whose secret is shorter than 32 bytes now
refuses to start with `DEBUG` off — generate a new one with
`python -c "import secrets; print(secrets.token_urlsafe(64))"` (this signs out
every session, as rotating the key always does).

Always set a strong, unique `SECRET_KEY` (or `JWT_SECRET_KEY`) in
production, e.g. from an environment variable.

### Token in ViewSet

> **Prefer the built-in auth router.** The recommended path is the shipped
> auth router (wire it with the `setup_auth` agent tool, or
> `zeeb_api.auth.create_auth_router()`). Its **actual** contract is:
> - `POST /auth/login` — body `{"email", "password"}` → returns
>   `{"access_token", "refresh_token", "token_type", "expires_in"}`
> - `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`,
>   `POST /auth/register` (canonical paths are **slash-less**).
>
> The hand-rolled `AuthViewSet` below is only an illustration of building a
> custom flow — it authenticates by `username` and returns a different
> `{"token", "user"}` shape, which does **not** match the built-in router.
> Build clients against the router contract above (and `/openapi.json`).

```python
from zeeb_api.viewsets import ViewSet
from zeeb_api.auth.jwt import create_access_token
from zeeb_api.auth import authenticate


class AuthViewSet(ViewSet):
    
    async def login(self, request):
        """POST /auth/login/ (custom example — see note above)"""
        data = await request.json()
        
        user = await authenticate(
            username=data.get("username"),
            password=data.get("password"),
        )
        
        if not user:
            return {"error": "Invalid credentials"}, 401
        
        if not user.is_active:
            return {"error": "Account disabled"}, 401
        
        token = create_access_token(user)
        return {
            "token": token,
            "user": {
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
            }
        }
    
    async def me(self, request):
        """GET /auth/me/ - Current user info."""
        user = request.user
        if not user.is_authenticated:
            return {"error": "Not authenticated"}, 401
        
        return {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
        }
```

### Token Middleware

You do not need to write this — `JWTAuthMiddleware` ships with the framework
and a scaffolded project installs it already. It reads the `Authorization:
Bearer` header and sets `request.state.user`.

```python
# settings.py
JWT_SECRET_KEY = "your-secret-key"
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
```

```python
from zeeb_api.middleware import JWTAuthMiddleware

app.add_middleware(JWTAuthMiddleware)
```

By default (`AUTH_LOAD_USER_FROM_DB = True`) the middleware loads the full user
row, and a deleted or inactive user is treated as unauthenticated. Setting it to
`False` skips the query and builds an `AuthenticatedUser` from the token claims
alone — faster, but see the warning in
[Permissions](permissions.md) about combining that with `ModelPermissions`.

`JWTAuthMiddleware(app, user_loader=None, load_user_from_db=None,
external_validators=None)` resolves its loader in this order: an explicit
`user_loader` wins; otherwise `load_user_from_db` (or, when that is `None`, the
`AUTH_LOAD_USER_FROM_DB` setting) picks `default_user_loader` or the
claims-only `AuthenticatedUser`. `external_validators` is covered in
[External Bearer Mode](oauth.md#external-bearer-mode-accept-idp-tokens-directly).

#### default_user_loader

`default_user_loader(payload)` is the loader used when
`AUTH_LOAD_USER_FROM_DB` is on. It takes the decoded `TokenPayload` of a valid
access token and:

1. resolves the user model with `get_user_model()`;
2. converts `payload.sub` to a `UUID` when it parses as one (otherwise the raw
   string is used) and fetches `User.objects.get(id=...)`;
3. returns `None` — the request is anonymous — when no row, or more than one,
   matches, or when the account has `is_active = False`;
4. otherwise attaches the payload as `user._token_payload` (so the token's
   claims stay reachable, e.g. for `get_user_roles`) and returns the user.

It fails **closed**: it never falls back to a claims-only user, and any other
error (the database is down, say) propagates and fails the request instead of
authenticating it from claims nobody could check. When the middleware's loader
returns `None` for a valid token, it records `AUTH_TOKEN_INVALID` on
`request.state.auth_error`, so a later permission denial answers
`401 AUTH_TOKEN_INVALID` rather than "credentials not provided".

#### Custom user loaders (UserLoaderFunc)

`zeeb_api.auth.middleware.UserLoaderFunc` is the type of a loader:
`Callable[[TokenPayload], Awaitable[Any]]` — an async function that receives the
decoded access token and returns the object to place on `request.state.user`,
or `None` to treat the request as anonymous. Pass one as `user_loader=` to
replace the database lookup, for instance to add a check on top of the default
one:

```python
from zeeb_api.auth import TokenPayload
from zeeb_api.auth.middleware import UserLoaderFunc, default_user_loader
from zeeb_api.middleware import JWTAuthMiddleware


async def load_unlocked_user(payload: TokenPayload):
    user = await default_user_loader(payload)
    if user is not None and getattr(user, "is_locked", False):
        return None   # anonymous; recorded as AUTH_TOKEN_INVALID
    return user


loader: UserLoaderFunc = load_unlocked_user
app.add_middleware(JWTAuthMiddleware, user_loader=loader)
```

Keep a custom loader fail-closed in the same way: return `None` for an account
that should no longer authenticate, and let unexpected errors raise. A loader
passed to the middleware applies only to the middleware; the route
dependencies, when they resolve a token themselves because no middleware ran,
use the settings-driven choice (`default_user_loader` or claims only).

### Per-viewset Authentication

The middleware authenticates every request the same way. A viewset can take
over authentication for its own endpoints with `authentication_classes`, a list
of backend classes from `zeeb_api.authentication`:

```python
from zeeb_api.authentication import JWTAuthentication, OAuth2BearerAuthentication
from zeeb_api.permissions import IsAuthenticated
from zeeb_api.viewsets import ModelViewSet


class ReportViewSet(ModelViewSet):
    queryset = Report.objects.all()
    serializer_class = ReportSerializer
    authentication_classes = [JWTAuthentication, OAuth2BearerAuthentication]
    permission_classes = [IsAuthenticated]
```

How the router applies the list, on every action of the viewset:

- **Unset (`None`, the default) or empty** — nothing happens; whatever
  `JWTAuthMiddleware` put on `request.state.user` stands.
- **Non-empty** — the viewset owns authentication. The middleware's
  `request.state.user` and `request.state.auth_error` are discarded, then each
  class is instantiated (with no arguments) and its `authenticate(request)`
  awaited in order. The first one that returns a user wins and becomes
  `request.state.user`; the rest are not called.
- If none returns a user, the request continues **unauthenticated** and the
  permission classes decide — with `IsAuthenticated` that is a 401, carrying
  `AUTH_TOKEN_EXPIRED` or `AUTH_TOKEN_INVALID` when a backend recorded why the
  token failed. Without permission classes the request proceeds anonymously.

Authentication runs before the permission check, which runs before
throttling.

#### BaseAuthentication

The base class every backend subclasses. Implement one method:

```python
async def authenticate(self, request) -> object | None: ...
```

- return a user object to authenticate the request (it becomes
  `request.state.user`; permission classes treat it as signed in unless it has
  `is_authenticated = False`);
- return `None` when the request carries no credentials this backend
  understands, so the next class in the list can try;
- raise `zeeb_api.exceptions.AuthenticationException` to reject the request
  outright with a 401 — no further backend is tried.

Calling `authenticate()` on `BaseAuthentication` itself raises
`NotImplementedError`. Backends are instantiated with no arguments for every
request, so put configuration in class attributes. An API-key backend:

```python
from zeeb_api.authentication import BaseAuthentication
from zeeb_api.exceptions import AuthenticationException, ErrorCode


class ApiKeyAuthentication(BaseAuthentication):
    header = "X-API-Key"

    async def authenticate(self, request):
        key = request.headers.get(self.header)
        if key is None:
            return None   # not ours: let the next backend try
        client = await ApiClient.objects.filter(key=key, is_active=True).first()
        if client is None:
            raise AuthenticationException(
                code=ErrorCode.AUTH_TOKEN_INVALID,
                message="Unknown API key",
            )
        return client
```

The built-in backends below never raise for a bad token: they record the
reason on `request.state.auth_error` (where applicable) and return `None`, so
they compose — a token one backend cannot validate may still be valid for the
next.

#### JWTAuthentication

Authenticates the framework's own access tokens from the
`Authorization: Bearer <token>` header, using the same validation as
`JWTAuthMiddleware`:

- no Bearer header → `None` (no error recorded);
- an expired token → `None` with `auth_error = AUTH_TOKEN_EXPIRED`;
- any other invalid token (bad signature, wrong `type`, issuer/audience
  mismatch, …) → `None` with `auth_error = AUTH_TOKEN_INVALID`;
- a valid token → the user from `default_user_loader`. A deleted or
  deactivated account gives `None` with `AUTH_TOKEN_INVALID`; a database
  error propagates.

The database lookup is controlled by the class attribute
`load_user_from_db = True` — not by the `AUTH_LOAD_USER_FROM_DB` setting.
Unlike the middleware, `JWTAuthentication` does not consult
`OAUTH_ACCEPT_EXTERNAL_TOKENS`; add `OAuth2BearerAuthentication` to the list
for that.

#### JWTStatelessAuthentication

`JWTAuthentication` with `load_user_from_db = False`: a valid access token
yields an `AuthenticatedUser` built from the token's claims alone (`id`,
`claims`, and `is_staff` / `is_admin` / `is_superuser` read from those
claims), with no database query. Error handling is identical. The trade-off is
the same as `AUTH_LOAD_USER_FROM_DB = False`: a user deleted or deactivated
after the token was issued stays authenticated until it expires.

#### OAuth2BearerAuthentication

Authenticates bearer tokens issued by an external identity provider (Azure AD,
Google, …), validated against the provider's JWKS through
`ExternalTokenValidator` and resolved to the linked local user — see
[External Bearer Mode](oauth.md#external-bearer-mode-accept-idp-tokens-directly)
for exactly what a validator checks and returns. It needs the
`zeebpy[oauth]` extra; the OAuth package is imported only when the backend
first runs.

The class attribute `providers` picks the providers to try:

- `None` (default) — the names in `OAUTH_ACCEPT_EXTERNAL_TOKENS`, the same
  setting the middleware honours;
- a list of names — looked up in `OAUTH_PROVIDERS` via `get_oauth_provider()`.
  A name that is not configured raises `KeyError` when the backend runs.

Each provider's validator is tried in turn and the first user returned wins.
A token no provider accepts (or a validator that raises) gives `None`; this
backend records no `auth_error`.

```python
from zeeb_api.authentication import OAuth2BearerAuthentication


class AzureOnly(OAuth2BearerAuthentication):
    providers = ["azure"]


class PartnerViewSet(ModelViewSet):
    authentication_classes = [AzureOnly]
    permission_classes = [IsAuthenticated]
```

## OAuth2 / OIDC Single Sign-On

Zeeb includes an OAuth2/OpenID Connect layer with presets for Azure AD
(Microsoft Entra ID), Google and GitHub. Users authenticate at the identity
provider; Zeeb links or provisions a local user and issues its own JWT
access/refresh tokens. The middleware can also accept IdP-issued tokens
directly (SPA bearer mode).

```python
# settings.py
OAUTH_PROVIDERS = {
    "azure": {"tenant": "common", "client_id": "...", "client_secret": "..."},
}
```

```python
from zeeb_api.auth.oauth import create_oauth_router

app.include_router(create_oauth_router())
```

Requires the optional extra: `pip install zeebpy[oauth]`.

See [OAuth2 / OIDC Authentication](oauth.md) for the full guide (Azure AD
walkthrough, tenant modes, SPA flow, external bearer mode, security notes).

## Anonymous User

There is no `AnonymousUser` class. An unauthenticated request simply has
`request.state.user is None`:

```python
user = getattr(request.state, "user", None)

if user is None:
    ...  # anonymous
elif user.is_authenticated:
    ...  # signed in
```

Permission classes already encode this check — use `IsAuthenticated` rather
than testing for `None` by hand. See [Permissions](permissions.md).

## Registration Flow

```python
class RegistrationSerializer(Serializer):
    username = fields.CharField(max_length=50)
    email = fields.EmailField()
    password = fields.CharField(write_only=True, min_length=8)
    password_confirm = fields.CharField(write_only=True)
    
    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise ValidationError({"password_confirm": "Passwords don't match"})
        attrs.pop("password_confirm")
        return attrs


class AuthViewSet(ViewSet):
    
    async def register(self, request):
        """POST /auth/register/"""
        serializer = RegistrationSerializer(data=await request.json())
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Check if exists
        User = get_user_model()
        if await User.objects.filter(username=data["username"]).exists():
            return {"error": "Username taken"}, 400
        if await User.objects.filter(email=data["email"]).exists():
            return {"error": "Email taken"}, 400
        
        # Create user
        user = await User.objects.create_user(
            username=data["username"],
            email=data["email"],
            password=data["password"],
        )
        
        # Generate token
        token = create_access_token(user)
        
        return {
            "token": token,
            "user": {"id": str(user.id), "username": user.username},
        }, 201
```

## Next Steps

- [OAuth2 / OIDC](oauth.md) - Single sign-on with Azure AD, Google, GitHub
- [Permissions](permissions.md) - Access control
- [ViewSets](viewsets.md) - Protected endpoints
- [Settings](../configuration/settings.md) - Auth configuration
