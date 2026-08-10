"""Generated-test scaffolding: every built feature ships a runnable smoke suite.

``build_feature``/``apply_plan`` emit a ``generate_tests`` plan op (default
``tests=True``) that writes, per feature app, a pytest file exercising each
entity: an ORM roundtrip (create → get → update → delete), a uniqueness
constraint test, an API list smoke, write-side CRUD over HTTP, serializer
validation (empty body → 400), a permission negative (anonymous write → 401/403),
and a workflow-transition conflict test (legal POST → 200, repeat → 409). One
OpenAPI contract test per app asserts every served endpoint is documented.

Tests are generated for the identity the endpoint's permission actually admits:
``conftest.py`` provides ``client`` (anonymous), ``auth_client`` and
``admin_client`` (JWT-minted). A permission no synthesized identity can satisfy
(``IsOwner``, ``ModelPermissions``, a project's own class) generates no test
rather than one that cannot pass. Operations the spec withheld are never
exercised. The shared ``tests/conftest.py`` boots the project app against an
isolated sqlite file.

Generation is idempotent and **never overwrites an existing file** — the
generated tests are a starting point the user may edit freely.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import fail
from zeeb_agents._utils.field_types import FIELD_TYPE_MAP

# The shared harness (pytest.ini + the conftest fixtures) lives one layer down
# so that ``zeeb startproject`` and this module write the *same* file: a feature
# generated here has to run against the fixtures the CLI already shipped.
from zeeb_orm.scaffold.harness import CONFTEST_PY as _CONFTEST  # noqa: F401  (public alias)
from zeeb_orm.scaffold.harness import PYTEST_INI as _PYTEST_INI
from zeeb_orm.scaffold.harness import find_settings_module as _settings_module
from zeeb_orm.scaffold.harness import render_conftest

#: Permission classes that let an unauthenticated request through. Anything
#: else — including a project's own permission class — is treated as closed.
_ANONYMOUS_READ_PERMISSIONS = frozenset({"AllowAny", "IsAuthenticatedOrReadOnly"})
_ANONYMOUS_WRITE_PERMISSIONS = frozenset({"AllowAny"})

#: Only for descriptors compiled before ``permission`` was recorded on them.
_AUTHENTICATION_FALLBACK = {
    "public": "AllowAny",
    "read_only_public": "IsAuthenticatedOrReadOnly",
    "required": "IsAuthenticated",
}

_FILE_HEADER = '''\
"""Generated smoke tests for the '{app}' feature. Safe to edit."""

from __future__ import annotations

import pytest
'''

# Sample literals per native field type (rendered with repr()).
_SAMPLE_VALUES = {
    "CharField": "sample",
    "TextField": "sample text",
    "IntegerField": 1,
    "SmallIntegerField": 1,
    "BigIntegerField": 1,
    "PositiveIntegerField": 1,
    "FloatField": 1.5,
    "DecimalField": "9.99",
    "BooleanField": True,
    "DateField": "2026-01-01",
    "DateTimeField": "2026-01-01T00:00:00",
    "TimeField": "12:00:00",
    "DurationField": 60,
    "JSONField": {},
    "UUIDField": "00000000-0000-0000-0000-000000000001",
    "EmailField": "sample@example.com",
    "SlugField": "sample",
    "URLField": "https://example.com",
    "GenericIPAddressField": "127.0.0.1",
}


def _native_type(field: dict) -> str:
    ftype = str(field.get("type", ""))
    return FIELD_TYPE_MAP.get(ftype) or FIELD_TYPE_MAP.get(ftype.lower()) or ftype


def _is_required_input(field: dict) -> bool:
    """Whether a create() call must supply this field."""
    if field.get("null") or field.get("blank") or "default" in field:
        return False
    if field.get("auto_now") or field.get("auto_now_add"):
        return False
    return _native_type(field) != "ManyToManyField"


def _sample_literal(field: dict) -> str | None:
    native = _native_type(field)
    if field.get("choices"):
        return repr(field["choices"][0][0])
    value = _SAMPLE_VALUES.get(native)
    if value is None:
        return None
    if native == "CharField" and isinstance(value, str):
        value = value[: int(field.get("max_length", 255))]
    return repr(value)


def _entity_create_kwargs(entity: dict) -> tuple[list[str], list[str], bool]:
    """(kwargs lines, required spec-internal fk targets, supported?) for create()."""
    kwargs: list[str] = []
    fk_targets: list[str] = []
    for field in entity.get("fields", []):
        if not _is_required_input(field):
            continue
        native = _native_type(field)
        if native in ("ForeignKey", "OneToOneField"):
            to = str(field.get("to", ""))
            if "." in to or to == "self":
                return [], [], False  # external/self target — ORM roundtrip skipped
            fk_targets.append(to)
            kwargs.append(f"{field['name']}_id={to.lower()}.id")
            continue
        literal = _sample_literal(field)
        if literal is None:
            return [], [], False  # no sample synthesis for this type
        kwargs.append(f"{field['name']}={literal}")
    return kwargs, fk_targets, True


def _render_orm_test(app: str, entity: dict, by_name: dict[str, dict]) -> str | None:
    name = entity["name"]
    kwargs, fk_targets, supported = _entity_create_kwargs(entity)
    lines: list[str] = []
    if not supported:
        return None
    setup: list[str] = []
    imports = {name}
    for target in fk_targets:
        target_entity = by_name.get(target)
        if target_entity is None:
            return None
        t_kwargs, t_fks, t_ok = _entity_create_kwargs(target_entity)
        if not t_ok or t_fks:  # one level of fk chasing is enough for a smoke test
            return None
        imports.add(target)
        setup.append(f"    {target.lower()} = await {target}.objects.create({', '.join(t_kwargs)})")
    lines.append(f"async def test_{name.lower()}_orm_roundtrip(db):")
    lines.append(f"    from apps.{app}.models import {', '.join(sorted(imports))}")
    lines.append("")
    lines.extend(setup)
    lines.append(f"    obj = await {name}.objects.create({', '.join(kwargs)})")
    lines.append(f"    fetched = await {name}.objects.get(id=obj.id)")
    lines.append("    assert fetched.id == obj.id")
    patchable = _first_patchable_field(entity)
    if patchable is not None:
        field_name, literal = patchable
        lines.append(f"    fetched.{field_name} = {literal}")
        lines.append("    await fetched.save()")
        lines.append(f"    assert (await {name}.objects.get(id=obj.id)).{field_name} == {literal}")
    lines.append("    await obj.delete()")
    lines.append(f"    assert await {name}.objects.filter(id=obj.id).count() == 0")
    return "\n".join(lines)


def _entity_permissions(entity: dict) -> list[str]:
    """The permission classes the endpoint was actually built with.

    Falls back to the ``authentication`` shorthand for descriptors compiled
    before the resolved permission was recorded.
    """
    permission = entity.get("permission")
    if permission:
        return [permission] if isinstance(permission, str) else list(permission)
    return [_AUTHENTICATION_FALLBACK.get(entity.get("authentication"), "IsAuthenticated")]


def _allows_anonymous(entity: dict, *, write: bool) -> bool:
    """Whether an unauthenticated request may reach this endpoint.

    Permission classes are ANDed at request time (``ViewSet.check_permissions``),
    so every class must allow it. An unrecognized class — a hand-written one from
    the project — is treated as closed: generating a test that assumes otherwise
    is what produced false failures.
    """
    allowed = _ANONYMOUS_WRITE_PERMISSIONS if write else _ANONYMOUS_READ_PERMISSIONS
    permissions = _entity_permissions(entity)
    return bool(permissions) and all(p in allowed for p in permissions)


#: Permission class → the conftest fixture whose identity satisfies it. A class
#: that is absent here (IsOwner, ModelPermissions, a project's own class) cannot
#: be satisfied by a synthesized identity, so tests needing it are not generated
#: at all — a generated test that cannot pass is worse than no test.
_READ_CLIENT_FOR = {
    "AllowAny": "client",
    "IsAuthenticatedOrReadOnly": "client",
    "IsAuthenticated": "auth_client",
    "IsAdminUser": "admin_client",
}
_WRITE_CLIENT_FOR = {
    "AllowAny": "client",
    "IsAuthenticatedOrReadOnly": "auth_client",
    "IsAuthenticated": "auth_client",
    "IsAdminUser": "admin_client",
}

#: Widest fixture wins when several permission classes are ANDed together.
_CLIENT_RANK = {"client": 0, "auth_client": 1, "admin_client": 2}


def _client_fixture(entity: dict, *, write: bool) -> str | None:
    """The fixture that can satisfy every permission class on this endpoint."""
    table = _WRITE_CLIENT_FOR if write else _READ_CLIENT_FOR
    chosen = "client"
    for permission in _entity_permissions(entity):
        needed = table.get(permission)
        if needed is None:
            return None
        if _CLIENT_RANK[needed] > _CLIENT_RANK[chosen]:
            chosen = needed
    return chosen


def _serves(entity: dict, operation: str) -> bool:
    """Whether the endpoint actually exposes *operation* (see api.operations)."""
    operations = entity.get("operations")
    if not operations:
        return True  # descriptor predates operations — assume full CRUD
    return operation in operations


def _render_api_smoke(entity: dict) -> str | None:
    """GET the collection as whoever the endpoint's permission admits."""
    if not entity.get("exposed") or not entity.get("prefix") or not _serves(entity, "list"):
        return None
    fixture = _client_fixture(entity, write=False)
    if fixture is None:
        return None
    name = entity["name"].lower()
    prefix = entity["prefix"]
    return (
        f"async def test_{name}_endpoint_lists({fixture}, api_prefix):\n"
        f'    resp = await {fixture}.get(f"{{api_prefix}}/{prefix}")\n'
        "    assert resp.status_code == 200"
    )


