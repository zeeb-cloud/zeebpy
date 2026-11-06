"""The on-demand skill a coding agent loads inside a generated project.

``AGENTS.md`` (see :mod:`zeeb_orm.scaffold.agent_guide`) is the always-on
summary: short, read on every turn. The framework itself does not fit in it —
the field classes, the lookups, the viewset options, the feature lifecycle — and
an agent that has to improvise those reaches for Django and DRF idioms this
framework does not have. The skill is where that depth lives: a ``SKILL.md``
whose one-paragraph description is all an agent sees until a task matches it,
and ``references/`` it reads one file at a time.

Where it is written. ``.agents/skills/zeebpy/`` is the cross-vendor location
(Codex, Cursor and Copilot read it); Claude Code reads ``.claude/skills/`` only,
so it gets a second ``SKILL.md`` rendered from the same constant. The body cites
every reference by its project-root-relative path, which is what lets the two
copies be byte-identical with a single ``references/`` directory — a test holds
them identical, as it holds the Cursor rule to ``AGENTS.md``. The generated
``AGENTS.md`` names the skill too, for an agent with no skill support at all.

Every path, command, flag, class, method, lookup, setting, error code and
function the skill names is verified against the real code by
``tests/test_scaffold_skill.py``: an instruction file is trusted harder than
data, so a wrong name here is worse than no name.

Rendered with :func:`str.replace` on ``@PROJECT@`` like ``AGENTS.md`` — these
are markdown documents full of JSON and ``{id}`` path params, and doubling every
brace for ``str.format`` would be a standing correction hazard.
"""

from __future__ import annotations

from zeeb_orm.scaffold.agent_guide import PROJECT_PLACEHOLDER

#: Where the canonical skill lives in a generated project.
SKILL_DIR = ".agents/skills/zeebpy"

#: Claude Code's project-level skill location; gets the same ``SKILL.md``.
CLAUDE_SKILL_DIR = ".claude/skills/zeebpy"

SKILL_MD = """\
---
name: zeebpy
description: >-
  Build and change this zeebpy project: a Django-shaped async ORM on SQLAlchemy
  and a DRF-shaped API on FastAPI (it is not Django: no django.db, no
  rest_framework). Use when adding or changing models, fields, serializers,
  viewsets, routes, auth, permissions, queries, migrations or tests; when a
  zeeb_orm or zeeb_api import or a manage.py command fails; and before reporting
  a change as done.
---

# zeebpy

`AGENTS.md` is the always-on summary of this project. This skill is the depth
behind it: pick the path below, then read only the reference it names.

## Pick the path

| You want | Do this |
|---|---|
| A new resource: model, serializer, viewset, route and a passing test | `python manage.py startapp <app> --model <Name>`, then migrate |
| A whole feature — several entities, relations, auth rules, custom actions — that stays trackable, so it can be deactivated or deleted later | the feature lifecycle: `.agents/skills/zeebpy/references/lifecycle.md` |
| Custom logic inside generated code: a `perform_create`, an `action`, a `validate_<field>`, a query | edit the file by hand with `.agents/skills/zeebpy/references/orm.md` and `.agents/skills/zeebpy/references/api.md` |
| A schema change on an existing model | change `models.py`, then follow `.agents/skills/zeebpy/references/migrations.md` |
| A command or a test fails | `.agents/skills/zeebpy/references/troubleshooting.md` |

Code written by hand is not tracked as a feature: it still works, and it still
migrates, but `list_features` reports it as inferred and the deactivate/delete
lifecycle cannot act on it. Use the lifecycle for anything you may want to
switch off again.

## The loop

Every change ends with the same four commands, and nothing is done until all
four pass:

```bash
pytest -q
python manage.py makemigrations --check   # fails if a model change has no migration
python manage.py check
ruff check .
```

Add `--json` to a management command to get one object on stdout. A failure
carries an `error_code` and a `next_command`: the exact command to run next.
`python manage.py inspect --json` is the live inventory of apps, models and
routes; never trust a list of them written into a file.

## Reference index

- `.agents/skills/zeebpy/references/orm.md` — fields, `Meta`, lookups,
  QuerySet methods, expressions, relations, signals, validation, transactions.
- `.agents/skills/zeebpy/references/api.md` — serializers, viewsets, routers,
  permissions, filtering, pagination, throttling, versioning, errors, auth,
  settings and `.env`.
- `.agents/skills/zeebpy/references/lifecycle.md` — `build_feature`,
  `change_feature`, `verify_project` and `diagnose_problem` from the shell, and
  the FeatureSpec format.
- `.agents/skills/zeebpy/references/migrations.md` — makemigrations, migrate,
  rollback, squash, renames.
- `.agents/skills/zeebpy/references/troubleshooting.md` — error codes and the
  common failures.
- `.agents/skills/zeebpy/scripts/zeeb_agent.py` — the shell entry point for
  the lifecycle tools.

## Gotchas that AGENTS.md does not cover

- `save()` and `create()` run `full_clean()` first: `choices`, `validators`
  and `null=False` are enforced on write. `save(validate=False)` opts out;
  `bulk_create()` skips validation and fires no signals.
- `pre_save` fires before the write and a raising receiver aborts it;
  `post_save` fires after the write and cannot roll it back. Defer side effects
  with `on_commit()` from `zeeb_orm.db.transaction`.
- `select_related()` is for `ForeignKey` and `OneToOneField` (one JOIN);
  `prefetch_related()` is for `ManyToManyField` and reverse relations (one
  extra query per level). The synchronous `data` property of a serializer
  cannot load an unfetched to-many relation; the viewsets use `adata()`.
- The query endpoint filters only on fields the serializer returns. Set
  `query_fields` on the viewset to widen or narrow that, and list a path
  through a relation in full.
- `validated_data` holds only the keys the client sent — read optional fields
  with `dict.get`, never by subscript.
- A viewset without a `pagination_class` returns a bare list, not the paginated
  envelope.
- Throttle counts live in memory per process. Behind several workers install a
  shared `BaseThrottleCache` with `set_throttle_cache()` first.
- `order_by()` replaces `Meta.ordering`. There is no limit method: slice the
  queryset. There is no not-equal lookup: use `exclude()`. A bare `Exists(...)`
  passed to `filter()` positionally is dropped silently — annotate it first.
- Every setting in `@PROJECT@/settings.py` stays on one line and has an `.env`
  variable (see `.env.example`). Change the env, not the literal.
"""

