# Models

Models are the single source of truth for your data. They define the fields and behaviors of the data you're storing.

## Defining Models

```python
from zeeb_orm import Model, fields


class Article(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    published = fields.BooleanField(default=False)
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "articles"
        ordering = ["-created_at"]
```

## Field Types

See [Fields Reference](fields.md) for complete list. Common fields:

| Field | Description | Example |
|-------|-------------|---------|
| `CharField` | String with max length | `fields.CharField(max_length=100)` |
| `TextField` | Unlimited text | `fields.TextField()` |
| `IntegerField` | Integer | `fields.IntegerField(default=0)` |
| `BooleanField` | True/False | `fields.BooleanField(default=False)` |
| `DateTimeField` | Date and time | `fields.DateTimeField(auto_now=True)` |
| `ForeignKey` | Relationship | `fields.ForeignKey(User)` |

## Field Options

All fields accept these common options:

```python
class Example(Model):
    # Required field
    name = fields.CharField(max_length=100)
    
    # Optional field (allows NULL)
    description = fields.TextField(null=True, blank=True)
    
    # Field with default value
    status = fields.CharField(max_length=20, default="draft")
    
    # Unique constraint
    email = fields.EmailField(unique=True)
    
    # Database index
    slug = fields.SlugField(index=True)
    
    # Custom database column name
    created = fields.DateTimeField(db_column="created_timestamp")
    
    # Choices
    priority = fields.CharField(
        max_length=10,
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")]
    )
```

### Option Reference

| Option | Default | Description |
|--------|---------|-------------|
| `null` | `False` | If `True`, NULL is allowed in database |
| `blank` | `False` | If `True`, field can be empty in forms/serializers |
| `default` | None | Default value (can be callable) |
| `primary_key` | `False` | If `True`, this is the primary key |
| `unique` | `False` | If `True`, value must be unique |
| `index` | `False` | If `True`, create database index |
| `db_column` | None | Custom database column name |
| `verbose_name` | None | Human-readable name |
| `help_text` | None | Help text for documentation |
| `choices` | None | List of valid choices |
| `validators` | `[]` | List of validator functions |
| `editable` | `True` | If `False`, excluded from forms |

## Primary Keys

By default, Zeeb adds a UUID primary key to every model:

```python
class Article(Model):
    # 'id' field is automatically added as UUIDAutoField
    title = fields.CharField(max_length=200)
```

You can customize the primary key:

```python
class Article(Model):
    # Use integer auto-increment
    id = fields.BigAutoField(primary_key=True)
    title = fields.CharField(max_length=200)

class Article(Model):
    # Use custom field as primary key
    slug = fields.SlugField(primary_key=True, max_length=100)
    title = fields.CharField(max_length=200)
```

## Meta Options

The `Meta` class configures model behavior:

```python
class Article(Model):
    title = fields.CharField(max_length=200)
    
    class Meta:
        # Database table name (default: lowercase model name)
        table_name = "blog_articles"
        
        # Default ordering for queries
        ordering = ["-created_at", "title"]
        
        # Make this an abstract base class (no table created)
        abstract = True
        
        # Human-readable names
        verbose_name = "Article"
        verbose_name_plural = "Articles"
        
        # Database indexes
        indexes = [
            Index(fields=["title", "created_at"]),
            Index(fields=["author", "published"], name="author_published_idx"),
        ]
        
        # Unique together constraints
        unique_together = [
            ("author", "slug"),
        ]
        
        # Check constraints
        constraints = [
            CheckConstraint(check="views >= 0", name="positive_views"),
        ]
```

### Meta Options Reference

| Option | Description |
|--------|-------------|
| `table_name` / `db_table` | Database table name |
| `ordering` | Default query ordering (list of field names, prefix `-` for descending) |
| `abstract` | If `True`, no table is created (use as base class) |
| `verbose_name` | Human-readable singular name |
| `verbose_name_plural` | Human-readable plural name |
| `indexes` | List of `Index` objects |
| `unique_together` | List of field tuples that must be unique together |
| `constraints` | List of `CheckConstraint` objects |

