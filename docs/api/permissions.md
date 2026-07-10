# Permissions

Control access to API endpoints with permission classes.

## Quick Start

```python
from zeeb_api.viewsets import ModelViewSet
from zeeb_api.permissions import IsAuthenticated


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsAuthenticated]
```

## Built-in Permission Classes

### AllowAny

Allow unrestricted access to all users (authenticated or not).

```python
from zeeb_api.permissions import AllowAny


class PublicViewSet(ModelViewSet):
    permission_classes = [AllowAny]
```

### IsAuthenticated

Require authenticated users only.

```python
from zeeb_api.permissions import IsAuthenticated


class ProtectedViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated]
```

### IsAdminUser

Require authenticated users with `is_staff=True`.

```python
from zeeb_api.permissions import IsAdminUser


class AdminViewSet(ModelViewSet):
    permission_classes = [IsAdminUser]
```

### IsAuthenticatedOrReadOnly

- `GET`, `HEAD`, `OPTIONS` - Allow any
- `POST`, `PUT`, `PATCH`, `DELETE` - Require authentication

```python
from zeeb_api.permissions import IsAuthenticatedOrReadOnly


class ArticleViewSet(ModelViewSet):
    permission_classes = [IsAuthenticatedOrReadOnly]
    # Anyone can read, only authenticated can write
```

### IsOwner

Object-level permission allowing only the object's owner (the model's
`owner` attribute by default, falling back to `user`; override with
`owner_field`).

```python
from zeeb_api.permissions import IsOwner


class ArticleOwnerPermission(IsOwner):
    owner_field = "author"


class ArticleViewSet(ModelViewSet):
    permission_classes = [ArticleOwnerPermission]
```

### IsOwnerOrReadOnly

Like `IsOwner`, but `GET`/`HEAD`/`OPTIONS` are allowed for everyone.

```python
from zeeb_api.permissions import IsOwnerOrReadOnly


class ArticleViewSet(ModelViewSet):
    permission_classes = [IsOwnerOrReadOnly]
```

### ModelPermissions

Maps HTTP methods to per-model permissions
(`view_<model>` / `add_<model>` / `change_<model>` / `delete_<model>`)
checked via the user's permission set.

```python
from zeeb_api.permissions import ModelPermissions


class ArticleViewSet(ModelViewSet):
    permission_classes = [ModelPermissions]
```

## Per-Action Permissions

Different permissions for different actions:

```python
from zeeb_api.permissions import AllowAny, IsAuthenticated, IsAdminUser


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    def get_permissions(self):
        """Return permission classes based on action."""
        if self.action in ["list", "retrieve"]:
            # Anyone can read
            return [AllowAny()]
        elif self.action == "create":
            # Authenticated users can create
            return [IsAuthenticated()]
        else:
            # Only admins can update/delete
            return [IsAdminUser()]
```

## Custom Permission Classes

### Basic Custom Permission

```python
from zeeb_api.permissions import BasePermission


class IsSuperUser(BasePermission):
    """Only allow superusers."""
    
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            request.user.is_superuser
        )
```

### Object-Level Permission

```python
class IsOwner(BasePermission):
    """Only allow owners of an object."""
    
    def has_permission(self, request, view):
        # Allow list/create for authenticated users
        return request.user and request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Check object ownership
        return obj.author == request.user
```

Usage:

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsOwner]
    
    async def get_object(self):
        obj = await super().get_object()
        # Check object-level permission
        self.check_object_permissions(self.request, obj)
        return obj
```

### Permission with Message

```python
class IsPremiumUser(BasePermission):
    """Only allow premium subscribers."""
    message = "Premium subscription required."
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_premium
```

## Combining Permissions

### AND Logic (Default)

All permissions must pass:

```python
class MyViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsOwner]
    # Must be authenticated AND owner
```

### OR Logic

Create a custom permission:

```python
class IsOwnerOrAdmin(BasePermission):
    """Allow owner or admin."""
    
    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.author == request.user
```

Or use a combinator:

```python
from zeeb_api.permissions import OR


class MyViewSet(ModelViewSet):
    permission_classes = [OR(IsOwner, IsAdminUser)]
```

## Permission Examples

### Read-Only for Non-Staff

```python
class IsStaffOrReadOnly(BasePermission):
    """Staff can do anything, others read-only."""
    
    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return request.user and request.user.is_staff
