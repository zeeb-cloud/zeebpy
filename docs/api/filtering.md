# Filtering & Search

Filter backends turn query-string parameters into QuerySet operations. A
viewset lists them in `filter_backends`; each one is applied in order to the
queryset returned by `get_queryset()`.

```python
from zeeb_api.filters import OrderingFilter, SearchFilter
from zeeb_api.viewsets import ModelViewSet


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["title", "body"]
    ordering_fields = ["title", "created_at"]
    ordering = ["-created_at"]
```

`GET /articles?search=async&ordering=-created_at` now works.

> Filter backends apply to the `GET` collection endpoint. The
> `POST /<prefix>/query/` endpoint is a separate mechanism with its own
> allow-list — see [ViewSets](viewsets.md).

A project-wide default is `DEFAULT_FILTER_BACKENDS` in settings (dotted paths,
default `[]`); it applies to every viewset that does not declare
`filter_backends`:

```python
# settings.py
DEFAULT_FILTER_BACKENDS = ["zeeb_api.filters.OrderingFilter"]
```

## SearchFilter

Reads a single query parameter (`?search=`) and ORs it across every field named
in the viewset's `search_fields`.

```python
class ArticleViewSet(ModelViewSet):
    filter_backends = [SearchFilter]
    search_fields = ["title", "body", "author__name"]
```

A prefix on a field name changes the lookup:

| `search_fields` entry | Lookup | Matches |
|---|---|---|
| `"title"` | `title__icontains` | anywhere, case-insensitive (default) |
| `"^title"` | `title__istartswith` | at the start |
| `"=title"` | `title__iexact` | the whole value |
| `"@title"` | `title__icontains` | reserved for full-text; currently the same as the default |

With no `search_fields`, or no `?search=` in the request, the queryset is
returned untouched. The parameter name is `search`; change it by subclassing:

```python
class QueryFilter(SearchFilter):
    search_param = "q"
```

## OrderingFilter

Reads `?ordering=` — a comma-separated list of field names, each optionally
prefixed with `-` for descending.

```
GET /articles?ordering=-created_at,title
```

```python
class ArticleViewSet(ModelViewSet):
    filter_backends = [OrderingFilter]
    ordering_fields = ["title", "created_at"]   # allow-list
    ordering = ["-created_at"]                  # applied when ?ordering is absent
```

### What a client may order by

`ordering_fields` is an allow-list. Anything not on it is **dropped silently**,
so a request never fails because of an unknown ordering field — it just does
not take effect.

When `ordering_fields` is not set, the filter does **not** allow everything. It
defaults to the field names the serializer actually exposes, so a client cannot
order by a column the API does not surface. Only when those fields cannot be
resolved does it fall back to permissive.

Set `ordering_fields = "__all__"` to allow every field **of the model
itself**.

Validation checks the **whole** path. Listing `author` permits
`?ordering=author` (the foreign key) but not `?ordering=author__name`: ordering
by a related model's column is a blind oracle over it (`author__password` sorts
the rows by the hidden hash). To permit a path through a relation, list it in
full — `ordering_fields = ["title", "author__name"]`. `"__all__"` never admits
such a path. The view's own default `ordering` is developer-written and is
applied as declared.

## FilterSet

Declarative per-field filtering. Each `field: [lookups]` pair in `Meta.fields`
creates one query parameter per lookup.

```python
from zeeb_api.filters import FilterSet


class ProductFilter(FilterSet):
    class Meta:
        model = Product
        fields = {
            "category": ["exact", "in"],
            "price": ["gte", "lte"],
            "name": ["contains", "icontains"],
        }


class ProductViewSet(ModelViewSet):
    filter_backends = [ProductFilter]
```

That accepts `?category=books`, `?category__in=books,music`, `?price__gte=10`,
`?price__lte=99`, `?name__icontains=widget`.

Supported lookups: `exact`, `iexact`, `contains`, `icontains`, `in`, `gt`,
`gte`, `lt`, `lte`, `startswith`, `istartswith`, `endswith`, `iendswith`,
`isnull`.

Values arrive as strings and are converted per lookup: `in` splits on commas,
`isnull` accepts `true`/`1`/`yes`, and the comparison lookups (`gt`, `gte`,
`lt`, `lte`) are coerced to `int`/`float` when they parse as numbers. The
`exact` lookup uses the bare field name as the parameter (`?category=`, not
`?category__exact=`).

## Custom Backends

Subclass `BaseFilter` and implement `filter_queryset()`:

```python
from zeeb_api.filters import BaseFilter


class OwnedByCurrentUser(BaseFilter):
    """Restrict the collection to rows owned by the caller."""

    def filter_queryset(self, request, queryset, view):
        user = getattr(request.state, "user", None)
        if user is None:
            return queryset.none()
        return queryset.filter(owner_id=user.id)


class ArticleViewSet(ModelViewSet):
    filter_backends = [OwnedByCurrentUser, OrderingFilter]
```