def _render_anonymous_write_rejected(
    app: str, entity: dict, by_name: dict[str, dict]
) -> str | None:
    """A gated endpoint must actually reject an unauthenticated write.

    The body has to be *valid*: an empty one is rejected at request-schema
    validation (422) before the permission layer ever runs, which would assert
    nothing about the gate.
    """
    if not entity.get("exposed") or not entity.get("prefix") or not _serves(entity, "create"):
        return None
    if _allows_anonymous(entity, write=True):
        return None  # deliberately open — nothing to assert
    prepared = _prepare_payload(app, entity, by_name)
    if prepared is None:
        return None
    setup, imports, body = prepared
    name = entity["name"].lower()
    prefix = entity["prefix"]
    lines = [f"async def test_{name}_rejects_anonymous_create(client, api_prefix, db):"]
    if setup:
        lines.append(f"    from apps.{app}.models import {', '.join(sorted(imports))}")
        lines.append("")
        lines.extend(setup)
        lines.append("")
    lines += [
        f'    resp = await client.post(f"{{api_prefix}}/{prefix}", json={body})',
        "    # 401 unauthenticated / 403 authenticated-but-forbidden; never a write.",
        "    assert resp.status_code in (401, 403), resp.text",
    ]
    return "\n".join(lines)


def _render_required_field_validation(entity: dict) -> str | None:
    """An empty create body must be rejected as a client error, never accepted."""
    if not entity.get("exposed") or not entity.get("prefix") or not _serves(entity, "create"):
        return None
    fixture = _client_fixture(entity, write=True)
    if fixture is None:
        return None
    required = [f["name"] for f in entity.get("fields", []) if _is_required_input(f)]
    if not required:
        return None
    name = entity["name"].lower()
    prefix = entity["prefix"]
    return "\n".join(
        [
            f"async def test_{name}_create_rejects_missing_required_fields({fixture}, api_prefix):",
            f'    resp = await {fixture}.post(f"{{api_prefix}}/{prefix}", json={{}})',
            f"    # required: {', '.join(required)}",
            "    # 422 from request-schema validation, 400 from serializer validation.",
            "    assert resp.status_code in (400, 422), resp.text",
        ]
    )


