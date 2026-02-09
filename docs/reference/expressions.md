# Expressions Reference

Complete reference for all expression classes.

## F Expressions

Reference a field value in queries and annotations.

```python
from zeeb_orm import F

# Compare fields
posts = await Post.objects.filter(views__gt=F("likes") * 10).all()

# Update with field reference
await Product.objects.update(price=F("price") * 1.1)

# Annotation
products = await Product.objects.annotate(
    profit=F("price") - F("cost")
).all()
```

### Arithmetic Operations

```python
F("price") + 10        # Add constant
F("price") - F("cost") # Subtract fields
F("price") * 1.1       # Multiply
F("price") / 2         # Divide
F("quantity") % 10     # Modulo
```

---

## Q Objects

Complex query conditions with AND, OR, NOT.

```python
from zeeb_orm import Q

# AND (default)
Q(is_active=True, role="admin")
Q(is_active=True) & Q(role="admin")

# OR
Q(role="admin") | Q(role="moderator")

# NOT
~Q(is_banned=True)

# Complex
Q(is_active=True) & (Q(role="admin") | Q(role="moderator")) & ~Q(is_banned=True)
```

### Combining Q Objects

```python
# Build incrementally
conditions = Q(is_active=True)
if role:
    conditions &= Q(role=role)
if not include_banned:
    conditions &= ~Q(is_banned=True)

users = await User.objects.filter(conditions).all()
```

---

## Aggregate Functions

### Count

Count rows or non-null values.

```python
from zeeb_orm import Count

# Count all
result = await User.objects.aggregate(total=Count("id"))

# Count with filter
result = await User.objects.aggregate(
    active=Count("id", filter=Q(is_active=True))
)

# Distinct count
result = await Post.objects.aggregate(
    unique_authors=Count("author_id", distinct=True)
)

# In annotation
users = await User.objects.annotate(
    post_count=Count("posts")
).all()
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | str | Field to count |
| `distinct` | bool | Count distinct values |
| `filter` | Q | Filter condition |

### Sum

Sum numeric values.

```python
from zeeb_orm import Sum

result = await Order.objects.aggregate(
    total_revenue=Sum("amount")
)

