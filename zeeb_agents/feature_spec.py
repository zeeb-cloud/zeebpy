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
from zeeb_agents._utils.code_gen import VIEWSET_OPERATIONS
from zeeb_agents._utils.code_gen import pluralize as _pluralize
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.field_types import (
    FIELD_TYPE_MAP,
    validate_field_spec,
)

PLAN_VERSION = 2

#: Plan versions ``apply_plan`` accepts. v1 plans (compiled before the
#: staleness fingerprint existed) still apply — with a warning that full
#: checking is unavailable.
SUPPORTED_PLAN_VERSIONS = frozenset({1, PLAN_VERSION})

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

#: One source of truth with the code generator — the spec vocabulary and the
#: mixin table must not drift.
VALID_OPERATIONS = VIEWSET_OPERATIONS
_READ_ONLY_OPERATIONS = {"list", "retrieve"}

_API_DEFAULTS: dict[str, Any] = {
    "expose": True,
    "operations": list(VALID_OPERATIONS),
    "authentication": "read_only_public",
    "permissions": None,
    #: Per-row ownership. ``{"field": "owner", "scope_reads": true}`` — or the
    #: shorthand ``"owner"`` — makes the endpoint owner-gated: the permission
    #: class becomes IsOwner/IsOwnerOrReadOnly, perform_create stamps the
    #: authenticated user, and (with scope_reads) reads are filtered to the
    #: caller's rows. The FK field is added to the entity when it is missing.
    "ownership": None,
    "search_fields": None,
    "ordering_fields": None,
    "pagination": None,
}

#: Permission classes for an owner-gated endpoint, by authentication mode.
#: Classes are ANDed at request time, so pairing IsAuthenticated with IsOwner
#: gives an anonymous caller a 401 instead of an empty 200.
_OWNERSHIP_PERMISSIONS = {
    "required": ["IsAuthenticated", "IsOwner"],
    "read_only_public": ["IsOwnerOrReadOnly"],
    "public": ["IsOwnerOrReadOnly"],
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
    "sync_serializer",
    "delete_serializer",
    "create_viewset",
    "update_viewset",
    "delete_viewset",
    "register_route",
    "unregister_route",
    "add_field",
    "alter_field",
    "remove_field",
    "add_relationship",
    "delete_model",
    "add_viewset_action",
    "create_route",
    "create_signal_receiver",
    "create_task",
    "create_permission_class",
    "delete_function",
    "generate_tests",
    "create_user_model",
    "setup_auth",
    "setup_oauth",
    "create_health_endpoint",
    "make_migrations",
    "run_migrations",
)

# Per-op required payload keys: validate_plan rejects payloads that could only
# fail (or silently mis-execute) deep inside the executor. Kept in lockstep
# with KNOWN_OPS (guarded by tests).
_OP_REQUIRED: dict[str, tuple[str, ...]] = {
    "create_app": ("app",),
    "create_model": ("app", "model", "fields"),
    "create_serializer": ("app", "model"),
    "sync_serializer": ("app", "model", "field_name"),
    "delete_serializer": ("app", "model"),
    "create_viewset": ("app", "model"),
    "update_viewset": ("app", "model"),
    "delete_viewset": ("app", "model"),
    "register_route": ("app", "model"),
    "unregister_route": ("app", "model"),
    "add_field": ("app", "model", "field"),
    "alter_field": ("app", "model", "field"),
    "remove_field": ("app", "model", "field_name"),
    "add_relationship": ("app", "model", "rel"),
    "delete_model": ("app", "model"),
    "add_viewset_action": ("app", "model", "action_name", "body"),
    "create_route": ("app", "path", "method", "function_name"),
    "create_signal_receiver": ("app", "signal_name", "model_name", "function_name"),
    "create_task": ("app", "function_name"),
    "create_permission_class": ("app", "class_name"),
    "delete_function": ("app", "name", "kind"),
    "generate_tests": ("app", "entities"),
    "create_user_model": ("app",),
    "setup_auth": (),
    "setup_oauth": ("provider",),
    "create_health_endpoint": (),
    "make_migrations": (),
    "run_migrations": (),
}

# Recognized keys per level — unknown keys warn (never error) so typos like
# "filds" cannot be silently ignored.
_SPEC_KEYS = {"name", "app", "entities", "api", "auth", "description", "functions"}
_ENTITY_KEYS = {
    "name", "fields", "timestamps", "ordering", "constraints", "indexes", "api", "workflow",
    "functions",
}
_WORKFLOW_KEYS = {"field", "states", "initial", "transitions"}
_TRANSITION_KEYS = {"name", "from", "to", "actor", "permission"}
_FUNCTION_KEYS = {
    "name", "kind", "entity", "body", "detail", "methods", "path", "method",
    "trigger", "schedule", "actor", "permission", "logic", "imports",
    "request_schema", "response_schema",
}

# What a declared function becomes. Each kind compiles to the operation named
# here, which is the same agent function the per-object tool calls — the spec
# is a front door onto them, not a second implementation.
FUNCTION_KINDS = {
    "action": "add_viewset_action",
    "endpoint": "create_route",
    "hook": "create_signal_receiver",
    "task": "create_task",
    "rule": "create_permission_class",
}

#: HTTP methods a declared ``endpoint`` or ``action`` function may use.
_FUNCTION_METHODS = ("get", "post", "put", "patch", "delete")

#: Model lifecycle points a declared ``hook`` function may fire on. Mirrors
#: ``zeeb_agents.signals._VALID_SIGNALS`` (guarded by a test).
_SIGNAL_TRIGGERS = ("pre_save", "post_save", "pre_delete", "post_delete")

#: Ops produced by the ``functions`` block, mapped to what to call them in the
#: step log. ``action`` is absent: it shares the workflow-transition lane.
_FUNCTION_OPS = {
    "create_route": "endpoint",
    "create_signal_receiver": "hook",
    "create_task": "task",
    "create_permission_class": "rule",
}

# Workflow transition ``actor`` → permission class on the generated action.
ACTOR_PERMISSION_MAP = {
    "anyone": "AllowAny",
    "authenticated": "IsAuthenticated",
    "owner": "IsOwner",
    "admin": "IsAdminUser",
}

# Method names a transition may not use (generated ViewSet API + CRUD verbs).
_RESERVED_ACTION_NAMES = frozenset(
    {
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
        "get_queryset",
        "get_object",
        "get_serializer",
        "get_action_request_body",
        "perform_create",
    }
)

_DB_CHANGING_OPS = {
    "create_model",
    "create_user_model",
    "add_field",
    "alter_field",
    "remove_field",
    "add_relationship",
    "delete_model",
    "make_migrations",
    "run_migrations",
}
_DESTRUCTIVE_OPS = {"remove_field", "delete_model"}
#: Destructive ops whose blast radius is a whole entity — its table, its rows,
#: and every endpoint it served. Distinct from routine schema churn so a
#: confirmation layer (and the agent) can treat the two differently.
_HIGH_RISK_OPS = {"delete_model"}
_MIGRATION_OPS = {"make_migrations", "run_migrations"}


def _problem(
    path: str,
    code: str,
    message: str,
    suggestions: list[str] | None = None,
    fix_path: str | None = None,
) -> dict:
    """One validation problem, optionally with a directly applicable correction.

    ``suggestions`` are candidate names for a human/agent to choose from. Pass
    *fix_path* — the path of the **scalar** the best suggestion replaces — to
    also emit ``fix = {"path", "set"}``, which a caller can apply without
    re-deriving what the message meant.

    It is deliberately explicit rather than automatic. ``problems[].path`` names
    the thing that is wrong, which is not always the thing to overwrite: a bad
    relation target is reported on the *field* but set on ``field.target``, and
    for a list-valued path like ``api.operations`` replacing the whole field
    with one suggestion would be wrong.
    """
    entry: dict = {"path": path, "code": code, "message": message}
    if suggestions:
        entry["suggestions"] = suggestions
        if fix_path:
            entry["fix"] = {"path": fix_path, "set": suggestions[0]}
    return entry


#: Models the framework registers itself, so they resolve at runtime but never appear
#: in the project's app inventory. A project that has not created a custom user model
#: still relates to the built-in one by this name.
_FRAMEWORK_MODELS = frozenset({"User"})


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and value.isidentifier()


