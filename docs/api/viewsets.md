# ViewSets

ViewSets combine related views into a single class, automatically generating URL routes.

## Basic ViewSet

```python
from zeeb_api.viewsets import ViewSet
from zeeb_api.response import Response


class ArticleViewSet(ViewSet):
    """Basic ViewSet with manual actions."""
    
    async def list(self, request):
        """GET /articles/"""
        articles = await Article.objects.all()
        return [{"id": str(a.id), "title": a.title} for a in articles]
    
    async def create(self, request):
        """POST /articles/"""
        data = await request.json()
        article = await Article.objects.create(**data)
        return {"id": str(article.id)}, 201
    
    async def retrieve(self, request, pk):
        """GET /articles/{pk}/"""
        article = await Article.objects.get(pk=pk)
        return {"id": str(article.id), "title": article.title}
    
    async def update(self, request, pk):
        """PUT /articles/{pk}/"""
        data = await request.json()
        article = await Article.objects.get(pk=pk)
        for key, value in data.items():
            setattr(article, key, value)
        await article.save()
        return {"id": str(article.id)}
    
    async def destroy(self, request, pk):
        """DELETE /articles/{pk}/"""
        article = await Article.objects.get(pk=pk)
        await article.delete()
        return None, 204
```

## ModelViewSet

`ModelViewSet` provides CRUD operations automatically:

```python
from zeeb_api.viewsets import ModelViewSet
from apps.blog.models import Article
from apps.blog.serializers import ArticleSerializer


class ArticleViewSet(ModelViewSet):
    """Full CRUD with automatic serialization."""
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
```

This provides:
- `GET /articles/` - List all articles
- `POST /articles/` - Create article
- `GET /articles/{id}/` - Get single article
- `PUT /articles/{id}/` - Update article
- `PATCH /articles/{id}/` - Partial update
- `DELETE /articles/{id}/` - Delete article

## ReadOnlyModelViewSet

For read-only APIs:

```python
from zeeb_api.viewsets import ReadOnlyModelViewSet


class ArticleViewSet(ReadOnlyModelViewSet):
    """Read-only: list and retrieve only."""
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
```

Provides only:
- `GET /articles/` - List
- `GET /articles/{id}/` - Retrieve

## Registering ViewSets

```python
from zeeb_api.routers import Router
from apps.blog.views import ArticleViewSet, AuthorViewSet

router = Router()
router.register("articles", ArticleViewSet)
router.register("authors", AuthorViewSet)

# Get URL patterns
urlpatterns = router.urls
```

## Custom Actions

Add custom endpoints with the `@action` decorator:

```python
from zeeb_api.viewsets import ModelViewSet, action


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    @action(detail=True, methods=["POST"])
    async def publish(self, request, pk=None):
        """POST /articles/{pk}/publish/"""
        article = await self.get_object()
        article.published = True
        await article.save()
        return {"status": "published"}
    
    @action(detail=True, methods=["POST"])
    async def archive(self, request, pk=None):
        """POST /articles/{pk}/archive/"""
        article = await self.get_object()
        article.archived = True
        await article.save()
        return {"status": "archived"}
    
    @action(detail=False, methods=["GET"])
    async def published(self, request):
        """GET /articles/published/"""
        articles = await self.get_queryset().filter(published=True)
        serializer = self.get_serializer(articles, many=True)
        return serializer.data
    
    @action(detail=False, methods=["GET"])
    async def stats(self, request):
        """GET /articles/stats/"""
        total = await Article.objects.count()
        published = await Article.objects.filter(published=True).count()
        return {"total": total, "published": published}
```

### Action Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `detail` | - | `True` for item actions (requires pk), `False` for collection |
| `methods` | `["GET"]` | HTTP methods allowed |
| `url_path` | Action name | Custom URL path |
| `url_name` | Action name | Custom URL name for reversing |
| `permission_classes` | ViewSet's | Override permissions for this action |
| `request_schema` | None | Pydantic model for request body validation |
| `response_schema` | None | Pydantic model for response (OpenAPI docs) |
| `request_serializer` | None | Custom Serializer for request validation |
| `response_serializer` | None | Custom Serializer for response |

