# Expressions

Expressions allow you to perform computations and reference fields in queries.

## F Expressions

`F()` references the value of a model field, allowing database-level operations without loading data into Python.

### Basic Usage

```python
from zeeb_orm import F

# Compare two fields
articles = await Article.objects.filter(views__gt=F("min_views"))

# Reference in updates
await Article.objects.filter(pk=article_id).update(views=F("views") + 1)

# Arithmetic operations
await Product.objects.update(price=F("price") * 1.1)  # 10% price increase
```

### Traversing Relations and Dates

`F()` also resolves a related field or a datetime transform:

```python
# A related field across a join
await Author.objects.annotate(top_post_views=Max(F("posts__views")))

# A datetime component
await Article.objects.annotate(year=F("created_at__year")).order_by("-year")
```

Both need a join context, which only `annotate()`, `aggregate()` and
`order_by()` provide. `F("posts__views")` inside a plain `filter()` raises
`FieldError` — annotate it to a name first, then filter on that name.

### Arithmetic Operators

```python
# Addition
F("price") + 10
F("price") + F("tax")

# Subtraction
F("price") - F("discount")

# Multiplication
F("quantity") * F("unit_price")

# Division
F("total") / F("count")

# Modulo
F("number") % 2
```

### Use Cases

```python
# Increment counter without race conditions
await Article.objects.filter(pk=id).update(views=F("views") + 1)

# Compare fields
await Product.objects.filter(stock__lt=F("min_stock"))

# Calculate profit margin
products = await Product.objects.annotate(
    profit=F("price") - F("cost")
)

# Transfer between fields
await Account.objects.filter(pk=from_id).update(balance=F("balance") - amount)
await Account.objects.filter(pk=to_id).update(balance=F("balance") + amount)
```

## Q Objects

`Q()` enables complex query logic with AND, OR, and NOT operations.

### Basic Usage

```python
from zeeb_orm import Q

# OR condition
Q(status="published") | Q(status="featured")

# AND condition
Q(published=True) & Q(views__gte=100)

# NOT condition
~Q(status="draft")
```

### Complex Queries

```python
# Nested conditions
articles = await Article.objects.filter(
    Q(published=True) & (
        Q(category="tech") | Q(category="science")
    )
)

# Multiple OR conditions
articles = await Article.objects.filter(
    Q(author=user1) | Q(author=user2) | Q(author=user3)
)

# Exclude with complex condition
articles = await Article.objects.exclude(
    Q(status="draft") & Q(created_at__lt=cutoff_date)
)
```

### Dynamic Queries

```python
# Build query dynamically
filters = Q()
if status:
    filters &= Q(status=status)
if author:
    filters &= Q(author=author)
if search:
    filters &= Q(title__icontains=search) | Q(content__icontains=search)

articles = await Article.objects.filter(filters)
```

## Annotations

`annotate()` adds computed fields to each object in a QuerySet.

### Basic Annotations

```python
from zeeb_orm import F, Count, Sum, Avg

# Add computed field
products = await Product.objects.annotate(
    profit=F("price") - F("cost")
)
for product in products:
    print(f"{product.name}: ${product.profit}")

# Count related objects
authors = await Author.objects.annotate(
    post_count=Count("posts")
)
for author in authors:
    print(f"{author.name}: {author.post_count} posts")
```

### Filter and Order by Annotation

```python
# Filter by annotation
prolific_authors = await Author.objects.annotate(
    post_count=Count("posts")
).filter(post_count__gte=10)

# Order by annotation
top_authors = await Author.objects.annotate(
    post_count=Count("posts")
).order_by("-post_count")
```

Filtering on an **aggregate** annotation compiles to `GROUP BY` + `HAVING`,
not `WHERE` — the grouping keys are the plain (non-aggregate) columns being
selected. Filtering on a non-aggregate annotation still compiles to `WHERE`.

