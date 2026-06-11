# Queries

Zeeb's QuerySet provides a powerful, Django-like interface for querying data.

## Basic Queries

### Retrieving All Objects

```python
# Get all articles
articles = await Article.objects.all()

# Iterate
async for article in Article.objects.all():
    print(article.title)
```

### Filtering

```python
# Simple filter
published = await Article.objects.filter(published=True)

# Multiple conditions (AND)
recent_published = await Article.objects.filter(
    published=True,
    created_at__gte=last_week,
)

# Chain filters
articles = await Article.objects.filter(published=True).filter(author=user)
```

### Excluding

```python
# Exclude matching objects
articles = await Article.objects.exclude(status="draft")

# Combine with filter
articles = await Article.objects.filter(published=True).exclude(author=None)
```

## Lookup Expressions

Field lookups are specified as `field__lookup=value`:

### Comparison Lookups

```python
# Exact match (default)
Article.objects.filter(title="Hello")
Article.objects.filter(title__exact="Hello")

# Case-insensitive exact
Article.objects.filter(title__iexact="hello")

# Greater/less than
Article.objects.filter(views__gt=100)    # > 100
Article.objects.filter(views__gte=100)   # >= 100
Article.objects.filter(views__lt=100)    # < 100
Article.objects.filter(views__lte=100)   # <= 100
```

### String Lookups

```python
# Contains
Article.objects.filter(title__contains="python")
Article.objects.filter(title__icontains="python")  # Case-insensitive

# Starts/ends with
Article.objects.filter(title__startswith="How")
Article.objects.filter(title__istartswith="how")
Article.objects.filter(title__endswith="?")
Article.objects.filter(title__iendswith="?")

# Regex
Article.objects.filter(title__regex=r"^\d{4}")
Article.objects.filter(title__iregex=r"^[a-z]+")
```

### Collection Lookups

```python
# In list
Article.objects.filter(status__in=["draft", "review"])

# Range (inclusive)
Article.objects.filter(views__range=(100, 1000))
```

### Null Lookups

```python
# Is NULL
Article.objects.filter(published_at__isnull=True)

# Is NOT NULL
Article.objects.filter(published_at__isnull=False)
```

### Date and Time Lookups

`DateTimeField`, `DateField` and `TimeField` columns support Django-style
datetime *transforms* between the field name and the (optional) lookup:

```python
from datetime import date, time

# Exact date part (implicit __exact)
Article.objects.filter(created_at__year=2024)
Article.objects.filter(created_at__quarter=2)
Article.objects.filter(created_at__week_day=1)       # Sunday

# Transform combined with any lookup
Article.objects.filter(created_at__year__gte=2024)
Article.objects.filter(created_at__date__range=(date(2024, 1, 1), date(2024, 12, 31)))
Article.objects.filter(created_at__time__gte=time(9, 0))

# Transforms work across relations too
Article.objects.filter(author__joined_at__year=2023)
```

#### Datetime Transform Reference

| Transform | Result | Example |
|-----------|--------|---------|
| `year` | Calendar year | `created_at__year=2024` |
| `iso_year` | ISO-8601 week-numbering year | `created_at__iso_year=2024` |
| `month` | Month (1-12) | `created_at__month=5` |
| `day` | Day of month (1-31) | `created_at__day=15` |
| `week` | ISO-8601 week number (1-53) | `created_at__week=20` |
| `week_day` | Day of week, Sunday=1 .. Saturday=7 | `created_at__week_day=1` |
| `iso_week_day` | Day of week, Monday=1 .. Sunday=7 | `created_at__iso_week_day=7` |
| `quarter` | Quarter (1-4) | `created_at__quarter=2` |
| `hour` | Hour (0-23) | `created_at__hour=10` |
| `minute` | Minute (0-59) | `created_at__minute=30` |
| `second` | Second (0-59) | `created_at__second=45` |
| `date` | Date part (compare with `datetime.date`) | `created_at__date=date(2024, 5, 15)` |
| `time` | Time part (compare with `datetime.time`) | `created_at__time__gte=time(9, 0)` |