### Actions with Request/Response Schemas

Use Pydantic models for request body validation and OpenAPI documentation:

```python
from pydantic import BaseModel
from zeeb_api.viewsets import ModelViewSet, action
from zeeb_api.permissions import IsAuthenticated


class SendEmailRequest(BaseModel):
    subject: str
    body: str
    recipient_ids: list[int]


class SendEmailResponse(BaseModel):
    queued: bool
    job_id: str


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    @action(
        detail=False,
        methods=["POST"],
        request_schema=SendEmailRequest,
        response_schema=SendEmailResponse,
    )
    async def send_newsletter(self, request):
        """POST /articles/send_newsletter/"""
        # Access validated request body
        data = self.get_action_request_body()
        
        # data is already validated against SendEmailRequest
        subject = data["subject"]
        body = data["body"]
        recipient_ids = data["recipient_ids"]
        
        # Queue the email job...
        job_id = await queue_newsletter(subject, body, recipient_ids)
        
        return {"queued": True, "job_id": job_id}
```

You can also use custom Serializers instead of Pydantic models:

```python
from zeeb_api.serializers import Serializer, CharField, ListField, IntegerField


class BulkUpdateSerializer(Serializer):
    ids = ListField(child=IntegerField())
    status = CharField()


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    @action(
        detail=False,
        methods=["POST"],
        request_serializer=BulkUpdateSerializer,
    )
    async def bulk_update_status(self, request):
        """POST /articles/bulk_update_status/"""
        data = self.get_action_request_body()
        
        await Article.objects.filter(id__in=data["ids"]).update(
            status=data["status"]
        )
        return {"updated": len(data["ids"])}
```

### Actions with Custom Permissions

Override permissions for specific actions:

```python
from zeeb_api.viewsets import ModelViewSet, action
from zeeb_api.permissions import IsAuthenticated, IsAdminUser


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsAuthenticated]  # Default for all actions
    
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAdminUser],  # Only admins
    )
    async def feature(self, request, pk=None):
        """POST /articles/{pk}/feature/ - Admin only"""
        article = await self.get_object()
        article.featured = True
        await article.save()
        return {"status": "featured"}
    
    @action(
        detail=True,
        methods=["POST", "DELETE"],
        url_path="toggle-featured",
        permission_classes=[IsAdminUser],
    )
    async def toggle_featured(self, request, pk=None):
        """POST/DELETE /articles/{pk}/toggle-featured/"""
        ...
```

## Customizing Behavior

### get_queryset()

Customize the base queryset:

```python
class ArticleViewSet(ModelViewSet):
    serializer_class = ArticleSerializer
    
    def get_queryset(self):
        """Return articles based on user."""
        qs = Article.objects.all()
        
        # Only own articles for non-staff
        if not self.request.user.is_staff:
            qs = qs.filter(author=self.request.user)
        
        # Apply filters from query params
        if self.request.query_params.get("published"):
            qs = qs.filter(published=True)
        
        return qs
```

### get_serializer_class()

Use different serializers for different actions:

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    
    def get_serializer_class(self):
        if self.action == "list":
            return ArticleListSerializer
        if self.action == "create":
            return ArticleCreateSerializer
        return ArticleDetailSerializer
```

### get_object()

Customize single object retrieval:

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    lookup_field = "slug"  # Use slug instead of pk
    
    async def get_object(self):
        """Get object with additional checks."""
        obj = await super().get_object()
        
        # Check ownership
        if obj.author != self.request.user:
            raise PermissionDenied("Not your article")
        
        return obj
```

### perform_create() / perform_update() / perform_destroy()

Hook into CRUD operations:

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    async def perform_create(self, serializer):
        """Called during create."""
        # Set author to current user
        await serializer.save(author=self.request.user)
    
    async def perform_update(self, serializer):
        """Called during update."""
        await serializer.save(updated_by=self.request.user)
    
    async def perform_destroy(self, instance):
        """Called during delete."""
        # Soft delete instead
        instance.deleted = True
        await instance.save()