```

### IP-Based Permission

```python
class IsInternalNetwork(BasePermission):
    """Only allow internal IPs."""
    
    INTERNAL_IPS = ["127.0.0.1", "10.0.0.0/8", "192.168.0.0/16"]
    
    def has_permission(self, request, view):
        ip = request.client.host
        return self._is_internal(ip)
    
    def _is_internal(self, ip):
        # Check if IP is in allowed ranges
        ...
```

### Rate-Limited Permission

```python
from datetime import datetime, timedelta


class RateLimitPermission(BasePermission):
    """Limit requests per user."""
    
    RATE_LIMIT = 100  # requests
    PERIOD = timedelta(hours=1)
    
    # In-memory store (use Redis in production)
    _requests = {}
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return True  # Don't rate limit anonymous
        
        user_id = str(request.user.id)
        now = datetime.now()
        
        # Clean old entries
        cutoff = now - self.PERIOD
        if user_id in self._requests:
            self._requests[user_id] = [
                t for t in self._requests[user_id] if t > cutoff
            ]
        else:
            self._requests[user_id] = []
        
        # Check limit
        if len(self._requests[user_id]) >= self.RATE_LIMIT:
            self.message = f"Rate limit exceeded. Try again later."
            return False
        
        # Record request
        self._requests[user_id].append(now)
        return True
```

### Permission Based on Model Field

```python
class IsArticlePublished(BasePermission):
    """Only allow access to published articles."""
    
    def has_object_permission(self, request, view, obj):
        # Staff can see unpublished
        if request.user and request.user.is_staff:
            return True
        return obj.published
```

### Time-Based Permission

```python
from datetime import datetime


class IsBusinessHours(BasePermission):
    """Only allow during business hours."""
    message = "API available Mon-Fri, 9am-5pm EST only."
    
    def has_permission(self, request, view):
        now = datetime.now()
        # Monday=0, Sunday=6
        if now.weekday() > 4:  # Weekend
            return False
        if not (9 <= now.hour < 17):  # Outside 9-5
            return False
        return True
```

## Permission Check Order

1. **Authentication** - User is identified
2. **has_permission()** - Check view-level access
3. **get_object()** - Retrieve object
4. **has_object_permission()** - Check object-level access

```python
class MyPermission(BasePermission):
    
    def has_permission(self, request, view):
        """
        Called for every request.
        Return True to continue, False to deny.
        """
        return True
    
    def has_object_permission(self, request, view, obj):
        """
        Called after get_object() for detail views.
        Return True to allow, False to deny.
        """
        return True
```

## Handling Denied Permissions

When permission is denied:

```python
from zeeb_api.exceptions import PermissionDenied

# Default behavior
raise PermissionDenied()  # 403 Forbidden

# Custom message
raise PermissionDenied("You don't have permission to edit this article.")

# Custom response in ViewSet
class MyViewSet(ModelViewSet):
    async def permission_denied(self, request, message=None):
        """Custom permission denied handler."""
        if not request.user.is_authenticated:
            return {"error": "Please log in"}, 401
        return {"error": message or "Access denied"}, 403
```

## Action Decorator Permissions

```python
from zeeb_api.viewsets import action
from zeeb_api.permissions import IsAdminUser


class ArticleViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated]
    
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAdminUser],  # Override for this action
    )
    async def publish(self, request, pk=None):
        """Only admins can publish."""
        ...
```

## Best Practices

### 1. Default to Restrictive

```python
# Good: Start restrictive, open up as needed
class MyViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated]
    
    def get_permissions(self):
        if self.action == "list":
            return [AllowAny()]
        return super().get_permissions()
```

### 2. Use Object Permissions for Ownership

```python
class IsOwnerOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return True  # Allow to proceed to object check
    
    def has_object_permission(self, request, view, obj):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return obj.owner == request.user
```

### 3. Combine View and Object Permissions

```python
class ArticlePermission(BasePermission):
    def has_permission(self, request, view):
        # View-level: must be authenticated for writes
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Object-level: must be author for writes
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return obj.published or obj.author == request.user
        return obj.author == request.user
```

### 4. Document Permission Requirements

```python
class ArticleViewSet(ModelViewSet):
    """
    Article API endpoint.
    
    Permissions:
        - list/retrieve: Anyone (only published for non-staff)
        - create: Authenticated users
        - update/delete: Article author or staff
    """
    permission_classes = [ArticlePermission]
```

## Next Steps

- [Authentication](authentication.md) - User authentication
- [ViewSets](viewsets.md) - Using permissions in views
- [Settings](../configuration/settings.md) - Default permissions