Because the two land in different SQL clauses, they cannot be mixed inside a
single `OR` or `NOT`:

```python
# OK — separate conditions, one HAVING and one WHERE
Author.objects.annotate(post_count=Count("posts")).filter(
    post_count__gte=10, is_active=True,
)

# NotSupportedError — an aggregate and a plain field inside one OR
Author.objects.annotate(post_count=Count("posts")).filter(
    Q(post_count__gte=10) | Q(is_active=True)
)
```

### Multiple Annotations

```python
stats = await Author.objects.annotate(
    post_count=Count("posts"),
    total_views=Sum("posts__views"),
    avg_views=Avg("posts__views"),
)
```

## Aggregate Functions

### Count

```python
from zeeb_orm import Count

# Count all
await Article.objects.aggregate(total=Count("*"))

# Count distinct
await Article.objects.aggregate(authors=Count("author", distinct=True))

# Count with filter
await Article.objects.filter(published=True).aggregate(count=Count("*"))
```

### Sum

```python
from zeeb_orm import Sum

# Sum field values
await Order.objects.aggregate(total_revenue=Sum("amount"))

# Sum with filter
await Order.objects.filter(status="completed").aggregate(revenue=Sum("amount"))
```

### Avg

```python
from zeeb_orm import Avg

# Average value
await Product.objects.aggregate(avg_price=Avg("price"))

# Average with annotation
await Author.objects.annotate(avg_post_views=Avg("posts__views"))
```

### Min / Max

```python
from zeeb_orm import Min, Max

await Product.objects.aggregate(
    cheapest=Min("price"),
    most_expensive=Max("price"),
)
```

### StdDev / Variance

```python
from zeeb_orm import StdDev, Variance

await Product.objects.aggregate(
    price_stddev=StdDev("price"),
    price_variance=Variance("price"),
)
```

## Conditional Expressions

### Case / When

```python
from zeeb_orm import Case, When, Value

# Simple case
products = await Product.objects.annotate(
    price_tier=Case(
        When(price__gte=1000, then=Value("Premium")),
        When(price__gte=100, then=Value("Standard")),
        default=Value("Budget"),
    )
)

# Case with multiple conditions
products = await Product.objects.annotate(
    discount=Case(
        When(category="electronics", stock__gt=100, then=Value(0.2)),
        When(category="electronics", then=Value(0.1)),
        default=Value(0),
    )
)

# Use in filter
expensive = await Product.objects.annotate(
    tier=Case(
        When(price__gte=1000, then=Value("premium")),
        default=Value("standard"),
    )
).filter(tier="premium")
```

`When()` fails loudly rather than silently skipping a condition it cannot
resolve: an unknown field or an unknown lookup raises `FieldError`, and a
positional argument that is not a `Q` raises `TypeError`. Traversing a relation
inside `When`/`Case` also needs a join context, so it raises where none exists —
the same rule as `F()`.

The `filter=Q(...)` argument on an aggregate behaves the same way:

```python
await Author.objects.annotate(
    published_count=Count("posts", filter=Q(posts__published=True)),
)
```

### Coalesce

Return first non-null value:

```python
from zeeb_orm import Coalesce, Value

# Use default if null
products = await Product.objects.annotate(
    display_name=Coalesce("nickname", "name", Value("Unknown"))
)

# Use in filter — annotate first, then filter on the alias
await Product.objects.annotate(
    effective_price=Coalesce("sale_price", "price"),
).filter(effective_price__lt=100)
```

## String Functions

### Concat

```python
from zeeb_orm import Concat, Value

users = await User.objects.annotate(
    full_name=Concat("first_name", Value(" "), "last_name")
)
```

### Lower / Upper

```python
from zeeb_orm import Lower, Upper

users = await User.objects.annotate(
    email_lower=Lower("email"),
    name_upper=Upper("name"),
)
```

### Length

```python
from zeeb_orm import Length

# Filter by string length
short_titles = await Article.objects.annotate(
    title_length=Length("title")
).filter(title_length__lt=50)
```

