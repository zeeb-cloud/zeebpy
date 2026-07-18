# QuerySet API Reference

Complete reference for QuerySet methods.

## Creating QuerySets

```python
# From model's objects manager
queryset = User.objects.all()

# Chain methods
queryset = User.objects.filter(is_active=True).order_by("-created_at")
```

---

## Filtering Methods

### filter(\*\*kwargs)

Returns objects matching conditions.

```python
# Single condition
users = await User.objects.filter(is_active=True).all()

# Multiple conditions (AND)
users = await User.objects.filter(is_active=True, role="admin").all()

# Chained filters (AND)
users = await User.objects.filter(is_active=True).filter(role="admin").all()
```

### exclude(\*\*kwargs)

Returns objects NOT matching conditions.

```python
# Exclude inactive
users = await User.objects.exclude(is_active=False).all()

# Combine with filter
users = await User.objects.filter(role="user").exclude(is_banned=True).all()
```

### filter(Q(...))

Complex queries with Q objects.

```python
from zeeb_orm import Q

# OR condition
users = await User.objects.filter(
    Q(role="admin") | Q(role="moderator")
).all()

# AND condition
users = await User.objects.filter(
    Q(is_active=True) & Q(email_verified=True)
).all()

# NOT condition
users = await User.objects.filter(~Q(is_banned=True)).all()

# Complex
users = await User.objects.filter(
    Q(is_active=True) & (Q(role="admin") | Q(role="moderator"))
).all()
```

---

## Field Lookups

Use `field__lookup=value` syntax:

| Lookup | Description | Example |
|--------|-------------|---------|
| `exact` | Exact match (default) | `name__exact="John"` |
| `iexact` | Case-insensitive exact | `name__iexact="john"` |
| `contains` | Contains substring | `name__contains="oh"` |
| `icontains` | Case-insensitive contains | `name__icontains="OH"` |
| `startswith` | Starts with | `name__startswith="J"` |
| `istartswith` | Case-insensitive starts with | `name__istartswith="j"` |
| `endswith` | Ends with | `name__endswith="n"` |
| `iendswith` | Case-insensitive ends with | `name__iendswith="N"` |
| `in` | In list | `status__in=["active", "pending"]` |
| `gt` | Greater than | `age__gt=18` |
| `gte` | Greater than or equal | `age__gte=18` |
| `lt` | Less than | `age__lt=65` |
| `lte` | Less than or equal | `age__lte=65` |
| `range` | Between range | `age__range=(18, 65)` |
| `isnull` | Is NULL | `deleted_at__isnull=True` |
| `date` | Date part | `created_at__date="2024-01-15"` |
| `year` | Year part | `created_at__year=2024` |
| `month` | Month part | `created_at__month=1` |
| `day` | Day part | `created_at__day=15` |
| `week_day` | Day of week | `created_at__week_day=1` |

### Examples

```python
# Numeric
products = await Product.objects.filter(price__gte=100, price__lte=500).all()

# String
users = await User.objects.filter(email__icontains="@gmail.com").all()

# Date
posts = await Post.objects.filter(created_at__year=2024).all()

# List
orders = await Order.objects.filter(status__in=["pending", "processing"]).all()

# NULL
profiles = await Profile.objects.filter(bio__isnull=False).all()
```

---

## Retrieval Methods

### all()

Execute query and return all results as list.

```python
users = await User.objects.all()
users = await User.objects.filter(is_active=True).all()
```

### get(\*\*kwargs)

Return single object or raise exception.

```python
user = await User.objects.get(id=1)
user = await User.objects.get(email="user@example.com")
```

Raises:
- `DoesNotExist` - No matching object
- `MultipleObjectsReturned` - Multiple matches

### first()

Return first object or None.

```python
user = await User.objects.filter(is_active=True).order_by("created_at").first()
```

### last()

Return last object or None. Reverses the effective ordering (explicit
`order_by`, then `Meta.ordering`, then the primary key), so it is
deterministic and mirror-images `first()` even on unordered querysets.

```python
user = await User.objects.order_by("created_at").last()
```

### exists()

Return True if any results exist.

```python
has_admins = await User.objects.filter(role="admin").exists()
```

### count()

Return count of results.

```python
total = await User.objects.count()
active = await User.objects.filter(is_active=True).count()
```

---

## Ordering Methods

### order_by(\*fields)

Order results by fields.

```python
# Ascending
users = await User.objects.order_by("created_at").all()

# Descending (prefix with -)
users = await User.objects.order_by("-created_at").all()

# Multiple fields
users = await User.objects.order_by("role", "-created_at").all()
```

### order_by with F expressions

```python
from zeeb_orm import F

# Order by expression
products = await Product.objects.annotate(
    profit=F("price") - F("cost")
).order_by("-profit").all()
```

---

## Slicing Methods

### \[start:end\]

Slice results (LIMIT/OFFSET).

```python
# First 10
users = await User.objects.all()[:10]

# Skip 10, take 10
users = await User.objects.all()[10:20]

# Single item by index
user = await User.objects.order_by("created_at").all()[0]
```

### limit(n)

Limit results.

```python
users = await User.objects.limit(10).all()
```

### offset(n)

Skip results.

```python
users = await User.objects.offset(10).limit(10).all()
```

---

## Annotation Methods

### annotate(\*\*kwargs)

Add computed fields.

```python
from zeeb_orm import Count, Sum, Avg, F

# Aggregate
users = await User.objects.annotate(
    post_count=Count("posts")
).all()

# Expression
products = await Product.objects.annotate(
    profit=F("price") - F("cost")
).all()

# Access annotation
for product in products:
    print(product.profit)
```

### aggregate(\*\*kwargs)

Return aggregated values (dict, not queryset).