All transforms compile to dialect-specific SQL for SQLite, PostgreSQL and
MySQL (e.g. `strftime` on SQLite, `EXTRACT` on PostgreSQL) and follow
Django semantics (`week_day` counts Sunday as 1).

### Related Field Lookups

Filters, excludes, `order_by()`, `values()`/`values_list()`, `count()`,
`aggregate()`, `update()` and `delete()` can traverse relations with the
`__` separator — each hop adds a single LEFT JOIN:

```python
# Forward ForeignKey / OneToOne traversal
posts = await Post.objects.filter(author__name="Alice")
posts = await Post.objects.filter(author__age__gte=30)

# Multiple hops (forward and reverse mixed)
posts = await Post.objects.filter(author__profile__bio__contains="rust")

# Reverse traversal (related_name or default <model>_set)
authors = await Author.objects.filter(posts__views__gt=100)

# Reverse joins can yield one row per matching related object —
# use .distinct() to deduplicate (Django parity)
authors = await Author.objects.filter(posts__published=True).distinct()

# Compare a relation directly with an instance (no JOIN, uses the FK column)
posts = await Post.objects.filter(author=alice)

# Ordering and values across relations
posts = await Post.objects.order_by("author__name", "-views")
rows = await Post.objects.values("title", "author__name")
names = await Post.objects.values_list("author__name", flat=True)

# Updates and deletes with traversed filters are rewritten as
# `pk IN (SELECT pk FROM ... JOIN ...)` automatically
await Post.objects.filter(author__name="Alice").update(published=True)
await Post.objects.filter(author__name="Bob").delete()
```

A path that is used by both `select_related()` and a filter shares one JOIN:

```python
# Single LEFT OUTER JOIN, author objects hydrated on the results
posts = await Post.objects.select_related("author").filter(author__name="Alice")
```

Invalid paths raise `zeeb_orm.FieldError` with the available choices:

```python
from zeeb_orm import FieldError

try:
    await Post.objects.filter(author__bogus="x")
except FieldError as e:
    print(e)  # Cannot resolve keyword 'bogus' into field on Author. Choices are: ...
```

### Lookup Reference

| Lookup | Description | Example |
|--------|-------------|---------|
| `exact` | Exact match | `name__exact="John"` |
| `iexact` | Case-insensitive exact | `name__iexact="john"` |
| `contains` | Contains substring | `name__contains="oh"` |
| `icontains` | Case-insensitive contains | `name__icontains="OH"` |
| `startswith` | Starts with | `name__startswith="Jo"` |
| `istartswith` | Case-insensitive starts with | `name__istartswith="jo"` |
| `endswith` | Ends with | `name__endswith="hn"` |
| `iendswith` | Case-insensitive ends with | `name__iendswith="HN"` |
| `gt` | Greater than | `age__gt=18` |
| `gte` | Greater than or equal | `age__gte=18` |
| `lt` | Less than | `age__lt=65` |
| `lte` | Less than or equal | `age__lte=65` |
| `in` | In list | `status__in=["a", "b"]` |
| `range` | Between (inclusive) | `age__range=(18, 65)` |
| `isnull` | Is NULL | `email__isnull=True` |
| `regex` | Regex match | `code__regex=r"^[A-Z]"` |
| `iregex` | Case-insensitive regex | `code__iregex=r"^[a-z]"` |

## Q Objects

Q objects enable complex queries with AND, OR, and NOT:

```python
from zeeb_orm import Q

# OR queries
articles = await Article.objects.filter(
    Q(status="published") | Q(author=current_user)
)

# AND queries
articles = await Article.objects.filter(
    Q(published=True) & Q(views__gte=100)
)

# NOT queries
articles = await Article.objects.filter(
    ~Q(status="draft")
)

# Complex combinations
articles = await Article.objects.filter(
    Q(published=True) & (
        Q(author=user1) | Q(author=user2)
    )
)

# Exclude with Q
articles = await Article.objects.exclude(
    Q(status="draft") | Q(status="archived")
)
```

### Combining Q with kwargs

