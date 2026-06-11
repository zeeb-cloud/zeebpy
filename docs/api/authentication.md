# Authentication

Zeeb provides a flexible authentication system with swappable user models.

## User Model

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
has_perm = await user.has_perm("blog.add_article")
has_perms = await user.has_perms(["blog.add_article", "blog.change_article"])

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

### Supported Hashers

1. **PBKDF2** (default) - Secure, widely supported
2. **Argon2** - Modern, recommended (requires `argon2-cffi`)
3. **BCrypt** - Well-tested (requires `bcrypt`)

Configure in settings:

```python
# settings.py
PASSWORD_HASHERS = [
    "zeeb_api.auth.hashers.Argon2PasswordHasher",
    "zeeb_api.auth.hashers.PBKDF2PasswordHasher",
    "zeeb_api.auth.hashers.BCryptPasswordHasher",
]
```

### Password Validation

```python
from zeeb_api.auth.validators import (
    MinimumLengthValidator,
    CommonPasswordValidator,
    NumericPasswordValidator,
)

# settings.py
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "zeeb_api.auth.validators.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "zeeb_api.auth.validators.CommonPasswordValidator"},
    {"NAME": "zeeb_api.auth.validators.NumericPasswordValidator"},
]
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

### Custom Backend

```python
# apps/accounts/backends.py
from zeeb_api.auth.backends import BaseBackend
from zeeb_api.auth import get_user_model


class EmailBackend(BaseBackend):
    """Authenticate by email only."""
    
    async def authenticate(self, request=None, email=None, password=None):
        User = get_user_model()
        
        try:
            user = await User.objects.get(email=email)
            if user.check_password(password):
                return user
        except User.DoesNotExist:
            pass
        
        return None
```

Configure:

```python
# settings.py
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailBackend",
]
```

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

```python
# Single permission
await user.has_perm("blog.add_article")

# Multiple permissions (AND)
await user.has_perms(["blog.add_article", "blog.change_article"])

# Superusers have all permissions
if user.is_superuser:
    # Has all permissions automatically
    ...
```

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
from zeeb_api.auth.tokens import create_access_token, decode_access_token

# Create token
token = create_access_token(user)
# "eyJ0eXAiOiJKV1QiLC..."

# Decode token
payload = decode_access_token(token)
# {"user_id": "uuid", "username": "john", ...}
```

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

```python
from zeeb_api.viewsets import ViewSet
from zeeb_api.auth.tokens import create_access_token
from zeeb_api.auth import authenticate


class AuthViewSet(ViewSet):
    
    async def login(self, request):
        """POST /auth/login/"""
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

```python
# settings.py
JWT_SECRET_KEY = "your-secret-key"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24
```

```python
# middleware.py
from zeeb_api.auth.tokens import decode_access_token
from zeeb_api.auth import get_user_model


async def auth_middleware(request, call_next):
    """Extract user from JWT token."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    
    if token:
        try:
            payload = decode_access_token(token)
            User = get_user_model()
            request.user = await User.objects.get(pk=payload["user_id"])
        except Exception:
            request.user = AnonymousUser()
    else:
        request.user = AnonymousUser()
    
    return await call_next(request)
```

## Anonymous User

```python
from zeeb_api.auth.models import AnonymousUser

anon = AnonymousUser()
anon.is_authenticated  # False
anon.is_anonymous      # True
anon.is_staff          # False
anon.is_superuser      # False

await anon.has_perm("any.permission")  # Always False
```

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

- [Permissions](permissions.md) - Access control
- [ViewSets](viewsets.md) - Protected endpoints
- [Settings](../configuration/settings.md) - Auth configuration