```python
from zeeb_orm import Count, Sum, Avg, Max, Min

result = await Product.objects.aggregate(
    total=Sum("price"),
    average=Avg("price"),
    count=Count("id"),
    highest=Max("price"),
    lowest=Min("price"),
)
# {'total': 5000, 'average': 50, 'count': 100, 'highest': 200, 'lowest': 10}
```

---

## Value Methods

### values(\*fields)

Return dictionaries instead of model instances.

```python
# All fields
users = await User.objects.values().all()
# [{'id': 1, 'name': 'John', 'email': '...'}, ...]

# Specific fields
users = await User.objects.values("id", "name").all()
# [{'id': 1, 'name': 'John'}, ...]
```

### values_list(\*fields, flat=False)

Return tuples instead of dictionaries. With no fields, every model field
is returned in declaration order (relation fields as their `<name>_id`
column). `flat=True` requires exactly one field.

```python
# Tuples
users = await User.objects.values_list("id", "name").all()
# [(1, 'John'), (2, 'Jane'), ...]

# Flat list (single field)
ids = await User.objects.values_list("id", flat=True).all()
# [1, 2, 3, ...]
```

---

## Field Selection Methods

### only(\*fields)

Load only the specified fields (plus PK, which is always included).

```python
# Load only name and email
users = await User.objects.only("name", "email").all()
```

### defer(\*fields)

Load all fields except the specified ones (PK is never deferred).

```python
# Load everything except the large content field
articles = await Article.objects.defer("content").all()
```

---

## Relationship Methods

### select_related(\*fields)

Join related objects (foreign keys).

```python
# Single relation
posts = await Post.objects.select_related("author").all()
for post in posts:
    print(post.author.name)  # No additional query

# Multiple relations
posts = await Post.objects.select_related("author", "category").all()

# Nested relations
comments = await Comment.objects.select_related("post__author").all()
```

### prefetch_related(\*fields)

Prefetch related objects (many-to-many, reverse foreign keys).

```python
# Prefetch many-to-many
posts = await Post.objects.prefetch_related("tags").all()
for post in posts:
    for tag in post.tags:  # No additional query
        print(tag.name)

# Prefetch reverse relation
users = await User.objects.prefetch_related("posts").all()
for user in users:
    for post in user.posts:  # No additional query
        print(post.title)
```

---

## Distinct

### distinct()

Return distinct results (row-level de-duplication). Field arguments are
not supported and raise `NotSupportedError` — de-duplicate on specific
columns with `values()`/`values_list()` + `distinct()` instead.

```python
# All unique rows
users = await User.objects.distinct().all()

# Unique values of one column
emails = await User.objects.values_list("email", flat=True).distinct()
```

---

## Update Methods

### update(\*\*kwargs)

Bulk update matching records.

```python
# Update all
count = await User.objects.update(is_active=True)

# Update filtered
count = await User.objects.filter(is_banned=True).update(is_active=False)

# With F expressions
count = await Product.objects.update(price=F("price") * 1.1)
```

### bulk_update(objects, fields)

Update multiple objects efficiently.

```python
users = await User.objects.all()
for user in users:
    user.login_count += 1

await User.objects.bulk_update(users, ["login_count"])
```

---

## Delete Methods

### delete()

Delete matching records.

```python
# Delete all (careful!)
count = await User.objects.delete()

# Delete filtered
count = await User.objects.filter(is_banned=True).delete()
```

---

## Creation Methods

### create(\*\*kwargs)

Create and return new object.

```python
user = await User.objects.create(
    name="John",
    email="john@example.com"
)
```

### bulk_create(objects)

Create multiple objects efficiently.

```python
users = await User.objects.bulk_create([
    User(name="John", email="john@example.com"),
    User(name="Jane", email="jane@example.com"),
])
```

### get_or_create(\*\*kwargs, defaults=None)

Get existing or create new.

```python
user, created = await User.objects.get_or_create(
    email="john@example.com",
    defaults={"name": "John"}
)
# created is True if new object was created
```

### update_or_create(\*\*kwargs, defaults=None)

Update existing or create new.

```python
user, created = await User.objects.update_or_create(
    email="john@example.com",
    defaults={"name": "John Doe", "is_active": True}
)
```

---

## Raw Queries

### raw(sql, params)

Execute raw SQL.

```python
users = await User.objects.raw(
    "SELECT * FROM users WHERE created_at > :date",
    {"date": "2024-01-01"}
)
```

---

## QuerySet Methods Summary

| Category | Methods |
|----------|---------|
| **Filtering** | `filter()`, `exclude()` |
| **Retrieval** | `all()`, `get()`, `first()`, `last()`, `exists()`, `count()` |
| **Ordering** | `order_by()` |
| **Slicing** | `limit()`, `offset()`, `[start:end]` |
| **Annotation** | `annotate()`, `aggregate()` |
| **Values** | `values()`, `values_list()` |
| **Field Selection** | `only()`, `defer()` |
| **Relations** | `select_related()`, `prefetch_related()` |
| **Distinct** | `distinct()` |
| **Update** | `update()`, `bulk_update()` |
| **Delete** | `delete()` |
| **Create** | `create()`, `bulk_create()`, `get_or_create()`, `update_or_create()` |
| **Raw** | `raw()` |

---

## QuerySet Chaining

QuerySets are lazy and chainable:

```python
# Build query
queryset = User.objects
queryset = queryset.filter(is_active=True)
queryset = queryset.exclude(is_banned=True)
queryset = queryset.order_by("-created_at")
queryset = queryset.limit(10)

# Execute when needed
users = await queryset.all()
```

## Next Steps

- [Expressions](expressions.md) - Expression reference
- [Field Types](field-types.md) - Field type reference
- [Queries](../orm/queries.md) - Query guide