def _is_class_name(value: object) -> bool:
    """An identifier that starts with an uppercase letter, as a class must."""
    return _is_identifier(value) and str(value)[0].isupper()


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
    if not matches and target in _FRAMEWORK_MODELS:
        # The framework's built-in user model lives outside apps/, so it is absent
        # from the project inventory — but it is a registered model and the single
        # most common relation target there is (ownership, IsOwner, "my" endpoints).
        # Rejecting it made every ownership relation impossible on a project that had
        # not created a custom user model.
        return target
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
    known = entity_names + sorted({m["model"] for m in existing_models} | set(_FRAMEWORK_MODELS))
    problems.append(
        _problem(
            path,
            "model_not_found",
            f"relation target '{target}' matches no entity in this spec and no "
            f"existing model. Known models: {', '.join(known) or '(none)'}",
            suggestions=close_matches(target, known),
            # The problem is reported on the field; the value to replace is its
            # relation target.
            fix_path=f"{path}.target",
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


def _normalize_ownership(raw: Any) -> dict[str, Any] | None:
    """Accept ``"owner"``, ``True``, or ``{"field", "scope_reads"}``."""
    if raw in (None, False):
        return None
    if raw is True:
        return {"field": "owner", "scope_reads": None}
    if isinstance(raw, str):
        return {"field": raw, "scope_reads": None}
    if isinstance(raw, dict):
        scope = raw.get("scope_reads")
        return {
            "field": str(raw.get("field") or "owner"),
            # ``None`` means "decide from authentication": scope reads on a
            # private endpoint, leave them open on a publicly readable one.
            "scope_reads": None if scope is None else bool(scope),
        }
    return {}  # invalid — reported by the validator


def _operation_suggestions(bad: Any) -> list[str] | None:
    """Suggest the operation an invalid name most likely meant.

    Shares the code generator's alias table so the spec layer and the viewset
    layer recover from the same mistakes (``destroy`` → ``delete``, which
    difflib is not close enough to find).
    """
    from zeeb_agents._utils.code_gen import _OPERATION_ALIASES

    alias = _OPERATION_ALIASES.get(str(bad).lower())
    return [alias] if alias else close_matches(str(bad), list(VALID_OPERATIONS))


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
                suggestions=_operation_suggestions(bad[0]) if bad else None,
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
                fix_path=f"{path}.api.authentication",
            )
        )
    ownership = _normalize_ownership(merged.get("ownership"))
    if ownership == {}:
        problems.append(
            _problem(
                f"{path}.api.ownership",
                "invalid_input",
                "'ownership' must be a field name, true, or "
                "{'field': ..., 'scope_reads': bool}",
            )
        )
        ownership = None
    merged["ownership"] = ownership
    return merged


def _collect_functions(spec: dict) -> list[tuple[str, dict, str | None]]:
    """Every declared function as ``(path, function, default_entity)``.

    Functions may be declared at the top of the spec or inside an entity; an
    entity-level one defaults its ``entity`` to the entity it sits in, which is
    the whole reason for allowing both placements.
    """
    out: list[tuple[str, dict, str | None]] = []
    for i, function in enumerate(spec.get("functions") or []):
        out.append((f"spec.functions[{i}]", function, None))
    for i, entity in enumerate(spec.get("entities") or []):
        if not isinstance(entity, dict):
            continue
        for j, function in enumerate(entity.get("functions") or []):
            out.append((f"spec.entities[{i}].functions[{j}]", function, entity.get("name")))
    return out


def _validate_functions(
    spec: dict,
    entity_names: list[str],
    existing_models: list[dict],
    problems: list[dict],
    warnings: list[str],
) -> None:
    """Validate the declared ``functions``, reporting every problem at once.

    Actions and workflow transitions both become methods on the same generated
    ViewSet, so they share one namespace — a function that silently overwrote a
    transition would break the state machine in a way nothing else would catch.
    """
    known_entities = {*entity_names, *(m.get("model") for m in existing_models)}
    transition_names = {
        transition.get("name")
        for entity in spec.get("entities") or []
        if isinstance(entity, dict) and isinstance(entity.get("workflow"), dict)
        for transition in entity["workflow"].get("transitions") or []
        if isinstance(transition, dict)
    }
    seen: set[str] = set()

    for path, function, default_entity in _collect_functions(spec):
        if not isinstance(function, dict):
            problems.append(_problem(path, "invalid_input", "function must be a dict"))
            continue
        _warn_unknown_keys(function, _FUNCTION_KEYS, path, warnings)

        name = function.get("name")
        kind = function.get("kind")
        # A rule is a permission CLASS, so it is named like one; everything
        # else becomes a Python function.
        valid_name = _is_class_name(name) if kind == "rule" else _is_identifier(name)
        if not valid_name:
            problems.append(
                _problem(
                    f"{path}.name",
                    "invalid_identifier",
                    f"function needs an identifier 'name', got {name!r}",
                )
            )
            continue
        if kind not in FUNCTION_KINDS:
            problems.append(
                _problem(
                    f"{path}.kind",
                    "invalid_input",
                    f"unknown function kind {kind!r}. Valid: "
                    f"{', '.join(sorted(FUNCTION_KINDS))}",
                    suggestions=close_matches(str(kind), sorted(FUNCTION_KINDS)),
                )
            )
            continue
        if name in seen:
            problems.append(
                _problem(f"{path}.name", "already_exists", f"duplicate function '{name}' in spec")
            )
        seen.add(name)

        if kind in ("action", "hook"):
            entity = function.get("entity") or default_entity
            if not entity:
                problems.append(
                    _problem(
                        f"{path}.entity",
                        "invalid_input",
                        f"a '{kind}' function needs 'entity' naming the entity it "
                        "belongs to (or declare it inside that entity)",
                    )
                )
            elif entity not in known_entities:
                problems.append(
                    _problem(
                        f"{path}.entity",
                        "model_not_found",
                        f"unknown entity {entity!r}",
                        suggestions=close_matches(
                            str(entity), sorted(n for n in known_entities if n)
                        ),
                    )
                )

        if kind == "action":
            if name in _RESERVED_ACTION_NAMES:
                problems.append(
                    _problem(
                        f"{path}.name",
                        "invalid_input",
                        f"'{name}' is a reserved endpoint method name",
                    )
                )
            if name in transition_names:
                problems.append(
                    _problem(
                        f"{path}.name",
                        "already_exists",
                        f"'{name}' is already a workflow transition on this feature — "
                        "both become the same endpoint method, so one would "
                        "overwrite the other",
                    )
                )
            methods = function.get("methods")
            if methods is not None and (
                not isinstance(methods, list)
                or not methods
                or any(str(m).lower() not in _FUNCTION_METHODS for m in methods)
            ):
                problems.append(
                    _problem(
                        f"{path}.methods",
                        "invalid_input",
                        f"'methods' must be a non-empty list of {', '.join(_FUNCTION_METHODS)}",
                    )
                )

        if kind == "endpoint":
            path_value = function.get("path")
            if not isinstance(path_value, str) or not path_value.startswith("/"):
                problems.append(
                    _problem(
                        f"{path}.path",
                        "invalid_input",
                        f"an 'endpoint' function needs a 'path' starting with '/', "
                        f"got {path_value!r}",
                    )
                )
            method = str(function.get("method", "get")).lower()
            if method not in _FUNCTION_METHODS:
                problems.append(
                    _problem(
                        f"{path}.method",
                        "invalid_input",
                        f"invalid method {method!r}. Valid: {', '.join(_FUNCTION_METHODS)}",
                        suggestions=close_matches(method, list(_FUNCTION_METHODS)),
                    )
                )

        if kind == "hook":
            trigger = function.get("trigger")
            if trigger not in _SIGNAL_TRIGGERS:
                problems.append(
                    _problem(
                        f"{path}.trigger",
                        "invalid_input",
                        f"a 'hook' function needs 'trigger' — one of "
                        f"{', '.join(_SIGNAL_TRIGGERS)}; got {trigger!r}",
                        suggestions=close_matches(str(trigger), list(_SIGNAL_TRIGGERS)),
                    )
                )

        if kind == "rule":
            logic = function.get("logic", "deny_all")
            from zeeb_agents.permissions_scaffold import _LOGIC_PRESETS

            if logic not in _LOGIC_PRESETS:
                problems.append(
                    _problem(
                        f"{path}.logic",
                        "invalid_input",
                        f"unknown permission logic {logic!r}. Valid: "
                        f"{', '.join(sorted(_LOGIC_PRESETS))}",
                        suggestions=close_matches(str(logic), sorted(_LOGIC_PRESETS)),
                    )
                )

        actor = function.get("actor")
        if actor is not None and actor not in ACTOR_PERMISSION_MAP:
            problems.append(
                _problem(
                    f"{path}.actor",
                    "invalid_input",
                    f"unknown actor {actor!r}. Valid: "
                    f"{', '.join(sorted(ACTOR_PERMISSION_MAP))}",
                    suggestions=close_matches(str(actor), sorted(ACTOR_PERMISSION_MAP)),
                )
            )


def _function_permission(function: dict) -> str | None:
    """The permission class a declared function carries (None = inherit)."""
    if function.get("permission"):
        return function["permission"]
    actor = function.get("actor")
    if actor:
        return ACTOR_PERMISSION_MAP.get(actor)
    return None