# With filter
result = await Order.objects.aggregate(
    completed_revenue=Sum("amount", filter=Q(status="completed"))
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | str | Field to sum |
| `filter` | Q | Filter condition |

### Avg

Calculate average.

```python
from zeeb_orm import Avg

result = await Product.objects.aggregate(
    avg_price=Avg("price")
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | str | Field to average |
| `filter` | Q | Filter condition |

### Max

Maximum value.

```python
from zeeb_orm import Max

result = await Product.objects.aggregate(
    highest_price=Max("price")
)
```

### Min

Minimum value.

```python
from zeeb_orm import Min

result = await Product.objects.aggregate(
    lowest_price=Min("price")
)
```

---

## Conditional Expressions

### Case / When

Conditional logic in queries.

```python
from zeeb_orm import Case, When, Value

# Simple case
products = await Product.objects.annotate(
    price_category=Case(
        When(price__lt=50, then=Value("cheap")),
        When(price__lt=200, then=Value("medium")),
        default=Value("expensive"),
    )
).all()

# With expressions
products = await Product.objects.annotate(
    discounted_price=Case(
        When(is_sale=True, then=F("price") * 0.8),
        default=F("price"),
    )
).all()
```

### When Parameters

```python
When(condition, then=result)

# Keyword conditions
When(is_active=True, then=Value("Active"))
When(price__gte=100, then=Value("Premium"))

# Q object condition
When(Q(is_active=True) & Q(role="admin"), then=Value("Admin"))
```

### Coalesce

Return first non-null value.

```python
from zeeb_orm import Coalesce

users = await User.objects.annotate(
    display_name=Coalesce(F("nickname"), F("name"), Value("Anonymous"))
).all()
```

---

## String Functions

### Concat

Concatenate strings.

```python
from zeeb_orm import Concat, Value

users = await User.objects.annotate(
    full_name=Concat(F("first_name"), Value(" "), F("last_name"))
).all()
```

### Lower / Upper

Convert case.

```python
from zeeb_orm import Lower, Upper

users = await User.objects.annotate(
    email_lower=Lower(F("email")),
    name_upper=Upper(F("name")),
).all()
```

### Length

String length.

```python
from zeeb_orm import Length

users = await User.objects.annotate(
    name_length=Length(F("name"))
).filter(name_length__gt=10).all()
```

### Trim

Remove whitespace.

```python
from zeeb_orm import Trim, LTrim, RTrim

users = await User.objects.annotate(
    clean_name=Trim(F("name")),
    left_trimmed=LTrim(F("name")),
    right_trimmed=RTrim(F("name")),
).all()
```

### Substr

Extract substring.

```python
from zeeb_orm import Substr

users = await User.objects.annotate(
    initials=Substr(F("name"), 1, 1)  # First character
).all()
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | F/str | Source string |
| `start` | int | Start position (1-indexed) |
| `length` | int | Number of characters |

---

## Date Functions

### Now

Current timestamp.

```python
from zeeb_orm import Now

orders = await Order.objects.filter(
    due_date__lt=Now()
).all()
```

### Extract

Extract date/time part.

```python
from zeeb_orm import Extract

users = await User.objects.annotate(
    birth_year=Extract(F("birth_date"), "year"),
    birth_month=Extract(F("birth_date"), "month"),
    birth_day=Extract(F("birth_date"), "day"),
).all()
```

| Part | Description |
|------|-------------|
| `year` | Year |
| `month` | Month (1-12) |
| `day` | Day of month (1-31) |
| `hour` | Hour (0-23) |
| `minute` | Minute (0-59) |
| `second` | Second (0-59) |
| `week` | Week of year |
| `dow` | Day of week (0-6) |

### TruncDate / TruncMonth / TruncYear

Truncate datetime to date/month/year.

```python
from zeeb_orm import TruncDate, TruncMonth, TruncYear

# Group by date
orders = await Order.objects.annotate(
    order_date=TruncDate(F("created_at"))
).values("order_date").annotate(
    count=Count("id")
).all()

# Group by month
orders = await Order.objects.annotate(
    order_month=TruncMonth(F("created_at"))
).values("order_month").annotate(
    total=Sum("amount")
).all()

# Group by year
orders = await Order.objects.annotate(
    order_year=TruncYear(F("created_at"))
).values("order_year").annotate(
    total=Sum("amount")
).all()
```

---

## Math Functions

### Abs

Absolute value.

```python
from zeeb_orm import Abs

orders = await Order.objects.annotate(
    variance=Abs(F("actual") - F("expected"))
).all()
```

### Round

Round to decimal places.

```python
from zeeb_orm import Round

products = await Product.objects.annotate(
    rounded_price=Round(F("price"), 2)
).all()
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | F/str | Value to round |
| `precision` | int | Decimal places (default: 0) |

### Ceil / Floor

Round up/down to integer.

```python
from zeeb_orm import Ceil, Floor

products = await Product.objects.annotate(
    ceil_price=Ceil(F("price")),
    floor_price=Floor(F("price")),
).all()
```

### Greatest / Least

Maximum/minimum of multiple values.

```python
from zeeb_orm import Greatest, Least

products = await Product.objects.annotate(
    effective_price=Least(F("price"), F("sale_price")),
    highest_price=Greatest(F("price"), F("msrp")),
).all()
```

---

## Subquery Expressions

### Subquery

Use a subquery as an expression.

```python
from zeeb_orm import Subquery, OuterRef

# Latest post date for each user
latest_post = Post.objects.filter(
    author_id=OuterRef("id")
).order_by("-created_at").values("created_at")[:1]

users = await User.objects.annotate(
    latest_post_date=Subquery(latest_post)
).all()
```

### OuterRef

Reference outer query field in subquery.

```python
from zeeb_orm import OuterRef

# Posts by same author
same_author = Post.objects.filter(
    author_id=OuterRef("author_id"),
    id__ne=OuterRef("id"),
).values("id")
```

### Exists

Check if subquery has results.

```python
from zeeb_orm import Exists, OuterRef

# Users with posts
has_posts = Post.objects.filter(author_id=OuterRef("id"))

users = await User.objects.annotate(
    has_posts=Exists(has_posts)
).filter(has_posts=True).all()

# Alternative
users = await User.objects.filter(
    Exists(Post.objects.filter(author_id=OuterRef("id")))
).all()
```

---

## Value

Wrap a literal value.

```python
from zeeb_orm import Value

users = await User.objects.annotate(
    status=Value("active"),
    multiplier=Value(1.5),
    threshold=Value(100),
).all()
```

---

## Expression Reference Table

| Category | Expressions |
|----------|-------------|
| **Field** | `F` |
| **Conditional** | `Q` |
| **Aggregates** | `Count`, `Sum`, `Avg`, `Max`, `Min` |
| **Conditionals** | `Case`, `When`, `Value`, `Coalesce` |
| **String** | `Concat`, `Lower`, `Upper`, `Length`, `Trim`, `LTrim`, `RTrim`, `Substr` |
| **Date** | `Now`, `Extract`, `TruncDate`, `TruncMonth`, `TruncYear` |
| **Math** | `Abs`, `Round`, `Ceil`, `Floor`, `Greatest`, `Least` |
| **Subquery** | `Subquery`, `OuterRef`, `Exists` |

---

## Combining Expressions

Expressions can be combined:

```python
from zeeb_orm import F, Case, When, Value, Coalesce, Round

products = await Product.objects.annotate(
    # Combine F expressions
    profit=F("price") - F("cost"),
    
    # Case with F expressions
    margin=Round(
        (F("price") - F("cost")) / F("price") * 100,
        2
    ),
    
    # Nested expressions
    final_price=Case(
        When(is_sale=True, then=F("price") * 0.8),
        default=F("price"),
    ),
    
    # Coalesce with expressions
    effective_name=Coalesce(F("display_name"), F("name")),
).all()
```

## Next Steps

- [QuerySet API](queryset-api.md) - QuerySet methods reference
- [Field Types](field-types.md) - Field type reference
- [Expressions Guide](../orm/expressions.md) - Expression usage guide