```

## Filtering

### Query Parameters

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    def get_queryset(self):
        qs = Article.objects.all()
        
        # Filter by query params
        params = self.request.query_params
        
        if params.get("author"):
            qs = qs.filter(author_id=params["author"])
        
        if params.get("published") == "true":
            qs = qs.filter(published=True)
        
        if params.get("search"):
            qs = qs.filter(
                Q(title__icontains=params["search"]) |
                Q(content__icontains=params["search"])
            )
        
        return qs
```

### Ordering

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    ordering_fields = ["created_at", "title", "views"]
    ordering = ["-created_at"]  # Default ordering
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Handle ordering param
        ordering = self.request.query_params.get("ordering")
        if ordering and ordering.lstrip("-") in self.ordering_fields:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by(*self.ordering)
        
        return qs
```

## Pagination

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    page_size = 20
    max_page_size = 100
    
    async def list(self, request):
        """Paginated list."""
        qs = self.get_queryset()
        
        # Get pagination params
        page = int(request.query_params.get("page", 1))
        page_size = min(
            int(request.query_params.get("page_size", self.page_size)),
            self.max_page_size
        )
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Get total count
        total = await qs.count()
        
        # Get page of results
        items = await qs[offset:offset + page_size]
        
        serializer = self.get_serializer(items, many=True)
        
        return {
            "count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "results": serializer.data,
        }
```

## Permissions

```python
from zeeb_api.permissions import IsAuthenticated, IsAdminUser, AllowAny


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsAuthenticated]
    
    def get_permissions(self):
        """Different permissions per action."""
        if self.action in ["list", "retrieve"]:
            return [AllowAny()]
        if self.action in ["create"]:
            return [IsAuthenticated()]
        return [IsAdminUser()]
```

## Response Handling

> **Exceptions:** `zeeb_api.exceptions` is the canonical home for all API
> exceptions (`APIException`, `ValidationError`, `NotFound`,
> `PermissionDenied`, `AuthenticationFailed`, `MethodNotAllowed`,
> `Throttled`). Importing them from `zeeb_api.response` is deprecated and
> emits a `DeprecationWarning`.

```python
from zeeb_api.response import Response
from zeeb_api.exceptions import NotFound, ValidationError


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    
    async def retrieve(self, request, pk=None):
        """Custom retrieve with error handling."""
        try:
            article = await Article.objects.get(pk=pk)
        except Article.DoesNotExist:
            raise NotFound(f"Article {pk} not found")
        
        serializer = self.get_serializer(article)
        return Response(serializer.data)
    
    async def create(self, request):
        """Custom create with validation."""
        serializer = self.get_serializer(data=await request.json())
        
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)
        
        await self.perform_create(serializer)
        return Response(serializer.data, status=201)
```

## ViewSet Mixins

Compose ViewSets from mixins:

```python
from zeeb_api.viewsets import (
    GenericViewSet,
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
)


class ArticleViewSet(
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """Create, list, and retrieve only (no update/delete)."""
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
```

Available mixins:
- `CreateModelMixin` - Adds `create()` action
- `ListModelMixin` - Adds `list()` action
- `RetrieveModelMixin` - Adds `retrieve()` action
- `UpdateModelMixin` - Adds `update()` and `partial_update()` actions
- `DestroyModelMixin` - Adds `destroy()` action

## Nested Routes

```python
from zeeb_api.routers import Router

router = Router()
router.register("articles", ArticleViewSet)
router.register("articles/{article_pk}/comments", CommentViewSet)

# URLs:
# GET /articles/
# GET /articles/{id}/
# GET /articles/{article_pk}/comments/
# GET /articles/{article_pk}/comments/{id}/
```

```python
class CommentViewSet(ModelViewSet):
    serializer_class = CommentSerializer
    
    def get_queryset(self):
        """Filter by parent article."""
        article_pk = self.kwargs.get("article_pk")
        return Comment.objects.filter(article_id=article_pk)
    
    async def perform_create(self, serializer):
        """Set article from URL."""
        article_pk = self.kwargs.get("article_pk")
        await serializer.save(article_id=article_pk)
```

## Next Steps

- [Serializers](serializers.md) - Data serialization
- [Permissions](permissions.md) - Access control
- [Authentication](authentication.md) - User authentication