REFERENCE_ORM_MD = """\
# zeeb_orm reference

Everything is async: every query, `save()`, `delete()` and `refresh_from_db()`
is awaited. A queryset is lazy until awaited or iterated.

## Defining a model

```python
from zeeb_orm import Index, Model, fields


class Post(Model):
    title = fields.CharField(max_length=200)
    body = fields.TextField(blank=True)
    status = fields.CharField(max_length=10, choices=[("draft", "Draft"), ("live", "Live")],
                              default="draft")
    owner = fields.ForeignKey("accounts.User", on_delete="CASCADE", related_name="posts")
    tags = fields.ManyToManyField("blog.Tag", related_name="posts")
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "blog_post"
        ordering = ["-created_at"]
        indexes = [Index(fields=["status", "created_at"])]
        unique_together = [("owner", "title")]
```

A model that declares no primary key gets a `UUIDAutoField` named `id`. Name
related models by label — `"accounts.User"`, and `app.Model` in general —
never by the bare class name.

## Field classes

| Class | Required / notable kwargs |
|---|---|
| `CharField(max_length=200)` | `max_length` required; `min_length` |
| `TextField()` | `min_length` |
| `EmailField()`, `URLField()`, `SlugField()`, `GenericIPAddressField()` | validated on write |
| `IntegerField()`, `SmallIntegerField()`, `BigIntegerField()` | `min_value`, `max_value` |
| `PositiveIntegerField()`, `PositiveSmallIntegerField()`, `PositiveBigIntegerField()` | non-negative |
| `FloatField()` | `min_value`, `max_value` |
| `DecimalField(max_digits=10, decimal_places=2)` | both required |
| `BooleanField(default=False)` | |
| `DateField()`, `TimeField()`, `DateTimeField()` | `auto_now`, `auto_now_add` |
| `DurationField()` | timedelta values |
| `UUIDField()`, `BinaryField()`, `JSONField(default=dict)` | callable defaults for mutables |
| `AutoField`, `BigAutoField`, `UUIDAutoField` | explicit primary keys |
| `ForeignKey("app.Model", on_delete="CASCADE")` | `related_name`, `null`, `db_column` |
| `OneToOneField("app.Model", on_delete="CASCADE")` | as `ForeignKey`, plus a unique constraint |
| `ManyToManyField("app.Model")` | `related_name`, `through`; takes no `on_delete` or `null` |

Common kwargs on every field: `null`, `blank`, `default`, `unique`,
`index`, `primary_key`, `db_column`, `verbose_name`, `help_text`,
`validators`, `choices`. `on_delete` is one of `CASCADE`, `PROTECT`,
`RESTRICT`, `SET_NULL` (needs `null=True`), `SET_DEFAULT`, `DO_NOTHING`.
Validators live in `zeeb_orm.validators`: `MinValueValidator`,
`MaxValueValidator`, `MinLengthValidator`, `MaxLengthValidator`,
`RegexValidator`, `EmailValidator`, `URLValidator`.

Not available: file and image fields (store a path or URL), array or range
fields, generic foreign keys, proxy models.

## Meta

`table_name` (alias `db_table`), `ordering`, `abstract`, `managed`, `indexes`
(`Index`), `constraints` (`UniqueConstraint`, `CheckConstraint`),
`unique_together`, `index_together`, `app_label`. Anything else on `Meta` is
ignored. `Meta` is not inherited by subclasses except `ordering`, `managed`,
`indexes`, `constraints`, `unique_together`, `index_together` and
`app_label`.

## Lookups

A keyword of the form field, two underscores, lookup — on `filter()`,
`exclude()`, `get()` and `Q()`:

| Lookup | Meaning |
|---|---|
| `__exact` (default), `__iexact` | equality, case-insensitive equality |
| `__contains`, `__icontains`, `__startswith`, `__istartswith`, `__endswith`, `__iendswith` | substring; percent and underscore match literally |
| `__in`, `__range` | membership, inclusive bounds |
| `__gt`, `__gte`, `__lt`, `__lte` | comparison |
| `__isnull` | `True` / `False` |
| `__regex`, `__iregex` | never with untrusted patterns (SQLite runs Python `re`) |

Datetime transforms chain before a lookup: `__year`, `__iso_year`, `__month`,
`__day`, `__week`, `__week_day`, `__iso_week_day`, `__quarter`, `__hour`,
`__minute`, `__second`, `__date`, `__time`. Relations traverse with the same
separator: `filter(owner__email__iendswith="@example.com")`.

## QuerySet methods

| Group | Methods |
|---|---|
| Filter | `filter()`, `exclude()`, `none()` |
| Fetch | `all()`, `get()`, `first()`, `last()`, `exists()`, `count()`, `in_bulk()`, `iterator()` |
| Shape | `order_by()`, `distinct()`, `values()`, `values_list()`, `only()`, `defer()`, slicing |
| Relations | `select_related()`, `prefetch_related()` |
| Compute | `annotate()`, `aggregate()`, `explain()` |
| Sets | `union()`, `intersection()`, `difference()` |
| Write | `create()`, `get_or_create()`, `update_or_create()`, `update()`, `delete()`, `bulk_create()`, `bulk_update()` |
| Other | `raw()`, `using()`, `select_for_update()` |

```python
posts = await Post.objects.filter(status="live").order_by("-created_at")[:20]
post = await Post.objects.select_related("owner").get(pk=post_id)
count = await Post.objects.filter(owner_id=user.id).count()
page = await Post.objects.prefetch_related("tags").all()
n = await Post.objects.filter(status="draft").update(status="live")
```

`get()` raises `DoesNotExist` or `MultipleObjectsReturned` (also reachable as
`Post.DoesNotExist`). `distinct()` takes no field arguments. Slicing is the
only limit/offset. `update()` and `delete()` on a queryset bypass `save()`,
validation and signals. Custom managers: `Manager.from_queryset()` and
`QuerySet.as_manager()`.

## Expressions

Import from `zeeb_orm`: `F`, `Q`, `Value`, `Case`, `When`, `Coalesce`,
`Subquery`, `OuterRef`, `Exists`, `Cast`, aggregates `Count`, `Sum`, `Avg`,
`Min`, `Max`, `StdDev`, `Variance`, `StringAgg`, string functions `Concat`,
`Lower`, `Upper`, `Length`, `Trim`, `Substr`, date functions `Now`,
`Extract`, `TruncDate`, `TruncMonth`, `TruncYear`, math `Abs`, `Round`,
`Ceil`, `Floor`, `Greatest`, `Least`, and window functions `Window`,
`RowNumber`, `Rank`, `DenseRank`, `Lag`, `Lead`, `FirstValue`, `LastValue`,
`Ntile`.

```python
from zeeb_orm import Count, Exists, F, OuterRef, Q

busy = await Author.objects.annotate(n=Count("posts")).filter(n__gt=5).all()
cheap = await Product.objects.filter(Q(price__lt=10) | Q(on_sale=True)).all()
await Product.objects.update(price=F("price") * 1.1)
has_posts = Post.objects.filter(owner_id=OuterRef("id"))
users = await User.objects.annotate(has_posts=Exists(has_posts)).filter(has_posts=True).all()
```

An aggregate annotation groups the query and drops `Meta.ordering`; order
such a query explicitly. `Subquery` needs a single-column inner queryset.

## Relations

- Forward access: `await post.owner` loads the object; `post.owner_id` is the
  key with no query.
- Reverse and many-to-many managers: `await user.posts.all()`,
  `await post.tags.add(tag)`, `remove()`, `set()`, `clear()`, `create()`.
- `on_delete` is enforced both in the database and by the collector, so
  `PROTECT` raises `ProtectedError` and `RESTRICT` raises `RestrictedError`.
- A `through` model is supported read-only; add rows through the through model.

## Signals

`pre_save`, `post_save`, `pre_delete`, `post_delete` from `zeeb_orm.signals`,
connected with `receiver`:

```python
from zeeb_orm.db.transaction import on_commit
from zeeb_orm.signals import post_save, receiver


@receiver(post_save, sender=Post)
async def on_post_saved(sender, instance, created, **kwargs):
    if created:
        on_commit(lambda: notify(instance))
```

Save signals fire for `save()`, `create()`, `get_or_create()` and
`update_or_create()` — not for `bulk_create()`, `bulk_update()` or
`update()`. Delete signals fire per instance inside the delete's transaction,
so a raising `pre_delete` receiver rolls the delete back.

## Validation and transactions

`full_clean()` runs `clean_fields()` (nullability, `choices`, `validators`)
then the async `clean()` hook; `save()` and `create()` call it unless
`validate=False`. A `ValidationError` carries `message_dict`; record-level
errors sit under `NON_FIELD_ERRORS`.

`atomic()` from `zeeb_orm` is both a context manager and a decorator; nested
blocks become savepoints, and `on_commit()` callbacks run after the outermost
commit. The viewset write actions already wrap the save and the perform hook
in one block.

## Exceptions (`zeeb_orm.exceptions`)

`DoesNotExist`, `MultipleObjectsReturned`, `ValidationError`, `FieldError`
(unknown field or lookup), `FieldDoesNotExist`, `IntegrityError`,
`ProtectedError`, `RestrictedError`, `NotSupportedError`,
`TransactionManagementError`, `ConnectionDoesNotExist`.
"""

