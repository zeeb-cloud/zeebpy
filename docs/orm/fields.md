# Fields

Field types for defining model attributes.

## String Fields

### CharField

Variable-length string with maximum length.

```python
from zeeb_orm import fields

class Article(Model):
    title = fields.CharField(max_length=200)
    subtitle = fields.CharField(max_length=100, blank=True, default="")
```

**Options:**
- `max_length` (required) - Maximum character length

### TextField

Unlimited-length text.

```python
class Article(Model):
    content = fields.TextField()
    summary = fields.TextField(null=True, blank=True)
```

### EmailField

String field that validates email format.

```python
class User(Model):
    email = fields.EmailField(unique=True)
```

### SlugField

URL-safe string field (letters, numbers, underscores, hyphens).

```python
class Article(Model):
    slug = fields.SlugField(max_length=100, unique=True)
```

**Options:**
- `max_length` (default: 50)
- `allow_unicode` (default: False) - Allow unicode characters

### URLField

String field that validates URL format.

```python
class Link(Model):
    url = fields.URLField()
    display_name = fields.CharField(max_length=100)
```

**Options:**
- `max_length` (default: 200)

## Numeric Fields

### IntegerField

Standard integer.

```python
class Product(Model):
    quantity = fields.IntegerField(default=0)
    priority = fields.IntegerField(null=True)
```

### SmallIntegerField

Small integer (-32768 to 32767).

```python
class Rating(Model):
    score = fields.SmallIntegerField()  # -32768 to 32767
```

### BigIntegerField

Large integer (-9223372036854775808 to 9223372036854775807).

```python
class Analytics(Model):
    page_views = fields.BigIntegerField(default=0)
```

### PositiveIntegerField

Non-negative integer (0 to 2147483647). The `>= 0` constraint is enforced by
validation (no database CHECK constraint).

```python
class Order(Model):
    quantity = fields.PositiveIntegerField()
```

### PositiveSmallIntegerField

Non-negative small integer (0 to 32767). Validation-only `>= 0` constraint.

```python
class Rating(Model):
    stars = fields.PositiveSmallIntegerField(default=0)
```

### PositiveBigIntegerField

Non-negative big integer (0 to 9223372036854775807). Validation-only `>= 0`
constraint.

```python
class Analytics(Model):
    total_bytes = fields.PositiveBigIntegerField(default=0)
```

### FloatField

Floating-point number.

```python
class Location(Model):
    latitude = fields.FloatField()
    longitude = fields.FloatField()
```

### DecimalField

Fixed-precision decimal number. Use for money and exact decimal values.

```python
class Product(Model):
    price = fields.DecimalField(max_digits=10, decimal_places=2)
    tax_rate = fields.DecimalField(max_digits=5, decimal_places=4)
```

**Options:**
- `max_digits` (required) - Total number of digits
- `decimal_places` (required) - Number of decimal places

## Boolean Field

### BooleanField

True/False value.

```python
class Article(Model):
    published = fields.BooleanField(default=False)
    featured = fields.BooleanField(default=False)
```

For nullable boolean (True/False/None):

```python
class Survey(Model):
    approved = fields.BooleanField(null=True)  # True, False, or None
```

## Date and Time Fields

### DateField

Date without time.

```python
class Event(Model):
    event_date = fields.DateField()
    registration_deadline = fields.DateField(null=True)
```

**Options:**
- `auto_now` - Update to current date on every save
- `auto_now_add` - Set to current date on creation

### TimeField

Time without date.

```python
class Schedule(Model):
    start_time = fields.TimeField()
    end_time = fields.TimeField()
```

### DateTimeField

Date and time combined.

```python
class Article(Model):
    # Set automatically on creation
    created_at = fields.DateTimeField(auto_now_add=True)
    
    # Update automatically on every save
    updated_at = fields.DateTimeField(auto_now=True)
    
    # Manual datetime
    published_at = fields.DateTimeField(null=True)
```

**Options:**
- `auto_now` - Update to current datetime on every save
- `auto_now_add` - Set to current datetime on creation
- `timezone` - Include timezone info (default: True)

