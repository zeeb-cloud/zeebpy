# Serializers

Serializers handle conversion between complex data types (like model instances) and Python primitives that can be rendered to JSON.

## Basic Serializer

```python
from zeeb_api.serializers import Serializer, fields


class ArticleSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    title = fields.CharField(max_length=200)
    content = fields.CharField()
    published = fields.BooleanField(default=False)
    created_at = fields.DateTimeField(read_only=True)
```

## Serializing Data

### Single Object

```python
article = await Article.objects.get(pk=article_id)
serializer = ArticleSerializer(article)
data = serializer.data
# {"id": "uuid...", "title": "Hello", "content": "...", ...}
```

### Multiple Objects

```python
articles = await Article.objects.all()
serializer = ArticleSerializer(articles, many=True)
data = serializer.data
# [{"id": "...", ...}, {"id": "...", ...}]
```

### From Dictionary

```python
data = {"title": "Hello", "content": "World"}
serializer = ArticleSerializer(data=data)
```

## Deserializing Data

### Validation

```python
data = {"title": "New Article", "content": "Content here"}
serializer = ArticleSerializer(data=data)

if serializer.is_valid():
    validated_data = serializer.validated_data
    # Create object with validated data
else:
    errors = serializer.errors
    # {"title": ["This field is required."]}
```

> **`validated_data` contains only the keys the client actually sent.** A field
> the request omitted is **absent** from the dict — it does not come back as
> `None` or as its default. This matters most in `perform_create()` /
> `perform_update()` hooks that reach into `validated_data` for a field the
> client may not have supplied.

Read optional fields with `.get()`, never by subscript:

```python
summary = serializer.validated_data.get("summary")   # None when omitted
serializer.validated_data["summary"]                 # KeyError when omitted
```

### Partial Updates

`partial=True` (what `PATCH` uses) validates against an all-optional variant of
the schema, exposed as `Serializer.PartialRequestSchema` alongside the usual
`Schema` / `RequestSchema` / `ResponseSchema`:

```python
serializer = ArticleSerializer(instance=article, data=data, partial=True)
serializer.is_valid(raise_exception=True)
```

Missing fields are allowed; **supplied fields are fully validated**. A `PATCH`
carrying `{"views": "abc"}` is a 400, and combined with the rule above,
`validated_data` holds exactly the fields the client sent — which is what makes
a partial update safe to apply directly.

### Raise on Invalid

```python
serializer = ArticleSerializer(data=data)
serializer.is_valid(raise_exception=True)  # Raises ValidationError if invalid
```

### Error Handling

The canonical import for `ValidationError` is:

```python
from zeeb_api.exceptions import ValidationError
```

(Importing it from `zeeb_api.response` still works but is deprecated and
emits a `DeprecationWarning`.)

`ValidationError` is both a `ZeebException` and a FastAPI `HTTPException`:

- With `install_exception_handlers()` (the default in `create_app()`), it
  produces the standardized error envelope
  (`{"success": false, "error": {"code": "VALIDATION_ERROR", ...}}`) with
  per-field details.
- Without the handlers, FastAPI/Starlette's built-in `HTTPException`
  handling applies and returns `{"detail": {...}}` with status 400.

## Field Types

### String Fields

```python
class UserSerializer(Serializer):
    # Basic string
    name = fields.CharField(max_length=100)
    
    # Email with validation
    email = fields.EmailField()
    
    # UUID string
    token = fields.UUIDField()
```

### Numeric Fields

```python
class ProductSerializer(Serializer):
    # Integer
    quantity = fields.IntegerField(min_value=0)
    
    # Float
    rating = fields.FloatField(min_value=0, max_value=5)
    
    # Decimal (for money)
    price = fields.DecimalField(max_digits=10, decimal_places=2)
```

### Boolean Field

```python
class SettingsSerializer(Serializer):
    notifications_enabled = fields.BooleanField(default=True)
    marketing_emails = fields.BooleanField(default=False)
```

### Date/Time Fields

```python
class EventSerializer(Serializer):
    # Date only
    event_date = fields.DateField()
    
    # Date and time
    starts_at = fields.DateTimeField()
    
    # Time only
    duration = fields.TimeField()
```

### Collection Fields

```python
class ConfigSerializer(Serializer):
    # List of values
    tags = fields.ListField(child=fields.CharField())
    
    # Dictionary
    metadata = fields.DictField()
    
    # JSON (any structure)
    settings = fields.JSONField()
```

## Field Options

### Common Options