def _prepare_payload(
    app: str, entity: dict, by_name: dict[str, dict]
) -> tuple[list[str], set[str], str] | None:
    """(ORM setup lines, model imports, JSON body literal) for a create request."""
    payload = _json_payload(entity)
    if payload is None:
        return None
    body, fk_targets = payload
    setup: list[str] = []
    imports: set[str] = set()
    for target in fk_targets:
        target_entity = by_name.get(target)
        if target_entity is None:
            return None
        t_kwargs, t_fks, t_ok = _entity_create_kwargs(target_entity)
        if not t_ok or t_fks:  # one level of FK chasing only
            return None
        imports.add(target)
        setup.append(f"    {target.lower()} = await {target}.objects.create({', '.join(t_kwargs)})")
    return setup, imports, body


def _json_payload(entity: dict) -> tuple[str, list[str]] | None:
    """A JSON-safe create body literal plus the FK targets it needs set up."""
    items: list[str] = []
    fk_targets: list[str] = []
    for field in entity.get("fields", []):
        if not _is_required_input(field):
            continue
        native = _native_type(field)
        if native in ("ForeignKey", "OneToOneField"):
            to = str(field.get("to", ""))
            if "." in to or to == "self":
                return None
            fk_targets.append(to)
            items.append(f'"{field["name"]}_id": str({to.lower()}.id)')
            continue
        literal = _sample_literal(field)
        if literal is None:
            return None
        items.append(f'"{field["name"]}": {literal}')
    return "{" + ", ".join(items) + "}", fk_targets