REFERENCE_API_MD = """\
# zeeb_api reference

## Serializers

```python
from zeeb_api import serializers

from .models import Post


class PostSerializer(serializers.ModelSerializer):
    owner_email = serializers.CharField(source="owner.email", read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ["id", "title", "body", "status", "owner", "owner_email", "summary"]
        read_only_fields = ["id", "owner"]
        extra_kwargs = {"body": {"required": False}}

    def get_summary(self, obj):
        return obj.body[:80]

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Title cannot be blank")
        return value.strip()
```

- `Meta` keys: `model`, `fields` (a list or `"__all__"`), `exclude`,
  `read_only_fields`, `extra_kwargs` (`read_only`, `write_only`, `required`,
  `allow_null`, `default`, `max_length`, `min_length`, `help_text`).
- A declared field must appear in `Meta.fields`; a name that is neither a
  serializer field nor a model field raises `ImproperlyConfigured` at import.
- A `ForeignKey` serializes as the related primary key and is written as
  `<name>_id` or `<name>`. To-many relations are not in `"__all__"`; list them
  to get a read-only list of keys, or declare a `NestedSerializer`,
  `PrimaryKeyRelatedField` or `SlugRelatedField`.
- Validation order: schema, then every `validate_<field>` present in the
  input, then `validate()` on the whole dict. Hooks may be async; the
  viewsets call `ais_valid()`. Reject with `ValidationError` from
  `zeeb_api.exceptions`.
- `validated_data` contains only the keys the client sent (`partial=True` on
  `PATCH`). `is_valid()` + `save()` persist; `adata()` serializes with
  to-many relations loaded.
- Serializer field classes from `zeeb_api.serializers`: `CharField`,
  `EmailField`, `IntegerField`, `FloatField`, `DecimalField`, `BooleanField`,
  `DateTimeField`, `DateField`, `TimeField`, `UUIDField`, `ListField`,
  `DictField`, `SerializerMethodField`.

## ViewSets

```python
from zeeb_api import permissions, viewsets
from zeeb_api.exceptions import ResourceConflictException
from zeeb_api.filters import OrderingFilter, SearchFilter
from zeeb_api.pagination import PageNumberPagination
from zeeb_api.viewsets import action

from .models import Post
from .serializers import PostSerializer


class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = PageNumberPagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["title", "body"]
    ordering_fields = ["created_at", "title"]
    ordering = ["-created_at"]
    query_fields = ["title", "status", "owner", "owner__email"]

    async def perform_create(self, serializer: PostSerializer) -> None:
        await serializer.save(owner_id=self.request.state.user.id)

    @action(detail=True, methods=["POST"], permission_classes=[permissions.IsAdminUser])
    async def publish(self, request, pk=None):
        post = await self.get_object()
        if post.status == "live":
            raise ResourceConflictException(message="Already published")
        post.status = "live"
        await post.save()
        return PostSerializer(post).data
```

| Attribute | Meaning |
|---|---|
| `queryset`, `serializer_class` | required on a `ModelViewSet`; `ReadOnlyModelViewSet` serves list and retrieve only |
| `permission_classes` | ANDed; empty list makes the viewset public; default from `DEFAULT_PERMISSION_CLASSES` |
| `lookup_field`, `lookup_url_kwarg` | detail route parameter, default `id` |
| `pagination_class` | `PageNumberPagination`, `LimitOffsetPagination`, `CursorPagination` from `zeeb_api.pagination`; `None` returns a bare list |
| `filter_backends`, `search_fields`, `ordering_fields`, `ordering` | `SearchFilter`, `OrderingFilter`, a `FilterSet` subclass from `zeeb_api.filters` |
| `throttle_classes`, `throttle_scope` | per-viewset rate limits; `[]` opts out of the project default |
| `query_fields` | what the query endpoint may filter and order by; `"__all__"` for every model field |
| `atomic_writes` | `False` to stop wrapping each write action in one transaction |

Hooks: `get_queryset()`, `get_serializer_class()`, `get_object()`,
`get_permissions()`, `perform_create()`, `perform_update()`,
`perform_destroy()`. The authenticated user is `request.state.user`. A custom
action reads a validated body with `get_action_request_body()` or
`get_action_request_model()`; there is no request data attribute. The write
actions run the perform hook and the save in one transaction.

`action` parameters: `detail`, `methods`, `url_path`, `url_name`,
`permission_classes`, `permission_type`, `request_schema`,
`response_schema`, `request_serializer`, `response_serializer`,
`status_code`, `responses`. A plain `ViewSet` declares bodies with
`extend_schema`.

Each registered `ModelViewSet` serves list and create on the collection,
retrieve/update/partial update/delete on the detail path, and a `POST`
query endpoint under the collection that takes a serialized `Q` filter
(`parse_q_filter` parses it; it is not `eval`), `order_by`, `limit` and
`offset`. Canonical paths have no trailing slash; the slash form is served
too, hidden from OpenAPI.

## Routers

```python
from zeeb_api.routers import DefaultRouter

from .views import CommentViewSet, PostViewSet

router = DefaultRouter()
router.register("posts", PostViewSet)
router.register("posts/{post_pk}/comments", CommentViewSet)
```

`register(prefix, viewset, basename=None)`; a prefix registered twice raises
`ValueError`. The project router nests app routers with `include()`
(`startapp` appends that line for you). Keep the module-level `router` and
`get_routes()` in the project `urls.py`.

## Permissions (`zeeb_api.permissions`)

`AllowAny`, `IsAuthenticated`, `IsAdminUser`, `IsAuthenticatedOrReadOnly`,
`IsOwner`, `IsOwnerOrReadOnly`, `ModelPermissions`, and `BasePermission` for
your own (`has_permission`, `has_object_permission`). `ModelPermissions`
needs a database-backed user and the bare view, add, change and delete
codenames per model. For a plain route use the dependencies
`get_current_user`, `get_current_user_optional` and `require_auth` from
`zeeb_api`.

## Filtering

`SearchFilter` reads `?search=` across `search_fields` (a caret prefix means
starts-with, an equals prefix means exact). `OrderingFilter` reads
`?ordering=` restricted to `ordering_fields`, which defaults to what the
serializer returns. A `FilterSet` declares one query parameter per lookup:

```python
from zeeb_api.filters import FilterSet

from .models import Post


class PostFilter(FilterSet):
    class Meta:
        model = Post
        fields = {"status": ["exact", "in"], "created_at": ["gte", "lte"]}
```

Project-wide: `DEFAULT_FILTER_BACKENDS` in settings.

## Pagination, throttling, versioning

- Paginated responses carry `count`, `next`, `previous`, `results`; cursor
  pagination reports no count. Page size comes from `DEFAULT_LIMIT` and is
  capped by `MAX_LIMIT`.
- The generated project throttles with `AnonRateThrottle` and
  `UserRateThrottle` (`THROTTLE_ANON_RATE`, `THROTTLE_USER_RATE` in `.env`);
  `ScopedRateThrottle` keys on `throttle_scope`. Rates read `"100/min"`,
  `"10/s"`, `"1000/hour"`, `"10000/day"`.
- Versioning is `HeaderVersioning` on `X-API-Version`, bounded by
  `ALLOWED_VERSIONS`; the viewset sees `self.version`, a plain route uses
  `get_api_version`.

## Errors (`zeeb_api.exceptions`)

Raise the canonical hierarchy, never a bare `HTTPException`, so every error
stays inside the envelope:

```json
{"success": false, "error": {"code": "RESOURCE_NOT_FOUND", "message": "Post not found"}}
```

- Typed: `ValidationException` (400), `AuthenticationException` (401),
  `PermissionException` (403), `ResourceNotFoundException` (404),
  `ResourceConflictException` (409), `QueryException` (400),
  `RateLimitException` (429), `ServerException` (500),
  `MethodNotAllowedException` (405). Codes come from `ErrorCode`, for
  example `ErrorCode.FIELD_INVALID_EMAIL`, `ErrorCode.PERM_INSUFFICIENT_ROLE`,
  `ErrorCode.RESOURCE_NOT_FOUND`.
- Shorthand: `NotFound`, `ValidationError`, `PermissionDenied`,
  `AuthenticationFailed`, `MethodNotAllowed`, `Throttled`, `APIException`.
- Helpers: `field_error()`, `validation_error()`.

```python
from zeeb_api.exceptions import ErrorCode, NotFound, ValidationException, field_error

raise NotFound("Post not found")
raise ValidationException(
    message="Validation failed",
    details=[field_error("email", ErrorCode.FIELD_INVALID_EMAIL, "Invalid email")],
)
```

## Authentication

Already wired: `POST /auth/register`, `/auth/login`, `/auth/refresh`,
`/auth/logout`, `GET /auth/me` under the API prefix, `JWTAuthMiddleware`
setting `request.state.user`, and the user model in
`apps/accounts/models.py` extending `AbstractUser` (`email` is the login
field; `username`, `first_name`, `last_name`, `is_active`, `is_staff`,
`is_superuser`, `date_joined`, password hashing). Add fields to that class;
never create a second user model. Create users with `create_user()` and
`create_superuser()` from `zeeb_api`, or `python manage.py createsuperuser`.
OAuth providers activate when their client id is set in `.env`
(`GOOGLE_CLIENT_ID`, `GITHUB_CLIENT_ID`, `AZURE_CLIENT_ID`).

## Settings and environment

`@PROJECT@/settings.py` reads every value from `.env` through
`zeeb_api.conf.env` (`load_env`, `env_str`, `env_bool`, `env_int`,
`env_list`). The keys that matter most: `DEBUG`, `SECRET_KEY`, `DATABASE_URL`,
`API_PREFIX`, `CORS_ALLOW_ORIGINS`, `CORS_ALLOW_ORIGIN_REGEX`,
`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`,
`AUTH_ENABLE_REGISTRATION`, `THROTTLE_ANON_RATE`, `DEFAULT_VERSIONING_CLASS`,
`ALLOWED_VERSIONS`, `LOG_LEVEL`. With `DEBUG=false` an insecure or short
`SECRET_KEY` makes `create_app()` refuse to start (`InsecureSecretError`).
"""