def _function_op(function: dict, app: str, default_entity: str | None) -> dict:
    """Compile one declared function into its plan operation.

    Every branch targets an operation the executor already dispatches to the
    matching agent function, so a declared function and the hand-written call
    that used to be needed produce byte-identical code.
    """
    kind = function["kind"]
    name = function["name"]
    body = function.get("body")

    if kind == "action":
        methods = [str(m).lower() for m in (function.get("methods") or ["post"])]
        return {
            "op": "add_viewset_action",
            "app": app,
            "model": function.get("entity") or default_entity,
            "action_name": name,
            "detail": bool(function.get("detail", True)),
            "methods": methods,
            "body": body or "return {}",
            "permission": _function_permission(function),
            "request_schema": function.get("request_schema"),
            "response_schema": function.get("response_schema"),
            "imports": function.get("imports"),
        }
    if kind == "endpoint":
        return {
            "op": "create_route",
            "app": app,
            "path": function["path"],
            "method": str(function.get("method", "get")).lower(),
            "function_name": name,
            "body": body,
            "imports": function.get("imports"),
        }
    if kind == "hook":
        return {
            "op": "create_signal_receiver",
            "app": app,
            "signal_name": function["trigger"],
            "model_name": function.get("entity") or default_entity,
            "function_name": name,
        }
    if kind == "task":
        return {
            "op": "create_task",
            "app": app,
            "function_name": name,
            "schedule": function.get("schedule"),
        }
    return {
        "op": "create_permission_class",
        "app": app,
        "class_name": name,
        "logic": function.get("logic", "deny_all"),
    }


def _remove_function_op(function: dict, app: str) -> dict:
    """Compile a ``remove_function`` change into its plan operation."""
    return {
        "op": "delete_function",
        "app": app,
        "name": function["name"],
        "kind": function.get("kind", "action"),
        "entity": function.get("entity"),
    }


def _transition_from_states(transition: dict) -> list[str]:
    """Normalize a transition's ``from`` to a list of state names."""
    raw = transition.get("from")
    return [raw] if isinstance(raw, str) else list(raw or [])


def _transition_permission(transition: dict) -> str | None:
    """The permission class a transition's action carries (None = inherit)."""
    if transition.get("permission"):
        return transition["permission"]
    actor = transition.get("actor")
    if actor and actor != "anyone":
        return ACTOR_PERMISSION_MAP.get(actor)
    if actor == "anyone":
        return ACTOR_PERMISSION_MAP["anyone"]
    return None


def _validate_workflow(
    entity: dict,
    path: str,
    field_names: set[str],
    problems: list[dict],
    warnings: list[str],
) -> None:
    """Validate an entity-level ``workflow`` block (states + transitions)."""
    from zeeb_agents._utils.code_gen import VIEWSET_PERMISSIONS

    workflow = entity.get("workflow")
    if workflow is None:
        return
    if not isinstance(workflow, dict):
        problems.append(_problem(path, "invalid_input", "'workflow' must be a dict"))
        return
    _warn_unknown_keys(workflow, _WORKFLOW_KEYS, path, warnings)

    states = workflow.get("states")
    if (
        not isinstance(states, list)
        or not states
        or not all(isinstance(s, str) and s for s in states)
        or len(set(states)) != len(states)
    ):
        problems.append(
            _problem(
                f"{path}.states",
                "invalid_input",
                "workflow needs a non-empty 'states' list of unique strings",
            )
        )
        return
    initial = workflow.get("initial", states[0])
    if initial not in states:
        problems.append(
            _problem(
                f"{path}.initial",
                "invalid_input",
                f"workflow initial {initial!r} is not one of {states}",
                suggestions=close_matches(str(initial), states),
            )
        )
    status_field = workflow.get("field", "status")
    if not _is_identifier(status_field):
        problems.append(
            _problem(
                f"{path}.field",
                "invalid_identifier",
                f"workflow field must be an identifier, got {status_field!r}",
            )
        )

    # A declared field of the same name must be an enum covering the states.
    declared = next(
        (
            f
            for f in entity.get("fields", []) or []
            if isinstance(f, dict) and f.get("name") == status_field
        ),
        None,
    )
    if declared is not None:
        values = declared.get("values") if declared.get("type") == "enum" else None
        if not isinstance(values, list) or not set(states) <= set(values):
            problems.append(
                _problem(
                    f"{path}.field",
                    "invalid_field_spec",
                    f"declared field '{status_field}' must be an enum whose "
                    f"values cover the workflow states {states}",
                )
            )

    transitions = workflow.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        problems.append(
            _problem(
                f"{path}.transitions",
                "invalid_input",
                "workflow needs a non-empty 'transitions' list",
            )
        )
        return
    seen_names: set[str] = set()
    has_owner_relation = any(
        isinstance(f, dict) and f.get("name") in ("owner", "user")
        for f in entity.get("fields", []) or []
    )
    for j, transition in enumerate(transitions):
        tpath = f"{path}.transitions[{j}]"
        if not isinstance(transition, dict):
            problems.append(_problem(tpath, "invalid_input", "transition must be a dict"))
            continue
        _warn_unknown_keys(transition, _TRANSITION_KEYS, tpath, warnings)
        t_name = transition.get("name")
        if not _is_identifier(t_name):
            problems.append(
                _problem(
                    f"{tpath}.name",
                    "invalid_identifier",
                    f"transition needs an identifier 'name', got {t_name!r}",
                )
            )
            continue
        if t_name in seen_names:
            problems.append(
                _problem(f"{tpath}.name", "already_exists", f"duplicate transition '{t_name}'")
            )
        seen_names.add(t_name)
        if t_name in _RESERVED_ACTION_NAMES or t_name in field_names:
            problems.append(
                _problem(
                    f"{tpath}.name",
                    "invalid_input",
                    f"transition name '{t_name}' collides with a reserved "
                    "endpoint method or a field name",
                )
            )
        from_states = _transition_from_states(transition)
        bad_from = [s for s in from_states if s not in states]
        if not from_states or bad_from:
            problems.append(
                _problem(
                    f"{tpath}.from",
                    "invalid_input",
                    f"'from' must name workflow states; got {transition.get('from')!r}",
                    suggestions=close_matches(str((bad_from or [""])[0]), states),
                )
            )
        to = transition.get("to")
        if to not in states:
            problems.append(
                _problem(
                    f"{tpath}.to",
                    "invalid_input",
                    f"'to' must be one workflow state; got {to!r}",
                    suggestions=close_matches(str(to), states),
                )
            )
        actor = transition.get("actor")
        permission = transition.get("permission")
        if actor and permission:
            problems.append(
                _problem(
                    tpath,
                    "invalid_input",
                    "'actor' and 'permission' are mutually exclusive — pick one",
                )
            )
        if actor is not None and actor not in ACTOR_PERMISSION_MAP:
            problems.append(
                _problem(
                    f"{tpath}.actor",
                    "invalid_input",
                    f"unknown actor {actor!r}. Valid: {', '.join(ACTOR_PERMISSION_MAP)}",
                    suggestions=close_matches(str(actor), list(ACTOR_PERMISSION_MAP)),
                )
            )
        if actor == "owner" and not has_owner_relation:
            warnings.append(
                f"{tpath}: actor 'owner' maps to IsOwner, which checks an "
                "'owner' (or 'user') field — this entity declares neither, so "
                "the transition would deny every request."
            )
        if permission is not None:
            if not isinstance(permission, str) or not permission:
                problems.append(
                    _problem(f"{tpath}.permission", "invalid_permission", "'permission' must be a class name")
                )
            elif "." in permission:
                warnings.append(
                    f"{tpath}: dotted permission '{permission}' is accepted "
                    "but can only be checked at execute time."
                )
            elif permission not in VIEWSET_PERMISSIONS:
                problems.append(
                    _problem(
                        f"{tpath}.permission",
                        "invalid_permission",
                        f"unknown permission class '{permission}'. Valid: "
                        f"{', '.join(sorted(VIEWSET_PERMISSIONS))} (or a custom "
                        "class via a dotted apps.<app>.permissions.<Class> reference)",
                        suggestions=close_matches(permission, sorted(VIEWSET_PERMISSIONS)),
                    )
                )


def _render_transition_body(
    entity: str,
    status_field: str,
    transition: dict,
) -> str:
    """Render the generated action body for one workflow transition."""
    from_states = _transition_from_states(transition)
    allowed = ", ".join(from_states)
    from_tuple = ", ".join(f'"{s}"' for s in from_states)
    name = transition["name"]
    to = transition["to"]
    return (
        "obj = await self.get_object()\n"
        f"if obj.{status_field} not in ({from_tuple},):\n"
        "    raise ResourceConflictException(\n"
        f"        message=f\"Cannot {name}: {status_field} is '{{obj.{status_field}}}' (allowed: {allowed})\"\n"
        "    )\n"
        f'obj.{status_field} = "{to}"\n'
        "await obj.save()\n"
        f"return {entity}Serializer(obj).data"
    )


