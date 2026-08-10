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
3. A `SECRET_KEY` that is not one of the framework's known-insecure defaults.
   `startproject` generates one into `.env`; without it `create_app()` refuses
   to start once `DEBUG` is off.

`/login` and `/register` are rate limited per client — they are the two routes
an attacker can drive without a token. The limit comes from
`AUTH_LOGIN_THROTTLE_RATE` (default `10/min`, empty turns it off) and is passed
through `create_auth_router(login_throttle=...)`. `/refresh`, `/logout` and
`/me` are token-bound and stay unthrottled.

Related env variables: `AUTH_URL_PREFIX`, `AUTH_ENABLE_REGISTRATION`,
`AUTH_LOGIN_THROTTLE_RATE`, `JWT_*`. See
[Settings](../configuration/settings.md).

> **`/logout` does not revoke anything by itself.** JWTs are stateless: the
> endpoint calls the optional `on_logout(jti)` hook you pass to
> `create_auth_router()` and returns success. It receives the **access**
> token's `jti`, and it does not touch the rotated-refresh-token store — a
> refresh token stays redeemable after logout unless your `on_logout` hook
> records it. Clients should discard both tokens locally. To actually
> invalidate server-side, supply an `on_logout` that writes to a shared
> denylist your `BaseRefreshTokenStore` consults.

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

# Returns None if failed
if user is None:
    print("Invalid credentials")
elif not user.is_active:
    print("Account disabled")
else:
    print(f"Welcome, {user.username}!")
```

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
        await self._redis.set(f"rt:{jti}", "1", ex=int(ttl_seconds))


set_refresh_token_store(RedisRefreshTokenStore(redis))
```

`consume()` receives the token's **remaining** lifetime as `ttl_seconds`, so
entries can expire rather than accumulating forever. `get_refresh_token_store()`
returns the active store; call `set_refresh_token_store(None)` to restore the
default, which is also how tests reset between cases.

These names live in `zeeb_api.auth.refresh_store` and are not re-exported from
`zeeb_api.auth`.

### JWT Secret Hardening

Zeeb refuses to sign or verify JWTs with a known insecure default secret
(e.g. `"change-me-in-production"` or the startproject template default):

- **`DEBUG = False`** (the default): `create_access_token()`,
  `create_refresh_token()` and `decode_token()` raise
  `zeeb_api.exceptions.InsecureSecretError`.
- **`DEBUG = True`**: the calls work, but a warning is logged once per
  process reminding you to set a real secret.
- **`create_app()`** fails fast with
  `zeeb_api.exceptions.ImproperlyConfigured` when `DEBUG` is off and
  `SECRET_KEY`/`JWT_SECRET_KEY` is an insecure default.

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