REFERENCE_LIFECYCLE_MD = """\
# The feature lifecycle from the shell

`zeeb_agents` ships with the framework: one async function per operation,
each returning `success`, `message` and `data`, never raising. The intent
layer compiles a whole feature — app, models, serializers, viewsets, routes,
migrations, tests, verification — from one spec, and records what it owns in
`.zeeb/features.json` so the feature can later be switched off or deleted.
Features built by hand are only inferred from disk and cannot be deactivated.

The skill ships a runner so none of this needs an MCP server:

```bash
python .agents/skills/zeebpy/scripts/zeeb_agent.py describe_project
python .agents/skills/zeebpy/scripts/zeeb_agent.py plan_feature '{"spec": {...}}'
```

The first argument is the function name, the second a JSON object of keyword
arguments. It prints the result as one JSON object and exits 0 on
`success`. The project is the one the script lives in.

## Start

- `get_started` — the build recipe and the recommended next action.
- `describe_project` — apps, models, endpoints, migrations, runtime, and
  `warnings` for any wiring gap. The fastest way to orient.
- `list_capabilities` — every function with its signature; pass
  `{"tier": "core"}` for the ones to start with.

## Build

`plan_feature` validates a spec against the project and returns the ordered
plan without writing anything — every problem at once, under `problems`.
`build_feature` runs the same compiler and executor and then migrates and
verifies. Re-running it with an extended spec adds what is new and never
removes anything; dropped fields come back under `drift`.

```json
{
  "spec": {
    "name": "blog",
    "entities": [
      {
        "name": "Post",
        "fields": [
          {"name": "title", "type": "string", "max_length": 200},
          {"name": "body", "type": "text"},
          {"name": "status", "type": "enum", "values": ["draft", "live"],
           "default": "draft"},
          {"name": "author", "type": "relation", "target": "User",
           "cardinality": "many-to-one", "required": false}
        ],
        "functions": [
          {"name": "publish", "kind": "action", "detail": true,
           "methods": ["post"], "actor": "admin",
           "body": "post = await self.get_object()\\nreturn {\\"ok\\": True}"}
        ]
      }
    ],
    "api": {"authentication": "read_only_public"}
  }
}
```

FeatureSpec keys:

- top level: `name`, `app` (defaults to the name), `entities`, `functions`,
  `api`, `auth`, `description`.
- entity: `name`, `fields`, `timestamps` (default on: `created_at` and
  `updated_at`), `ordering`, `constraints`, `indexes`, `api`, `functions`,
  `workflow`.
- field: `name`, `type` — `string`, `text`, `integer`, `float`, `decimal`,
  `boolean`, `datetime`, `date`, `uuid`, `email`, `json`, or the exact
  `zeeb_orm` class name — plus `required` (default true), `max_length`,
  `default`, `unique`; `enum` takes `values`; `relation` takes `target`,
  `cardinality` (`many-to-one`, `one-to-one`, `many-to-many`) and
  `required`. `target` is an entity in the spec, an existing model, or
  `User`.
- `api`: `authentication` — `public`, `read_only_public` or `required` —
  `permissions`, `ownership`, `operations`, `search_fields`,
  `ordering_fields`, `pagination`, `expose`.
- function: `kind` is `action` (`entity`, `detail`, `methods`, `body`,
  `actor` or `permission`), `endpoint` (`path`, `method`, `body`), `hook`
  (`entity`, `trigger`), `task` (`schedule`) or `rule` (`logic`). `actor`
  is `anyone`, `authenticated`, `owner` or `admin`.

## Change

`change_feature` takes semantic changes, not file edits; pass `feature` to
keep the stored spec current:

```json
{
  "feature": "blog",
  "changes": [
    {"operation": "add_field", "entity": "Post",
     "field": {"name": "subtitle", "type": "string", "required": false}},
    {"operation": "alter_field", "entity": "Post",
     "field": {"name": "body", "type": "text"}},
    {"operation": "add_relation", "entity": "Post",
     "field": {"name": "category", "target": "Category", "cardinality": "many-to-one"}},
    {"operation": "remove_field", "entity": "Post", "field_name": "subtitle"},
    {"operation": "set_authentication", "entity": "Post", "authentication": "required"}
  ]
}
```

Other operations: `add_entity`, `remove_entity`, `set_permissions`,
`add_function`, `remove_function`, `add_workflow`, `add_transition`.
`alter_field` restates the whole definition in place; a remove plus add would
drop the column's data.

## Verify and diagnose

- `verify_project` — the acceptance gate: code compiles and imports,
  migrations are applied, tests pass, routes are served. `data.verified` is
  the verdict; never report a change as done unless it is true. Note that a
  failed verification does not flip `success`.
- `diagnose_problem` — pass `symptom` and optionally `endpoint`; correlates
  settings, wiring, migrations and logs into a root cause and a fix.
- `check_code`, `run_tests`, `make_migrations`, `run_migrations`,
  `list_models`, `describe_table`, `run_query` (read-only SQL, always rolled
  back) and `read_logs` are the smaller gates.

## Manage

- `list_features` — name, app, status, entities, endpoints; hand-written
  apps show as inferred.
- `deactivate_feature` — archives the serializers, viewsets, routes and tests
  into the archive directory and leaves `models.py` and the tables alone. No
  migration, no data loss. `activate_feature` restores them verbatim.
- `delete_feature` — removes the models too and writes the migration that
  drops the tables; refuses without `confirm` and reports the scope under
  `would_delete`.
- `regenerate_tests` — rewrites a feature's generated test file after its
  fields changed.

`.zeeb/` is committed project state. A `partial_failure` result carries
`steps_completed` and `remaining_operations`: fix the first failed operation
and re-run the same call — completed steps are skipped.
"""

