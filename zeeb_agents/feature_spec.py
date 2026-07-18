"""FeatureSpec — declarative feature specification: validate, compile, execute.

Private helpers behind the intent workflow functions in
:mod:`zeeb_agents.intent`.  A *FeatureSpec* describes a bounded backend
feature (entities, relations, API exposure) as one JSON-friendly dict; this
module validates it (collecting **every** problem, not just the first),
compiles it into a deterministic *plan* (an ordered list of operations, each
mapping 1:1 to an existing exported agent function), and executes such plans
idempotently.

Nothing here is exported from the package — the public surface is the intent
functions, which share this single compiler/executor so ``plan_feature`` →
``apply_plan`` and ``build_feature`` can never diverge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.code_gen import pluralize as _pluralize
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.field_types import (
    FIELD_TYPE_MAP,
    validate_field_spec,
)
from zeeb_agents._utils.project import get_app_path

PLAN_VERSION = 1

# FeatureSpec relation cardinality → native field-type alias.
CARDINALITY_MAP = {
    "many-to-one": "fk",
    "one-to-one": "o2o",
    "many-to-many": "m2m",
}

# FeatureSpec api.authentication → viewset permission class.
AUTHENTICATION_MAP = {
    "required": "IsAuthenticated",
    "read_only_public": "IsAuthenticatedOrReadOnly",
    "public": "AllowAny",
}

VALID_OPERATIONS = ("list", "retrieve", "create", "update", "delete")
_READ_ONLY_OPERATIONS = {"list", "retrieve"}

_API_DEFAULTS: dict[str, Any] = {
    "expose": True,
    "operations": list(VALID_OPERATIONS),
    "authentication": "read_only_public",
    "permissions": None,
    "search_fields": None,
    "ordering_fields": None,
    "pagination": None,
}

_TIMESTAMP_FIELDS = (
    {"name": "created_at", "type": "DateTimeField", "auto_now_add": True},
    {"name": "updated_at", "type": "DateTimeField", "auto_now": True},
)

# Every op type a plan may contain, mapped to the exported agent function of
# the same name (the executor dispatches on this).
KNOWN_OPS = (
    "create_app",
    "create_model",
    "create_serializer",
    "create_viewset",
    "register_route",
    "add_field",
    "remove_field",
    "add_relationship",
    "create_user_model",
    "setup_auth",
    "setup_oauth",
    "create_health_endpoint",
    "make_migrations",
    "run_migrations",
)

_DB_CHANGING_OPS = {
    "create_model",
    "create_user_model",
    "add_field",
    "remove_field",
    "add_relationship",
    "make_migrations",
    "run_migrations",
}
_DESTRUCTIVE_OPS = {"remove_field"}
_MIGRATION_OPS = {"make_migrations", "run_migrations"}


def _problem(
    path: str, code: str, message: str, suggestions: list[str] | None = None
) -> dict:
    entry: dict = {"path": path, "code": code, "message": message}
    if suggestions:
        entry["suggestions"] = suggestions
    return entry


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and value.isidentifier()


def _resolve_relation_target(
    target: object,
    path: str,
    entity_names: list[str],
    existing_models: list[dict],
    app: str,
    problems: list[dict],
) -> str | None:
    """Resolve a FeatureSpec relation target to a native ``to`` reference."""
    if not isinstance(target, str) or not target:
        problems.append(
            _problem(path, "invalid_field_spec", "relation needs a 'target' model name")
        )
        return None
    if target == "self":
        return "self"
    if "." in target:
        return target  # dotted app.Model — passed through as-is
    if target in entity_names:
        return target  # another entity in this spec (same app)
    matches = [m for m in existing_models if m.get("model") == target]
    if len(matches) == 1:
        owner = matches[0].get("app")
        return target if owner == app else f"{owner}.{target}"
    if len(matches) > 1:
        dotted = sorted(f"{m['app']}.{target}" for m in matches)
        problems.append(
            _problem(
                path,
                "invalid_input",
                f"relation target '{target}' is ambiguous — use a dotted "
                f"reference: {', '.join(dotted)}",
                suggestions=dotted,
            )
        )
        return None
    known = entity_names + sorted({m["model"] for m in existing_models})
    problems.append(
        _problem(
            path,
            "model_not_found",
            f"relation target '{target}' matches no entity in this spec and no "
            f"existing model. Known models: {', '.join(known) or '(none)'}",
            suggestions=close_matches(target, known),
        )
    )
    return None


def _compile_field(
    field: dict,
    path: str,
    entity_names: list[str],
    existing_models: list[dict],
    app: str,
    problems: list[dict],
    warnings: list[str],
) -> dict | None:
    """Translate one FeatureSpec field into the native field-spec dialect."""
    spec = dict(field)
    name = spec.get("name")
    if not _is_identifier(name):
        problems.append(
            _problem(path, "invalid_field_spec", f"field needs an identifier 'name', got {name!r}")
        )
        return None
    ftype = spec.get("type")
    if not isinstance(ftype, str) or not ftype:
        problems.append(_problem(path, "invalid_field_spec", f"field '{name}' is missing 'type'"))
        return None

    required = spec.pop("required", True)

    if ftype == "relation":
        cardinality = spec.pop("cardinality", "many-to-one")
        if cardinality == "one-to-many":
            problems.append(
                _problem(
                    path,
                    "invalid_input",
                    f"field '{name}': cardinality 'one-to-many' is not declared on "
                    "this side — declare the relation as 'many-to-one' on the "
                    "target entity instead",
                )
            )
            return None
        alias = CARDINALITY_MAP.get(cardinality)
        if alias is None:
            problems.append(
                _problem(
                    path,
                    "invalid_input",
                    f"field '{name}': unknown cardinality '{cardinality}'. "
                    f"Valid: {', '.join(CARDINALITY_MAP)}",
                    suggestions=close_matches(str(cardinality), list(CARDINALITY_MAP)),
                )
            )
            return None
        to = _resolve_relation_target(
            spec.pop("target", None), path, entity_names, existing_models, app, problems
        )
        if to is None:
            return None
        spec["type"] = alias
        spec["to"] = to
        if alias == "m2m":
            # M2M rows are inherently optional and zeeb_orm's M2M accepts no
            # null/blank kwargs — the required flag simply does not apply.
            pass
        else:
            spec.setdefault("on_delete", "CASCADE")
            if required is False:
                spec.setdefault("null", True)
                spec.setdefault("blank", True)
    elif ftype == "enum":
        values = spec.pop("values", None)
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, str) and v for v in values)
        ):
            problems.append(
                _problem(
                    path,
                    "invalid_field_spec",
                    f"field '{name}': enum needs a non-empty 'values' list of strings",
                )
            )
            return None
        default = spec.get("default")
        if default is not None and default not in values:
            problems.append(
                _problem(
                    path,
                    "invalid_field_spec",
                    f"field '{name}': enum default {default!r} is not one of "
                    f"{values}",
                    suggestions=close_matches(str(default), values),
                )
            )
            return None
        spec["type"] = "CharField"
        spec["choices"] = [[v, v] for v in values]
        spec.setdefault("max_length", max(len(v) for v in values))
        if required is False:
            spec.setdefault("null", True)
            spec.setdefault("blank", True)
    else:
        resolved = FIELD_TYPE_MAP.get(ftype) or FIELD_TYPE_MAP.get(ftype.lower())
        if resolved is None:
            problems.append(
                _problem(
                    path,
                    "invalid_field_type",
                    f"field '{name}': unknown type '{ftype}'",
                    suggestions=close_matches(ftype, list(FIELD_TYPE_MAP)),
                )
            )
            return None
        spec["type"] = ftype
        if resolved == "CharField":
            spec.setdefault("max_length", 255)
        if required is False:
            spec.setdefault("null", True)
            spec.setdefault("blank", True)

    # Delegate every remaining rule (on_delete values, SET_NULL⇒null, M2M
    # kwargs, literal-renderable values, …) to the shared validator so the
    # compiler and the low-level tools can never disagree.
    try:
        validate_field_spec(spec)
    except AgentError as exc:
        data = exc.result.data or {}
        problems.append(
            _problem(
                path,
                data.get("error_code", "invalid_field_spec"),
                str(exc),
                suggestions=data.get("suggestions"),
            )
        )
        return None
    return spec


def _merged_api(spec: dict, entity: dict, path: str, problems: list[dict]) -> dict:
    merged = {**_API_DEFAULTS, **(spec.get("api") or {}), **(entity.get("api") or {})}
    ops = merged.get("operations")
    if not isinstance(ops, list) or not ops or not set(ops) <= set(VALID_OPERATIONS):
        bad = [o for o in ops if o not in VALID_OPERATIONS] if isinstance(ops, list) else [ops]
        problems.append(
            _problem(
                f"{path}.api.operations",
                "invalid_input",
                f"invalid operations {bad!r}. Valid: {', '.join(VALID_OPERATIONS)}",
                suggestions=close_matches(str(bad[0]), list(VALID_OPERATIONS)) if bad else None,
            )
        )
    auth = merged.get("authentication")
    if auth not in AUTHENTICATION_MAP:
        problems.append(
            _problem(
                f"{path}.api.authentication",
                "invalid_authentication",
                f"invalid authentication {auth!r}. Valid: "
                f"{', '.join(AUTHENTICATION_MAP)}",
                suggestions=close_matches(str(auth), list(AUTHENTICATION_MAP)),
            )
        )
    return merged


def _topo_order(entities: list[dict], warnings: list[str]) -> list[dict]:
    """Order entities so fk/o2o targets are created first (Kahn's algorithm).

    Deterministic: ready entities are emitted in declaration order.  A cycle
    (legal — zeeb_orm resolves string references lazily) falls back to
    declaration order for the cyclic remainder plus a warning.
    """
    names = [e["name"] for e in entities]
    deps: dict[str, set[str]] = {}
    for e in entities:
        wanted: set[str] = set()
        for f in e.get("fields", []) or []:
            if not isinstance(f, dict) or f.get("type") != "relation":
                continue
            if f.get("cardinality", "many-to-one") == "many-to-many":
                continue
            target = f.get("target")
            if isinstance(target, str) and target in names and target != e["name"]:
                wanted.add(target)
        deps[e["name"]] = wanted

    ordered: list[str] = []
    remaining = set(names)
    while remaining:
        ready = [n for n in names if n in remaining and not (deps[n] & remaining)]
        if not ready:
            leftovers = [n for n in names if n in remaining]
            warnings.append(
                f"Circular relation(s) among {', '.join(leftovers)} — keeping "
                "declaration order (string references resolve lazily)."
            )
            ordered.extend(leftovers)
            break
        for n in ready:
            ordered.append(n)
            remaining.discard(n)

    index = {n: i for i, n in enumerate(ordered)}
    return sorted(entities, key=lambda e: index[e["name"]])


def validate_feature_spec(
    spec: object,
    existing_models: list[dict],
    existing_apps: list[str],
) -> list[dict]:
    """Validate a FeatureSpec, collecting **every** problem found.

    Returns a list of ``{"path", "code", "message", "suggestions"?}`` dicts —
    empty when the spec is valid.  Mirrors ``validate_field_specs``'s
    report-everything-at-once behavior at the spec level.
    """
    problems: list[dict] = []
    if not isinstance(spec, dict):
        return [
            _problem("spec", "invalid_input", f"spec must be a dict, got {type(spec).__name__}")
        ]

    name = spec.get("name")
    if not _is_identifier(name):
        problems.append(
            _problem(
                "spec.name",
                "invalid_identifier",
                f"spec needs an identifier 'name', got {name!r}",
            )
        )
    app = spec.get("app", name)
    if not _is_identifier(app):
        problems.append(
            _problem("spec.app", "invalid_identifier", f"'app' must be an identifier, got {app!r}")
        )

    entities = spec.get("entities")
    if not isinstance(entities, list) or not entities:
        problems.append(
            _problem("spec.entities", "invalid_input", "spec needs a non-empty 'entities' list")
        )
        return problems

    entity_names: list[str] = []
    for i, entity in enumerate(entities):
        path = f"spec.entities[{i}]"
        if not isinstance(entity, dict):
            problems.append(_problem(path, "invalid_input", "entity must be a dict"))
            continue
        ename = entity.get("name")
        if not _is_identifier(ename):
            problems.append(
                _problem(
                    f"{path}.name",
                    "invalid_identifier",
                    f"entity needs an identifier 'name', got {ename!r}",
                )
            )
            continue
        if ename in entity_names:
            problems.append(
                _problem(f"{path}.name", "already_exists", f"duplicate entity '{ename}' in spec")
            )
        entity_names.append(ename)

    scratch_warnings: list[str] = []
    for i, entity in enumerate(entities):
        if not isinstance(entity, dict) or not _is_identifier(entity.get("name")):
            continue
        path = f"spec.entities[{i}]"
        fields = entity.get("fields")
        if not isinstance(fields, list) or not fields:
            problems.append(
                _problem(
                    f"{path}.fields",
                    "invalid_field_spec",
                    f"entity '{entity['name']}' needs a non-empty 'fields' list",
                )
            )
            continue
        seen: set[str] = set()
        for j, field in enumerate(fields):
            fpath = f"{path}.fields[{j}]"
            if not isinstance(field, dict):
                problems.append(_problem(fpath, "invalid_field_spec", "field must be a dict"))
                continue
            fname = field.get("name")
            if isinstance(fname, str):
                if fname in seen:
                    problems.append(
                        _problem(
                            fpath,
                            "already_exists",
                            f"duplicate field '{fname}' on entity '{entity['name']}'",
                        )
                    )
                seen.add(fname)
            _compile_field(
                field,
                fpath,
                entity_names,
                existing_models,
                app if isinstance(app, str) else "",
                problems,
                scratch_warnings,
            )
        for j, constraint in enumerate(entity.get("constraints") or []):
            cpath = f"{path}.constraints[{j}]"
            if not isinstance(constraint, dict) or constraint.get("type") != "unique":
                problems.append(
                    _problem(
                        cpath,
                        "invalid_input",
                        "only {'type': 'unique', 'fields': [...]} constraints are supported",
                    )
                )
                continue
            cfields = constraint.get("fields")
            known = seen | {"id", "created_at", "updated_at"}
            if not isinstance(cfields, list) or not cfields or not set(cfields) <= known:
                problems.append(
                    _problem(
                        cpath,
                        "invalid_input",
                        f"unique constraint fields {cfields!r} must name fields of "
                        f"entity '{entity['name']}'",
                    )
                )
        ordering = entity.get("ordering")
        if ordering is not None and (
            not isinstance(ordering, list) or not all(isinstance(o, str) for o in ordering)
        ):
            problems.append(
                _problem(
                    f"{path}.ordering",
                    "invalid_input",
                    "'ordering' must be a list of field names (prefix with '-' for descending)",
                )
            )
        _merged_api(spec, entity, path, problems)

    return problems


def compile_feature_spec(
    spec: dict,
    existing_models: list[dict],
    existing_apps: list[str],
) -> dict:
    """Compile a validated FeatureSpec into a deterministic plan dict.

    Raises :class:`AgentError` (code = the first problem's code, full list in
    ``data["problems"]``) when validation fails; the same spec against the
    same project state always compiles to the same plan.
    """
    problems = validate_feature_spec(spec, existing_models, existing_apps)
    if problems:
        raise AgentError(
            f"FeatureSpec has {len(problems)} problem(s): "
            + "; ".join(p["message"] for p in problems[:5])
            + ("; …" if len(problems) > 5 else ""),
            code=problems[0]["code"],
            problems=problems,
            suggestions=problems[0].get("suggestions"),
        )

    name: str = spec["name"]
    app: str = spec.get("app", name)
    warnings: list[str] = []
    operations: list[dict] = []

    if app not in existing_apps:
        operations.append({"op": "create_app", "app": app})

    existing_names = {m["model"] for m in existing_models if m.get("app") == app}
    entity_names = [e["name"] for e in spec["entities"]]
    endpoints = 0

    for entity in _topo_order(spec["entities"], warnings):
        ename = entity["name"]
        if ename in existing_names:
            warnings.append(
                f"Model '{app}.{ename}' already exists — its create steps will "
                "be skipped (idempotent)."
            )
        fields: list[dict] = []
        for j, field in enumerate(entity["fields"]):
            compiled = _compile_field(
                field, f"spec.entities.{ename}.fields[{j}]",
                entity_names, existing_models, app, [], warnings,
            )
            if compiled is not None:
                fields.append(compiled)
        field_names = [f["name"] for f in fields]
        if entity.get("timestamps", True):
            for ts in _TIMESTAMP_FIELDS:
                if ts["name"] not in field_names:
                    fields.append(dict(ts))
                    field_names.append(ts["name"])

        meta: dict = {}
        if entity.get("ordering"):
            meta["ordering"] = entity["ordering"]
        unique_sets = [
            c["fields"] for c in (entity.get("constraints") or []) if c.get("type") == "unique"
        ]
        if unique_sets:
            meta["unique_together"] = unique_sets

        operations.append(
            {
                "op": "create_model",
                "app": app,
                "model": ename,
                "fields": fields,
                "meta": meta or None,
            }
        )

        api = _merged_api(spec, entity, ename, [])
        if not api["expose"]:
            continue
        endpoints += 1
        requested = list(dict.fromkeys(api["operations"]))
        read_only = set(requested) <= _READ_ONLY_OPERATIONS
        if not read_only and set(requested) != set(VALID_OPERATIONS):
            warnings.append(
                f"Entity '{ename}': operations {requested} approximated by a "
                "full-CRUD endpoint (endpoints are read-only or full CRUD)."
            )
        permission = api["permissions"] or AUTHENTICATION_MAP[api["authentication"]]
        if api["authentication"] == "required" and not api["permissions"]:
            warnings.append(
                f"Entity '{ename}': authentication='required' assumes JWT auth "
                "is wired — run configure_auth or bootstrap_project first if it "
                "is not."
            )
        serializer_fields = ["id"] + field_names
        read_only_fields = ["id"] + [
            ts["name"] for ts in _TIMESTAMP_FIELDS if ts["name"] in field_names
        ]
        operations.append(
            {
                "op": "create_serializer",
                "app": app,
                "model": ename,
                "fields": serializer_fields,
                "read_only_fields": read_only_fields,
            }
        )
        viewset_op: dict = {
            "op": "create_viewset",
            "app": app,
            "model": ename,
            "permission": permission,
            "read_only": read_only,
        }
        for key in ("search_fields", "ordering_fields", "pagination"):
            if api.get(key):
                viewset_op[key] = api[key]
        operations.append(viewset_op)
        operations.append(
            {
                "op": "register_route",
                "app": app,
                "model": ename,
                "prefix": _pluralize(ename.lower()),
            }
        )

    if (spec.get("auth") or {}).get("required"):
        warnings.append(
            "spec.auth.required is informational — the plan does not wire "
            "authentication. Run configure_auth (or bootstrap_project) to set "
            "up JWT auth."
        )

    operations.append({"op": "make_migrations", "name": name})
    operations.append({"op": "run_migrations"})

    return {
        "plan_version": PLAN_VERSION,
        "summary": (
            f"Create feature '{name}': {len(entity_names)} model(s), "
            f"{endpoints} endpoint(s) in app '{app}'"
        ),
        "feature": {"name": name, "app": app},
        "operations": operations,
        "risk": _plan_risk(operations),
        "warnings": warnings,
    }


def compile_changes(
    changes: object,
    existing_models: list[dict],
    existing_apps: list[str],
    app: str | None,
) -> tuple[list[dict], list[str]]:
    """Compile ``change_feature`` semantic changes into plan operations.

    Returns ``(operations, warnings)``; raises :class:`AgentError` with the
    collected problems when the changes are invalid.
    """
    problems: list[dict] = []
    warnings: list[str] = []
    operations: list[dict] = []

    if not isinstance(changes, list) or not changes:
        raise AgentError(
            "changes must be a non-empty list of change dicts, e.g. "
            '[{"operation": "add_field", "entity": "Post", '
            '"field": {"name": "subtitle", "type": "string"}}]',
            code="invalid_input",
        )

    def _entity_app(entity: str, path: str) -> str | None:
        matches = [m for m in existing_models if m.get("model") == entity]
        if app is not None:
            if _is_identifier(app):
                return app
            problems.append(
                _problem(path, "invalid_identifier", f"'app' must be an identifier, got {app!r}")
            )
            return None
        if len(matches) == 1:
            return matches[0]["app"]
        if len(matches) > 1:
            candidates = sorted(m["app"] for m in matches)
            problems.append(
                _problem(
                    path,
                    "invalid_input",
                    f"entity '{entity}' exists in several apps "
                    f"({', '.join(candidates)}) — pass app= to disambiguate",
                    suggestions=candidates,
                )
            )
            return None
        known = sorted({m["model"] for m in existing_models})
        problems.append(
            _problem(
                path,
                "model_not_found",
                f"entity '{entity}' matches no existing model. Known: "
                f"{', '.join(known) or '(none)'}",
                suggestions=close_matches(entity, known),
            )
        )
        return None

    known_change_ops = ("add_field", "remove_field", "add_relation", "add_entity")
    for i, change in enumerate(changes):
        path = f"changes[{i}]"
        if not isinstance(change, dict):
            problems.append(_problem(path, "invalid_input", "change must be a dict"))
            continue
        operation = change.get("operation")
        if operation == "add_relationship":
            operation = "add_relation"
        if operation not in known_change_ops:
            problems.append(
                _problem(
                    path,
                    "invalid_input",
                    f"unknown operation {operation!r}. Valid: "
                    f"{', '.join(known_change_ops)}",
                    suggestions=close_matches(str(operation), list(known_change_ops)),
                )
            )
            continue

        if operation == "add_entity":
            entity = change.get("entity")
            if not isinstance(entity, dict):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        "add_entity needs 'entity': a full entity dict (name, fields, …)",
                    )
                )
                continue
            target_app = change.get("app") or app
            if not _is_identifier(target_app):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        "add_entity needs 'app' (or a top-level app=) naming an existing app",
                    )
                )
                continue
            sub_spec = {
                "name": target_app,
                "app": target_app,
                "entities": [entity],
            }
            try:
                sub_plan = compile_feature_spec(sub_spec, existing_models, existing_apps)
            except AgentError as exc:
                data = exc.result.data or {}
                for p in data.get("problems", []):
                    problems.append({**p, "path": f"{path}.{p['path']}"})
                continue
            operations.extend(
                op for op in sub_plan["operations"] if op["op"] not in _MIGRATION_OPS
            )
            warnings.extend(sub_plan["warnings"])
            continue

        entity_name = change.get("entity")
        if not _is_identifier(entity_name):
            problems.append(
                _problem(
                    path,
                    "invalid_input",
                    f"'{operation}' needs 'entity': an existing model name, got {entity_name!r}",
                )
            )
            continue
        entity_app = _entity_app(entity_name, path)
        if entity_app is None:
            continue

        if operation == "remove_field":
            field_name = change.get("field_name") or change.get("field")
            if not _is_identifier(field_name):
                problems.append(
                    _problem(path, "invalid_input", "remove_field needs 'field_name'")
                )
                continue
            operations.append(
                {
                    "op": "remove_field",
                    "app": entity_app,
                    "model": entity_name,
                    "field_name": field_name,
                }
            )
            continue

        # add_field / add_relation share the FeatureSpec field dialect.
        raw_field = change.get("field") or change.get("relation")
        if not isinstance(raw_field, dict):
            problems.append(
                _problem(path, "invalid_input", f"'{operation}' needs 'field': a field spec dict")
            )
            continue
        if operation == "add_relation" and "type" not in raw_field:
            raw_field = {**raw_field, "type": "relation"}
        compiled = _compile_field(
            raw_field, f"{path}.field", [], existing_models, entity_app, problems, warnings
        )
        if compiled is None:
            continue
        op_name = "add_relationship" if compiled["type"] in ("fk", "o2o", "m2m") else "add_field"
        key = "rel" if op_name == "add_relationship" else "field"
        operations.append(
            {"op": op_name, "app": entity_app, "model": entity_name, key: compiled}
        )

    if problems:
        raise AgentError(
            f"changes have {len(problems)} problem(s): "
            + "; ".join(p["message"] for p in problems[:5])
            + ("; …" if len(problems) > 5 else ""),
            code=problems[0]["code"],
            problems=problems,
            suggestions=problems[0].get("suggestions"),
        )
    return operations, warnings


def _plan_risk(operations: list[dict]) -> dict:
    database_changes = any(op["op"] in _DB_CHANGING_OPS for op in operations)
    destructive = any(op["op"] in _DESTRUCTIVE_OPS for op in operations)
    return {
        "level": "medium" if (database_changes or destructive) else "low",
        "destructive": destructive,
        "database_changes": database_changes,
    }


def validate_plan(plan: object) -> str | None:
    """Light structural check of a plan dict; returns a problem string or None."""
    if not isinstance(plan, dict):
        return f"plan must be a dict (the object plan_feature returned), got {type(plan).__name__}"
    if plan.get("plan_version") != PLAN_VERSION:
        return (
            f"plan_version {plan.get('plan_version')!r} is not supported "
            f"(expected {PLAN_VERSION}) — re-run plan_feature to get a fresh plan"
        )
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        return "plan has no 'operations' list — re-run plan_feature"
    for i, op in enumerate(operations):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            return f"operations[{i}] is not an operation dict — re-run plan_feature"
        if op["op"] not in KNOWN_OPS:
            hint = close_matches(op["op"], list(KNOWN_OPS))
            suffix = f" Did you mean: {', '.join(hint)}?" if hint else ""
            return f"operations[{i}]: unknown op '{op['op']}'.{suffix} Re-run plan_feature."
    return None


def new_changes() -> dict:
    """Fresh accumulator for the intent-envelope ``changes`` dict."""
    return {
        "apps_created": [],
        "models_created": [],
        "endpoints_created": [],
        "fields_added": [],
        "fields_removed": [],
        "migrations_created": [],
        "migrations_applied": [],
    }


def _skipped(result: AgentResult) -> bool:
    return bool(result.data and result.data.get("skipped"))


def _tolerated(result: AgentResult, codes: tuple[str, ...]) -> bool:
    """True when a failure is an acceptable no-op for idempotent re-runs."""
    return bool(result.data and result.data.get("error_code") in codes)


async def execute_plan(
    plan: dict,
    project_root: Path,
    migrate: bool = True,
) -> dict:
    """Execute a plan's operations sequentially and idempotently.

    Structural ops run with ``if_exists="skip"`` (the ``generate_crud``
    resumability pattern) and failures are collected rather than aborting, so
    a re-run completes whatever is missing.  Migration ops stop the run on
    failure (nothing meaningful follows them).

    Returns ``{"changes", "steps", "errors", "warnings", "state_changed"}``.
    ``warnings`` holds non-fatal, project-global conditions (e.g. an unrelated
    app not in ``INSTALLED_APPS``) that must not flip a successful build to a
    failure.
    """
    from zeeb_agents.auth_scaffold import create_user_model, setup_auth, setup_oauth
    from zeeb_agents.health import create_health_endpoint
    from zeeb_agents.migrations import make_migrations, run_migrations
    from zeeb_agents.models import add_field, add_relationship, create_model, remove_field
    from zeeb_agents.project import create_app
    from zeeb_agents.serializers import create_serializer
    from zeeb_agents.viewsets import create_viewset, register_route

    changes = new_changes()
    steps: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    state_changed = False

    for op in plan.get("operations", []):
        kind = op["op"]
        if kind in _MIGRATION_OPS and not migrate:
            steps.append(f"Skipped {kind} (migrate=False)")
            continue

        if kind == "create_app":
            if get_app_path(op["app"], project_root).is_dir():
                steps.append(f"App '{op['app']}' already exists")
                continue
            result = await create_app(op["app"], project_id=project_root)
            if result.success:
                changes["apps_created"].append(op["app"])
                steps.append(f"Created app '{op['app']}'")
                state_changed = True
            else:
                errors.append(f"create_app({op['app']}): {result.message}")
        elif kind == "create_model":
            result = await create_model(
                op["app"], op["model"], op["fields"],
                meta=op.get("meta"), if_exists="skip", project_id=project_root,
            )
            if result.success:
                if _skipped(result):
                    steps.append(f"Model '{op['model']}' already exists; skipped")
                else:
                    changes["models_created"].append(f"{op['app']}.{op['model']}")
                    steps.append(f"Created model '{op['model']}'")
                    state_changed = True
            else:
                errors.append(f"create_model({op['model']}): {result.message}")
        elif kind == "create_serializer":
            result = await create_serializer(
                op["app"], op["model"],
                fields=op.get("fields"), read_only_fields=op.get("read_only_fields"),
                if_exists="skip", project_id=project_root,
            )
            if result.success:
                if not _skipped(result):
                    steps.append(f"Created serializer '{op['model']}Serializer'")
                    state_changed = True
            else:
                errors.append(f"create_serializer({op['model']}): {result.message}")
        elif kind == "create_viewset":
            result = await create_viewset(
                op["app"], op["model"],
                permission=op.get("permission", "IsAuthenticatedOrReadOnly"),
                read_only=op.get("read_only", False),
                pagination=op.get("pagination"),
                search_fields=op.get("search_fields"),
                ordering_fields=op.get("ordering_fields"),
                if_exists="skip", project_id=project_root,
            )
            if result.success:
                if not _skipped(result):
                    steps.append(f"Created viewset '{op['model']}ViewSet'")
                    state_changed = True
            else:
                errors.append(f"create_viewset({op['model']}): {result.message}")
        elif kind == "register_route":
            result = await register_route(
                op["app"], op["model"], url_prefix=op.get("prefix"),
                if_exists="skip", project_id=project_root,
            )
            if result.success:
                prefix = (result.data or {}).get("prefix", op.get("prefix"))
                if not _skipped(result):
                    changes["endpoints_created"].append(prefix)
                    steps.append(f"Registered route '{prefix}/'")
                    state_changed = True
            else:
                errors.append(f"register_route({op['model']}): {result.message}")
        elif kind == "add_field":
            result = await add_field(op["app"], op["model"], op["field"], project_id=project_root)
            if result.success:
                changes["fields_added"].append(f"{op['model']}.{op['field']['name']}")
                steps.append(f"Added field '{op['model']}.{op['field']['name']}'")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append(f"Field '{op['model']}.{op['field']['name']}' already exists; skipped")
            else:
                errors.append(f"add_field({op['model']}): {result.message}")
        elif kind == "add_relationship":
            result = await add_relationship(
                op["app"], op["model"], op["rel"], project_id=project_root
            )
            if result.success:
                changes["fields_added"].append(f"{op['model']}.{op['rel']['name']}")
                steps.append(f"Added relation '{op['model']}.{op['rel']['name']}'")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append(
                    f"Relation '{op['model']}.{op['rel']['name']}' already exists; skipped"
                )
            else:
                errors.append(f"add_relationship({op['model']}): {result.message}")
        elif kind == "remove_field":
            result = await remove_field(
                op["app"], op["model"], op["field_name"], project_id=project_root
            )
            if result.success:
                changes["fields_removed"].append(f"{op['model']}.{op['field_name']}")
                steps.append(f"Removed field '{op['model']}.{op['field_name']}'")
                state_changed = True
            elif _tolerated(result, ("field_not_found",)):
                steps.append(f"Field '{op['model']}.{op['field_name']}' already absent; skipped")
            else:
                errors.append(f"remove_field({op['model']}): {result.message}")
        elif kind == "create_user_model":
            result = await create_user_model(
                op["app"], op.get("model_name", "User"),
                extra_fields=op.get("extra_fields"), project_id=project_root,
            )
            if result.success:
                changes["models_created"].append(f"{op['app']}.{op.get('model_name', 'User')}")
                steps.append(f"Created user model '{op.get('model_name', 'User')}'")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append("User model already exists; skipped")
            else:
                errors.append(f"create_user_model: {result.message}")
        elif kind == "setup_auth":
            result = await setup_auth(
                enable_registration=op.get("enable_registration", True),
                project_id=project_root,
            )
            if result.success:
                wired = (result.data or {}).get("wired")
                steps.append("Wired JWT auth" if wired else "JWT auth already wired")
                state_changed = state_changed or bool(wired)
            else:
                errors.append(f"setup_auth: {result.message}")
        elif kind == "setup_oauth":
            result = await setup_oauth(
                op["provider"], scopes=op.get("scopes"), project_id=project_root
            )
            if result.success:
                steps.append(f"Configured OAuth provider '{op['provider']}'")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append(f"OAuth provider '{op['provider']}' already configured; skipped")
            else:
                errors.append(f"setup_oauth({op['provider']}): {result.message}")
        elif kind == "create_health_endpoint":
            result = await create_health_endpoint(project_id=project_root)
            if result.success:
                steps.append("Created health endpoints (/health, /ready)")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append("health.py already exists; skipped")
            else:
                errors.append(f"create_health_endpoint: {result.message}")
        elif kind == "make_migrations":
            result = await make_migrations(name=op.get("name"), project_id=project_root)
            if result.success:
                created = (result.data or {}).get("created")
                if created:
                    changes["migrations_created"].append(created)
                    steps.append(f"Created migration '{created}'")
                    state_changed = True
                else:
                    steps.append("No migration needed")
                if (result.data or {}).get("warning"):
                    # A project-global, non-fatal condition (e.g. an unrelated
                    # app on disk not in INSTALLED_APPS) — surface it as a
                    # warning, never as an error, or a fully successful build
                    # would be reported as a failure the user cannot clear.
                    warnings.append(f"make_migrations: {result.data['warning']}")
            else:
                errors.append(f"make_migrations: {result.message}")
                break
        elif kind == "run_migrations":
            result = await run_migrations(project_id=project_root)
            if result.success:
                applied = (result.data or {}).get("applied", [])
                changes["migrations_applied"].extend(applied)
                steps.append(
                    f"Applied {len(applied)} migration(s)" if applied else "No pending migrations"
                )
                state_changed = state_changed or bool(applied)
            else:
                errors.append(f"run_migrations: {result.message}")
                break
        else:  # pragma: no cover — validate_plan gates unknown ops
            errors.append(f"unknown op '{kind}'")
            break

    return {
        "changes": changes,
        "steps": steps,
        "errors": errors,
        "warnings": warnings,
        "state_changed": state_changed,
    }