```python
class ArticleSerializer(Serializer):
    # Read-only (not accepted in input)
    id = fields.UUIDField(read_only=True)
    
    # Write-only (not included in output)
    password = fields.CharField(write_only=True)
    
    # Required (default: True)
    title = fields.CharField(required=True)
    
    # Optional with default
    status = fields.CharField(default="draft")
    
    # Allow null
    description = fields.CharField(allow_null=True)
    
    # Allow empty string
    subtitle = fields.CharField(allow_blank=True)
    
    # Custom source field
    author_name = fields.CharField(source="author.name", read_only=True)
    
    # Custom error messages
    email = fields.EmailField(error_messages={
        "invalid": "Please enter a valid email address",
    })
```

### Option Reference

| Option | Default | Description |
|--------|---------|-------------|
| `read_only` | `False` | Exclude from input validation |
| `write_only` | `False` | Exclude from serialized output |
| `required` | `True` | Field must be present in input |
| `default` | - | Default value if not provided |
| `allow_null` | `False` | Accept `None` as value |
| `allow_blank` | `False` | Accept empty string |
| `source` | - | Attribute to get value from |
| `validators` | `[]` | List of validator functions |
| `error_messages` | `{}` | Custom error message overrides |

`max_length`, `min_length`, `allow_blank=False` and `validators` are enforced
on **input**, not only in the generated OpenAPI schema — the field's own
validation runs as part of Pydantic validation. A value that violates one is a
400 before your `create()`/`update()` sees it.

## Foreign Keys

By default, a `ModelSerializer` represents a `ForeignKey` (and `OneToOneField`)
as the **primary key of the related object** — not the related object itself and
not a lazy relation wrapper. The output field is typed as the target model's PK
type (e.g. `UUID`):

```python
class TaskSerializer(ModelSerializer):
    class Meta:
        model = Task          # Task.project is a ForeignKey(Project)
        fields = "__all__"
```

Output:
```json
{
  "id": "...",
  "title": "Ship it",
  "project": "e5e5277f-13e1-4395-a02c-a29350ed9546"
}
```

The serializer reads the raw FK id directly (the `project_id`
column attribute), so serializing a queryset never triggers a lazy database
fetch for the relation. To embed the full related object instead, declare a nested
serializer for that field (see below).

## To-Many & Reverse Relations

`ManyToManyField`, reverse foreign keys and reverse many-to-many accessors are
**not** included by `fields = "__all__"` (they are not local columns). List them
explicitly to serialize them as a **list of related primary keys**:

```python
class ArticleSerializer(ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title", "tags"]   # tags is a ManyToManyField


class ProjectSerializer(ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name", "tasks"]   # tasks is the reverse FK accessor
```

Output:
```json
{ "id": "...", "title": "Hello", "tags": ["uuid-1", "uuid-2"] }
{ "id": "...", "name": "Apollo", "tasks": ["uuid-3", "uuid-4"] }
```

These fields are **read-only** and serialize to `list[PK]` (an empty list when
there are no related rows). A reverse one-to-one accessor serializes to a single
`PK | None`. To embed full related objects instead, use a nested serializer.

### Sync vs. async serialization

Resolving a to-many / reverse relation requires a database query. The generated
viewsets serialize responses through `await serializer.adata()`, which loads
these relations automatically — so the auto CRUD and `POST /query/` endpoints
work out of the box.

The synchronous `serializer.data` property cannot load an unfetched relation: it
resolves ForeignKey ids and *prefetched* to-many relations, but raises
`NotSupportedError` for an unresolved relation manager. In custom code, prefer
`await serializer.adata()`, or use `prefetch_related()` before accessing `.data`.

## Nested Serializers

### Include Related Objects

```python
class AuthorSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    name = fields.CharField()


class ArticleSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    title = fields.CharField()
    author = AuthorSerializer(read_only=True)  # Nested
```

Output:
```json
{
  "id": "...",
  "title": "Hello",
  "author": {
    "id": "...",
    "name": "John Doe"
  }
}
```

### Writable Nested Serializers

```python
class CommentSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    content = fields.CharField()


class ArticleSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    title = fields.CharField()
    comments = CommentSerializer(many=True)

    def create(self, validated_data):
        comments_data = validated_data.pop("comments", [])
        article = Article(**validated_data)
        for comment_data in comments_data:
            Comment.objects.create(article=article, **comment_data)
        return article
```

### Many-to-Many Relationships

```python
class TagSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    name = fields.CharField()


class ArticleSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    title = fields.CharField()
    tags = TagSerializer(many=True, read_only=True)
    tag_ids = fields.ListField(
        child=fields.UUIDField(),
        write_only=True,
    )
```

## Validation

### Field-Level Validation

```python
class UserSerializer(Serializer):
    username = fields.CharField(max_length=50)
    email = fields.EmailField()
    age = fields.IntegerField()

    def validate_username(self, value):
        """Validate username field."""
        if value.lower() in ["admin", "root"]:
            raise ValidationError("This username is reserved")
        return value.lower()  # Transform value

    def validate_age(self, value):
        """Validate age field."""
        if value < 13:
            raise ValidationError("Must be at least 13 years old")
        return value
```