REFERENCE_MIGRATIONS_MD = """\
# Migrations

Migration files live flat in `migrations/`, are generated, and are never
edited by hand: change the model and generate again.

```bash
python manage.py makemigrations                 # after any model change
python manage.py makemigrations --name add_post_status
python manage.py makemigrations --dry-run       # show the operations, write nothing
python manage.py makemigrations --check         # exit 1 if a change has no migration
python manage.py migrate                        # apply everything pending
python manage.py migrate 0003                   # move the schema to one migration
python manage.py migrate zero                   # unapply everything
python manage.py migrate --rollback 1           # back one step
python manage.py migrate --plan                 # what would run
python manage.py migrate --fake                 # record as applied, run nothing
python manage.py migrate --fake-initial         # tables already exist
python manage.py showmigrations                 # [X] applied, [ ] pending
python manage.py squashmigrations 0001_initial 0005_add_views --name initial_to_views
```

- Migrations run in dependency order under a database-wide lock, so several
  replicas can run `migrate` on startup and each migration applies once.
- `check` fails on a fresh project until the first `makemigrations` and
  `migrate` have run, and says so.
- A removed and an added field with the same definition look like a rename.
  `makemigrations` reports them as possible renames and writes a remove plus
  an add, which drops the old column's data. If it is a rename, delete that
  file and run `python manage.py makemigrations --accept-renames`, which
  writes a rename operation instead.
- Adding a `null=False` column without a `default` to a table with rows
  fails; give the field a default or `null=True` first.
- Changing `on_delete` on an existing `ForeignKey` and check constraints are
  not auto-detected; write those operations by hand in an `--empty`
  migration.
- A squashed migration records the originals it replaces; delete the
  original files only after the squash is deployed everywhere.
- `pytest` passes before the first migration: the test `db` fixture builds
  the schema from the model registry, so a missing migration shows up in
  `python manage.py check`, not in the tests.
"""

