# Relationships

Define relationships between models using ForeignKey, OneToOne, and ManyToMany fields.

## ForeignKey (Many-to-One)

Many objects relate to one object. For example, many posts belong to one author.

### Defining ForeignKey

```python
from zeeb_orm import Model, fields


class Author(Model):
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)

    class Meta:
        table_name = "authors"


class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    author = fields.ForeignKey(
        Author,
        on_delete="CASCADE",
        related_name="posts",
    )

    class Meta:
        table_name = "posts"
```

### ForeignKey Options

| Option | Description |
|--------|-------------|
| `to` | Related model (class or string) |
| `on_delete` | Deletion behavior (required) |
| `related_name` | Name for reverse relation |
| `null` | Allow NULL (default: False) |
| `blank` | Allow empty in forms |
| `db_column` | Custom column name |

### on_delete Options

The constants are importable from `zeeb_orm` (plain strings, so passing
`on_delete="CASCADE"` keeps working):

```python
from zeeb_orm import CASCADE, PROTECT, RESTRICT, SET_NULL, SET_DEFAULT, DO_NOTHING
```

| Option | Behavior on deleting the referenced object |
|--------|--------------------------------------------|
| `CASCADE` (default) | Delete the referencing objects too (recursively) |
| `PROTECT` | Raise `ProtectedError` if referencing objects exist |
| `RESTRICT` | Raise `RestrictedError`, unless the referencing objects are themselves deleted by the same operation (via another cascade path) |
| `SET_NULL` | Set the FK column to NULL — requires `null=True` |
| `SET_DEFAULT` | Set the FK column to the field default — requires a `default` |
| `DO_NOTHING` | Leave referencing rows untouched (may leave dangling FKs) |

```python
class Comment(Model):
    post = fields.ForeignKey(Post, on_delete=CASCADE)  # Delete with post
    user = fields.ForeignKey(User, on_delete=SET_NULL, null=True)  # Keep if user deleted
    category = fields.ForeignKey(Category, on_delete=PROTECT)  # Prevent category deletion
```

Invalid combinations raise `ValueError` at field definition time: unknown
`on_delete` values, `SET_NULL` without `null=True`, and `SET_DEFAULT`
without a `default`.

#### How deletion works

`on_delete` is enforced **in Python** by a collector, so it
works regardless of database FK enforcement (e.g. SQLite's `foreign_keys`
PRAGMA, which is off by default):

```python
total, per_model = await author.delete()
# (3, {"Author": 1, "Post": 2}) — counts include cascaded rows
```

- `PROTECT`/`RESTRICT` are checked **before** any row is deleted.
  `ProtectedError.protected_objects` / `RestrictedError.restricted_objects`
  contain the referencing instances.
- All updates and deletes run in a single transaction (`atomic()` is opened
  automatically unless one is already active).
- Cascaded rows are deleted leaf-first, and `pre_delete`/`post_delete`
  signals fire for **every** affected instance.
- `QuerySet.delete()` routes through the collector whenever another model
  references the queryset's model with a non-`DO_NOTHING` FK (its count then
  includes cascaded rows, and per-instance delete signals fire). Models with
  no such inbound FKs keep the fast single-statement DELETE (no signals).

At the database level the constants map to standard `ON DELETE` actions in
the generated DDL: `CASCADE` → `ON DELETE CASCADE`, `SET_NULL` →
`ON DELETE SET NULL`, `SET_DEFAULT` → `ON DELETE SET DEFAULT`,
`PROTECT`/`RESTRICT` → `ON DELETE RESTRICT`, and `DO_NOTHING` emits no
clause.

### Accessing Related Objects

```python
# Forward access (post -> author)
post = await Post.objects.get(pk=1)
author = post.author  # Lazy load
print(author.name)

# Access foreign key ID directly
author_id = post.author_id

# Reverse access (author -> posts)
author = await Author.objects.get(pk=1)
posts = await author.posts.all()  # Uses related_name

# Filter by relation
posts = await Post.objects.filter(author__name="John")
posts = await Post.objects.filter(author=author)
posts = await Post.objects.filter(author_id=author.id)
```

### Eager Loading

```python
# select_related for ForeignKey (single JOIN query)
posts = await Post.objects.select_related("author")
for post in posts:
    print(post.author.name)  # No additional query

# Multiple relations
posts = await Post.objects.select_related("author", "category")

# Nested relations
comments = await Comment.objects.select_related("post__author")
```

### Creating with ForeignKey

```python
# Create with related object
author = await Author.objects.get(pk=1)
post = await Post.objects.create(
    title="New Post",
    content="...",
    author=author,
)

# Create with ID
post = await Post.objects.create(
    title="New Post",
    content="...",
    author_id=author_id,
)
```

## OneToOneField

One object relates to exactly one other object. For example, a user has one profile.

### Defining OneToOne

