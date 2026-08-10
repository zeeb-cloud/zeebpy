# Field Types Reference

Complete reference for all Zeeb ORM field types.

## String Fields

### CharField

Fixed-length character field.

```python
name = CharField(max_length=100)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_length` | int | Required | Maximum character length |
| `min_length` | int | None | Minimum character length |
| `choices` | list | None | Allowed values |

SQL Type: `VARCHAR(max_length)`

### TextField

Unlimited text field.

```python
description = TextField()
content = TextField(default="")
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_length` | int | None | Minimum character length |

SQL Type: `TEXT`

### EmailField

Email address with validation.

```python
email = EmailField(unique=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_length` | int | 254 | Maximum length |

SQL Type: `VARCHAR(254)`

### SlugField

URL-friendly identifier.

```python
slug = SlugField(max_length=50, unique=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_length` | int | 50 | Maximum length |
| `allow_unicode` | bool | False | Allow unicode characters |

SQL Type: `VARCHAR(max_length)`

### URLField

URL with validation.

```python
website = URLField(null=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_length` | int | 2048 | Maximum length |

SQL Type: `VARCHAR(max_length)`

---

## Numeric Fields

### IntegerField

32-bit integer.

```python
count = IntegerField(default=0)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_value` | int | None | Minimum allowed value |
| `max_value` | int | None | Maximum allowed value |

SQL Type: `INTEGER`

Range: -2,147,483,648 to 2,147,483,647

### BigIntegerField

64-bit integer for large numbers.

```python
views = BigIntegerField(default=0)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_value` | int | None | Minimum allowed value |
| `max_value` | int | None | Maximum allowed value |

SQL Type: `BIGINT`

Range: -9,223,372,036,854,775,808 to 9,223,372,036,854,775,807

### SmallIntegerField

16-bit integer for small numbers.

```python
quantity = SmallIntegerField(default=1)
```

SQL Type: `SMALLINT`

Range: -32,768 to 32,767

### PositiveIntegerField

Non-negative 32-bit integer.

```python
stock = PositiveIntegerField(default=0)
```

SQL Type: `INTEGER` with CHECK constraint

Range: 0 to 2,147,483,647

### FloatField

Floating-point number.

```python
rating = FloatField(null=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_value` | float | None | Minimum allowed value |
| `max_value` | float | None | Maximum allowed value |

SQL Type: `FLOAT`

### DecimalField

Fixed-precision decimal.

```python
price = DecimalField(max_digits=10, decimal_places=2)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_digits` | int | Required | Total digits |
| `decimal_places` | int | Required | Decimal places |

SQL Type: `NUMERIC(max_digits, decimal_places)`

---

## Boolean Fields

### BooleanField

True/False field.

```python
is_active = BooleanField(default=True)
is_verified = BooleanField(default=False)
```

SQL Type: `BOOLEAN`

---

## Date/Time Fields

### DateField

Date without time.