```python
# Q objects and keyword arguments are ANDed together
articles = await Article.objects.filter(
    Q(status="published") | Q(featured=True),
    created_at__gte=last_week,  # ANDed with the Q expression
)
```

## Ordering

```python
# Ascending
articles = await Article.objects.order_by("title")

# Descending (prefix with -)
articles = await Article.objects.order_by("-created_at")

# Multiple fields
articles = await Article.objects.order_by("-featured", "-created_at", "title")

# Clear ordering
articles = await Article.objects.order_by()  # No ordering

# Order by related field
posts = await Post.objects.order_by("author__name")

# Order by annotation
articles = await Article.objects.annotate(
    comment_count=Count("comments")
).order_by("-comment_count")
```

## Slicing and Pagination

```python
# First N results
articles = await Article.objects.all()[:10]

# Skip and limit (offset, limit)
articles = await Article.objects.all()[20:30]  # Skip 20, get 10

# Single item by index
article = await Article.objects.order_by("-views")[0]  # Most viewed
```

> **Note:** Negative indexing is not supported.

## Single Object Retrieval

### get()

Returns exactly one object. Raises exceptions for 0 or >1 results.

```python
# Get by primary key
article = await Article.objects.get(pk=article_id)
article = await Article.objects.get(id=article_id)

# Get by any field
user = await User.objects.get(email="user@example.com")

# Raises Article.DoesNotExist if not found
try:
    article = await Article.objects.get(pk=999)
except Article.DoesNotExist:
    print("Not found")

# Raises Article.MultipleObjectsReturned if multiple found
try:
    article = await Article.objects.get(published=True)
except Article.MultipleObjectsReturned:
    print("Multiple matches")
```

### first() / last()

Returns first/last object or None.

```python
# First by ordering
article = await Article.objects.order_by("created_at").first()

# Last by ordering (reverses order)
article = await Article.objects.order_by("created_at").last()

# With filter
latest = await Article.objects.filter(published=True).order_by("-created_at").first()
```

## Aggregation

### count()

```python
total = await Article.objects.count()
published = await Article.objects.filter(published=True).count()
```

### exists()

```python
has_articles = await Article.objects.filter(author=user).exists()
if has_articles:
    print("User has articles")
```

### aggregate()

Compute values over entire QuerySet:

```python
from zeeb_orm import Count, Sum, Avg, Min, Max

# Single aggregate
result = await Article.objects.aggregate(total=Count("*"))
print(result["total"])  # 42

# Multiple aggregates
stats = await Article.objects.aggregate(
    total=Count("*"),
    total_views=Sum("views"),
    avg_views=Avg("views"),
    max_views=Max("views"),
    min_views=Min("views"),
)
print(stats)
# {"total": 42, "total_views": 10000, "avg_views": 238.1, ...}

# With filter
stats = await Article.objects.filter(published=True).aggregate(
    count=Count("*"),
    avg_views=Avg("views"),
)
```

## Field Selection

### values()

Return dictionaries instead of model instances:

```python
# All fields
articles = await Article.objects.values()
# [{"id": 1, "title": "...", ...}, ...]

# Specific fields
articles = await Article.objects.values("id", "title")
# [{"id": 1, "title": "Hello"}, {"id": 2, "title": "World"}]

# With filter
titles = await Article.objects.filter(published=True).values("title")
```

### values_list()

Return tuples instead of model instances:

```python
# Tuples
articles = await Article.objects.values_list("id", "title")
# [(1, "Hello"), (2, "World")]

# Single field - flat list
titles = await Article.objects.values_list("title", flat=True)
# ["Hello", "World", ...]

# Get all IDs
ids = await Article.objects.values_list("id", flat=True)
```

### only() / defer()

Control which fields are loaded:

```python
# Load only specific fields
articles = await Article.objects.only("id", "title")

# Defer loading of specific fields
articles = await Article.objects.defer("content")  # Load all except content
```

## Distinct

```python
# Remove duplicates
categories = await Article.objects.values_list("category", flat=True).distinct()
```

## Related Object Loading

### select_related()

Fetch related objects in single query (for ForeignKey/OneToOne):