```python
class User(Model):
    username = fields.CharField(max_length=50, unique=True)
    email = fields.EmailField(unique=True)

    class Meta:
        table_name = "users"


class Profile(Model):
    user = fields.OneToOne(
        User,
        on_delete="CASCADE",
        related_name="profile",
    )
    bio = fields.TextField(blank=True)
    avatar_url = fields.URLField(null=True)

    class Meta:
        table_name = "profiles"
```

### Accessing OneToOne

```python
# Forward access (profile -> user)
profile = await Profile.objects.get(pk=1)
user = profile.user

# Reverse access (user -> profile)
user = await User.objects.get(pk=1)
profile = user.profile  # Returns single object, not queryset

# With select_related
users = await User.objects.select_related("profile")
for user in users:
    print(user.profile.bio)
```

### Creating OneToOne

```python
user = await User.objects.create(username="john", email="john@example.com")
profile = await Profile.objects.create(user=user, bio="Hello!")

# Or check if exists
profile, created = await Profile.objects.get_or_create(
    user=user,
    defaults={"bio": "New user"},
)
```

## ManyToManyField

Many objects relate to many other objects. For example, articles can have many tags, and tags can have many articles.

### Defining ManyToMany

```python
class Tag(Model):
    name = fields.CharField(max_length=50, unique=True)
    slug = fields.SlugField(unique=True)

    class Meta:
        table_name = "tags"


class Article(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    tags = fields.ManyToMany(
        Tag,
        related_name="articles",
    )

    class Meta:
        table_name = "articles"
```

This auto-creates an intermediate join table in the shared metadata,
named `"{source_table}_{field_name}"` (here: `articles_tags`), with:

- An `{source}_id` / `{target}_id` column pair (here: `article_id` /
  `tag_id`, derived from the lowercased model names), each typed like the
  referenced model's primary key.
- `ON DELETE CASCADE` foreign keys on both columns.
- A unique constraint over the column pair (no duplicate links).

`Database.create_all()` creates the join table along with the model tables,
and the migration autodetector sees it as a plain new table. Self-referential
M2M fields use `from_{model}_id` / `to_{model}_id` columns.

If no `related_name` is given, the reverse accessor on the target model
defaults to `<sourcemodel>_set` (e.g. `tag.article_set`).

### ManyToMany Options

| Option | Description |
|--------|-------------|
| `to` | Related model (class, registry name, or `"self"`) |
| `related_name` | Name for reverse relation (default `<model>_set`) |
| `through` | Custom intermediate model (class or name) |
| `through_fields` | `(source_fk, target_fk)` field names on the through model |
| `db_table` | Custom join table name (auto-created tables only) |

### Managing ManyToMany Relations

Accessing the field on an instance returns a `ManyRelatedManager`:

```python
article = await Article.objects.get(pk=1)
tag = await Tag.objects.get(name="python")

# Add relations — duplicates are silently ignored, raw pk values work too
await article.tags.add(tag)
await article.tags.add(tag1, tag2, tag3)  # Multiple
await article.tags.add(tag.pk)            # pk value instead of instance

# Remove relations (instances or pk values)
await article.tags.remove(tag)

# Clear all relations
await article.tags.clear()

# Set specific relations (diff-based: removes stale links, adds missing)
await article.tags.set([tag1, tag2])
await article.tags.set([tag1], clear=True)  # delete all, then re-insert

# Counting / reading
n = await article.tags.count()
tags = await article.tags.all()
py_tags = await article.tags.filter(name__startswith="py")

# Create-and-link in one call
tag = await article.tags.create(name="asyncio")

# Reverse side works the same way
await tag.articles.add(article)
```

`add()` and `set()` run inside a transaction (`atomic()`), checking for
existing pairs first and inserting only the missing ones — portable across
SQLite, PostgreSQL and MySQL.

The instance must be saved before the relation can be used; accessing the
manager on an unsaved instance raises
`ValueError: "..." needs to have a value for field "id" before this
many-to-many relationship can be used.`

### Querying ManyToMany

```python
# Forward access (article -> tags)
article = await Article.objects.get(pk=1)
tags = await article.tags.all()

# Reverse access (tag -> articles)
tag = await Tag.objects.get(name="python")
articles = await tag.articles.all()  # Uses related_name

# Filter by ManyToMany
# Articles with specific tag
articles = await Article.objects.filter(tags__name="python")

# Articles with any of these tags
articles = await Article.objects.filter(tags__name__in=["python", "async"])

# Articles with ALL of these tags
from zeeb_orm import Count

articles = await Article.objects.filter(
    tags__name__in=["python", "async"]
).annotate(
    tag_count=Count("tags")
).filter(tag_count=2)
```

M2M traversal joins the base table to the join table and on to the target
table (two hops). As with reverse ForeignKeys, a row can match through
multiple links and appear more than once — add `.distinct()` to deduplicate:

```python
articles = await Article.objects.filter(
    tags__name__in=["python", "async"]
).distinct()
```

### Prefetching ManyToMany

`prefetch_related()` works in both directions with one extra IN-query over
the join table plus one query for the related rows; the result is attached
as a plain list:

```python
articles = await Article.objects.prefetch_related("tags")
for article in articles:
    print(article.title, [t.name for t in article.tags])  # no extra queries

tags = await Tag.objects.prefetch_related("articles")
```