### Substr

```python
from zeeb_orm import Substr

# Extract substring
users = await User.objects.annotate(
    initials=Substr("name", 1, 1)  # First character
)
```

### Trim

```python
from zeeb_orm import Trim

users = await User.objects.annotate(
    clean_name=Trim("name")
)
```

## Math Functions

### Abs

```python
from zeeb_orm import Abs

# Absolute value
accounts = await Account.objects.annotate(
    abs_balance=Abs("balance")
)
```

### Round / Ceil / Floor

```python
from zeeb_orm import Round, Ceil, Floor

products = await Product.objects.annotate(
    rounded_price=Round("price", 0),
    ceil_price=Ceil("price"),
    floor_price=Floor("price"),
)
```

### Greatest / Least

```python
from zeeb_orm import Greatest, Least

products = await Product.objects.annotate(
    best_price=Least("price", "sale_price", "clearance_price"),
    highest_price=Greatest("price", "original_price"),
)
```

## Date/Time Functions

### Now

```python
from zeeb_orm import Now

# Compare with current time
recent = await Article.objects.filter(created_at__gte=Now() - timedelta(days=7))
```

### Extract

```python
from zeeb_orm import Extract

# Extract date parts
articles = await Article.objects.annotate(
    year=Extract("created_at", "year"),
    month=Extract("created_at", "month"),
    day=Extract("created_at", "day"),
    hour=Extract("created_at", "hour"),
)

# Group by month
monthly = await Article.objects.annotate(
    month=Extract("created_at", "month")
).values("month").annotate(count=Count("*"))
```

### Trunc Functions

```python
from zeeb_orm import TruncDate, TruncMonth, TruncYear

# Truncate to date (remove time)
articles = await Article.objects.annotate(
    date_only=TruncDate("created_at")
)

# Group by month
monthly_stats = await Article.objects.annotate(
    month=TruncMonth("created_at")
).values("month").annotate(count=Count("*"))

# Group by year
yearly_stats = await Article.objects.annotate(
    year=TruncYear("created_at")
).values("year").annotate(total_views=Sum("views"))
```

## Subqueries

### Subquery

```python
from zeeb_orm import Subquery, OuterRef

# Get latest comment date for each post
latest_comment = Comment.objects.filter(
    post_id=OuterRef("id")
).order_by("-created_at").values("created_at")[:1]

posts = await Post.objects.annotate(
    latest_comment_date=Subquery(latest_comment)
)
```

### OuterRef

Reference the outer query in a subquery:

```python
from zeeb_orm import OuterRef

# Posts with comments from same author
related_comments = Comment.objects.filter(
    post_id=OuterRef("id"),
    author_id=OuterRef("author_id"),
)
```

### Exists

Check for existence of related objects:

```python
from zeeb_orm import Exists, OuterRef

# Posts that have comments
has_comments = Comment.objects.filter(post_id=OuterRef("id"))

posts = await Post.objects.annotate(
    has_comments=Exists(has_comments)
).filter(has_comments=True)
```

## Value Expression

Wrap Python values for use in expressions:

```python
from zeeb_orm import Value

# Use literal value in annotation
products = await Product.objects.annotate(
    currency=Value("USD"),
    tax_rate=Value(0.1),
)

# In Case/When
tier = Case(
    When(price__gte=1000, then=Value("premium")),
    default=Value("standard"),
)
```

## Combining Expressions

```python
from zeeb_orm import F, Value, Case, When, Coalesce, Round

# Complex calculation
products = await Product.objects.annotate(
    # Calculate discounted price
    effective_price=Coalesce("sale_price", "price"),
    
    # Calculate savings percentage
    savings_pct=Case(
        When(
            sale_price__isnull=False,
            then=Round((F("price") - F("sale_price")) / F("price") * 100, 1)
        ),
        default=Value(0),
    ),
    
    # Price tier
    tier=Case(
        When(price__gte=1000, then=Value("Premium")),
        When(price__gte=100, then=Value("Standard")),
        default=Value("Budget"),
    ),
)
```