`filter_queryset()` receives the request, the current queryset and the viewset,
and returns a queryset. It is **synchronous** — it composes the query without
awaiting it; the viewset awaits the result once every backend has run.

Returning `queryset.none()` is the correct way to express "match nothing".
Filtering on a nullable column (`filter(owner_id=None)`) matches rows where the
column *is* null, which is not the same thing.

## Checking client-supplied field paths

`OrderingFilter` and the `POST /<prefix>/query` endpoint share one allow-list
check, exported from `zeeb_api.query` for code that accepts field names from a
client by other means — a hand-written endpoint, a custom backend, an export
route.

### `check_field_path()`

```text
check_field_path(model, path, allowed, *, allow_regex=True) -> None
```

Returns `None` when `path` may be used on `model`, and raises `FieldPathError`
when it may not.

| Parameter | Meaning |
|---|---|
| `model` | The model the path is resolved against (`queryset.model`). `None` — or an object without `_meta` — falls back to a purely syntactic check |
| `path` | A filter keyword (`author__name__icontains`) or an ordering term (`-created_at`; a leading `-` is ignored) |
| `allowed` | The allow-list: a collection of names, `"__all__"` for every field of `model` itself, or `None` for unrestricted (only the regex rule applies) |
| `allow_regex` | Whether `regex`/`iregex` lookups are accepted (keyword-only) |

The **whole** path is resolved against the model, transforms and lookups
included, and every hop must be allowed:

- A path that stays on the model (`title`, `title__icontains`,
  `created_at__year__gte`) needs its field in `allowed`. A foreign key is
  known by either name: listing `author` admits `author` and `author_id`.
- A path through a relation needs its **full** field path listed —
  `"author__name"` admits `author__name` and `author__name__icontains`.
  `"__all__"` never admits such a path.
- The one exception is the related primary key right behind an allowed
  relation (`author__id`, `author__in=[...]`): that is the value the relation
  field already exposes.

`FieldPathError` is raised for an empty path, a field outside the allow-list,
a relation path that is not listed in full, a `regex`/`iregex` lookup when
`allow_regex=False`, and a path the model cannot resolve (an unknown field
behind a relation, an unsupported lookup or transform — the message is the
ORM's own). `FieldPathError` subclasses `ValueError`.

```python
from zeeb_api.exceptions import ValidationError
from zeeb_api.query import FieldPathError, check_field_path

ALLOWED = ["title", "created_at", "author", "author__name"]


async def export_articles(order_by: str):
    try:
        check_field_path(Article, order_by, ALLOWED, allow_regex=False)
    except FieldPathError as exc:
        raise ValidationError({"order_by": [str(exc)]})
    return await Article.objects.order_by(order_by)
```

With that list, `-created_at`, `author_id`, `author__id` and `author__name`
pass; `author__password` is refused (`Field 'author__password' follows a
relation; list the full path in the allow-list to permit it`).

`OrderingFilter` calls it with `allow_regex=False` and drops a refused term;
the `/query` endpoint refuses the request with a 400 instead. Neither ever
admits a regex lookup in ordering.

### `extract_q_paths()` and `extract_q_fields()`

Both read a serialized `Q` expression — the `filter` string of a `/query`
body — without evaluating it.

`extract_q_paths(expr) -> set[str]` returns every keyword path the expression
uses, at any depth of `&`, `|`, `~` and nesting. `extract_q_fields(expr) ->
set[str]` returns only their **roots** (the part before the first `__`):

```python
from zeeb_api.query import extract_q_fields, extract_q_paths

expr = "Q(author__name__icontains='ann') | ~Q(title='Draft', created_at__year__gte=2024)"

extract_q_paths(expr)   # {"author__name__icontains", "title", "created_at__year__gte"}
extract_q_fields(expr)  # {"author", "title", "created_at"}
```

An empty or blank string gives an empty set; a string that is not valid
Python syntax raises `QFilterError`. Neither function validates the
expression — only keywords of `Q(...)` calls are collected, and anything else
in the string is ignored rather than refused; `parse_q_filter()` is the
validating parser.

Validate allow-lists with `extract_q_paths()` and `check_field_path()`, never
with `extract_q_fields()`: a root check lets `author__password__startswith`
through wherever `author` is allowed. That is the order the `/query` endpoint
uses:

```python
from zeeb_api.query import FieldPathError, check_field_path, extract_q_paths, parse_q_filter


def safe_filter(queryset, expr: str, allowed: list[str]):
    for path in extract_q_paths(expr):
        check_field_path(queryset.model, path, allowed, allow_regex=False)  # FieldPathError
    return queryset.filter(parse_q_filter(expr))
```

## Related

- [ViewSets](viewsets.md) - `get_queryset()`, `query_fields`, and the `/query/` endpoint- [Pagination](pagination.md) - applied after filtering
- [Queries](../orm/queries.md) - the lookups these backends generate
