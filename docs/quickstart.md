# Quick Start Tutorial

Build a complete blog API in 10 minutes.

## 1. Create Project

```bash
# Create project — authentication is already wired and running
zeeb startproject blog_api
cd blog_api

# Create the blog app; it registers itself in INSTALLED_APPS and urls.py
python manage.py startapp blog
```

## 2. Define Models


Edit `apps/blog/models.py`:

```python
from zeeb_orm import Model, fields


class Author(Model):
    """Blog post author."""
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    bio = fields.TextField(null=True, blank=True)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "authors"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Post(Model):
    """Blog post."""
    title = fields.CharField(max_length=200)
    slug = fields.SlugField(unique=True)
    content = fields.TextField()
    author = fields.ForeignKey(Author, on_delete="CASCADE", related_name="posts")
    published = fields.BooleanField(default=False)
    views = fields.IntegerField(default=0)
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "posts"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Comment(Model):
    """Post comment."""
    post = fields.ForeignKey(Post, on_delete="CASCADE", related_name="comments")
    author_name = fields.CharField(max_length=100)
    content = fields.TextField()
    approved = fields.BooleanField(default=False)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "comments"
        ordering = ["-created_at"]
```

## 3. Create and Run Migrations

```bash
# Generate migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate
```

## 4. Create Serializers

Edit `apps/blog/serializers.py`. A `ModelSerializer` derives request validation
and the response shape from the model, so the two cannot drift:

```python
from zeeb_api import serializers

from .models import Author, Comment, Post


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name", "email", "bio", "created_at"]
        read_only_fields = ["id", "created_at"]


class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Comment
        fields = ["id", "post", "author_name", "content", "approved", "created_at"]
        # Server-owned: returned, never accepted from the request body.
        read_only_fields = ["id", "approved", "created_at"]


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = [
            "id", "title", "slug", "content", "author",
            "published", "views", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "views", "created_at", "updated_at"]


class PostListSerializer(serializers.ModelSerializer):
    """A narrower shape for list responses — no body, no counters."""

    class Meta:
        model = Post
        fields = ["id", "title", "slug", "author", "published", "created_at"]
        read_only_fields = fields
```

`read_only_fields` is the write boundary: such a field is returned but never
accepted from the request body. A foreign key is returned under its bare name
(`author`) and accepted as either `author` or `author_id`.

Declare fields explicitly only where you need to override the model — a
`SerializerMethodField`, a nested serializer, or a validation rule.

## 5. Create ViewSets

Edit `apps/blog/views.py`:

```python
from zeeb_api.viewsets import ModelViewSet, action
from zeeb_api.permissions import IsAuthenticated, AllowAny
from zeeb_orm import Q, F, Count

from apps.blog.models import Author, Post, Comment
from apps.blog.serializers import (
    AuthorSerializer,
    PostSerializer,
    PostListSerializer,
    CommentSerializer,
)


class AuthorViewSet(ModelViewSet):
    """
    API endpoint for authors.
    
    list: GET /authors
    create: POST /authors
    retrieve: GET /authors/{id}
    update: PUT /authors/{id}
    partial_update: PATCH /authors/{id}
    destroy: DELETE /authors/{id}
    """
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    
    @action(detail=True, methods=["GET"])
    async def posts(self, request, pk=None):
        """GET /authors/{id}/posts — the author's published posts."""
        author = await self.get_object()
        posts = await Post.objects.filter(author=author, published=True)
        serializer = PostListSerializer(posts, many=True)
        return {"posts": serializer.data}


class PostViewSet(ModelViewSet):
    """API endpoint for posts."""
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    lookup_field = "slug"  # Use slug instead of id in URLs
    
    def get_serializer_class(self):
        if self.action == "list":
            return PostListSerializer
        return PostSerializer
    
    def get_queryset(self):
        qs = Post.objects.all()
        
        # Filter by published status
        if self.request.query_params.get("published"):
            qs = qs.filter(published=True)
        
        # Filter by author
        author_id = self.request.query_params.get("author")
        if author_id:
            qs = qs.filter(author_id=author_id)
        
        # Search in title
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(content__icontains=search))
        
        return qs
    
    @action(detail=True, methods=["POST"])
    async def publish(self, request, slug=None):
        """POST /posts/{slug}/publish/ - Publish a post."""
        post = await self.get_object()
        post.published = True
        await post.save()
        return {"status": "published", "post": PostSerializer(post).data}
    
    @action(detail=True, methods=["POST"])
    async def increment_views(self, request, slug=None):
        """POST /posts/{slug}/increment_views/ - Increment view count."""
        await Post.objects.filter(slug=slug).update(views=F("views") + 1)
        post = await self.get_object()
        return {"views": post.views}
    
    @action(detail=True, methods=["GET"])
    async def comments(self, request, slug=None):
        """GET /posts/{slug}/comments/ - Get post comments."""
        post = await self.get_object()
        comments = await Comment.objects.filter(post=post, approved=True)
        return {"comments": CommentSerializer(comments, many=True).data}


class CommentViewSet(ModelViewSet):
    """API endpoint for comments."""
    queryset = Comment.objects.all()
    serializer_class = CommentSerializer
    
    def get_queryset(self):
        qs = Comment.objects.all()
        
        # Filter by post
        post_id = self.request.query_params.get("post")
        if post_id:
            qs = qs.filter(post_id=post_id)
        
        # Only show approved comments by default
        if not self.request.query_params.get("all"):
            qs = qs.filter(approved=True)
        
        return qs
    
    @action(detail=True, methods=["POST"])
    async def approve(self, request, pk=None):
        """POST /comments/{id}/approve/ - Approve a comment."""
        comment = await self.get_object()
        comment.approved = True
        await comment.save()
        return {"status": "approved"}
```

