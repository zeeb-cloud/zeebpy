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
| `ordering` | Default query ordering (list of field names, prefix `-` for descending; an unknown name raises `FieldError` when a query is built; replaced by any explicit `order_by()`) | yes |
| `managed` | If `False`, `makemigrations` generates nothing for the table and test schemas (`create_all`, `temporary_database`) do not create it | yes |
| `indexes` | List of `Index` objects | yes |
| `constraints` | List of `UniqueConstraint` / `CheckConstraint` objects | yes |
| `unique_together` | List of field tuples that must be unique together | yes |
| `index_together` | List of field tuples to index together | yes |
| `app_label` | Application label (default: derived from the module, see below) | yes |

Any other attribute on `class Meta` is ignored; there is no `verbose_name`.

### App labels and the model registry

Every concrete model is registered under its label, `"<app_label>.<ClassName>"`.
Without `Meta.app_label` the label comes from the defining module:
`apps/blog/models.py` (and `apps/blog/models/post.py`) → `blog`,
`zeeb_api/auth/models.py` → `auth`, any other module → its last segment. Two
apps may therefore each define a `Post`; neither replaces the other. The
default **table name is unchanged** — it still derives from the class name
alone, so set `table_name` when two same-named models share a database.

String references (`ForeignKey("accounts.User")`, `ForeignKey("Author")`)
resolve in this order:

1. the exact label (`"accounts.User"`, also `"apps.accounts.User"`);
2. a bare name from inside an app — the model of that name **in the same app**
   (so `ForeignKey("Author")` in `blog` means `blog.Author`);
3. the only registered model with that class name. A project model shadows a
   framework model of the same name (a project's `accounts.User` wins a bare
   `"User"` over zeeb_api's own). Two project models with the name raise
   `AmbiguousModelReferenceError` asking for the app-qualified label.

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

Relations are inherited per subclass: a `ForeignKey("self")` or
`ManyToMany("self")` on an abstract base points at each concrete subclass,
and an inherited `ManyToManyField` gets a join table per subclass. Give an
inherited relation a `related_name` with `%(class)s` (and optionally
`%(app_label)s`) so every subclass installs its own reverse accessor:

```python
class Owned(Model):
    owner = fields.ForeignKey("accounts.User", related_name="%(class)s_items")

    class Meta:
        abstract = True


class Pen(Owned):
    pass            # user.pen_items


class Cup(Owned):
    pass            # user.cup_items
```

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

### Creating instances

`Model(**fields)` works like this:

- A field that is **not passed** gets its default; a field passed explicitly
  keeps the value given — `None` included (`Article(views=None)` is `None`,
  not `0`). Callable defaults and `auto_now_add` are filled in on INSERT.
- A ForeignKey takes the related instance under its name or the raw id under
  `<name>_id`. A settable property (such as `pk`) may be passed too.
- **Anything else raises `TypeError`** — a misspelt field in
  `Article.objects.create(titel=...)` is an error, not a silently dropped
  value.

Loading from the database never applies defaults: a `NULL` column is `None`
on the instance, so a `BooleanField(null=True)` or
`IntegerField(null=True, default=0)` round-trips its `NULL`.

### save() Options

```python
# Save all fields
await article.save()

# Save only specific fields (names, or a ForeignKey's "<name>_id")
await article.save(update_fields=["title", "updated_at"])
```

`save()` behaves like this:

- A new instance is INSERTed; a persisted one is UPDATEd. If that UPDATE
  matches no row (the row was deleted meanwhile), the instance is INSERTed
  again and `post_save` reports `created=True`.
- `update_fields` restricts the UPDATE. An unknown name raises `ValueError`,
  an empty list saves nothing, and an UPDATE that matches no row raises
  `zeeb_orm.exceptions.DatabaseError` instead of inserting. On an unsaved
  instance without a primary key it raises `ValueError`.
- An instance loaded with `only()`/`defer()` writes back only the fields
  that were loaded or assigned since — the unloaded columns are left alone.

### refresh_from_db()

```python
# Reload from database
await article.refresh_from_db()

# Reload specific fields
await article.refresh_from_db(fields=["views"])
```

A reloaded ForeignKey drops its cached related object, so after the reload
`await article.author` fetches the author the row now points at.

### Equality and hashing

Two instances are equal when they are the same model with the same primary
key. An unsaved instance (no pk yet) is equal only to itself, and hashing it
raises `TypeError` — its pk, and so its hash, would change on save:

```python
Article(title="a") == Article(title="a")   # False
hash(Article(title="a"))                   # TypeError
{await Article.objects.get(pk=pk)}         # fine: saved instances hash by pk
```

## Model State

Each model instance has a `_state` object:

```python
article = Article(title="New")
article._state.persisted  # False - not saved yet

await article.save()
article._state.persisted  # True - saved to database

article._state.db_alias  # Database alias used (None = "default")
article._state.deferred  # Fields not loaded (only()/defer())
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