```python
birth_date = DateField(null=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `auto_now` | bool | False | Update on every save |
| `auto_now_add` | bool | False | Set on creation |

SQL Type: `DATE`

### TimeField

Time without date.

```python
start_time = TimeField()
```

SQL Type: `TIME`

### DateTimeField

Date and time.

```python
created_at = DateTimeField(auto_now_add=True)
updated_at = DateTimeField(auto_now=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `auto_now` | bool | False | Update on every save |
| `auto_now_add` | bool | False | Set on creation |
| `timezone` | bool | True | Use timezone-aware datetime |

SQL Type: `TIMESTAMP` (with or without timezone)

### DurationField

Time duration.

```python
duration = DurationField()  # e.g., timedelta(hours=2)
```

SQL Type: `INTERVAL`

---

## UUID Fields

### UUIDField

Universally unique identifier.

```python
from uuid import uuid4

# Manual UUID
tracking_id = UUIDField(default=uuid4)

# Primary key
id = UUIDField(primary_key=True, default=uuid4)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `auto` | bool | False | Auto-generate on creation |

SQL Type: `UUID` (PostgreSQL) or `CHAR(36)` (others)

---

## Binary Fields

### BinaryField

Binary data storage.

```python
data = BinaryField()
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_length` | int | None | Maximum bytes |

SQL Type: `BYTEA` (PostgreSQL) or `BLOB`

---

## JSON Fields

### JSONField

JSON data storage.

```python
metadata = JSONField(default=dict)
tags = JSONField(default=list)
settings = JSONField(null=True)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `encoder` | class | None | Custom JSON encoder |
| `decoder` | class | None | Custom JSON decoder |

SQL Type: `JSON` or `JSONB` (PostgreSQL)

Usage:
```python
user = User(metadata={"theme": "dark", "notifications": True})
await user.save()

# Query JSON fields (PostgreSQL)
users = await User.objects.filter(metadata__contains={"theme": "dark"}).all()
```

---

## Relationship Fields

### ForeignKey

Many-to-one relationship.

```python
author = ForeignKey("User", related_name="posts")
category = ForeignKey("Category", on_delete="CASCADE")
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `to` | str/class | Required | Related model |
| `related_name` | str | Auto | Reverse relation name |
| `on_delete` | str | "CASCADE" | Delete behavior |
| `db_column` | str | Auto | Column name |

On Delete Options:
- `CASCADE` - Delete related objects
- `SET_NULL` - Set to NULL (requires `null=True`)
- `SET_DEFAULT` - Set to default value
- `PROTECT` - Prevent deletion
- `DO_NOTHING` - No action (may cause integrity errors)

### OneToOneField

One-to-one relationship.

```python
profile = OneToOneField("Profile", related_name="user")
```

Same options as ForeignKey, but creates unique constraint.

### ManyToManyField

Many-to-many relationship.

```python
tags = ManyToManyField("Tag", related_name="posts")
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `to` | str/class | Required | Related model |
| `related_name` | str | Auto | Reverse relation name |
| `through` | str | Auto | Custom junction table |

Usage:
```python
# Add
await post.tags.add(tag1, tag2)

# Remove
await post.tags.remove(tag1)

# Clear
await post.tags.clear()

# Set
await post.tags.set([tag1, tag2, tag3])

# All
tags = await post.tags.all()
```

---

## Common Field Options

All fields support these options:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `null` | bool | False | Allow NULL values |
| `blank` | bool | False | Allow empty strings |
| `default` | any | None | Default value |
| `unique` | bool | False | Unique constraint |
| `db_index` | bool | False | Create database index |
| `primary_key` | bool | False | Primary key |
| `db_column` | str | Auto | Database column name |
| `verbose_name` | str | Auto | Human-readable name |
| `help_text` | str | "" | Field description |
| `editable` | bool | True | Include in forms |
| `validators` | list | [] | Validation functions |
| `choices` | list | None | Allowed values |

### Examples

```python
# Nullable with default
middle_name = CharField(max_length=50, null=True, default=None)

# Unique with index
email = EmailField(unique=True, db_index=True)

# Choices
STATUS_CHOICES = [
    ("draft", "Draft"),
    ("published", "Published"),
    ("archived", "Archived"),
]
status = CharField(max_length=20, choices=STATUS_CHOICES, default="draft")

# Custom column name
user_type = CharField(max_length=20, db_column="type")

# Help text
password = CharField(max_length=128, help_text="Hashed password")

# Validators
def validate_positive(value):
    if value < 0:
        raise ValueError("Must be positive")

amount = IntegerField(validators=[validate_positive])
```

---

## AutoField Types

Auto-incrementing primary keys:

### AutoField

32-bit auto-increment.

```python
id = AutoField(primary_key=True)
```

### BigAutoField

64-bit auto-increment. Rendered as `INTEGER` on SQLite (only
`INTEGER PRIMARY KEY` is a rowid alias there and auto-assigns values);
`BIGSERIAL` on PostgreSQL and `BIGINT AUTO_INCREMENT` on MySQL.

```python
id = BigAutoField(primary_key=True)
```

The default primary key for a model that declares none is `UUIDAutoField`,
not an integer field.

---

## Field Inheritance

All fields inherit from `Field` base class:

```python
from zeeb_orm.models.fields import Field

class MyCustomField(Field):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    def get_column(self, name):
        from sqlalchemy import Column, String
        return Column(name, String(255), **self.column_kwargs)
```

---

## Best Practices

### 1. Use Appropriate Field Types

```python
# Good
price = DecimalField(max_digits=10, decimal_places=2)
created_at = DateTimeField(auto_now_add=True)
is_active = BooleanField(default=True)

# Avoid
price = FloatField()  # Precision issues
created_at = CharField(max_length=50)  # Use DateTimeField
is_active = IntegerField(default=1)  # Use BooleanField
```

### 2. Index Foreign Keys

```python
# Foreign keys are automatically indexed, but for frequently queried fields:
status = CharField(max_length=20, db_index=True)
category = ForeignKey("Category")  # Auto-indexed
```

### 3. Use Choices for Enums

```python
class Order(Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
    ]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
```

### 4. Default Values

```python
# Callable defaults for mutable types
metadata = JSONField(default=dict)  # Not default={}
tags = JSONField(default=list)  # Not default=[]

# Static defaults
is_active = BooleanField(default=True)
count = IntegerField(default=0)
```

## Next Steps

- [QuerySet API](queryset-api.md) - Query methods reference
- [Expressions](expressions.md) - Expression reference
- [Models](../orm/models.md) - Model definition guide