## 6. Register Routes

Edit `apps/blog/urls.py`:

```python
from zeeb_api.routers import DefaultRouter
from apps.blog.views import AuthorViewSet, PostViewSet, CommentViewSet

router = DefaultRouter()
router.register("authors", AuthorViewSet)
router.register("posts", PostViewSet)
router.register("comments", CommentViewSet)
```

Keep the module-level `router` symbol: it is what the project `urls.py` includes
and what `startapp` and the agent tooling look for.

`startapp` already added the include to `blog_api/urls.py`, so there is nothing
to edit:

```python
from apps.blog.urls import router as blog_router
...
router.include(blog_router)
```

The app router is included without a prefix on purpose: `router.register(...)`
already mounts each ViewSet under its own segment, and `API_PREFIX` (`/api/v1`
by default) is applied to everything by `create_app()`.

## 7. Run the tests

`startproject` shipped a working test harness, and `startapp` added a test for
this app:

```bash
pytest -q
```

`tests/conftest.py` gives every test a `db`, an anonymous `client`, an
`auth_client` and an `admin_client` carrying real tokens, and `api_prefix`.
Write the test for a new endpoint next to those — it is a faster loop than curl,
and it is what `check` and the agent tooling verify against.

## 8. Run the server

```bash
python manage.py runserver          # http://127.0.0.1:9000
python manage.py showurls           # what you can call
```

## 9. Call the API

The endpoints above use `IsAuthenticatedOrReadOnly`, so reads are open and
writes need a token. Canonical paths carry **no trailing slash** (a trailing
slash is also served).

### Get a token

Authentication is already wired — there is nothing to build:

```bash
curl -X POST http://127.0.0.1:9000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "jane@example.com", "password": "a-good-password"}'

TOKEN=$(curl -sX POST http://127.0.0.1:9000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "jane@example.com", "password": "a-good-password"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
```

### Create an author

```bash
curl -X POST http://127.0.0.1:9000/api/v1/authors \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Jane Doe", "email": "jane@example.com", "bio": "Tech writer"}'
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Jane Doe",
  "email": "jane@example.com",
  "bio": "Tech writer",
  "created_at": "2026-01-15T10:30:00Z"
}
```

### Create a post

```bash
curl -X POST http://127.0.0.1:9000/api/v1/posts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Getting Started with Zeeb",
    "slug": "getting-started-zeeb",
    "content": "Zeeb is an amazing framework...",
    "author": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

### Read posts (no token needed)

```bash
curl http://127.0.0.1:9000/api/v1/posts
curl "http://127.0.0.1:9000/api/v1/posts?published=true"
curl "http://127.0.0.1:9000/api/v1/posts?search=zeeb"
```

Without a token, a write is rejected with the standard envelope:

```json
{"success": false, "error": {"code": "AUTH_TOKEN_MISSING", "message": "..."}}
```

Branch on `error.code`, never on `message`.

### Publish a post

```bash
curl -X POST http://127.0.0.1:9000/api/v1/posts/getting-started-zeeb/publish \
  -H "Authorization: Bearer $TOKEN"
```

## 10. Interactive documentation

Visit `http://127.0.0.1:9000/docs` for Swagger UI, or fetch
`http://127.0.0.1:9000/openapi.json` for the schema. To hand the API to a
frontend developer or an AI app builder:

```bash
python manage.py frontend-brief
```

## Next Steps

- [Models](orm/models.md) - Learn more about models
- [Queries](orm/queries.md) - Advanced querying
- [ViewSets](api/viewsets.md) - ViewSet customization
- [Authentication](api/authentication.md) - Add user authentication