def _transition_action_op(
    app: str,
    entity: str,
    status_field: str,
    transition: dict,
) -> dict:
    """The ``add_viewset_action`` op implementing one workflow transition."""
    op = {
        "op": "add_viewset_action",
        "app": app,
        "model": entity,
        "action_name": transition["name"],
        "detail": True,
        "methods": ["post"],
        "response_serializer": f"{entity}Serializer",
        "imports": ["from zeeb_api.exceptions import ResourceConflictException"],
        "body": _render_transition_body(entity, status_field, transition),
    }
    permission = _transition_permission(transition)
    if permission:
        op["permission"] = permission
    return op


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


def _warn_unknown_keys(
    obj: dict,
    known: set[str],
    path: str,
    warnings: list[str],
) -> None:
    """Warn (never error) about unrecognized keys so typos are visible."""
    for key in obj:
        if key in known:
            continue
        hint = close_matches(key, sorted(known))
        suffix = f" — did you mean: {', '.join(hint)}?" if hint else ""
        warnings.append(f"{path}.{key} is not a recognized key and was ignored{suffix}")


def validate_feature_spec(
    spec: object,
    existing_models: list[dict],
    existing_apps: list[str],
    warnings: list[str] | None = None,
) -> list[dict]:
    """Validate a FeatureSpec, collecting **every** problem found.

    Returns a list of ``{"path", "code", "message", "suggestions"?}`` dicts —
    empty when the spec is valid.  Mirrors ``validate_field_specs``'s
    report-everything-at-once behavior at the spec level.  When ``warnings``
    is passed, non-fatal findings (unrecognized keys) are appended to it.
    """
    problems: list[dict] = []
    if warnings is None:
        warnings = []
    if not isinstance(spec, dict):
        return [
            _problem("spec", "invalid_input", f"spec must be a dict, got {type(spec).__name__}")
        ]
    _warn_unknown_keys(spec, _SPEC_KEYS, "spec", warnings)
    if isinstance(spec.get("api"), dict):
        _warn_unknown_keys(spec["api"], set(_API_DEFAULTS), "spec.api", warnings)

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
        _warn_unknown_keys(entity, _ENTITY_KEYS, path, warnings)
        if isinstance(entity.get("api"), dict):
            _warn_unknown_keys(entity["api"], set(_API_DEFAULTS), f"{path}.api", warnings)
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
        for j, index in enumerate(entity.get("indexes") or []):
            ipath = f"{path}.indexes[{j}]"
            known = seen | {"id", "created_at", "updated_at"}
            if isinstance(index, list):
                ifields = index
            elif isinstance(index, dict):
                ifields = index.get("fields")
            else:
                ifields = None
            if not isinstance(ifields, list) or not ifields or not set(ifields) <= known:
                problems.append(
                    _problem(
                        ipath,
                        "invalid_input",
                        f"index {index!r} must be {{'fields': [...]}} (optionally with "
                        f"'name') naming fields of entity '{entity['name']}'",
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
        api = _merged_api(spec, entity, path, problems)
        _validate_workflow(entity, f"{path}.workflow", seen, problems, warnings)
        if entity.get("workflow") is not None and not api.get("expose", True):
            problems.append(
                _problem(
                    f"{path}.workflow",
                    "invalid_input",
                    "workflow transitions are API endpoints — remove "
                    "'expose': false or drop the workflow",
                )
            )

    _validate_functions(spec, entity_names, existing_models, problems, warnings)
    return problems


def _reconcile_existing_entity(
    app: str,
    ename: str,
    compiled_fields: list[dict],
    raw_fields: list[dict],
    inventory: dict,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Diff a spec entity against the model already on disk.

    Re-running a build with an extended spec used to do nothing at all: every
    create step is skip-idempotent, so an added field was silently dropped. The
    diff closes that gap in the direction that is always safe.

    Returns ``(extra_ops, drift_entries, suggested_changes)``:

    - ``extra_ops`` add the fields the spec declares and the model lacks (plus
      the serializer sync that makes them visible over the API) — applied.
    - ``drift_entries`` describe what the spec would *destroy* if it were
      treated as the whole truth: fields on disk the spec no longer mentions,
      and fields whose type changed. Never applied — reported, with
      ``suggested_changes`` as a ready-to-send ``change_feature`` payload.

    Only the field class is compared, not its kwargs: a renamed type is
    unambiguous from the generated source, whereas a ``max_length`` diff would
    have to be parsed back out of a rendered line and would report drift that
    is not there.
    """
    disk_types: dict[str, str] = dict(inventory.get("field_types") or {})
    disk_names = set(disk_types) or set(inventory.get("fields") or [])
    raw_by_name = {
        f.get("name"): f for f in raw_fields if isinstance(f, dict) and f.get("name")
    }

    extra_ops: list[dict] = []
    drift: list[dict] = []
    suggested: list[dict] = []
    spec_names: set[str] = set()

    for field in compiled_fields:
        name = field["name"]
        spec_names.add(name)
        if name not in disk_names:
            is_relation = field["type"] in ("fk", "o2o", "m2m")
            extra_ops.append(
                {
                    "op": "add_relationship" if is_relation else "add_field",
                    "app": app,
                    "model": ename,
                    ("rel" if is_relation else "field"): field,
                }
            )
            extra_ops.append(
                {
                    "op": "sync_serializer",
                    "app": app,
                    "model": ename,
                    "field_name": name,
                    "present": True,
                }
            )
            continue
        # Type comparison needs the inventory to carry types; an older project
        # snapshot (names only) simply reports no type drift.
        native = FIELD_TYPE_MAP.get(field["type"], field["type"])
        current = disk_types.get(name)
        if current and current != native:
            drift.append(
                {
                    "entity": f"{app}.{ename}",
                    "field": name,
                    "kind": "type_changed",
                    "from": current,
                    "to": native,
                }
            )
            if name in raw_by_name:
                suggested.append(
                    {
                        "operation": "alter_field",
                        "entity": ename,
                        "app": app,
                        "field": raw_by_name[name],
                    }
                )

    for name in sorted(disk_names - spec_names - {"id"}):
        drift.append(
            {"entity": f"{app}.{ename}", "field": name, "kind": "missing_from_spec"}
        )
        suggested.append(
            {
                "operation": "remove_field",
                "entity": ename,
                "app": app,
                "field_name": name,
            }
        )

    return extra_ops, drift, suggested


def compile_feature_spec(
    spec: dict,
    existing_models: list[dict],
    existing_apps: list[str],
    tests: bool = True,
) -> dict:
    """Compile a validated FeatureSpec into a deterministic plan dict.

    Raises :class:`AgentError` (code = the first problem's code, full list in
    ``data["problems"]``) when validation fails; the same spec against the
    same project state always compiles to the same plan.
    """
    warnings: list[str] = []
    problems = validate_feature_spec(spec, existing_models, existing_apps, warnings)
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
    operations: list[dict] = []

    # Always emitted, even for an app that already exists: create_app has
    # ensure-semantics (it repairs INSTALLED_APPS and the project urls include
    # without touching existing files), so including it makes build_feature
    # self-healing for a half-wired app — the caller never needs to reach for
    # the per-object wiring tools to recover.
    operations.append({"op": "create_app", "app": app})

    inventory_by_name = {m["model"]: m for m in existing_models if m.get("app") == app}
    existing_names = set(inventory_by_name)
    entity_names = [e["name"] for e in spec["entities"]]
    endpoints = 0
    transitions = 0
    reconciled = 0
    test_entities: list[dict] = []
    reconcile_ops: list[dict] = []
    drift_entries: list[dict] = []
    suggested_changes: list[dict] = []

    for entity in _topo_order(spec["entities"], warnings):
        ename = entity["name"]
        fields: list[dict] = []
        for j, field in enumerate(entity["fields"]):
            compiled = _compile_field(
                field, f"spec.entities.{ename}.fields[{j}]",
                entity_names, existing_models, app, [], warnings,
            )
            if compiled is not None:
                fields.append(compiled)
        field_names = [f["name"] for f in fields]
        # Ownership needs a real column: scaffold the FK to the user model when
        # the spec did not declare it, so `ownership` alone is enough.
        entity_api = _merged_api(spec, entity, ename, [])
        ownership = entity_api.get("ownership")
        if ownership and ownership["field"] not in field_names:
            fields.append(
                {
                    "name": ownership["field"],
                    "type": "fk",
                    "to": "User",
                    "null": True,
                    "on_delete": "CASCADE",
                }
            )
            field_names.append(ownership["field"])
            warnings.append(
                f"Entity '{ename}': added the '{ownership['field']}' relation to User "
                "for api.ownership (nullable so existing rows migrate)."
            )
        workflow = entity.get("workflow")
        if workflow:
            status_field = workflow.get("field", "status")
            if status_field not in field_names:
                synthesized = _compile_field(
                    {
                        "name": status_field,
                        "type": "enum",
                        "values": list(workflow["states"]),
                        "default": workflow.get("initial", workflow["states"][0]),
                    },
                    f"spec.entities.{ename}.workflow.field",
                    entity_names, existing_models, app, [], warnings,
                )
                if synthesized is not None:
                    fields.append(synthesized)
                    field_names.append(status_field)
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
        # ``indexes`` reaches the model Meta verbatim — the ORM and the code
        # generator already accept it; only the spec layer was missing.
        declared_indexes = [
            {"fields": list(index)} if isinstance(index, list) else dict(index)
            for index in (entity.get("indexes") or [])
        ]
        if declared_indexes:
            meta["indexes"] = declared_indexes

        operations.append(
            {
                "op": "create_model",
                "app": app,
                "model": ename,
                "fields": fields,
                "meta": meta or None,
            }
        )

        if ename in existing_names:
            # The create steps above are all skip-idempotent, so on their own a
            # re-run with an extended spec would be a silent no-op. Reconcile the
            # difference instead — additively.
            extra_ops, entity_drift, entity_suggested = _reconcile_existing_entity(
                app, ename, fields, entity["fields"], inventory_by_name[ename]
            )
            reconcile_ops.extend(extra_ops)
            drift_entries.extend(entity_drift)
            suggested_changes.extend(entity_suggested)
            added = sum(1 for op in extra_ops if op["op"] != "sync_serializer")
            reconciled += added
            if added:
                warnings.append(
                    f"Model '{app}.{ename}' already exists — reconciling: "
                    f"{added} missing field(s) will be added; its create steps "
                    "are skipped (idempotent)."
                )
            else:
                warnings.append(
                    f"Model '{app}.{ename}' already exists — its create steps "
                    "will be skipped (idempotent)."
                )
            if entity_drift:
                warnings.append(
                    f"Destructive drift on '{app}.{ename}': {len(entity_drift)} "
                    "field(s) exist on disk but not in the spec, or changed type "
                    "— NOT applied. See data['drift'].suggested_changes, which is "
                    "a ready-to-send change_feature payload."
                )

        api = _merged_api(spec, entity, ename, [])
        # Resolve the permission ONCE and hand the same value to the viewset and
        # to the test descriptor. Deriving the tests from `authentication` while
        # the endpoint is built from `permissions` made an explicit
        # `api.permissions` emit an anonymous-GET test against a correctly
        # gated endpoint — a generated test that fails on correct code.
        # Explicit permissions win; otherwise ownership decides, then the
        # authentication shorthand.
        if api["permissions"]:
            permission = api["permissions"]
        elif ownership:
            permission = _OWNERSHIP_PERMISSIONS[api["authentication"]]
        else:
            permission = AUTHENTICATION_MAP[api["authentication"]]
        if ownership and ownership["scope_reads"] is None:
            # Private endpoint → hide other people's rows. Publicly readable
            # endpoint → scoping reads would contradict the declared access.
            ownership["scope_reads"] = api["authentication"] == "required"
        requested = list(dict.fromkeys(api["operations"]))
        descriptor: dict = {
            "name": ename,
            "exposed": bool(api["expose"]),
            "authentication": api["authentication"],
            "permission": [permission] if isinstance(permission, str) else list(permission),
            # The endpoints actually served — tests must not exercise an
            # operation the spec deliberately withheld.
            "operations": requested,
            "prefix": _pluralize(ename.lower()) if api["expose"] else None,
            "fields": fields,
            "unique_together": [list(group) for group in unique_sets],
        }
        if workflow:
            descriptor["workflow"] = {
                "field": workflow.get("field", "status"),
                "initial": workflow.get("initial", workflow["states"][0]),
                "transitions": [
                    {
                        "name": t["name"],
                        "from_states": _transition_from_states(t),
                        "to": t["to"],
                        "permission": _transition_permission(t),
                    }
                    for t in workflow["transitions"]
                ],
            }
        test_entities.append(descriptor)
        if not api["expose"]:
            continue
        endpoints += 1
        requested = list(dict.fromkeys(api["operations"]))
        read_only = set(requested) <= _READ_ONLY_OPERATIONS
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
            # Both are sent: ``operations`` is what the endpoint actually serves,
            # ``read_only`` keeps a v1 plan applied by an older executor honest.
            "operations": requested,
            "read_only": read_only,
        }
        if ownership:
            viewset_op["owner_field"] = ownership["field"]
            viewset_op["owner_scoped_reads"] = bool(ownership["scope_reads"])
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
        if workflow:
            status_field = workflow.get("field", "status")
            for transition in workflow["transitions"]:
                operations.append(
                    _transition_action_op(app, ename, status_field, transition)
                )
                transitions += 1

    # Reconciliation runs after every entity's create block so a synced field
    # always finds its serializer, whichever entity declared it.
    operations.extend(reconcile_ops)

    # Functions come after every entity is scaffolded: an action attaches to a
    # ViewSet and a hook references a model, so neither can be emitted before
    # the thing it hangs off exists.
    functions = 0
    for _path, function, default_entity in _collect_functions(spec):
        operations.append(_function_op(function, app, default_entity))
        functions += 1

    if (spec.get("auth") or {}).get("required"):
        warnings.append(
            "spec.auth.required is informational — the plan does not wire "
            "authentication. Run configure_auth (or bootstrap_project) to set "
            "up JWT auth."
        )

    if tests:
        operations.append({"op": "generate_tests", "app": app, "entities": test_entities})
    operations.append({"op": "make_migrations", "name": name})
    operations.append({"op": "run_migrations"})

    feature: dict = {"name": name, "app": app}
    if isinstance(spec.get("description"), str) and spec["description"]:
        feature["description"] = spec["description"]

    summary = (
        f"Create feature '{name}': {len(entity_names)} model(s), "
        f"{endpoints} endpoint(s) in app '{app}'"
    )
    if transitions:
        summary += f", {transitions} workflow transition(s)"
    if functions:
        summary += f", {functions} function(s)"
    if reconciled:
        summary += f", reconciling {reconciled} field(s) on existing model(s)"

    plan = {
        "plan_version": PLAN_VERSION,
        "summary": summary,
        "feature": feature,
        "operations": operations,
        "risk": _plan_risk(operations),
        "preconditions": plan_preconditions(operations, existing_models, existing_apps),
        "warnings": warnings,
    }
    if drift_entries:
        plan["drift"] = {"entries": drift_entries, "suggested_changes": suggested_changes}
    return plan


def compile_changes(
    changes: object,
    existing_models: list[dict],
    existing_apps: list[str],
    app: str | None,
    tests: bool = False,
) -> tuple[list[dict], list[str]]:
    """Compile ``change_feature`` semantic changes into plan operations.

    ``tests`` generates a smoke-test file for entities added by ``add_entity``
    (written per entity, so an app's existing generated suite is untouched —
    :func:`~zeeb_agents.test_scaffold.generate_tests` never overwrites).
    Field-level changes never regenerate tests; the caller says so instead.

    Returns ``(operations, warnings)``; raises :class:`AgentError` with the
    collected problems when the changes are invalid.
    """
    from zeeb_agents._utils.code_gen import VIEWSET_PERMISSIONS

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

    # Changes are compiled against the project as this batch builds it up, not as it
    # was when the call arrived: an entity added by one change is a valid relation
    # target (and a valid `entity`) for every later change in the same list.
    existing_models = [dict(m) for m in existing_models]
    existing_apps = list(existing_apps)

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

    known_change_ops = (
        "add_field",
        "alter_field",
        "remove_field",
        "add_relation",
        "add_entity",
        "remove_entity",
        "add_workflow",
        "add_transition",
        "add_function",
        "remove_function",
        "set_permissions",
        "set_authentication",
    )
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

        if operation in ("add_function", "remove_function"):
            function = change.get("function")
            if operation == "remove_function" and isinstance(function, str):
                function = {"name": function, "kind": change.get("kind", "action")}
            if not isinstance(function, dict):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        f"{operation} needs 'function': a function dict "
                        '(e.g. {"name": "publish", "kind": "action", '
                        '"entity": "Post", "body": "..."})',
                    )
                )
                continue
            target_app = change.get("app") or app or _entity_app(
                str(function.get("entity") or ""), path
            )
            if not _is_identifier(target_app):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        f"{operation} needs 'app' (or a top-level app=) — or an "
                        "'entity' whose app can be resolved",
                    )
                )
                continue
            # Validated through the same spec-level checker, so a change and a
            # build reject exactly the same function declarations.
            sub_problems: list[dict] = []
            _validate_functions(
                {"functions": [function], "entities": []},
                [m["model"] for m in existing_models if m.get("app") == target_app],
                existing_models,
                sub_problems,
                warnings,
            )
            if sub_problems:
                for p in sub_problems:
                    problems.append(
                        {**p, "path": p["path"].replace("spec.functions[0]", path)}
                    )
                continue
            if operation == "add_function":
                operations.append(_function_op(function, target_app, None))
            else:
                operations.append(_remove_function_op(function, target_app))
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
                sub_plan = compile_feature_spec(
                    sub_spec, existing_models, existing_apps, tests=tests
                )
            except AgentError as exc:
                data = exc.result.data or {}
                for p in data.get("problems", []):
                    problems.append({**p, "path": f"{path}.{p['path']}"})
                continue
            for op in sub_plan["operations"]:
                if op["op"] in _MIGRATION_OPS:
                    continue
                if op["op"] == "generate_tests":
                    # A per-entity file: the app's existing generated suite is
                    # never overwritten, so without its own name the new
                    # entity's tests would simply not be written.
                    op = {
                        **op,
                        "filename": f"tests/test_{target_app}_"
                        f"{entity['name'].lower()}_generated.py",
                    }
                operations.append(op)
            warnings.extend(sub_plan["warnings"])
            # Publish the new entity so later changes in this batch can reference it.
            if target_app not in existing_apps:
                existing_apps.append(target_app)
            existing_models.append(
                {
                    "app": target_app,
                    "model": entity["name"],
                    "fields": [
                        f.get("name")
                        for f in (entity.get("fields") or [])
                        if isinstance(f, dict) and f.get("name")
                    ],
                }
            )
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

        inventory_entry = next(
            (
                m
                for m in existing_models
                if m.get("model") == entity_name and m.get("app") == entity_app
            ),
            {},
        )

        if operation == "remove_entity":
            # Reverse creation order: every step removes the reference before its
            # target disappears. Skipping that leaves views.py importing a model
            # that is gone, which breaks the whole app at import time — every
            # other endpoint included, not just this one.
            for op_name in (
                "unregister_route",
                "delete_viewset",
                "delete_serializer",
                "delete_model",
            ):
                operations.append({"op": op_name, "app": entity_app, "model": entity_name})
            warnings.append(
                f"{path}: relations from other models to '{entity_name}' cannot "
                "be verified from the project inventory — a dangling relation "
                "breaks the next migration."
            )
            warnings.append(
                f"{path}: generated tests referencing '{entity_name}' are not "
                "removed — delete or edit tests/ if they now fail."
            )
            existing_models = [
                m
                for m in existing_models
                if not (m.get("model") == entity_name and m.get("app") == entity_app)
            ]
            continue

        if operation in ("set_permissions", "set_authentication"):
            if operation == "set_authentication":
                authentication = change.get("authentication")
                if authentication not in AUTHENTICATION_MAP:
                    problems.append(
                        _problem(
                            path,
                            "invalid_authentication",
                            f"unknown authentication {authentication!r}. Valid: "
                            f"{', '.join(AUTHENTICATION_MAP)}",
                            suggestions=close_matches(
                                str(authentication), list(AUTHENTICATION_MAP)
                            ),
                        )
                    )
                    continue
                permissions: list[str] = [AUTHENTICATION_MAP[authentication]]
            else:
                raw = change.get("permissions") or change.get("permission")
                permissions = [raw] if isinstance(raw, str) else list(raw or [])
                if not permissions or not all(isinstance(p, str) and p for p in permissions):
                    problems.append(
                        _problem(
                            path,
                            "invalid_permission",
                            "set_permissions needs 'permissions': a permission "
                            "class name or list of them",
                        )
                    )
                    continue
                invalid = False
                for permission in permissions:
                    if "." in permission:
                        warnings.append(
                            f"{path}: dotted permission '{permission}' is accepted "
                            "but can only be checked at execute time."
                        )
                    elif permission not in VIEWSET_PERMISSIONS:
                        problems.append(
                            _problem(
                                path,
                                "invalid_permission",
                                f"unknown permission class '{permission}'. Valid: "
                                f"{', '.join(sorted(VIEWSET_PERMISSIONS))} (or a "
                                "custom class in the app's permissions.py)",
                                suggestions=close_matches(
                                    permission, sorted(VIEWSET_PERMISSIONS)
                                ),
                            )
                        )
                        invalid = True
                if invalid:
                    continue
            operations.append(
                {
                    "op": "update_viewset",
                    "app": entity_app,
                    "model": entity_name,
                    "permission": permissions,
                }
            )
            continue

        if operation == "alter_field":
            raw_field = change.get("field")
            if not isinstance(raw_field, dict):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        "alter_field needs 'field': the complete replacement field "
                        "spec (every option restated — the old definition is "
                        "discarded, not merged)",
                    )
                )
                continue
            known_fields = list(inventory_entry.get("fields") or [])
            field_name = raw_field.get("name")
            if known_fields and field_name not in known_fields:
                problems.append(
                    _problem(
                        f"{path}.field",
                        "field_not_found",
                        f"'{entity_name}' has no field {field_name!r}. Fields: "
                        f"{', '.join(known_fields)}",
                        suggestions=close_matches(str(field_name), known_fields),
                    )
                )
                continue
            compiled = _compile_field(
                raw_field, f"{path}.field", [], existing_models, entity_app, problems, warnings
            )
            if compiled is None:
                continue
            current_type = (inventory_entry.get("field_types") or {}).get(compiled["name"])
            native = FIELD_TYPE_MAP.get(compiled["type"], compiled["type"])
            if current_type and current_type != native:
                warnings.append(
                    f"{path}: '{entity_name}.{compiled['name']}' changes type "
                    f"{current_type} → {native}. Review the generated migration "
                    "before applying it to data you care about — a type change "
                    "the database cannot cast loses the column's contents."
                )
            operations.append(
                {
                    "op": "alter_field",
                    "app": entity_app,
                    "model": entity_name,
                    "field": compiled,
                }
            )
            continue

        if operation == "add_workflow":
            workflow = change.get("workflow")
            model_fields = next(
                (
                    list(m.get("fields") or [])
                    for m in existing_models
                    if m.get("model") == entity_name and m.get("app") == entity_app
                ),
                [],
            )
            status_field = (
                workflow.get("field", "status") if isinstance(workflow, dict) else "status"
            )
            # Pseudo-entity for the shared validator: known field names minus
            # the status field (its type/choices are not in the inventory, so
            # the declared-enum check must not fire against a bare name).
            pseudo = {
                "name": entity_name,
                "fields": [{"name": n} for n in model_fields if n != status_field],
                "workflow": workflow,
            }
            before = len(problems)
            _validate_workflow(pseudo, f"{path}.workflow", set(model_fields), problems, warnings)
            if workflow is None:
                problems.append(
                    _problem(path, "invalid_input", "add_workflow needs 'workflow': a workflow dict")
                )
            if len(problems) > before or workflow is None:
                continue
            if status_field in model_fields:
                warnings.append(
                    f"{path}: assuming existing field '{status_field}' carries "
                    f"the states {workflow['states']} — enum choices cannot be "
                    "verified from the project inventory."
                )
            else:
                operations.append(
                    {
                        "op": "add_field",
                        "app": entity_app,
                        "model": entity_name,
                        "field": {
                            "name": status_field,
                            "type": "CharField",
                            "choices": [[s, s] for s in workflow["states"]],
                            "max_length": max(len(s) for s in workflow["states"]),
                            "default": workflow.get("initial", workflow["states"][0]),
                        },
                    }
                )
            for transition in workflow["transitions"]:
                operations.append(
                    _transition_action_op(entity_app, entity_name, status_field, transition)
                )
            continue

        if operation == "add_transition":
            transition = change.get("transition")
            if not isinstance(transition, dict) or not _is_identifier(transition.get("name")):
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        "add_transition needs 'transition': "
                        '{"name", "from", "to", "actor"?|"permission"?}',
                    )
                )
                continue
            status_field = change.get("field", "status")
            if not _is_identifier(status_field):
                problems.append(
                    _problem(path, "invalid_identifier", f"'field' must be an identifier, got {status_field!r}")
                )
                continue
            from_states = _transition_from_states(transition)
            to = transition.get("to")
            if not from_states or not isinstance(to, str) or not to:
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        "transition needs 'from' (a state or list of states) and 'to' (a state)",
                    )
                )
                continue
            if transition.get("actor") and transition.get("permission"):
                problems.append(
                    _problem(path, "invalid_input", "'actor' and 'permission' are mutually exclusive")
                )
                continue
            actor = transition.get("actor")
            if actor is not None and actor not in ACTOR_PERMISSION_MAP:
                problems.append(
                    _problem(
                        path,
                        "invalid_input",
                        f"unknown actor {actor!r}. Valid: {', '.join(ACTOR_PERMISSION_MAP)}",
                        suggestions=close_matches(str(actor), list(ACTOR_PERMISSION_MAP)),
                    )
                )
                continue
            warnings.append(
                f"{path}: 'from'/'to' cannot be checked against a declared "
                f"states list — ensure field '{status_field}' accepts "
                f"'{to}' on '{entity_name}'."
            )
            operations.append(
                _transition_action_op(entity_app, entity_name, status_field, transition)
            )
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
            # A field dropped from the model but left in Meta.fields breaks the
            # endpoint; keep the serializer in lockstep.
            operations.append(
                {
                    "op": "sync_serializer",
                    "app": entity_app,
                    "model": entity_name,
                    "field_name": field_name,
                    "present": False,
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
        # A model field the serializer never lists is invisible over the API —
        # writes to it are accepted and silently discarded. Keep them in lockstep.
        operations.append(
            {
                "op": "sync_serializer",
                "app": entity_app,
                "model": entity_name,
                "field_name": compiled["name"],
                "present": True,
            }
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
    if any(op["op"] in _HIGH_RISK_OPS for op in operations):
        level = "high"
    elif database_changes or destructive:
        level = "medium"
    else:
        level = "low"
    return {
        "level": level,
        "destructive": destructive,
        "database_changes": database_changes,
    }


def plan_preconditions(
    operations: list[dict],
    existing_models: list[dict],
    existing_apps: list[str],
) -> dict:
    """Fingerprint of the project state a plan was compiled against.

    Keyed off the plan's own operations: for every app the plan touches
    whether it existed, and for every ``app.Model`` the sorted field-name set
    it had (``None`` = did not exist yet). ``apply_plan`` re-inventories and
    diffs this to warn when a plan is applied against changed state.
    """
    apps: dict[str, bool] = {}
    models: dict[str, list[str] | None] = {}
    by_key = {(m.get("app"), m.get("model")): m for m in existing_models}
    for op in operations:
        app = op.get("app")
        if isinstance(app, str) and app not in apps:
            apps[app] = app in existing_apps
        model = op.get("model")
        if isinstance(app, str) and isinstance(model, str):
            dotted = f"{app}.{model}"
            if dotted not in models:
                match = by_key.get((app, model))
                models[dotted] = sorted(match.get("fields") or []) if match else None
    return {"apps": apps, "models": models}


def staleness_warnings(
    plan: dict,
    existing_models: list[dict],
    existing_apps: list[str],
) -> list[str]:
    """Diff a plan's compile-time preconditions against the current project.

    Warnings only — execution is skip-idempotent, and hard-failing here would
    break the documented "re-run the same call to resume" recovery loop
    (which by definition re-applies a plan against changed state).
    """
    pre = plan.get("preconditions")
    if not isinstance(pre, dict):
        return []  # v1 plan — no fingerprint to check
    warnings: list[str] = []
    by_key = {(m.get("app"), m.get("model")): m for m in existing_models}
    for app, existed in (pre.get("apps") or {}).items():
        if bool(existed) is not (app in existing_apps):
            state = "now exists" if not existed else "no longer exists"
            warnings.append(f"Plan is stale: app '{app}' {state}.")
    for dotted, fields in (pre.get("models") or {}).items():
        app, _, model = dotted.partition(".")
        match = by_key.get((app, model))
        now = sorted(match.get("fields") or []) if match else None
        if now == fields:
            continue
        if fields is None:
            warnings.append(
                f"Plan is stale: model '{dotted}' was created after the plan "
                "was compiled — its create steps will be skipped."
            )
        elif now is None:
            warnings.append(
                f"Plan is stale: model '{dotted}' no longer exists — steps "
                "that modify it will fail."
            )
        else:
            warnings.append(
                f"Plan is stale: the fields of '{dotted}' changed since the "
                "plan was compiled."
            )
    return warnings


def validate_plan(plan: object) -> str | None:
    """Structural + payload check of a plan dict; returns a problem string or None.

    Op payloads are validated against :data:`_OP_REQUIRED` (required keys,
    identifier validity, spec-dict shape) so a hand-edited plan fails here
    with an actionable message instead of mis-executing deep in the executor.
    """
    if not isinstance(plan, dict):
        return f"plan must be a dict (the object plan_feature returned), got {type(plan).__name__}"
    if plan.get("plan_version") not in SUPPORTED_PLAN_VERSIONS:
        return (
            f"plan_version {plan.get('plan_version')!r} is not supported "
            f"(supported: {sorted(SUPPORTED_PLAN_VERSIONS)}) — re-run "
            "plan_feature to get a fresh plan"
        )
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        return "plan has no 'operations' list — re-run plan_feature"
    problems: list[str] = []
    for i, op in enumerate(operations):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            problems.append(f"operations[{i}] is not an operation dict")
            continue
        kind = op["op"]
        if kind not in KNOWN_OPS:
            hint = close_matches(kind, list(KNOWN_OPS))
            suffix = f" Did you mean: {', '.join(hint)}?" if hint else ""
            problems.append(f"operations[{i}]: unknown op '{kind}'.{suffix}")
            continue
        for key in _OP_REQUIRED.get(kind, ()):
            value = op.get(key)
            if value is None:
                problems.append(f"operations[{i}] ({kind}): missing required key '{key}'")
            elif key in ("app", "model", "field_name", "action_name") and not _is_identifier(value):
                problems.append(
                    f"operations[{i}] ({kind}): '{key}' must be an identifier, got {value!r}"
                )
            elif key in ("field", "rel") and not (
                isinstance(value, dict) and _is_identifier(value.get("name"))
            ):
                problems.append(
                    f"operations[{i}] ({kind}): '{key}' must be a field-spec "
                    "dict with an identifier 'name'"
                )
            elif key in ("fields", "entities") and not isinstance(value, list):
                problems.append(f"operations[{i}] ({kind}): '{key}' must be a list")
    if problems:
        shown = "; ".join(problems[:5]) + ("; …" if len(problems) > 5 else "")
        return f"{shown} — re-run plan_feature"
    return None


def new_changes() -> dict:
    """Fresh accumulator for the intent-envelope ``changes`` dict."""
    return {
        "apps_created": [],
        "models_created": [],
        "models_removed": [],
        "endpoints_created": [],
        "endpoints_removed": [],
        "fields_added": [],
        "fields_altered": [],
        "fields_removed": [],
        "actions_created": [],
        "functions_created": [],
        "functions_removed": [],
        "tests_created": [],
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

    Returns ``{"changes", "steps", "errors", "failed_operations",
    "completed_count", "total_count", "remaining_operations", "warnings",
    "state_changed"}``.  ``warnings`` holds non-fatal, project-global conditions
    (e.g. an unrelated app not in ``INSTALLED_APPS``) that must not flip a
    successful build to a failure.  ``failed_operations`` and
    ``remaining_operations`` let a caller resume precisely instead of inferring
    what landed from a single ``state_changed`` boolean.
    """
    from zeeb_agents.auth_scaffold import create_user_model, setup_auth, setup_oauth
    from zeeb_agents.functions import delete_function
    from zeeb_agents.health import create_health_endpoint
    from zeeb_agents.migrations import make_migrations, run_migrations
    from zeeb_agents.models import (
        add_field,
        add_relationship,
        alter_field,
        create_model,
        delete_model,
        remove_field,
    )
    from zeeb_agents.permissions_scaffold import create_permission_class
    from zeeb_agents.project import create_app
    from zeeb_agents.routes import create_route
    from zeeb_agents.serializers import (
        create_serializer,
        delete_serializer,
        sync_serializer_field,
    )
    from zeeb_agents.signals import create_signal_receiver
    from zeeb_agents.tasks import create_task
    from zeeb_agents.test_scaffold import generate_tests
    from zeeb_agents.viewsets import (
        add_viewset_action,
        create_viewset,
        delete_viewset,
        register_route,
        unregister_route,
        update_viewset,
    )

    changes = new_changes()
    steps: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    failed_operations: list[dict] = []
    state_changed = False
    operations = plan.get("operations", [])
    attempted = 0
    remaining: list[dict] = []

    def _record(index: int, op: dict, label: str, result: AgentResult) -> None:
        """Log a failed op: the human line plus the position/code to resume from."""
        data = result.data or {}
        errors.append(f"{label}: {result.message}")
        failed_operations.append(
            {
                "index": index,
                "op": op,
                "error": result.message,
                "error_code": data.get("error_code"),
                "recoverable": data.get("recoverable", True),
            }
        )

    for index, op in enumerate(operations):
        kind = op["op"]
        attempted = index + 1
        if kind in _MIGRATION_OPS and not migrate:
            steps.append(f"Skipped {kind} (migrate=False)")
            continue

        if kind == "create_app":
            # create_app has ensure-semantics, so an existing app is not skipped
            # here: its wiring is re-applied. Skipping it left a pre-existing but
            # unregistered app unwired, and every model the plan then added to it
            # silently failed to migrate.
            result = await create_app(op["app"], project_id=project_root)
            if result.success:
                data = result.data or {}
                if data.get("created"):
                    changes["apps_created"].append(op["app"])
                    steps.append(f"Created app '{op['app']}'")
                    state_changed = True
                elif data.get("installed_apps_updated") or data.get("urls_wired"):
                    steps.append(f"App '{op['app']}' already existed; wiring repaired")
                    state_changed = True
                else:
                    steps.append(f"App '{op['app']}' already exists")
            else:
                _record(index, op, f"create_app({op['app']})", result)
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
                _record(index, op, f"create_model({op['model']})", result)
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
                _record(index, op, f"create_serializer({op['model']})", result)
        elif kind == "create_viewset":
            result = await create_viewset(
                op["app"], op["model"],
                permission=op.get("permission", "IsAuthenticatedOrReadOnly"),
                operations=op.get("operations"),
                owner_field=op.get("owner_field"),
                owner_scoped_reads=bool(op.get("owner_scoped_reads")),
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
                _record(index, op, f"create_viewset({op['model']})", result)
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
                _record(index, op, f"register_route({op['model']})", result)
        elif kind == "add_field":
            result = await add_field(op["app"], op["model"], op["field"], project_id=project_root)
            if result.success:
                changes["fields_added"].append(f"{op['model']}.{op['field']['name']}")
                steps.append(f"Added field '{op['model']}.{op['field']['name']}'")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append(f"Field '{op['model']}.{op['field']['name']}' already exists; skipped")
            else:
                _record(index, op, f"add_field({op['model']})", result)
        elif kind == "alter_field":
            result = await alter_field(
                op["app"], op["model"], op["field"], project_id=project_root
            )
            label = f"{op['model']}.{op['field']['name']}"
            if result.success:
                changes["fields_altered"].append(label)
                steps.append(f"Redefined field '{label}'")
                state_changed = True
            else:
                # No tolerated codes: a missing field here is real drift (the
                # field was compiled against an inventory that listed it), not
                # an idempotent re-run.
                _record(index, op, f"alter_field({op['model']})", result)
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
                _record(index, op, f"add_relationship({op['model']})", result)
        elif kind == "sync_serializer":
            result = await sync_serializer_field(
                op["app"], op["model"], op["field_name"],
                present=op.get("present", True), project_id=project_root,
            )
            label = f"{op['model']}Serializer.{op['field_name']}"
            if not result.success:
                _record(index, op, f"sync_serializer({op['model']})", result)
            elif not (result.data or {}).get("skipped"):
                steps.append(
                    f"{'Exposed' if op.get('present', True) else 'Unexposed'} '{label}'"
                )
                state_changed = True
        elif kind == "delete_serializer":
            result = await delete_serializer(op["app"], op["model"], project_id=project_root)
            if not result.success:
                _record(index, op, f"delete_serializer({op['model']})", result)
            elif not _skipped(result):
                steps.append(f"Removed serializer '{op['model']}Serializer'")
                state_changed = True
        elif kind == "update_viewset":
            result = await update_viewset(
                op["app"], op["model"],
                permission=op.get("permission"),
                pagination=op.get("pagination"),
                search_fields=op.get("search_fields"),
                ordering_fields=op.get("ordering_fields"),
                project_id=project_root,
            )
            if result.success:
                steps.append(f"Updated viewset '{op['model']}ViewSet'")
                state_changed = True
            else:
                _record(index, op, f"update_viewset({op['model']})", result)
        elif kind == "delete_viewset":
            result = await delete_viewset(op["app"], op["model"], project_id=project_root)
            if not result.success:
                _record(index, op, f"delete_viewset({op['model']})", result)
            elif not _skipped(result):
                steps.append(f"Removed viewset '{op['model']}ViewSet'")
                state_changed = True
        elif kind == "unregister_route":
            result = await unregister_route(op["app"], op["model"], project_id=project_root)
            if not result.success:
                _record(index, op, f"unregister_route({op['model']})", result)
            elif not _skipped(result):
                prefix = (result.data or {}).get("prefix")
                changes["endpoints_removed"].append(prefix)
                steps.append(f"Unregistered route '{prefix}/'")
                state_changed = True
        elif kind == "add_viewset_action":
            result = await add_viewset_action(
                op["app"], op["model"], op["action_name"],
                detail=op.get("detail", True), methods=op.get("methods"),
                body=op.get("body"), permission=op.get("permission"),
                response_serializer=op.get("response_serializer"),
                request_schema=op.get("request_schema"),
                response_schema=op.get("response_schema"),
                imports=op.get("imports"),
                if_exists="skip", project_id=project_root,
            )
            label = f"{op['model']}.{op['action_name']}"
            if result.success:
                if _skipped(result):
                    # Name-based skip: a hand-written method with this name
                    # blocks generation — say so loudly in the step log.
                    steps.append(f"Action '{label}' already defined; left untouched")
                else:
                    changes["actions_created"].append(label)
                    steps.append(f"Added action '{label}'")
                    state_changed = True
            else:
                _record(index, op, f"add_viewset_action({label})", result)
        elif kind in _FUNCTION_OPS:
            # The declarative `functions` block: each kind dispatches to the
            # same agent function the per-object tool calls, so a declared
            # function and a hand-written call generate identical code.
            name = op.get("function_name") or op.get("class_name")
            if kind == "create_route":
                result = await create_route(
                    op["app"], op["path"], op["method"], op["function_name"],
                    body=op.get("body"), imports=op.get("imports"),
                    if_exists="skip", project_id=project_root,
                )
            elif kind == "create_signal_receiver":
                result = await create_signal_receiver(
                    op["app"], op["signal_name"], op["model_name"], op["function_name"],
                    project_id=project_root,
                )
            elif kind == "create_task":
                result = await create_task(
                    op["app"], op["function_name"],
                    schedule=op.get("schedule"), project_id=project_root,
                )
            else:
                result = await create_permission_class(
                    op["app"], op["class_name"],
                    logic=op.get("logic", "deny_all"), project_id=project_root,
                )
            label = f"{op['app']}.{name}"
            if result.success:
                if _skipped(result):
                    steps.append(f"Function '{label}' already defined; left untouched")
                else:
                    changes["functions_created"].append(label)
                    steps.append(f"Added {_FUNCTION_OPS[kind]} '{label}'")
                    state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append(f"Function '{label}' already defined; left untouched")
            else:
                _record(index, op, f"{kind}({label})", result)
        elif kind == "delete_function":
            result = await delete_function(
                op["app"], op["name"], kind=op["kind"],
                entity=op.get("entity"), project_id=project_root,
            )
            label = f"{op['app']}.{op['name']}"
            if result.success:
                if (result.data or {}).get("removed"):
                    changes["functions_removed"].append(label)
                    steps.append(f"Removed {op['kind']} '{label}'")
                    state_changed = True
                else:
                    steps.append(f"Function '{label}' was not defined; nothing to remove")
            else:
                _record(index, op, f"delete_function({label})", result)
        elif kind == "generate_tests":
            result = await generate_tests(
                op["app"], op["entities"],
                filename=op.get("filename"), project_id=project_root,
            )
            if result.success:
                created = (result.data or {}).get("created", [])
                if created:
                    changes["tests_created"].extend(created)
                    steps.append(f"Generated {len(created)} test file(s)")
                    state_changed = True
                else:
                    steps.append("Test files already exist; skipped")
            else:
                _record(index, op, "generate_tests", result)
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
                _record(index, op, f"remove_field({op['model']})", result)
        elif kind == "delete_model":
            result = await delete_model(op["app"], op["model"], project_id=project_root)
            if result.success:
                changes["models_removed"].append(f"{op['app']}.{op['model']}")
                steps.append(f"Removed model '{op['app']}.{op['model']}'")
                state_changed = True
            elif _tolerated(result, ("model_not_found",)):
                steps.append(f"Model '{op['app']}.{op['model']}' already absent; skipped")
            else:
                _record(index, op, f"delete_model({op['model']})", result)
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
                _record(index, op, "create_user_model", result)
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
                _record(index, op, "setup_auth", result)
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
                _record(index, op, f"setup_oauth({op['provider']})", result)
        elif kind == "create_health_endpoint":
            result = await create_health_endpoint(project_id=project_root)
            if result.success:
                steps.append("Created health endpoints (/health, /ready)")
                state_changed = True
            elif _tolerated(result, ("already_exists",)):
                steps.append("health.py already exists; skipped")
            else:
                _record(index, op, "create_health_endpoint", result)
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
                _record(index, op, "make_migrations", result)
                remaining = operations[index + 1 :]
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
                _record(index, op, "run_migrations", result)
                remaining = operations[index + 1 :]
                break
        else:  # pragma: no cover — validate_plan gates unknown ops
            errors.append(f"unknown op '{kind}'")
            failed_operations.append(
                {
                    "index": index,
                    "op": op,
                    "error": f"unknown op '{kind}'",
                    "error_code": "invalid_input",
                    "recoverable": False,
                }
            )
            remaining = operations[index + 1 :]
            break

    return {
        "changes": changes,
        "steps": steps,
        "errors": errors,
        "failed_operations": failed_operations,
        "completed_count": attempted - len(failed_operations),
        "total_count": len(operations),
        "remaining_operations": remaining,
        "warnings": warnings,
        "state_changed": state_changed,
    }
