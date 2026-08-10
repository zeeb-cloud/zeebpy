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

Set `ordering_fields = "__all__"` to opt back in to unrestricted ordering.

Validation compares the **root** of the path, before any `__`, so listing
`author` in `ordering_fields` permits `?ordering=author__name`.

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

## Related

- [ViewSets](viewsets.md) - `get_queryset()`, `query_fields`, and the `/query/` endpoint
- [Pagination](pagination.md) - applied after filtering
- [Queries](../orm/queries.md) - the lookups these backends generate