### Custom Through Model

For additional fields on the relationship:

```python
class Membership(Model):
    """Custom through model with extra fields."""
    person = fields.ForeignKey("Person", on_delete="CASCADE")
    group = fields.ForeignKey("Group", on_delete="CASCADE")
    date_joined = fields.DateTimeField(auto_now_add=True)
    role = fields.CharField(max_length=50, default="member")

    class Meta:
        table_name = "memberships"
        unique_together = [("person", "group")]


class Person(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "people"


class Group(Model):
    name = fields.CharField(max_length=100)
    members = fields.ManyToMany(
        Person,
        through=Membership,
        related_name="groups",
    )

    class Meta:
        table_name = "groups"
```

With a custom through model, **reads work normally** (accessors, traversal,
prefetching) via the through model's table, but the write helpers
`add()` / `remove()` / `clear()` / `set()` raise `NotSupportedError` —
create and delete rows on the through model directly:

```python
members = await group.members.all()                      # OK
groups = await Group.objects.filter(members__name="Bo")  # OK
await group.members.add(person)                          # NotSupportedError!
```

Using the custom through model directly:

```python
# Add with extra data
membership = await Membership.objects.create(
    person=person,
    group=group,
    role="admin",
)

# Query through model
admins = await Membership.objects.filter(group=group, role="admin")

# Access extra data
memberships = await Membership.objects.filter(person=person).select_related("group")
for m in memberships:
    print(f"{m.group.name}: {m.role} since {m.date_joined}")
```

## Self-Referential Relationships

Models can relate to themselves:

### ForeignKey to Self

```python
class Employee(Model):
    name = fields.CharField(max_length=100)
    manager = fields.ForeignKey(
        "self",  # Self-reference
        on_delete="SET_NULL",
        null=True,
        related_name="subordinates",
    )

    class Meta:
        table_name = "employees"


# Usage
manager = await Employee.objects.create(name="Alice")
employee = await Employee.objects.create(name="Bob", manager=manager)

# Get subordinates
subordinates = await manager.subordinates.all()

# Get manager
boss = employee.manager
```

### ManyToMany to Self

```python
class Person(Model):
    name = fields.CharField(max_length=100)
    friends = fields.ManyToMany(
        "self",
        related_name="friend_of",
        symmetrical=True,  # If A is friend of B, B is friend of A
    )

    class Meta:
        table_name = "people"


# Usage
alice = await Person.objects.get(name="Alice")
bob = await Person.objects.get(name="Bob")

await alice.friends.add(bob)
# bob.friends now includes alice (if symmetrical=True)
```

## Querying Across Relationships

### Spanning Multiple Relationships

```python
# Get comments on posts by a specific author
comments = await Comment.objects.filter(post__author__name="John")

# Get articles in categories with specific tag
articles = await Article.objects.filter(category__tags__name="featured")
```

### Aggregating Across Relationships

```python
from zeeb_orm import Count, Avg, Sum

# Count posts per author
authors = await Author.objects.annotate(post_count=Count("posts"))

# Average views of author's posts
authors = await Author.objects.annotate(avg_views=Avg("posts__views"))

# Total revenue per customer
customers = await Customer.objects.annotate(
    total_spent=Sum("orders__total")
)
```

### Filtering with Annotations

```python
# Authors with more than 10 posts
prolific = await Author.objects.annotate(
    post_count=Count("posts")
).filter(post_count__gt=10)

# Categories with high-view posts
popular = await Category.objects.annotate(
    total_views=Sum("posts__views")
).filter(total_views__gt=10000)
```

## Best Practices

### 1. Use select_related for ForeignKey/OneToOne

```python
# Good - single query
posts = await Post.objects.select_related("author", "category")

# Bad - N+1 queries
posts = await Post.objects.all()
for post in posts:
    print(post.author.name)  # Each access is a query
```

### 2. Use prefetch_related for ManyToMany/Reverse FK

```python
# Good - 2 queries total
authors = await Author.objects.prefetch_related("posts")

# Bad - N+1 queries
authors = await Author.objects.all()
for author in authors:
    posts = await author.posts.all()  # Each is a query
```

### 3. Use related_name for Reverse Access

```python
# Good - clear and explicit
class Post(Model):
    author = fields.ForeignKey(Author, related_name="posts")

# Access: author.posts.all()

# Without related_name
# Access: author.post_set.all()  # Less clear
```

### 4. Consider on_delete Carefully

```python
# User data - preserve even if user deleted
class Post(Model):
    author = fields.ForeignKey(User, on_delete="SET_NULL", null=True)

# Dependent data - delete together
class Comment(Model):
    post = fields.ForeignKey(Post, on_delete="CASCADE")

# Reference data - protect from deletion
class Order(Model):
    product = fields.ForeignKey(Product, on_delete="PROTECT")
```

## Next Steps

- [Queries](queries.md) - Query across relationships
- [Expressions](expressions.md) - Aggregate related data
- [Models](models.md) - Model definition