## Window Functions

Window functions compute values over a set of rows related to the current
row, without collapsing them like aggregates do.

> **Database requirements:** SQLite >= 3.25, MySQL >= 8.0, any supported
> PostgreSQL version. No runtime version check is performed — older
> databases fail at execution time.

### Window

Wrap a window-function expression in an `OVER (...)` clause with optional
`partition_by` and `order_by` (single field name, expression, or list;
`"-"` prefix means descending):

```python
from zeeb_orm import Window, Rank, RowNumber, DenseRank

posts = await Post.objects.annotate(
    rank=Window(Rank(), partition_by="author_id", order_by="-score"),
    rn=Window(RowNumber(), order_by="-score"),
)
```

### Available Window Functions

| Expression | SQL |
|------------|-----|
| `RowNumber()` | `ROW_NUMBER()` |
| `Rank()` | `RANK()` |
| `DenseRank()` | `DENSE_RANK()` |
| `PercentRank()` | `PERCENT_RANK()` |
| `CumeDist()` | `CUME_DIST()` |
| `Lag(expr, offset=1, default=None)` | `LAG(expr, offset, default)` |
| `Lead(expr, offset=1, default=None)` | `LEAD(expr, offset, default)` |
| `FirstValue(expr)` | `FIRST_VALUE(expr)` |
| `LastValue(expr)` | `LAST_VALUE(expr)` |
| `Ntile(num_buckets)` | `NTILE(n)` |

```python
from zeeb_orm import Lag, Lead, FirstValue, Ntile

players = await Player.objects.annotate(
    prev_score=Window(Lag("score"), partition_by="team", order_by="score"),
    next_score=Window(Lead("score"), partition_by="team", order_by="score"),
    top_player=Window(FirstValue("name"), partition_by="team", order_by="-score"),
    quartile=Window(Ntile(4), order_by="-score"),
)
```

### Window Annotations in Filters

SQL forbids window functions in `WHERE` clauses. Referencing a `Window`
annotation in `filter()` or `exclude()` raises `FieldError`:

```python
# Raises FieldError — filter on a subquery instead
Post.objects.annotate(rn=Window(RowNumber(), order_by="-score")).filter(rn=1)
```

## Cast

Convert an expression to another type with SQL `CAST`. The target type is
given as a zeeb_orm field instance or class:

```python
from zeeb_orm import Cast, fields

products = await Product.objects.annotate(
    price_int=Cast("price", fields.IntegerField),
    score_text=Cast("score", fields.CharField(max_length=20)),
)
```

## StringAgg / GroupConcat

Aggregate values into a single delimited string. Compiles to the right
function per backend: `string_agg(expr, 'd')` on PostgreSQL,
`GROUP_CONCAT(expr SEPARATOR 'd')` on MySQL and `group_concat(expr, 'd')`
on SQLite. `GroupConcat` is an alias for `StringAgg`.

```python
from zeeb_orm import StringAgg

result = await Post.objects.filter(author=author).aggregate(
    titles=StringAgg("title", delimiter=", ")
)
# {"titles": "First Post, Second Post"}

# Deduplicate values first
result = await Post.objects.aggregate(
    cats=StringAgg("category", distinct=True)
)
```

Notes:

- The delimiter is inlined as an SQL string literal (MySQL's `SEPARATOR`
  does not accept bind parameters); single quotes are escaped.
- On SQLite, `distinct=True` falls back to the default `","` delimiter
  (SQLite forbids a custom delimiter with `DISTINCT`).

## Next Steps

- [Queries](queries.md) - Basic querying
- [QuerySet API Reference](../reference/queryset-api.md) - Complete API reference
- [Expression Reference](../reference/expressions.md) - All expressions