REFERENCE_TROUBLESHOOTING_MD = """\
# Troubleshooting

## Read the envelope

Every management command with `--json`, and every function the runner calls,
answers with `success`, `message` and `data`. On failure `data` carries:

| key | meaning |
|---|---|
| `error_code` | why, from a closed vocabulary |
| `next_command` | the exact command to run next (management commands) |
| `recoverable` | whether a corrected argument can succeed |
| `state_changed` | whether anything was written; `null` means unknown — re-run |
| `suggestions` | close matches for a rejected name |
| `problems` | every spec problem at once (`plan_feature`, `change_feature`) |

Management command codes: `project_not_found`, `file_not_found`,
`app_not_found`, `model_not_found`, `invalid_identifier`, `invalid_input`,
`already_exists`, `setting_not_found`, `dependency_missing`,
`permission_denied`, `partial_failure`.

Runner codes you will meet: `invalid_field_spec` (a relation needs a
target; `SET_NULL` needs `null=True`), `invalid_field_type`,
`invalid_permission`, `syntax_error` (the edit would not parse; nothing was
written), `field_in_use`, `feature_in_use`, `model_referenced`,
`verification_failed`, `manifest_version_unsupported` (another tool owns a
newer `.zeeb/features.json`; do not rewrite it), `delete_pending`,
`outside_project_root`, `invalid_sql`, `query_timeout`.

## Common failures

| Symptom | Cause and fix |
|---|---|
| `python manage.py check` fails on a fresh project | no migration yet: run `makemigrations` then `migrate` |
| `InsecureSecretError` at startup | `DEBUG=false` with a missing or known-insecure `SECRET_KEY`; set one in `.env` |
| a relation to the user resolves to the wrong class | the bare `"User"` label; use `"accounts.User"` |
| a setting change has no effect | the value is read from `.env`; the literal in `settings.py` is only the default |
| the tooling cannot find a setting | the assignment was wrapped onto several lines; keep each on one line |
| a new app's models never migrate or its routes 404 | the app is not registered; `startapp` adds it to `INSTALLED_APPS` and `urls.py`, `--no-wire` skips that |
| `ImproperlyConfigured` when importing a serializer | a declared field is missing from `fields`, or `read_only_fields` names something the model does not have |
| `FieldError` when building a query | an unknown field, lookup or `ordering` entry; there is no not-equal lookup |
| `NotSupportedError` from a serializer | an unfetched to-many relation under the synchronous `data` property; use `adata()` or `prefetch_related()` |
| `DoesNotExist` escapes as a 500 | catch it and raise `NotFound` from `zeeb_api.exceptions` |
| 400 `VALIDATION_ERROR` from the query endpoint | the filter names a field outside the allow-list; list it in `query_fields` |
| 400 `API_VERSION_INVALID` | the `X-API-Version` header is outside `ALLOWED_VERSIONS` |
| 401 `AUTH_TOKEN_MISSING` on an endpoint meant to be public | the viewset inherits `DEFAULT_PERMISSION_CLASSES`; set `permission_classes` to an empty list |
| 429 in tests or behind several workers | the rate limit; set `THROTTLE_ANON_RATE` and `THROTTLE_USER_RATE`, or a shared throttle cache |
| migration applies on one replica and fails on another | the originals of a squash are only partly applied there; keep the original files until every database has the squash |
| a versions subdirectory appeared under the migrations | something assumed another layout; migrations are flat in `migrations/` |

When a symptom is not in the table, run the runner's `diagnose_problem`
with the symptom text and the failing endpoint, and read its root cause
before changing anything.
"""

