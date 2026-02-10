# Object Permissions

Zeeb ORM provides a powerful per-object permission system that lets you define access rules directly on your models. These rules are used for both object-level checks and efficient database-level filtering.

## Overview

The permission system consists of:

1. **Rules** - Composable permission rules defined on models
2. **Model methods** - Auto-generated `check_*_permission()` methods
3. **QuerySet filtering** - Efficient database-level permission filtering
4. **ViewSet integration** - Automatic permission checking in API endpoints

## Quick Start

```python
from zeeb_orm import Model, fields
from zeeb_orm.permissions import Rule

class Post(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey("User", on_delete="CASCADE")
    status = fields.CharField(max_length=20, default="draft")
    
    # Permission rules
    read_permission = Rule.Q(status="published") | Rule.owner("author")
    add_permission = Rule.authenticated()
    change_permission = Rule.owner("author") | Rule.staff()
    delete_permission = Rule.owner("author") | Rule.staff()
```

This defines:
- **read**: Anyone can read published posts, authors can read their own
- **add**: Any authenticated user can create posts
- **change**: Authors can edit their own posts, staff can edit any
- **delete**: Authors can delete their own posts, staff can delete any

## Permission Rules

### Built-in Rules

| Rule | Description | Q Filter |
|------|-------------|----------|
| `Rule.public()` | Always allows access | No filter (match all) |
| `Rule.authenticated()` | Requires logged in user | No filter (match all if authenticated) |
| `Rule.staff()` | Requires staff user | No filter if staff, else match none |
| `Rule.superuser()` | Requires superuser | No filter if superuser, else match none |
| `Rule.owner("field")` | User matches field value | `Q(field_id=user.id)` |
| `Rule.Q(**kwargs)` | Field condition | `Q(**kwargs)` |
| `Rule.custom(fn)` | Custom async function | No filter (Python-only check) |

### Combining Rules

Rules can be combined with `|` (OR) and `&` (AND):

```python
# Published OR owned by user OR staff
read_permission = Rule.Q(status="published") | Rule.owner("author") | Rule.staff()

# Authenticated AND (owner OR staff)
change_permission = Rule.authenticated() & (Rule.owner("author") | Rule.staff())

# NOT draft (use ~ for NOT)
read_permission = ~Rule.Q(status="draft")
```

### Custom Rules

For complex logic, use custom async functions:

```python
async def check_team_member(obj, user):
    """Check if user is a team member."""
    if user is None:
        return False
    team = await obj.team.get()
    members = await team.members.all()
    return user.id in [m.id for m in members]

class Project(Model):
    team = fields.ForeignKey("Team", on_delete="CASCADE")
    
    read_permission = Rule.custom(check_team_member) | Rule.public()
```

> **Note**: Custom rules cannot generate SQL filters and are evaluated in Python after the query.

## Auto-Generated Methods

When you define `*_permission` attributes, the model metaclass generates:

### Instance Methods (for existing objects)

```python
# Check if user can read this post
can_read = await post.check_read_permission(user)

# Check if user can change this post
can_change = await post.check_change_permission(user)

# Check if user can delete this post
can_delete = await post.check_delete_permission(user)
```

### Class Methods (for creating new objects)

```python
# Check if user can add a new post
can_add = await Post.check_add_permission(user)
```

### Filter Methods (for queryset filtering)

```python
# Get Q filter for readable objects
q_filter = Post.get_read_filter(user)

# Get Q filter for changeable objects
q_filter = Post.get_change_filter(user)
```

## QuerySet Integration

The QuerySet provides convenient methods for permission filtering:

```python
# Get all posts readable by user
posts = await Post.objects.readable_by(user).all()

# Get posts user can change, filtered further
drafts = await Post.objects.changeable_by(user).filter(status="draft")

# Get posts user can delete
posts = await Post.objects.deletable_by(user).all()

# Generic method for any permission type
posts = await Post.objects.with_permission(user, "read").all()
```

### How It Works

The permission filter is applied at the SQL level, making it efficient even with large datasets:

```python
# This generates a single SQL query with WHERE clause:
posts = await Post.objects.readable_by(user).all()

# SQL equivalent:
# SELECT * FROM posts 
# WHERE status = 'published' OR author_id = :user_id
```

## ViewSet Integration

Enable automatic permission filtering in your ViewSets:

```python
from zeeb_api import ModelViewSet

class PostViewSet(ModelViewSet):
    queryset = Post.objects
    serializer_class = PostSerializer
    use_object_permissions = True  # Enable auto filtering
```

With `use_object_permissions = True`:

1. **List/Query**: QuerySet is filtered by `read` permission
2. **Retrieve**: Object is checked with `check_read_permission()`
3. **Create**: `check_add_permission()` is called before creating
4. **Update**: Object is checked with `check_change_permission()`
5. **Delete**: Object is checked with `check_delete_permission()`

### Action to Permission Mapping

| Action | Permission Type |
|--------|----------------|
| `list` | `read` |
| `retrieve` | `read` |
| `query` | `read` |
| `create` | `add` |
| `update` | `change` |
| `partial_update` | `change` |
| `destroy` | `delete` |

## Handling Anonymous Users

Rules handle `user=None` (anonymous users) gracefully:

```python
# Public access - always allows
Rule.public().check(obj, None)  # True

# Authenticated - denies anonymous
Rule.authenticated().check(obj, None)  # False

# Owner - denies anonymous
Rule.owner("author").check(obj, None)  # False

# Q filter - still applies (may match or not)
Rule.Q(status="published").check(obj, None)  # depends on obj.status
```

For querysets, anonymous users get appropriately filtered results:

```python
# Anonymous user sees only published posts
posts = await Post.objects.readable_by(None).all()
# SQL: SELECT * FROM posts WHERE status = 'published'
```

## Complete Example

```python
from zeeb_orm import Model, fields
from zeeb_orm.permissions import Rule

class Comment(Model):
    post = fields.ForeignKey("Post", on_delete="CASCADE")
    author = fields.ForeignKey("User", on_delete="CASCADE")
    content = fields.TextField()
    is_approved = fields.BooleanField(default=False)
    
    # Approved comments are public, authors see their own, staff sees all
    read_permission = Rule.Q(is_approved=True) | Rule.owner("author") | Rule.staff()
    
    # Authenticated users can comment
    add_permission = Rule.authenticated()
    
    # Authors can edit their own comments
    change_permission = Rule.owner("author")
    
    # Authors or staff can delete
    delete_permission = Rule.owner("author") | Rule.staff()


class CommentViewSet(ModelViewSet):
    queryset = Comment.objects
    serializer_class = CommentSerializer
    use_object_permissions = True
    
    async def perform_create(self, serializer):
        # Inject author from request
        serializer.validated_data["author_id"] = self.request.user.id
```

## Best Practices

1. **Use database-level filtering** - Prefer `Rule.Q()` and `Rule.owner()` over custom rules when possible, as they generate efficient SQL

2. **Combine with class-level permissions** - Use `permission_classes` for API-level access control and `*_permission` for object-level

3. **Test your permissions** - Write tests for each permission scenario:

```python
async def test_user_cannot_read_others_draft():
    user = await create_user()
    other = await create_user()
    post = await Post.objects.create(author=other, status="draft", ...)
    
    can_read = await post.check_read_permission(user)
    assert not can_read
```

4. **Consider anonymous users** - Always think about what anonymous users should see

5. **Use `Rule.public()` sparingly** - Only for truly public resources like published content