## Model Inheritance

### Abstract Base Classes

```python
class TimestampMixin(Model):
    """Adds created_at and updated_at to any model."""
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Article(TimestampMixin):
    """Article inherits timestamp fields."""
    title = fields.CharField(max_length=200)
    content = fields.TextField()
```

### Multi-table Inheritance

```python
class Place(Model):
    name = fields.CharField(max_length=50)
    address = fields.CharField(max_length=80)


class Restaurant(Place):
    """Extends Place with additional fields."""
    serves_pizza = fields.BooleanField(default=False)
    serves_pasta = fields.BooleanField(default=False)
```

## Model Methods

### Instance Methods

```python
class Article(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    views = fields.IntegerField(default=0)

    def __str__(self):
        """String representation."""
        return self.title

    def get_absolute_url(self):
        """URL for this article."""
        return f"/articles/{self.pk}/"

    async def increment_views(self):
        """Increment view count."""
        self.views += 1
        await self.save(update_fields=["views"])

    @property
    def is_popular(self):
        """Check if article is popular."""
        return self.views > 1000
```

### CRUD Operations

```python
# Create
article = Article(title="Hello", content="World")
await article.save()

# or use create()
article = await Article.objects.create(title="Hello", content="World")

# Read
article = await Article.objects.get(pk=article_id)

# Update
article.title = "Updated Title"
await article.save()

# or bulk update
await Article.objects.filter(published=False).update(published=True)

# Delete
await article.delete()

# or bulk delete
await Article.objects.filter(views=0).delete()
```

### save() Options

```python
# Save all fields
await article.save()

# Save only specific fields
await article.save(update_fields=["title", "updated_at"])

# Force insert (don't try to update)
await article.save(force_insert=True)
```

### refresh_from_db()

```python
# Reload from database
await article.refresh_from_db()

# Reload specific fields
await article.refresh_from_db(fields=["views"])
```

## Model State

Each model instance has a `_state` object:

```python
article = Article(title="New")
article._state.persisted  # False - not saved yet

await article.save()
article._state.persisted  # True - saved to database

article._state.db  # Database alias used
```

## Managers

The `objects` attribute is a Manager that provides query methods:

```python
# Default manager
articles = await Article.objects.all()
articles = await Article.objects.filter(published=True)

# Custom manager
class PublishedManager(Manager):
    def get_queryset(self):
        return super().get_queryset().filter(published=True)

class Article(Model):
    title = fields.CharField(max_length=200)
    published = fields.BooleanField(default=False)

    objects = Manager()  # Default manager
    published_objects = PublishedManager()  # Custom manager

# Use custom manager
published = await Article.published_objects.all()
```

## Model Validation

### Field-level Validation

```python
from zeeb_orm import fields, validators

class User(Model):
    age = fields.IntegerField(
        validators=[
            validators.MinValueValidator(0),
            validators.MaxValueValidator(150),
        ]
    )
    email = fields.EmailField()  # Automatically validates email format
```

### Model-level Validation

```python
class Event(Model):
    start_date = fields.DateTimeField()
    end_date = fields.DateTimeField()

    def clean(self):
        """Validate the model."""
        if self.end_date <= self.start_date:
            raise ValidationError("End date must be after start date")

    async def save(self, **kwargs):
        self.clean()
        await super().save(**kwargs)
```

## Signals (Hooks)

```python
class Article(Model):
    title = fields.CharField(max_length=200)
    slug = fields.SlugField()

    async def save(self, **kwargs):
        # Pre-save hook
        if not self.slug:
            self.slug = slugify(self.title)
        
        await super().save(**kwargs)
        
        # Post-save hook
        await self.notify_subscribers()
```

## Next Steps

- [Fields](fields.md) - Complete field reference
- [Queries](queries.md) - Query your models
- [Relationships](relationships.md) - Model relationships
