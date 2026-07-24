"""Unit tests for the FeatureSpec compiler (``zeeb_agents.feature_spec``).

Pure compilation tests — no project on disk needed: existing models/apps are
passed in directly.  Execution against a real project lives in
``test_intent.py``.
"""

from __future__ import annotations

import pytest

from zeeb_agents._utils.errors import AgentError
from zeeb_agents.feature_spec import (
    PLAN_VERSION,
    compile_changes,
    compile_feature_spec,
    validate_feature_spec,
    validate_plan,
)

EXISTING = [
    {"app": "accounts", "model": "User", "fields": ["id", "email"]},
    {"app": "shop", "model": "Product", "fields": ["id", "name"]},
]
APPS = ["accounts", "shop"]


def spec_with(fields, **entity_extra):
    return {
        "name": "blog",
        "entities": [{"name": "Post", "fields": fields, **entity_extra}],
    }


def ops_by_type(plan, op_type):
    return [op for op in plan["operations"] if op["op"] == op_type]


# ---------------------------------------------------------------------------
# Field dialect compilation
# ---------------------------------------------------------------------------


def test_scalar_fields_compile_with_sugar():
    plan = compile_feature_spec(
        spec_with(
            [
                {"name": "title", "type": "string", "max_length": 200},
                {"name": "summary", "type": "string"},
                {"name": "views", "type": "int", "required": False},
            ]
        ),
        [],
        [],
    )
    fields = {f["name"]: f for f in ops_by_type(plan, "create_model")[0]["fields"]}
    assert fields["title"]["max_length"] == 200
    assert fields["summary"]["max_length"] == 255  # string default
    assert fields["views"]["null"] is True and fields["views"]["blank"] is True
    assert "required" not in fields["views"]


def test_enum_compiles_to_choices():
    plan = compile_feature_spec(
        spec_with(
            [{"name": "status", "type": "enum", "values": ["draft", "published"],
              "default": "draft"}]
        ),
        [],
        [],
    )
    field = ops_by_type(plan, "create_model")[0]["fields"][0]
    assert field["type"] == "CharField"
    assert field["choices"] == [["draft", "draft"], ["published", "published"]]
    assert field["max_length"] == len("published")
    assert field["default"] == "draft"


def test_enum_default_must_be_a_value():
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(
            spec_with([{"name": "status", "type": "enum", "values": ["a", "b"], "default": "c"}]),
            [],
            [],
        )
    problems = exc.value.result.data["problems"]
    assert any("default" in p["message"] for p in problems)


def test_relation_cardinalities_map_to_field_types():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "entities": [
                {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
                {
                    "name": "Post",
                    "fields": [
                        {"name": "category", "type": "relation", "target": "Category",
                         "cardinality": "many-to-one"},
                        {"name": "twin", "type": "relation", "target": "self",
                         "cardinality": "one-to-one", "required": False},
                        {"name": "tags", "type": "relation", "target": "Category",
                         "cardinality": "many-to-many"},
                    ],
                },
            ],
        },
        [],
        [],
    )
    post_op = next(
        op for op in ops_by_type(plan, "create_model") if op["model"] == "Post"
    )
    fields = {f["name"]: f for f in post_op["fields"]}
    assert fields["category"]["type"] == "fk"
    assert fields["category"]["on_delete"] == "CASCADE"
    assert fields["twin"]["type"] == "o2o" and fields["twin"]["to"] == "self"
    assert fields["twin"]["null"] is True
    assert fields["tags"]["type"] == "m2m"
    assert "on_delete" not in fields["tags"] and "null" not in fields["tags"]


def test_one_to_many_is_rejected_with_corrective_message():
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(
            spec_with(
                [{"name": "posts", "type": "relation", "target": "Post",
                  "cardinality": "one-to-many"}]
            ),
            [],
            [],
        )
    problems = exc.value.result.data["problems"]
    assert any("many-to-one" in p["message"] for p in problems)


