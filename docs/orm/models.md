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

| Option | Description | Inherited |
|--------|-------------|-----------|
| `table_name` / `db_table` | Database table name | no |
| `abstract` | If `True`, no table is created (use as base class) | no |
| `ordering` | Default query ordering (list of field names, prefix `-` for descending) | yes |
| `managed` | If `False`, migrations leave the table alone | yes |
| `indexes` | List of `Index` objects | yes |
| `constraints` | List of `UniqueConstraint` / `CheckConstraint` objects | yes |
| `unique_together` | List of field tuples that must be unique together | yes |
| `index_together` | List of field tuples to index together | yes |
| `app_label` | Application label | yes |

Any other attribute on `class Meta` is ignored; there is no `verbose_name`.

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

#### What a subclass inherits

Fields, the primary key, and the inheritable `Meta` options from the table
above:

```python
class Base(Model):
    id = fields.BigAutoField()
    slug = fields.CharField(max_length=40)

    class Meta:
        abstract = True
        ordering = ["-slug"]
        table_name = "never_used"       # not inherited
        unique_together = [("slug",)]


class Child(Base):
    name = fields.CharField(max_length=40)


Child._meta.db_table         # "child"       — derived, not inherited
Child._meta.abstract         # False         — never inherited
Child._meta.ordering         # ["-slug"]     — inherited
Child._meta.unique_together  # [("slug",)]   — inherited
Child._meta.pk_name          # "id"          — the inherited BigAutoField
```

`abstract` and `table_name`/`db_table` are deliberately excluded: inheriting
them would make every subclass abstract and give siblings the same table.

An option declared in the subclass's own `Meta` wins, and with multiple
bases the first one in the MRO that supplies an option wins. An inherited
`Index` or constraint loses an explicit `name` so sibling models do not
collide on it — each gets a name derived from its own table.

Declaring a primary key in the subclass **replaces** the inherited one
rather than forming a composite key:

```python
class Coded(Base):
    code = fields.CharField(max_length=10, primary_key=True)

Coded._meta.pk_name  # "code"; `slug` is still inherited, `id` is not
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

### Custom QuerySet Methods (from_queryset / as_manager)

Define reusable query logic on a `QuerySet` subclass and expose it on a
manager. All public methods of the QuerySet class become manager methods,
and they keep chaining after `filter()` etc. because clones preserve the
QuerySet subclass:

```python
from zeeb_orm import Manager, Model, QuerySet, fields

class ArticleQuerySet(QuerySet):
    def published(self):
        return self.filter(published=True)

    def by_views(self):
        return self.order_by("-views")

class Article(Model):
    title = fields.CharField(max_length=200)
    published = fields.BooleanField(default=False)
    views = fields.IntegerField(default=0)

    # Option 1: build a Manager class from the QuerySet
    objects = Manager.from_queryset(ArticleQuerySet)()

    # Option 2: shortcut — same thing
    # objects = ArticleQuerySet.as_manager()

# Custom methods work on the manager and chain on querysets
hot = await Article.objects.published().by_views()
hot = await Article.objects.filter(views__gt=10).published()
```

`Manager.from_queryset(queryset_class, class_name=None)` returns a new
Manager subclass whose `get_queryset()` returns
`queryset_class(self.model)`; the originating QuerySet class is recorded
as `_built_with_queryset`. The generated class can be subclassed to
override `get_queryset()` — proxied methods pick the override up:

```python
class PublishedManager(Manager.from_queryset(ArticleQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(published=True)

class Article(Model):
    ...
    published_objects = PublishedManager()

# by_views() now only sees published articles
top = await Article.published_objects.by_views()
```

Note: QuerySet subclasses used this way must be constructible as
`queryset_class(model)` (no extra required `__init__` arguments), and
generated manager classes are zero-arg constructible — both requirements
of the manager rebinding machinery.

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

Override the async `clean()` hook for cross-field checks; it is called by
`full_clean()` after field validation:

```python
from zeeb_orm import ValidationError

class Event(Model):
    start_date = fields.DateTimeField()
    end_date = fields.DateTimeField()

    async def clean(self):
        """Validate the model."""
        if self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after start date"})
```

An error that belongs to the record as a whole rather than to one field is
raised with a plain message. It is collected under the `NON_FIELD_ERRORS` key
(the string `"__all__"`) in `ValidationError.message_dict`:

```python
from zeeb_orm import NON_FIELD_ERRORS, ValidationError

async def clean(self):
    if self.starts_at and self.venue_id is None:
        raise ValidationError("A scheduled event needs a venue.")

# Reading it back
try:
    await event.full_clean()
except ValidationError as exc:
    exc.message_dict[NON_FIELD_ERRORS]   # ["A scheduled event needs a venue."]
```

### full_clean() / clean_fields()

```python
event = Event(start_date=later, end_date=earlier)

# Validate one layer at a time
event.clean_fields()                  # per-field checks (sync)
await event.clean()                   # custom hook (async)

# Or everything at once — errors are merged into one ValidationError
try:
    await event.full_clean()
except ValidationError as exc:
    print(exc.message_dict)           # {"end_date": ["End date must be ..."]}

# Skip specific fields
await event.full_clean(exclude=["start_date"])
```

Field checks cover `null=False` (skipped for primary keys, `auto_now`/
`auto_now_add` fields and fields with a `default`, since those values are
filled in at insert time), `choices` membership, and all field `validators`
(including built-ins like `CharField.max_length`).

### Validation on save

Validation is **enforced by default** when writing:

```python
await event.save()                    # runs full_clean() first
await event.save(validate=False)      # opt out

# update_fields excludes non-updated fields from validation
await event.save(update_fields=["end_date"])

await Event.objects.create(...)                  # validates by default
await Event.objects.create(..., validate=False)  # opt out

# bulk_create skips validation by default (performance); opt in:
await Event.objects.bulk_create(events, validate=True)
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
