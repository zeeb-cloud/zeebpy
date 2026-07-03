"""
Authentication backends.

Provides:
- get_user_model(): Get the configured user model (from AUTH_USER_MODEL setting)
- authenticate(): Authenticate user against database
- configure_auth(): Configure authentication settings
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from zeeb_api.auth.models import AbstractBaseUser

# Type variable for user model
UserT = TypeVar("UserT", bound="AbstractBaseUser")

# Cache for resolved user model
_user_model_cache: type | None = None

# Explicit project root for settings discovery (set by out-of-process tooling
# such as zeeb_agents/migrations that operate on a project by path rather
# than from inside its working directory).
_project_root_override: Path | None = None


def set_project_root(project_root: Path | str | None) -> None:
    """Point settings discovery at an explicit project root.

    By default :func:`get_user_model` finds the project by walking up from
    the process CWD to the nearest ``manage.py``. Tooling that targets a
    project elsewhere on disk (e.g. migration registration in zeeb_agents)
    must call this first so ``AUTH_USER_MODEL`` is read from the right
    ``settings.py``. Pass ``None`` to restore CWD-based discovery. Clears
    the cached user model.
    """
    global _project_root_override
    _project_root_override = Path(project_root) if project_root is not None else None
    clear_user_model_cache()


def _get_project_settings() -> Any:
    """Load and return the project settings module."""
    if _project_root_override is not None:
        current = _project_root_override
    else:
        # Try to find project root
        current = Path.cwd()
        while current != current.parent:
            if (current / "manage.py").exists():
                break
            current = current.parent
        else:
            return None
    
    # Add to path if needed
    if str(current) not in sys.path:
        sys.path.insert(0, str(current))
    
    # Find settings module
    for item in current.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "settings", item / "settings.py"
                )
                if spec and spec.loader:
                    settings = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings)
                    return settings
            except Exception:
                pass
    
    return None


def _resolve_model_string(model_string: str) -> type:
    """
    Resolve a model string like 'apps.myapp.CustomUser' to the actual class.
    
    Supports formats:
    - 'apps.myapp.CustomUser' (full path)
    - 'myapp.CustomUser' (assumes apps.myapp.models)
    """
    import importlib
    
    if "." not in model_string:
        raise ValueError(
            f"AUTH_USER_MODEL must be in format 'app.Model', got '{model_string}'"
        )
    
    parts = model_string.rsplit(".", 1)
    model_name = parts[-1]
    
    # Try different import paths
    paths_to_try = [
        model_string.rsplit(".", 1)[0],  # Direct path
        f"apps.{parts[0]}.models",  # apps.myapp.models
        f"{parts[0]}.models",  # myapp.models
    ]
    
    for module_path in paths_to_try:
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, model_name):
                return getattr(module, model_name)
        except ImportError:
            continue
    
    raise ImportError(
        f"Could not import user model '{model_string}'. "
        f"Check that AUTH_USER_MODEL is set correctly and the model exists."
    )


def get_user_model() -> type:
    """
    Get the user model from AUTH_USER_MODEL setting.
    
    Like Django, this returns the custom user model if configured,
    otherwise returns the default User model.
    
    Settings example:
        AUTH_USER_MODEL = 'apps.accounts.CustomUser'
    
    Usage:
        User = get_user_model()
        user = await User.objects.get(email="test@example.com")
    """
    global _user_model_cache
    
    # Return cached model if available
    if _user_model_cache is not None:
        return _user_model_cache
    
    # Check settings for AUTH_USER_MODEL
    settings = _get_project_settings()
    if settings and hasattr(settings, "AUTH_USER_MODEL"):
        auth_user_model = settings.AUTH_USER_MODEL
        if auth_user_model:
            try:
                _user_model_cache = _resolve_model_string(auth_user_model)
                return _user_model_cache
            except (ImportError, ValueError) as e:
                # Don't cache fallback — the model may become resolvable
                # once sys.path is fully set up (e.g. inside _register_models)
                import warnings
                warnings.warn(str(e), stacklevel=2)
                from zeeb_api.auth.models import User
                return User
    
    # Fall back to default User model
    from zeeb_api.auth.models import User
    _user_model_cache = User
    return User


def clear_user_model_cache() -> None:
    """Clear the user model cache. Useful for testing."""
    global _user_model_cache
    _user_model_cache = None


def configure_auth(
    user_model: type[UserT] | None = None,
    **kwargs: Any,
) -> None:
    """
    Configure authentication settings programmatically.
    
    Note: Prefer using AUTH_USER_MODEL in settings.py instead.
    This function is mainly for testing and special cases.
    
    Args:
        user_model: Custom user model class (must subclass AbstractBaseUser)
    
    Example:
        from zeeb_api.auth import configure_auth
        from myapp.models import CustomUser
        
        configure_auth(user_model=CustomUser)
    """
    global _user_model_cache
    
    if user_model is not None:
        _user_model_cache = user_model


async def authenticate(
    email: str | None = None,
    password: str | None = None,
    username: str | None = None,
    **credentials: Any,
) -> UserT | None:
    """
    Authenticate a user against the database.
    
    Checks if the provided credentials match a user in the database.
    Uses the model specified in AUTH_USER_MODEL setting.
    
    Args:
        email: User's email address
        password: User's password (plain text)
        username: User's username (alternative to email)
        **credentials: Additional credentials
    
    Returns:
        User instance if authentication successful, None otherwise
    
    Example:
        user = await authenticate(email="test@example.com", password="secret")
        if user:
            print(f"Authenticated: {user.email}")
        else:
            print("Invalid credentials")
    """
    User = get_user_model()
    
    # Determine lookup field
    lookup_value = email or username
    if not lookup_value or not password:
        return None
    
    # Get the USERNAME_FIELD from the model
    username_field = getattr(User, "USERNAME_FIELD", "email")
    
    # Build filter
    if email:
        lookup_field = "email"
    elif username:
        lookup_field = "username"
    else:
        lookup_field = username_field
    
    try:
        # Query user
        user = await User.objects.filter(**{lookup_field: lookup_value}).first()
        
        if user is None:
            return None
        
        # Check if user is active
        if hasattr(user, "is_active") and not user.is_active:
            return None
        
        # Verify password
        if not user.check_password(password):
            return None
        
        return user
        
    except Exception:
        return None


async def authenticate_and_get_tokens(
    email: str | None = None,
    password: str | None = None,
    username: str | None = None,
    **credentials: Any,
) -> tuple[str, str, Any] | None:
    """
    Authenticate and return JWT tokens.
    
    Args:
        email: User's email
        password: User's password
        username: Alternative to email
    
    Returns:
        Tuple of (access_token, refresh_token, user) or None if auth fails
    """
    from zeeb_api.auth.jwt import create_token_pair
    
    user = await authenticate(
        email=email,
        password=password,
        username=username,
        **credentials,
    )
    
    if user is None:
        return None
    
    # Get claims from user
    claims = {}
    if hasattr(user, "get_claims"):
        claims = user.get_claims()
    
    # Create tokens
    access_token, refresh_token = create_token_pair(str(user.id), claims)
    
    # Update last login
    if hasattr(user, "update_last_login"):
        await user.update_last_login()
    
    return access_token, refresh_token, user


async def create_user(
    email: str,
    password: str,
    **extra_fields: Any,
) -> UserT:
    """
    Create a new user with the given email and password.
    
    Uses the model specified in AUTH_USER_MODEL setting.
    
    Args:
        email: User's email address
        password: User's password (will be hashed)
        **extra_fields: Additional fields for the user model
    
    Returns:
        The created user instance
    
    Example:
        user = await create_user(
            email="new@example.com",
            password="secretpassword",
            first_name="John",
            last_name="Doe"
        )
    """
    from datetime import datetime, timezone
    
    User = get_user_model()
    
    # Create user instance
    user = User(email=email, **extra_fields)
    user.set_password(password)
    
    # Set date_joined if the model has it
    if hasattr(user, "date_joined"):
        user.date_joined = datetime.now(timezone.utc)
    
    await user.save()
    return user


async def create_superuser(
    email: str,
    password: str,
    **extra_fields: Any,
) -> UserT:
    """
    Create a superuser with all permissions.
    
    Args:
        email: Superuser's email
        password: Superuser's password
        **extra_fields: Additional fields
    
    Returns:
        The created superuser instance
    """
    extra_fields.setdefault("is_staff", True)
    extra_fields.setdefault("is_superuser", True)
    extra_fields.setdefault("is_active", True)
    
    return await create_user(email, password, **extra_fields)