def test_unknown_cardinality_gets_suggestions():
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(
            spec_with(
                [{"name": "author", "type": "relation", "target": "User",
                  "cardinality": "many-2-one"}]
            ),
            EXISTING,
            APPS,
        )
    problems = exc.value.result.data["problems"]
    assert problems[0]["suggestions"][0] == "many-to-one"


def test_unknown_field_type_gets_suggestions():
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(spec_with([{"name": "title", "type": "strang"}]), [], [])
    problems = exc.value.result.data["problems"]
    assert problems[0]["code"] == "invalid_field_type"
    assert "string" in (problems[0].get("suggestions") or [])


# ---------------------------------------------------------------------------
# Relation target resolution
# ---------------------------------------------------------------------------


def test_target_resolution_spec_internal_and_existing_models():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "app": "shop",
            "entities": [
                {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
                {
                    "name": "Post",
                    "fields": [
                        {"name": "category", "type": "relation", "target": "Category"},
                        {"name": "author", "type": "relation", "target": "User"},
                        {"name": "product", "type": "relation", "target": "Product"},
                        {"name": "owner", "type": "relation", "target": "accounts.User"},
                    ],
                },
            ],
        },
        EXISTING,
        APPS,
    )
    post_op = next(op for op in ops_by_type(plan, "create_model") if op["model"] == "Post")
    to = {f["name"]: f["to"] for f in post_op["fields"] if "to" in f}
    assert to["category"] == "Category"  # spec-internal, same app
    assert to["author"] == "accounts.User"  # existing model, cross-app → dotted
    assert to["product"] == "Product"  # existing model, same app → bare
    assert to["owner"] == "accounts.User"  # dotted passthrough


def test_unknown_target_reports_model_not_found_with_suggestions():
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(
            spec_with([{"name": "author", "type": "relation", "target": "Usr"}]),
            EXISTING,
            APPS,
        )
    problems = exc.value.result.data["problems"]
    assert problems[0]["code"] == "model_not_found"
    assert "User" in problems[0]["suggestions"]


def test_ambiguous_target_lists_dotted_candidates():
    models = EXISTING + [{"app": "crm", "model": "User", "fields": ["id"]}]
    with pytest.raises(AgentError) as exc:
        compile_feature_spec(
            spec_with([{"name": "author", "type": "relation", "target": "User"}]),
            models,
            APPS,
        )
    problems = exc.value.result.data["problems"]
    assert problems[0]["code"] == "invalid_input"
    assert set(problems[0]["suggestions"]) == {"accounts.User", "crm.User"}


# ---------------------------------------------------------------------------
# Entity ordering
# ---------------------------------------------------------------------------


def test_entities_are_topologically_ordered():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "entities": [
                {
                    "name": "Post",
                    "fields": [
                        {"name": "category", "type": "relation", "target": "Category"}
                    ],
                },
                {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
            ],
        },
        [],
        [],
    )
    order = [op["model"] for op in ops_by_type(plan, "create_model")]
    assert order == ["Category", "Post"]


def test_relation_cycle_falls_back_to_declaration_order_with_warning():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "entities": [
                {
                    "name": "A",
                    "fields": [
                        {"name": "b", "type": "relation", "target": "B",
                         "required": False}
                    ],
                },
                {
                    "name": "B",
                    "fields": [
                        {"name": "a", "type": "relation", "target": "A",
                         "required": False}
                    ],
                },
            ],
        },
        [],
        [],
    )
    order = [op["model"] for op in ops_by_type(plan, "create_model")]
    assert order == ["A", "B"]
    assert any("Circular" in w for w in plan["warnings"])


# ---------------------------------------------------------------------------
# API compilation
# ---------------------------------------------------------------------------