```python
# Without select_related: N+1 queries
posts = await Post.objects.all()
for post in posts:
    print(post.author.name)  # Each access hits database

# With select_related: 1 query with JOIN
posts = await Post.objects.select_related("author")
for post in posts:
    print(post.author.name)  # Already loaded

# Multiple relations
posts = await Post.objects.select_related("author", "category")

# Nested relations
comments = await Comment.objects.select_related("post__author")
```

### prefetch_related()

Fetch related objects in separate query (for ManyToMany/reverse ForeignKey):

```python
# Prefetch many-to-many
articles = await Article.objects.prefetch_related("tags")

# Prefetch reverse relation
authors = await Author.objects.prefetch_related("posts")

# Custom prefetch queryset
from zeeb_orm import Prefetch

authors = await Author.objects.prefetch_related(
    Prefetch("posts", queryset=Post.objects.filter(published=True))
)
```

## CRUD Operations

### create()

```python
article = await Article.objects.create(
    title="New Article",
    content="Content here",
    author=user,
)
```

### get_or_create()

```python
user, created = await User.objects.get_or_create(
    email="user@example.com",
    defaults={"name": "New User"},
)
if created:
    print("Created new user")
```

### update_or_create()

```python
user, created = await User.objects.update_or_create(
    email="user@example.com",
    defaults={"last_login": datetime.now()},
)
```

### update()

Bulk update matching objects:

```python
# Update all matching
count = await Article.objects.filter(author=user).update(published=True)
print(f"Published {count} articles")

# Update with F expressions
from zeeb_orm import F
await Article.objects.filter(pk=article_id).update(views=F("views") + 1)
```

### delete()

Bulk delete matching objects:

```python
count = await Article.objects.filter(status="archived").delete()
print(f"Deleted {count} articles")
```

### bulk_create()

Efficiently create multiple objects:

```python
articles = [
    Article(title="Article 1", content="..."),
    Article(title="Article 2", content="..."),
    Article(title="Article 3", content="..."),
]
created = await Article.objects.bulk_create(articles)
```

### bulk_update()

Efficiently update multiple objects:

```python
articles = await Article.objects.filter(status="draft")
for article in articles:
    article.status = "review"

await Article.objects.bulk_update(articles, ["status"])
```

## Raw SQL

```python
# Raw query
articles = await Article.objects.raw(
    "SELECT * FROM articles WHERE views > ?",
    [100]
)
```

## Using Different Databases

```python
# Query specific database
articles = await Article.objects.using("replica").all()

# Create on specific database
article = await Article.objects.using("primary").create(...)
```

## Transactions

Use `atomic()` to group operations into a single transaction that commits or rolls back together:

```python
from zeeb_orm import atomic

# All operations commit together or roll back on error
async with atomic():
    user = await User.objects.create(name="John", email="john@example.com")
    await Profile.objects.create(user=user, bio="Hello")
    await AuditLog.objects.create(action="user_created", user=user)

# If any operation fails, all are rolled back
async with atomic():
    await Account.objects.filter(pk=from_account.pk).update(balance=F("balance") - 100)
    await Account.objects.filter(pk=to_account.pk).update(balance=F("balance") + 100)
    # If second update fails, first update is also rolled back
```

Operations inside `atomic()` share the same database session, ensuring proper transaction isolation.

### on_commit

Register a callback that runs only after the transaction successfully commits:

```python
from zeeb_orm.db.transaction import on_commit

async with atomic():
    user = await User.objects.create(name="John", email="john@example.com")
    on_commit(lambda: send_welcome_email(user.email))
    # If the transaction rolls back, the email is never sent
```

## QuerySet Chaining

QuerySets are lazy and chainable:

```python
# Nothing executed yet
qs = Article.objects.filter(published=True)
qs = qs.filter(views__gte=100)
qs = qs.exclude(author=None)
qs = qs.order_by("-created_at")
qs = qs[:10]

# Query executed here
articles = await qs
```

## Next Steps

- [Expressions](expressions.md) - F, Q, annotations, functions
- [Relationships](relationships.md) - Query across relationships
- [QuerySet API Reference](../reference/queryset-api.md) - Complete method reference