## UUID Field

### UUIDField

UUID (Universally Unique Identifier).

```python
import uuid

class Token(Model):
    token = fields.UUIDField(default=uuid.uuid4)
```

## JSON Field

### JSONField

JSON-encoded data (dict, list, etc.).

```python
class Config(Model):
    settings = fields.JSONField(default=dict)
    tags = fields.JSONField(default=list)
```

**Options:**
- `default` - Default value (use `dict` or `list` for empty)

```python
# Store any JSON-serializable data
config = Config(settings={"theme": "dark", "notifications": True})
await config.save()

# Access as Python dict
print(config.settings["theme"])  # "dark"
```

## Binary Field

### BinaryField

Raw binary data (`bytes`), stored as BLOB/BYTEA.

```python
class Upload(Model):
    data = fields.BinaryField()
    thumbnail = fields.BinaryField(max_length=65536, null=True)
```

**Options:**
- `max_length` - Optional maximum size in bytes (enforced by validation and,
  where supported, the column type)

## Duration Field

### DurationField

Stores `datetime.timedelta` values.

```python
import datetime

class Task(Model):
    estimated = fields.DurationField(null=True)

task = await Task.objects.create(estimated=datetime.timedelta(hours=2, minutes=30))
```

**Backend note:** PostgreSQL uses a native `INTERVAL` column. SQLite and
MySQL have no interval type, so SQLAlchemy stores the value as a datetime
relative to the epoch — precision is limited to microseconds and the
duration must stay within the representable datetime range (roughly years
1–9999 from the epoch).

## IP Address Field

### GenericIPAddressField

IPv4/IPv6 address stored as a string (`VARCHAR(39)`), validated on save.

```python
class AuditLog(Model):
    client_ip = fields.GenericIPAddressField()                  # IPv4 or IPv6
    legacy_ip = fields.GenericIPAddressField(protocol="IPv4")   # IPv4 only
    modern_ip = fields.GenericIPAddressField(protocol="IPv6")   # IPv6 only
```

**Options:**
- `protocol` - `"both"` (default), `"IPv4"` or `"IPv6"`

## Primary Key Fields

### AutoField

Auto-incrementing integer primary key.

```python
class Article(Model):
    id = fields.AutoField(primary_key=True)
```

### BigAutoField

Auto-incrementing big integer primary key.

```python
class Article(Model):
    id = fields.BigAutoField(primary_key=True)
```

### UUIDAutoField

UUID primary key with automatic generation.

```python
class Article(Model):
    id = fields.UUIDAutoField(primary_key=True)
```

> **Note:** `UUIDAutoField` is the default primary key if you don't specify one.

## Relationship Fields

### ForeignKey

Many-to-one relationship.

```python
class Post(Model):
    author = fields.ForeignKey(
        "User",  # Related model (string for forward reference)
        on_delete="CASCADE",
        related_name="posts",
    )

# Self-referential ForeignKey
class Comment(Model):
    parent = fields.ForeignKey(
        "self",  # Reference to same model
        on_delete="CASCADE",
        null=True,  # Allow top-level comments
        related_name="replies",
    )
```

**Options:**
- `to` - Related model (class, string, callable returning a class, or `"self"` for self-referential)
- `on_delete` (required) - Deletion behavior:
  - `"CASCADE"` - Delete related objects
  - `"PROTECT"` - Prevent deletion
  - `"SET_NULL"` - Set to NULL (requires `null=True`)
  - `"SET_DEFAULT"` - Set to default value
  - `"DO_NOTHING"` - No action
- `related_name` - Name for reverse relation
- `null` - Allow NULL values
- `db_column` - Database column name

```python
# Access
post = await Post.objects.get(pk=1)
author = post.author  # Get related author

# Reverse access
user = await User.objects.get(pk=1)
posts = await user.posts.all()  # Get user's posts (via related_name)

# Query across relations
posts = await Post.objects.filter(author__name="John")
```