def test_api_defaults_and_pluralized_prefixes():
    plan = compile_feature_spec(
        {"name": "shop2", "entities": [
            {"name": "Company", "fields": [{"name": "name", "type": "string"}]}
        ]},
        [],
        [],
    )
    (viewset_op,) = ops_by_type(plan, "create_viewset")
    assert viewset_op["permission"] == "IsAuthenticatedOrReadOnly"
    assert viewset_op["read_only"] is False
    (route_op,) = ops_by_type(plan, "register_route")
    assert route_op["prefix"] == "companies"


def test_read_only_operations_and_auth_required():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "api": {"operations": ["list", "retrieve"], "authentication": "required"},
            "entities": [{"name": "Post", "fields": [{"name": "title", "type": "string"}]}],
        },
        [],
        [],
    )
    (viewset_op,) = ops_by_type(plan, "create_viewset")
    assert viewset_op["read_only"] is True
    assert viewset_op["permission"] == "IsAuthenticated"
    assert any("configure_auth" in w for w in plan["warnings"])


def test_expose_false_skips_endpoint_ops():
    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "string"}], api={"expose": False}),
        [],
        [],
    )
    assert not ops_by_type(plan, "create_serializer")
    assert not ops_by_type(plan, "create_viewset")
    assert not ops_by_type(plan, "register_route")
    assert ops_by_type(plan, "create_model")


def test_invalid_operation_and_authentication_are_problems():
    problems = validate_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            api={"operations": ["list", "destroy"], "authentication": "jwt"},
        ),
        [],
        [],
    )
    codes = {p["code"] for p in problems}
    assert "invalid_input" in codes  # operations
    assert "invalid_authentication" in codes


# ---------------------------------------------------------------------------
# Meta, timestamps, constraints
# ---------------------------------------------------------------------------


def test_timestamps_ordering_and_unique_constraints():
    plan = compile_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            ordering=["-created_at"],
            constraints=[{"type": "unique", "fields": ["title"]}],
        ),
        [],
        [],
    )
    (model_op,) = ops_by_type(plan, "create_model")
    names = [f["name"] for f in model_op["fields"]]
    assert "created_at" in names and "updated_at" in names
    assert model_op["meta"]["ordering"] == ["-created_at"]
    assert model_op["meta"]["unique_together"] == [["title"]]
    (ser_op,) = ops_by_type(plan, "create_serializer")
    assert "created_at" in ser_op["read_only_fields"]


def test_timestamps_false_omits_audit_fields():
    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "string"}], timestamps=False),
        [],
        [],
    )
    names = [f["name"] for f in ops_by_type(plan, "create_model")[0]["fields"]]
    assert "created_at" not in names


def test_unknown_constraint_field_is_a_problem():
    problems = validate_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            constraints=[{"type": "unique", "fields": ["nope"]}],
        ),
        [],
        [],
    )
    assert any("unique constraint" in p["message"] for p in problems)


# ---------------------------------------------------------------------------
# Whole-spec validation & determinism
# ---------------------------------------------------------------------------


def test_all_problems_are_collected_at_once():
    problems = validate_feature_spec(
        {
            "name": "not an identifier!",
            "entities": [
                {"name": "Post", "fields": [{"name": "x", "type": "nope"}]},
                {"name": "Post", "fields": [{"name": "y"}]},
            ],
        },
        [],
        [],
    )
    assert len(problems) >= 3  # bad name, bad type, duplicate entity, missing type


def test_missing_entities_is_a_problem():
    assert validate_feature_spec({"name": "blog"}, [], [])
    assert validate_feature_spec({"name": "blog", "entities": []}, [], [])