def _render_http_crud(app: str, entity: dict, by_name: dict[str, dict]) -> str | None:
    """Exercise the write half of the endpoint over HTTP, honoring api.operations."""
    if not entity.get("exposed") or not entity.get("prefix") or not _serves(entity, "create"):
        return None
    fixture = _client_fixture(entity, write=True)
    if fixture is None:
        return None
    prepared = _prepare_payload(app, entity, by_name)
    if prepared is None:
        return None
    setup, imports, body = prepared

    name = entity["name"]
    prefix = entity["prefix"]
    lines = [
        f"async def test_{name.lower()}_crud_over_http({fixture}, api_prefix, db):",
    ]
    if setup:
        lines.append(f"    from apps.{app}.models import {', '.join(sorted(imports))}")
        lines.append("")
        lines.extend(setup)
        lines.append("")
    lines += [
        f'    created = await {fixture}.post(f"{{api_prefix}}/{prefix}", json={body})',
        "    assert created.status_code == 201, created.text",
        '    obj_id = created.json()["id"]',
    ]
    if _serves(entity, "retrieve"):
        lines += [
            f'    fetched = await {fixture}.get(f"{{api_prefix}}/{prefix}/{{obj_id}}")',
            "    assert fetched.status_code == 200",
        ]
    if _serves(entity, "update"):
        patch_field = _first_patchable_field(entity)
        if patch_field is not None:
            field_name, literal = patch_field
            lines += [
                f'    patched = await {fixture}.patch(',
                f'        f"{{api_prefix}}/{prefix}/{{obj_id}}",'
                f' json={{"{field_name}": {literal}}}',
                "    )",
                "    assert patched.status_code == 200, patched.text",
                f'    assert patched.json()["{field_name}"] == {literal}',
            ]
    if _serves(entity, "delete"):
        lines += [
            f'    removed = await {fixture}.delete(f"{{api_prefix}}/{prefix}/{{obj_id}}")',
            "    assert removed.status_code == 204",
            f'    gone = await {fixture}.get(f"{{api_prefix}}/{prefix}/{{obj_id}}")',
            "    assert gone.status_code == 404",
        ]
    return "\n".join(lines)


