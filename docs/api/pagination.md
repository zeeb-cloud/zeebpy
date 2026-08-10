# Pagination

Set `pagination_class` on a viewset and the `GET` collection endpoint returns a
paginated envelope instead of a bare list.

```python
from zeeb_api.pagination import PageNumberPagination
from zeeb_api.viewsets import ModelViewSet


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    pagination_class = PageNumberPagination
```

## The response envelope

A paginated list responds with four keys:

```json
{
  "count": 137,
  "next": "https://api.example.com/articles?page=3",
  "previous": "https://api.example.com/articles?page=1",
  "results": [ ... ]
}
```

> **`count` can be `null`.** Cursor pagination does not count the full table —
> that is the point of it — so `count` is `null` rather than a fabricated
> page-length number. Type it as `number | null` on the client.

Without a `pagination_class`, the endpoint returns the plain serialized list
with no envelope at all. Clients must handle whichever shape the endpoint is
configured for; the envelope is not added retroactively.

## PageNumberPagination

```
GET /articles?page=3&page_size=50
```

| Attribute | Default | Meaning |
|---|---|---|
| `page_size` | `20` | Items per page |
| `page_query_param` | `"page"` | Query parameter for the page number (1-indexed) |
| `page_size_query_param` | `"page_size"` | Client override; set to `None` to forbid it |
| `max_page_size` | `100` | Ceiling on the client override |

Extra keys in the envelope: `page`, `total_pages`, `page_size`. Out-of-range
values are clamped rather than rejected — `?page=0` reads as page 1, and a
`page_size` above `max_page_size` is capped.

## LimitOffsetPagination

```
GET /articles?limit=50&offset=100
```

| Attribute | Default | Meaning |
|---|---|---|
| `default_limit` | `DEFAULT_LIMIT` setting (`20`) | Page size when `?limit` is absent |
| `max_limit` | `MAX_LIMIT` setting (`100`) | Ceiling on `?limit` |
| `limit_query_param` | `"limit"` | |
| `offset_query_param` | `"offset"` | |

Extra keys in the envelope: `limit`, `offset`.

### Settings vs. subclass

`DEFAULT_LIMIT` and `MAX_LIMIT` are read from settings, but **an explicit
subclass override wins**:

```python
# Uses DEFAULT_LIMIT / MAX_LIMIT from settings
class ArticleViewSet(ModelViewSet):
    pagination_class = LimitOffsetPagination


# Ignores the settings — 50/500 always apply
class WidePagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 500
```

The check is whether the attribute differs from the base class's own value, so
setting `default_limit = 20` explicitly is indistinguishable from not setting
it, and the setting still applies.

## CursorPagination

Best for large or fast-changing tables: it seeks on an ordered column instead
of counting and skipping rows, so page 10,000 costs the same as page 1 and
rows inserted mid-scroll do not shift the window.

```python
class ArticlePagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class ArticleViewSet(ModelViewSet):
    pagination_class = ArticlePagination
```

| Attribute | Default | Meaning |
|---|---|---|
| `page_size` | `20` | Items per page |
| `ordering` | `"-id"` | The column to seek on — **required**, and must be unique and stable |
| `cursor_query_param` | `"cursor"` | |
| `page_size_query_param` | `"page_size"` | |
| `max_page_size` | `100` | |

Trade-offs to accept before choosing it:

- `count` is `null`, and there is no page number or total.
- Only forward and backward through `next`/`previous` — no random access.
- `ordering` overrides any ordering the client asks for.
- A non-unique `ordering` column silently skips or repeats rows at page
  boundaries.

Cursors are opaque, type-tagged and base64-encoded, so a value round-trips as
the right type (`int`, `float`, `bool`, `datetime`, `date`, `str`) rather than
coming back as a string. Do not parse or construct them client-side — pass back
exactly what `next`/`previous` contained.

## Custom pagination

Subclass `BasePagination` and implement `paginate_queryset()`, returning
`(items, page_info)`:

```python
from zeeb_api.pagination import BasePagination


class EverythingPagination(BasePagination):
    async def paginate_queryset(self, queryset, request):
        items = await queryset.all()
        return items, {"count": len(items), "next": None, "previous": None}
```

The keys `count`, `next` and `previous` are lifted into the response envelope;
anything else you return is merged in alongside them.

## Related

- [ViewSets](viewsets.md) - where `pagination_class` is set
- [Filtering & Search](filtering.md) - applied before pagination
- [Settings](../configuration/settings.md) - `DEFAULT_LIMIT`, `MAX_LIMIT`