def test_compile_is_deterministic():
    spec = {
        "name": "blog",
        "entities": [
            {"name": "Post", "fields": [
                {"name": "title", "type": "string"},
                {"name": "category", "type": "relation", "target": "Category"},
            ]},
            {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
        ],
    }
    assert compile_feature_spec(spec, [], []) == compile_feature_spec(spec, [], [])


def test_existing_app_skips_create_app_and_warns_on_existing_model():
    plan = compile_feature_spec(
        {"name": "shop", "entities": [
            {"name": "Product", "fields": [{"name": "name", "type": "string"}]}
        ]},
        EXISTING,
        APPS,
    )
    assert not ops_by_type(plan, "create_app")
    assert any("already exists" in w for w in plan["warnings"])


def test_plan_ends_with_migrations_and_reports_risk():
    plan = compile_feature_spec(spec_with([{"name": "title", "type": "string"}]), [], [])
    assert [op["op"] for op in plan["operations"][-2:]] == ["make_migrations", "run_migrations"]
    assert plan["risk"] == {"level": "medium", "destructive": False, "database_changes": True}
    assert plan["plan_version"] == PLAN_VERSION


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


def test_validate_plan_rejects_bad_shapes():
    assert validate_plan("nope") is not None
    assert validate_plan({"plan_version": 99, "operations": [{"op": "create_model"}]}) is not None
    assert validate_plan({"plan_version": PLAN_VERSION, "operations": []}) is not None
    bad_op = validate_plan(
        {"plan_version": PLAN_VERSION, "operations": [{"op": "create_modell"}]}
    )
    assert bad_op is not None and "create_model" in bad_op


def test_validate_plan_accepts_compiled_plan():
    plan = compile_feature_spec(spec_with([{"name": "title", "type": "string"}]), [], [])
    assert validate_plan(plan) is None


def test_validate_plan_accepts_v1_plans():
    plan = compile_feature_spec(spec_with([{"name": "title", "type": "string"}]), [], [])
    assert validate_plan({**plan, "plan_version": 1}) is None


def test_validate_plan_rejects_invalid_op_payloads():
    def _plan(op):
        return {"plan_version": PLAN_VERSION, "operations": [op]}

    missing = validate_plan(_plan({"op": "create_model", "app": "blog"}))
    assert missing is not None and "missing required key 'model'" in missing
    assert missing is not None and "missing required key 'fields'" in missing

    bad_ident = validate_plan(_plan({"op": "create_app", "app": "not an app"}))
    assert bad_ident is not None and "identifier" in bad_ident

    bad_field = validate_plan(
        _plan({"op": "add_field", "app": "blog", "model": "Post", "field": "title"})
    )
    assert bad_field is not None and "field-spec dict" in bad_field

    bad_list = validate_plan(
        _plan({"op": "create_model", "app": "blog", "model": "Post", "fields": "title"})
    )
    assert bad_list is not None and "'fields' must be a list" in bad_list


def test_op_required_table_covers_every_known_op():
    from zeeb_agents.feature_spec import _OP_REQUIRED, KNOWN_OPS

    assert set(_OP_REQUIRED) == set(KNOWN_OPS)


def test_plan_embeds_preconditions_fingerprint():
    plan = compile_feature_spec(
        {"name": "shop", "entities": [
            {"name": "Product", "fields": [{"name": "name", "type": "string"}]}
        ]},
        EXISTING,
        APPS,
    )
    pre = plan["preconditions"]
    assert pre["apps"]["shop"] is True
    assert pre["models"]["shop.Product"] == sorted(
        next(m["fields"] for m in EXISTING if m["model"] == "Product")
    )
    fresh = compile_feature_spec(
        {"name": "blog", "entities": [
            {"name": "Post", "fields": [{"name": "title", "type": "string"}]}
        ]},
        [],
        [],
    )
    assert fresh["preconditions"]["apps"]["blog"] is False
    assert fresh["preconditions"]["models"]["blog.Post"] is None


def test_staleness_warnings_diff_preconditions():
    from zeeb_agents.feature_spec import staleness_warnings

    plan = compile_feature_spec(
        {"name": "blog", "entities": [
            {"name": "Post", "fields": [{"name": "title", "type": "string"}]}
        ]},
        [],
        [],
    )
    # Unchanged state — no warnings; v1 plan without fingerprint — no warnings.
    assert staleness_warnings(plan, [], []) == []
    assert staleness_warnings({"plan_version": 1, "operations": []}, [], []) == []
    # Model created after compile.
    now = [{"app": "blog", "model": "Post", "fields": ["title"]}]
    warned = staleness_warnings(plan, now, ["blog"])
    assert any("created after the plan" in w for w in warned)
    assert any("app 'blog' now exists" in w for w in warned)


def test_unknown_spec_keys_warn_with_suggestions():
    warnings: list[str] = []
    problems = validate_feature_spec(
        {
            "name": "blog",
            "entitees": "typo",
            "entities": [
                {"name": "Post", "filds": [], "fields": [{"name": "t", "type": "string"}]}
            ],
        },
        [],
        [],
        warnings,
    )
    assert problems == []
    assert any("spec.entitees" in w and "entities" in w for w in warnings)
    assert any("filds" in w and "fields" in w for w in warnings)


def test_description_is_echoed_into_the_plan():
    plan = compile_feature_spec(
        {
            "name": "blog",
            "description": "Users write and publish posts",
            "entities": [
                {"name": "Post", "fields": [{"name": "title", "type": "string"}]}
            ],
        },
        [],
        [],
    )
    assert plan["feature"]["description"] == "Users write and publish posts"
    assert not any("description" in w for w in plan["warnings"])


# ---------------------------------------------------------------------------
# compile_changes
# ---------------------------------------------------------------------------


def test_compile_changes_add_field_resolves_app():
    ops, _ = compile_changes(
        [{"operation": "add_field", "entity": "Product",
          "field": {"name": "price", "type": "decimal", "required": False}}],
        EXISTING,
        APPS,
        None,
    )
    (op,) = ops
    assert op["op"] == "add_field" and op["app"] == "shop"
    assert op["field"]["null"] is True


def test_compile_changes_add_relation_and_remove_field():
    ops, _ = compile_changes(
        [
            {"operation": "add_relation", "entity": "Product",
             "field": {"name": "owner", "target": "User"}},
            {"operation": "remove_field", "entity": "Product", "field_name": "name"},
        ],
        EXISTING,
        APPS,
        None,
    )
    assert ops[0]["op"] == "add_relationship"
    assert ops[0]["rel"]["to"] == "accounts.User"
    assert ops[1] == {"op": "remove_field", "app": "shop", "model": "Product", "field_name": "name"}


def test_compile_changes_add_entity_expands_to_full_scaffold():
    ops, _ = compile_changes(
        [{"operation": "add_entity", "app": "shop",
          "entity": {"name": "Order", "fields": [{"name": "total", "type": "decimal"}]}}],
        EXISTING,
        APPS,
        None,
    )
    kinds = [op["op"] for op in ops]
    assert kinds == ["create_model", "create_serializer", "create_viewset", "register_route"]
    assert ops[-1]["prefix"] == "orders"


def test_compile_changes_unknown_operation_and_entity():
    with pytest.raises(AgentError) as exc:
        compile_changes(
            [
                {"operation": "add_feld", "entity": "Product", "field": {}},
                {"operation": "add_field", "entity": "Nope",
                 "field": {"name": "x", "type": "string"}},
            ],
            EXISTING,
            APPS,
            None,
        )
    problems = exc.value.result.data["problems"]
    assert len(problems) == 2
    assert "add_field" in (problems[0].get("suggestions") or [])
    assert problems[1]["code"] == "model_not_found"


def test_compile_changes_ambiguous_entity_needs_app():
    models = EXISTING + [{"app": "crm", "model": "Product", "fields": ["id"]}]
    with pytest.raises(AgentError) as exc:
        compile_changes(
            [{"operation": "add_field", "entity": "Product",
              "field": {"name": "x", "type": "string"}}],
            models,
            APPS,
            None,
        )
    assert "app=" in str(exc.value)
    ops, _ = compile_changes(
        [{"operation": "add_field", "entity": "Product",
          "field": {"name": "x", "type": "string"}}],
        models,
        APPS,
        "crm",
    )
    assert ops[0]["app"] == "crm"