def _first_patchable_field(entity: dict) -> tuple[str, str] | None:
    """A scalar field whose value can be changed and echoed back verbatim."""
    for field in entity.get("fields", []):
        native = _native_type(field)
        if native not in ("CharField", "TextField", "SlugField"):
            continue
        if field.get("choices") or field.get("auto_now") or field.get("auto_now_add"):
            continue
        if field.get("unique"):
            continue
        return field["name"], repr("patched")
    return None


def _render_unique_constraint_test(app: str, entity: dict) -> str | None:
    """A declared uniqueness constraint must actually be enforced by the database."""
    groups = entity.get("unique_together") or []
    if not groups:
        return None
    kwargs, fk_targets, supported = _entity_create_kwargs(entity)
    if not supported or fk_targets or not kwargs:
        return None
    name = entity["name"]
    fields = groups[0]
    return "\n".join(
        [
            f"async def test_{name.lower()}_enforces_unique_{'_'.join(fields)}(db):",
            f"    from apps.{app}.models import {name}",
            "",
            f"    await {name}.objects.create({', '.join(kwargs)})",
            f"    # unique_together {fields} — the duplicate must not be storable.",
            "    with pytest.raises(Exception):",
            f"        await {name}.objects.create({', '.join(kwargs)})",
        ]
    )


def _render_openapi_contract(entities: list[dict]) -> str | None:
    """Every served endpoint must appear in the OpenAPI document."""
    served = [e for e in entities if e.get("exposed") and e.get("prefix")]
    if not served:
        return None
    expectations = [
        f'        ("/{e["prefix"]}", {sorted(_openapi_methods(e))!r}),' for e in served
    ]
    return "\n".join(
        [
            "async def test_openapi_contract_lists_every_endpoint(client, api_prefix):",
            '    resp = await client.get("/openapi.json")',
            "    assert resp.status_code == 200",
            '    paths = resp.json()["paths"]',
            "    for suffix, methods in (",
            *expectations,
            "    ):",
            "        path = f\"{api_prefix}{suffix}\"",
            "        detail = f\"{path}/{{id}}\"",
            "        assert path in paths or detail in paths, f\"{path} missing from OpenAPI\"",
            "        for method in methods:",
            "            assert any(",
            "                method in (paths.get(candidate) or {})",
            "                for candidate in (path, detail, f\"{path}/\", f\"{detail}/\")",
            "            ), f\"{method.upper()} {path} missing from OpenAPI\"",
        ]
    )


def _openapi_methods(entity: dict) -> set[str]:
    """HTTP methods the entity's operations must document."""
    methods: set[str] = set()
    if _serves(entity, "list"):
        methods.add("get")
    if _serves(entity, "create"):
        methods.add("post")
    if _serves(entity, "update"):
        methods.add("patch")
    if _serves(entity, "delete"):
        methods.add("delete")
    return methods


def _anonymous_transition_ok(entity: dict, transition: dict) -> bool:
    """Whether an unauthenticated POST may call this transition.

    A per-transition ``AllowAny`` overrides the endpoint permission; without
    one the transition inherits it, and only a fully open endpoint accepts an
    anonymous POST (``IsAuthenticatedOrReadOnly`` opens GET only).
    """
    permission = transition.get("permission")
    if permission == "AllowAny":
        return True
    return permission is None and _allows_anonymous(entity, write=True)


