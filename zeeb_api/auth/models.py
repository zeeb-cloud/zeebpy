"""
User and Permission models for authentication.

Provides Django-like user models:
- AbstractBaseUser: Base with password hashing
- AbstractUser: Full user with common fields
- User: Default concrete user model
- Permission: Permission model
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from zeeb_orm import Model, fields
from zeeb_api.auth.hashers import make_password, check_password, is_password_usable


class AbstractBaseUser(Model):
    """
    Abstract base user with password functionality.
    
    Provides:
    - Password hashing and verification
    - Last login tracking
    - is_authenticated property
    
    Subclass this to create custom user models with password support.
    """
    
    password = fields.CharField(max_length=128, null=True)
    last_login = fields.DateTimeField(null=True)
    
    class Meta:
        abstract = True
    
    def set_password(self, raw_password: str) -> None:
        """
        Set the user's password (hashes it).
        
        Args:
            raw_password: Plain text password
        """
        self.password = make_password(raw_password)
    
    def check_password(self, raw_password: str) -> bool:
        """
        Check if the given password matches the stored hash.
        
        Args:
            raw_password: Plain text password to check
        
        Returns:
            True if password matches
        """
        return check_password(raw_password, self.password or "")
    
    def has_usable_password(self) -> bool:
        """Check if the user has a usable password."""
        return is_password_usable(self.password)
    
    @property
    def is_authenticated(self) -> bool:
        """Always returns True for authenticated users."""
        return True
    
    @property
    def is_anonymous(self) -> bool:
        """Always returns False for authenticated users."""
        return False
    
    async def update_last_login(self) -> None:
        """Update the last_login timestamp."""
        self.last_login = datetime.now(timezone.utc)
        await self.save()


class AbstractUser(AbstractBaseUser):
    """
    Abstract user with common fields.
    
    Includes:
    - email (unique, used for login)
    - username (optional)
    - first_name, last_name
    - is_active, is_staff, is_superuser
    - date_joined
    
    Subclass this for custom user models with these fields.
    """
    
    email = fields.EmailField(unique=True)
    username = fields.CharField(max_length=150, unique=True, null=True)
    first_name = fields.CharField(max_length=150, null=True)
    last_name = fields.CharField(max_length=150, null=True)
    is_active = fields.BooleanField(default=True)
    is_staff = fields.BooleanField(default=False)
    is_superuser = fields.BooleanField(default=False)
    date_joined = fields.DateTimeField(null=True)
    
    # Field used for authentication (email by default)
    USERNAME_FIELD: ClassVar[str] = "email"
    
    # Required fields for createsuperuser (besides USERNAME_FIELD and password)
    REQUIRED_FIELDS: ClassVar[list[str]] = []
    
    class Meta:
        abstract = True
    
    def __str__(self) -> str:
        return self.email or self.username or str(self.id)
    
    def get_full_name(self) -> str:
        """Return the first_name plus the last_name, with a space in between."""
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p).strip()
    
    def get_short_name(self) -> str:
        """Return the short name for the user."""
        return self.first_name or self.email or ""
    
    def has_perm(self, perm: str, obj: Any = None) -> bool:
        """
        Check if user has a specific permission (sync check).
        
        Superusers have all permissions.
        
        Note: For async permission checking with DB lookup, use has_perm_async().
        This sync version only checks superuser status without hitting the DB.
        """
        if self.is_superuser:
            return True
        # Permissions loaded via prefetch can be checked here
        if hasattr(self, '_permissions_cache'):
            return perm in self._permissions_cache
        return False
    
    async def has_perm_async(self, perm: str, obj: Any = None) -> bool:
        """
        Check if user has a specific permission (async, checks DB).
        
        Superusers have all permissions.
        """
        if self.is_superuser:
            return True
        
        # Query UserPermission table
        from zeeb_api.auth.models import UserPermission, Permission
        try:
            user_perm = await UserPermission.objects.filter(
                user_id=self.id
            ).select_related('permission').first()
            
            if user_perm:
                # Check if any permission matches
                perms = await UserPermission.objects.filter(user_id=self.id)
                for up in perms:
                    permission = await Permission.objects.get(id=up.permission_id)
                    if permission.codename == perm:
                        return True
        except Exception:
            pass
        return False
    
    def has_perms(self, perm_list: list[str], obj: Any = None) -> bool:
        """Check if user has all permissions in the list."""
        return all(self.has_perm(perm, obj) for perm in perm_list)
    
    def get_claims(self) -> dict[str, Any]:
        """
        Get JWT claims for this user.
        
        Override this to customize what's included in the JWT token.
        """
        return {
            "email": self.email,
            "username": self.username,
            "is_staff": self.is_staff,
            "is_superuser": self.is_superuser,
            "is_active": self.is_active,
        }


class User(AbstractUser):
    """
    Default User model.
    
    Use this directly or create your own by subclassing AbstractUser.
    
    To use a custom user model, configure it:
        configure_auth(user_model=MyCustomUser)
    """
    
    class Meta:
        table_name = "auth_users"
    
    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class Permission(Model):
    """
    Permission model for fine-grained access control.
    
    Example:
        perm = await Permission.objects.create(
            name="Can edit posts",
            codename="edit_post"
        )
    """
    
    name = fields.CharField(max_length=255)
    codename = fields.CharField(max_length=100, unique=True)
    
    class Meta:
        table_name = "auth_permissions"
    
    def __str__(self) -> str:
        return self.codename
    
    def __repr__(self) -> str:
        return f"<Permission codename={self.codename}>"


class UserPermission(Model):
    """
    Many-to-many relationship between User and Permission.
    
    Example:
        await UserPermission.objects.create(
            user_id=user.id,
            permission_id=permission.id
        )
    """
    
    user = fields.ForeignKey("User", related_name="user_permissions")
    permission = fields.ForeignKey(Permission, related_name="user_permissions")
    
    class Meta:
        table_name = "auth_user_permissions"
    
    def __repr__(self) -> str:
        return f"<UserPermission user_id={self.user_id} permission_id={self.permission_id}>"