### Object-Level Validation

```python
class RegistrationSerializer(Serializer):
    password = fields.CharField(write_only=True)
    password_confirm = fields.CharField(write_only=True)

    def validate(self, attrs):
        """Validate the entire object."""
        if attrs["password"] != attrs["password_confirm"]:
            raise ValidationError({"password_confirm": "Passwords don't match"})
        
        # Remove confirm field from validated data
        attrs.pop("password_confirm")
        return attrs
```

### Custom Validators

```python
def validate_positive(value):
    if value <= 0:
        raise ValidationError("Must be positive")


class ProductSerializer(Serializer):
    price = fields.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[validate_positive],
    )
```

### Unique Validation

```python
class UserSerializer(Serializer):
    email = fields.EmailField()

    async def validate_email(self, value):
        """Check email is unique."""
        exists = await User.objects.filter(email=value).exists()
        if exists:
            raise ValidationError("This email is already registered")
        return value
```

## Custom Methods

### create()

Override to customize object creation:

```python
class ArticleSerializer(Serializer):
    title = fields.CharField()
    content = fields.CharField()

    async def create(self, validated_data):
        """Create and return a new Article."""
        # Add extra data
        validated_data["author"] = self.context["request"].user
        return await Article.objects.create(**validated_data)
```

### update()

Override to customize object updates:

```python
class ArticleSerializer(Serializer):
    title = fields.CharField()
    content = fields.CharField()

    async def update(self, instance, validated_data):
        """Update and return existing Article."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        await instance.save()
        return instance
```

### to_representation()

Customize output format:

```python
class ArticleSerializer(Serializer):
    title = fields.CharField()
    created_at = fields.DateTimeField()

    def to_representation(self, instance):
        """Customize output."""
        data = super().to_representation(instance)
        # Add computed field
        data["title_length"] = len(instance.title)
        return data
```

### to_internal_value()

Customize input parsing:

```python
class ArticleSerializer(Serializer):
    title = fields.CharField()

    def to_internal_value(self, data):
        """Customize input parsing."""
        # Transform input before validation
        if "title" in data:
            data["title"] = data["title"].strip()
        return super().to_internal_value(data)
```

## Context

Pass additional data to serializers:

```python
# Pass context
serializer = ArticleSerializer(
    article,
    context={"request": request, "user": user}
)

# Access in methods
class ArticleSerializer(Serializer):
    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["author"] = user
        return Article.objects.create(**validated_data)
```

## Meta Class

Configure serializer with Meta class:

```python
class ArticleSerializer(Serializer):
    class Meta:
        # Fields to include
        fields = ["id", "title", "content", "created_at"]
        
        # Or exclude specific fields
        exclude = ["internal_notes"]
        
        # Read-only fields
        read_only_fields = ["id", "created_at"]
        
        # Extra kwargs for fields
        extra_kwargs = {
            "content": {"required": False},
            "title": {"max_length": 100},
        }
```

## Serializer Fields Reference

| Field | Description |
|-------|-------------|
| `CharField` | String field |
| `EmailField` | Email with validation |
| `IntegerField` | Integer |
| `FloatField` | Float |
| `DecimalField` | Decimal |
| `BooleanField` | Boolean |
| `DateTimeField` | DateTime |
| `DateField` | Date |
| `TimeField` | Time |
| `UUIDField` | UUID |
| `ListField` | List/Array |
| `DictField` | Dictionary |
| `JSONField` | JSON data |

### Relational Fields

| Field | Description |
|-------|-------------|
| `PrimaryKeyRelatedField` | Accepts and returns the related object's primary key. What `ModelSerializer` uses for a `ForeignKey` by default. |
| `SlugRelatedField` | Accepts and returns a unique non-PK column (`slug_field=`), e.g. a `username` or `slug` instead of a UUID. |
| `NestedSerializer` | Marker that embeds another serializer's output; `many=True` for to-many, `read_only=True` by default. |

```python
from zeeb_api.serializers import PrimaryKeyRelatedField, SlugRelatedField


class ArticleSerializer(Serializer):
    author = PrimaryKeyRelatedField(queryset=User.objects.all())
    tags = SlugRelatedField(slug_field="name", queryset=Tag.objects.all(), many=True)
```

### Response Schema Helpers

`create_list_response_schema(item_schema, name=None)` builds a paginated
list-response model from an item schema — useful when you hand-write a route
and want its OpenAPI response to match what a paginated viewset returns.

## Next Steps

- [ViewSets](viewsets.md) - Use serializers in APIs
- [Field Types](../reference/field-types.md) - All field types and their options
- [Error Handling](errors.md) - How validation failures reach the client