def _render_workflow_test(app: str, entity: dict) -> str | None:
    workflow = entity.get("workflow")
    if not workflow or not entity.get("exposed") or not entity.get("prefix"):
        return None
    transitions = workflow.get("transitions") or []
    # Pick a transition anonymous requests may call, leaving from the initial state.
    initial = workflow.get("initial")
    candidate = next(
        (
            t
            for t in transitions
            if _anonymous_transition_ok(entity, t) and initial in (t.get("from_states") or [])
        ),
        None,
    )
    if candidate is None:
        return None
    kwargs, fk_targets, supported = _entity_create_kwargs(entity)
    if not supported or fk_targets:
        return None
    name = entity["name"]
    field = workflow.get("field", "status")
    return "\n".join(
        [
            f"async def test_{name.lower()}_{candidate['name']}_transition_conflict(client, api_prefix, db):",
            f"    from apps.{app}.models import {name}",
            "",
            f"    obj = await {name}.objects.create({', '.join(kwargs)})",
            f'    first = await client.post(f"{{api_prefix}}/{entity["prefix"]}/{{obj.id}}/{candidate["name"]}")',
            "    assert first.status_code == 200",
            f'    assert first.json()["{field}"] == "{candidate["to"]}"',
            f'    again = await client.post(f"{{api_prefix}}/{entity["prefix"]}/{{obj.id}}/{candidate["name"]}")',
            "    assert again.status_code == 409",
        ]
    )


@agent_function
async def generate_tests(
    app: str,
    entities: list[dict],
    filename: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Write generated smoke tests for a feature app (idempotent, never overwrites).

    Creates ``tests/__init__.py``, ``tests/conftest.py`` (project app against
    an isolated sqlite file, plus anonymous/authenticated/admin clients), a root
    ``pytest.ini`` (asyncio auto mode), and ``tests/test_<app>_generated.py``
    with per-entity ORM roundtrips, uniqueness constraints, API list smokes,
    write-side CRUD over HTTP, serializer validation, permission negatives,
    workflow-transition conflicts, and one OpenAPI contract test.
    Existing files are always left untouched.

    Args:
        app: The feature app the tests exercise.
        entities: Compiled entity descriptors — ``{"name", "prefix",
            "exposed", "authentication", "permission", "operations", "fields",
            "unique_together"?, "workflow"?}`` (the ``generate_tests`` plan op
            carries these; fields are compiled field specs).
        filename: Project-relative path for the generated test file (default
            ``tests/test_<app>_generated.py``). Because existing files are never
            overwritten, adding an entity to an app that already has a generated
            suite needs its own filename or nothing would be written.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        created (list[str]): project-relative paths written.
        skipped (list[str]): paths that already existed and were kept.
        tests (int): number of test functions in the generated file (0 when
            the file already existed).
    """
    root = project_root
    if root is None or not Path(root).is_dir():
        return fail(f"Project not found: {root}", code="project_not_found")
    root = Path(root)
    settings_module = _settings_module(root)
    if settings_module is None:
        return fail(
            f"No settings.py found under {root}", code="project_not_found"
        )

    by_name = {e["name"]: e for e in entities}
    blocks: list[str] = []
    for entity in entities:
        for rendered in (
            _render_orm_test(app, entity, by_name),
            _render_unique_constraint_test(app, entity),
            _render_api_smoke(entity),
            _render_http_crud(app, entity, by_name),
            _render_required_field_validation(entity),
            _render_anonymous_write_rejected(app, entity, by_name),
            _render_workflow_test(app, entity),
        ):
            if rendered:
                blocks.append(rendered)
    contract = _render_openapi_contract(entities)
    if contract:
        blocks.append(contract)

    created: list[str] = []
    skipped: list[str] = []

    def _write(relative: str, content: str) -> None:
        path = root / relative
        if path.exists():
            skipped.append(relative)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(relative)

    target = filename or f"tests/test_{app}_generated.py"

    def _write_all() -> None:
        _write("pytest.ini", _PYTEST_INI)
        _write("tests/__init__.py", "")
        _write("tests/conftest.py", render_conftest(settings_module))
        body = _FILE_HEADER.format(app=app)
        if blocks:
            body += "\n\n" + "\n\n\n".join(blocks) + "\n"
        _write(target, body)

    await asyncio.to_thread(_write_all)
    test_count = len(blocks) if target in created else 0
    message = (
        f"Generated {len(created)} test file(s) for '{app}'"
        if created
        else f"Test files for '{app}' already exist; skipped"
    )
    return AgentResult(
        success=True,
        message=message,
        data={"created": created, "skipped": skipped, "tests": test_count},
    )