### OneToOneField

One-to-one relationship.

```python
class Profile(Model):
    user = fields.OneToOne(
        User,
        on_delete="CASCADE",
        related_name="profile",
    )
    bio = fields.TextField()
```

**Options:** Same as `ForeignKey`

```python
# Access
profile = await Profile.objects.get(user=user)
user = profile.user

# Reverse access
user = await User.objects.get(pk=1)
profile = user.profile
```

### ManyToManyField

Many-to-many relationship.

```python
class Article(Model):
    title = fields.CharField(max_length=200)
    tags = fields.ManyToMany("Tag", related_name="articles")


class Tag(Model):
    name = fields.CharField(max_length=50, unique=True)
```

**Options:**
- `to` - Related model
- `related_name` - Name for reverse relation
- `through` - Custom intermediate model
- `db_table` - Custom join table name

```python
# Add relations
article = await Article.objects.get(pk=1)
tag = await Tag.objects.get(name="python")
await article.tags.add(tag)

# Remove relations
await article.tags.remove(tag)

# Query
articles = await Article.objects.filter(tags__name="python")
```

## Common Field Options

All fields support these options:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `null` | bool | `False` | Allow NULL in database |
| `blank` | bool | `False` | Allow empty in validation |
| `default` | any | None | Default value (can be callable) |
| `primary_key` | bool | `False` | Use as primary key |
| `unique` | bool | `False` | Unique constraint |
| `index` | bool | `False` | Create database index |
| `db_column` | str | None | Database column name |
| `verbose_name` | str | None | Human-readable name |
| `help_text` | str | None | Help text |
| `choices` | list | None | Valid choices |
| `validators` | list | `[]` | Validator functions |
| `editable` | bool | `True` | Include in forms |

## Callable Defaults

Use callables for dynamic defaults:

```python
import uuid
from datetime import datetime

class Article(Model):
    # UUID generated per instance
    id = fields.UUIDField(default=uuid.uuid4)
    
    # Current time at creation
    created_at = fields.DateTimeField(default=datetime.now)
    
    # Custom function
    code = fields.CharField(max_length=10, default=generate_code)


def generate_code():
    return f"ART-{random.randint(1000, 9999)}"
```

## Choices

Restrict values to predefined choices:

```python
class Article(Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("review", "In Review"),
        ("published", "Published"),
    ]
    
    status = fields.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft",
    )
```

Access choice display value:

```python
article = await Article.objects.get(pk=1)
print(article.status)  # "draft"
print(article.get_status_display())  # "Draft"
```

**Choices are enforced:** `save()` and `objects.create()` run `full_clean()`
by default, so a value outside `choices` raises `ValidationError`. Pass
`validate=False` to opt out per call (see
[Model Validation](models.md#model-validation)).

## Custom Validators

```python
from zeeb_orm import validators

class Product(Model):
    price = fields.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            validators.MinValueValidator(0.01),
            validators.MaxValueValidator(999999.99),
        ],
    )
    
    sku = fields.CharField(
        max_length=20,
        validators=[
            validators.RegexValidator(r'^[A-Z]{3}-\d{4}$', "Invalid SKU format"),
        ],
    )
```

Available built-in validators (in `zeeb_orm.validators`):
`MinValueValidator`, `MaxValueValidator`, `MinLengthValidator`,
`MaxLengthValidator`, `RegexValidator`, `EmailValidator`, `URLValidator`,
`validate_slug`, `validate_ipv4_address`, `validate_ipv6_address`,
`validate_ipv46_address`.

Validators run during `full_clean()` — which `save()` and `create()` call by
default (`bulk_create()` only with `validate=True`). Several fields ship with
default validators: `CharField` enforces `max_length`; `EmailField`,
`URLField` and `SlugField` enforce their formats; `Positive*IntegerField`
enforce `>= 0`; `GenericIPAddressField` enforces valid addresses.

## Next Steps

- [Models](models.md) - Model definition
- [Relationships](relationships.md) - Model relationships
- [Queries](queries.md) - Querying data