AGENT_RUNNER_PY = '''\
"""Call one ``zeeb_agents`` function on this project from the shell.

    python .agents/skills/zeebpy/scripts/zeeb_agent.py <function> ['{"kwarg": value}']

Prints the result as one JSON object — ``success``, ``message``, ``data`` —
and exits 0 on success, 1 on a reported failure, 2 on bad arguments. The
project is the one this script lives in; ``project_id`` is filled in.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

import zeeb_agents

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main(argv: list[str]) -> int:
    """Run ``argv[1]`` with the keyword arguments in ``argv[2]`` and print the result."""
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 2
    name = argv[1]
    function = getattr(zeeb_agents, name, None)
    if name not in zeeb_agents.__all__ or not callable(function):
        print(json.dumps({"success": False, "message": f"unknown function: {name}"}))
        return 2
    try:
        kwargs = json.loads(argv[2]) if len(argv) > 2 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"success": False, "message": f"arguments are not JSON: {exc}"}))
        return 2
    if not isinstance(kwargs, dict):
        print(json.dumps({"success": False, "message": "arguments must be a JSON object"}))
        return 2
    if "project_id" in inspect.signature(function).parameters:
        zeeb_agents.configure(project_resolver=lambda _project_id: PROJECT_ROOT)
        kwargs.setdefault("project_id", PROJECT_ROOT.name)
    result = asyncio.run(function(**kwargs))
    payload = {"success": result.success, "message": result.message, "data": result.data}
    print(json.dumps(payload, indent=2, default=str))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

#: The reference files, by name under ``references/``.
SKILL_REFERENCES: dict[str, str] = {
    "orm.md": REFERENCE_ORM_MD,
    "api.md": REFERENCE_API_MD,
    "lifecycle.md": REFERENCE_LIFECYCLE_MD,
    "migrations.md": REFERENCE_MIGRATIONS_MD,
    "troubleshooting.md": REFERENCE_TROUBLESHOOTING_MD,
}


def render_skill_md(project_name: str) -> str:
    """``SKILL.md`` for a project named *project_name*."""
    return SKILL_MD.replace(PROJECT_PLACEHOLDER, project_name)


def render_skill_files(project_name: str) -> dict[str, str]:
    """Every skill file, as ``{project-relative path: content}``.

    The canonical skill under :data:`SKILL_DIR` (``SKILL.md``, ``references/``,
    ``scripts/``) and Claude Code's entry point under :data:`CLAUDE_SKILL_DIR`,
    which is the same ``SKILL.md`` byte for byte.
    """
    skill_md = render_skill_md(project_name)
    files = {f"{SKILL_DIR}/SKILL.md": skill_md}
    for name, content in SKILL_REFERENCES.items():
        files[f"{SKILL_DIR}/references/{name}"] = content.replace(
            PROJECT_PLACEHOLDER, project_name
        )
    files[f"{SKILL_DIR}/scripts/zeeb_agent.py"] = AGENT_RUNNER_PY
    files[f"{CLAUDE_SKILL_DIR}/SKILL.md"] = skill_md
    return files
