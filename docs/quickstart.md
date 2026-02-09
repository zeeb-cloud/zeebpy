# Quick Start Tutorial

Build a complete blog API in 10 minutes.

## 1. Create Project

```bash
# Create project
zeeb startproject blog_api
cd blog_api

# Create blog app
python manage.py startapp blog
```

## 2. Register the App

Edit `blog_api/settings.py`:

```python
INSTALLED_APPS = [
    "apps.blog",
]
```

## 3. Define Models

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

## 4. Create and Run Migrations

```bash
# Generate migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate
```

## 5. Create Serializers

Edit `apps/blog/serializers.py`:

```python
from zeeb_api.serializers import Serializer, fields


class AuthorSerializer(Serializer):
    """Author serializer."""
    id = fields.UUIDField(read_only=True)
    name = fields.CharField(max_length=100)
    email = fields.EmailField()
    bio = fields.CharField(required=False, allow_null=True)
    created_at = fields.DateTimeField(read_only=True)


class AuthorListSerializer(Serializer):
    """Simplified author for nested use."""
    id = fields.UUIDField(read_only=True)
    name = fields.CharField()


class CommentSerializer(Serializer):
    """Comment serializer."""
    id = fields.UUIDField(read_only=True)
    post_id = fields.UUIDField(write_only=True)
    author_name = fields.CharField(max_length=100)
    content = fields.CharField()
    approved = fields.BooleanField(read_only=True)
    created_at = fields.DateTimeField(read_only=True)


class PostSerializer(Serializer):
    """Post serializer."""
    id = fields.UUIDField(read_only=True)
    title = fields.CharField(max_length=200)
    slug = fields.CharField(max_length=200)
    content = fields.CharField()
    author_id = fields.UUIDField(write_only=True)
    author = AuthorListSerializer(read_only=True)
    published = fields.BooleanField(default=False)
    views = fields.IntegerField(read_only=True)
    created_at = fields.DateTimeField(read_only=True)
    updated_at = fields.DateTimeField(read_only=True)


class PostListSerializer(Serializer):
    """Simplified post for list views."""
    id = fields.UUIDField(read_only=True)
    title = fields.CharField()
    slug = fields.CharField()
    author = AuthorListSerializer(read_only=True)
    published = fields.BooleanField()
    created_at = fields.DateTimeField(read_only=True)
```

## 6. Create ViewSets

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
    
    list: GET /authors/
    create: POST /authors/
    retrieve: GET /authors/{id}/
    update: PUT /authors/{id}/
    partial_update: PATCH /authors/{id}/
    destroy: DELETE /authors/{id}/
    """
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    
    @action(detail=True, methods=["GET"])
    async def posts(self, request, pk=None):
        """GET /authors/{id}/posts/ - Get author's posts."""
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

## 7. Register Routes

Edit `apps/blog/urls.py`:

```python
from zeeb_api.routers import Router
from apps.blog.views import AuthorViewSet, PostViewSet, CommentViewSet

router = Router()
router.register("authors", AuthorViewSet)
router.register("posts", PostViewSet)
router.register("comments", CommentViewSet)

urlpatterns = router.urls
```

Edit `blog_api/urls.py`:

```python
from zeeb_api.routers import Router
from apps.blog.urls import router as blog_router

# Main router
router = Router()

# Include blog routes under /api/
router.include_router(blog_router, prefix="/api")

urlpatterns = router.urls
```

## 8. Run the Server

```bash
python manage.py runserver
```

## 9. Test the API

### Create an Author

```bash
curl -X POST http://localhost:8000/api/authors/ \
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
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Create a Post

```bash
curl -X POST http://localhost:8000/api/posts/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Getting Started with Zeeb",
    "slug": "getting-started-zeeb",
    "content": "Zeeb is an amazing framework...",
    "author_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

### List Posts

```bash
# All posts
curl http://localhost:8000/api/posts/

# Published only
curl http://localhost:8000/api/posts/?published=true

# Search
curl http://localhost:8000/api/posts/?search=zeeb
```

### Publish a Post

```bash
curl -X POST http://localhost:8000/api/posts/getting-started-zeeb/publish/
```

### Add a Comment

```bash
curl -X POST http://localhost:8000/api/comments/ \
  -H "Content-Type: application/json" \
  -d '{
    "post_id": "post-uuid-here",
    "author_name": "Reader",
    "content": "Great article!"
  }'
```

## 10. Interactive Documentation

Visit `http://localhost:8000/docs` for automatic Swagger/OpenAPI documentation.

## Next Steps

- [Models](orm/models.md) - Learn more about models
- [Queries](orm/queries.md) - Advanced querying
- [ViewSets](api/viewsets.md) - ViewSet customization
- [Authentication](api/authentication.md) - Add user authentication
