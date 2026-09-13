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


def test_partial_operations_compile_to_that_subset():
    """A subset must compile to that subset — not to full CRUD with a warning."""
    plan = compile_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            api={"operations": ["list", "retrieve", "create"]},
        ),
        [],
        [],
    )
    (viewset_op,) = ops_by_type(plan, "create_viewset")
    assert viewset_op["operations"] == ["list", "retrieve", "create"]
    assert viewset_op["read_only"] is False
    assert not any("approximated" in w for w in plan["warnings"])


def test_ownership_wires_permission_owner_field_and_scoping():
    """`ownership` must produce all three parts, not just a permission class."""
    plan = compile_feature_spec(
        spec_with(
            [{"name": "body", "type": "text"}],
            api={"authentication": "required", "ownership": "owner"},
        ),
        [],
        [],
    )
    (model_op,) = ops_by_type(plan, "create_model")
    (viewset_op,) = ops_by_type(plan, "create_viewset")

    # 1. the FK column exists
    owner = next(f for f in model_op["fields"] if f["name"] == "owner")
    assert owner["type"] == "fk" and owner["to"] == "User" and owner["null"] is True
    # 2. permissions are ANDed so anonymous gets 401, not an empty 200
    assert viewset_op["permission"] == ["IsAuthenticated", "IsOwner"]
    # 3. the viewset stamps the owner and scopes reads
    assert viewset_op["owner_field"] == "owner"
    assert viewset_op["owner_scoped_reads"] is True


def test_ownership_leaves_reads_open_on_a_public_endpoint():
    plan = compile_feature_spec(
        spec_with(
            [{"name": "body", "type": "text"}],
            api={"authentication": "read_only_public", "ownership": {"field": "author"}},
        ),
        [],
        [],
    )
    (viewset_op,) = ops_by_type(plan, "create_viewset")
    assert viewset_op["permission"] == ["IsOwnerOrReadOnly"]
    assert viewset_op["owner_field"] == "author"
    # Scoping reads would contradict the declared public readability.
    assert viewset_op["owner_scoped_reads"] is False


def test_problems_carry_a_directly_applicable_fix_for_scalar_mistakes():
    """A close-match suggestion is only half an answer without where to put it."""
    problems = validate_feature_spec(
        {
            "name": "shop",
            "entities": [
                {
                    "name": "Invoice",
                    "fields": [
                        {
                            "name": "customer",
                            "type": "relation",
                            "target": "Custome",
                            "cardinality": "many-to-one",
                        }
                    ],
                    "api": {"authentication": "requird"},
                },
                {"name": "Customer", "fields": [{"name": "name", "type": "string"}]},
            ],
        },
        [],
        [],
    )
    by_code = {p["code"]: p for p in problems}

    # The fix targets the scalar to overwrite, not the field that reported it.
    relation = by_code["model_not_found"]
    assert relation["fix"] == {
        "path": "spec.entities[0].fields[0].target",
        "set": "Customer",
    }
    auth = by_code["invalid_authentication"]
    assert auth["fix"] == {
        "path": "spec.entities[0].api.authentication",
        "set": "required",
    }


def test_list_valued_problems_get_no_fix():
    """Replacing a whole list with one suggestion would be wrong."""
    problems = validate_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            api={"operations": ["list", "destroy"]},
        ),
        [],
        [],
    )
    ops = next(p for p in problems if p["path"].endswith("api.operations"))
    assert ops["suggestions"] == ["delete"]
    assert "fix" not in ops


def test_indexes_reach_the_model_meta():
    plan = compile_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}, {"name": "region", "type": "string"}],
            indexes=[{"fields": ["region"], "name": "idx_region"}, ["title"]],
        ),
        [],
        [],
    )
    (model_op,) = ops_by_type(plan, "create_model")
    assert model_op["meta"]["indexes"] == [
        {"fields": ["region"], "name": "idx_region"},
        {"fields": ["title"]},
    ]


def test_index_on_unknown_field_is_a_problem():
    problems = validate_feature_spec(
        spec_with([{"name": "title", "type": "string"}], indexes=[{"fields": ["nope"]}]),
        [],
        [],
    )
    assert any(p["path"].endswith("indexes[0]") for p in problems)


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
    # "destroy" is the framework's internal action name and the first thing an
    # agent reaches for; difflib cannot bridge it, so the alias table must.
    ops_problem = next(p for p in problems if p["path"].endswith("api.operations"))
    assert ops_problem["suggestions"] == ["delete"]


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


def test_existing_app_still_gets_create_app_and_warns_on_existing_model():
    """create_app is always planned, so a half-wired app repairs itself.

    It has ensure-semantics — existing files are untouched, INSTALLED_APPS and
    the project urls include are repaired — so planning it unconditionally is
    what lets re-running a spec fix an app whose wiring was lost, without the
    caller dropping to the per-object wiring tools.
    """
    plan = compile_feature_spec(
        {"name": "shop", "entities": [
            {"name": "Product", "fields": [{"name": "name", "type": "string"}]}
        ]},
        EXISTING,
        APPS,
    )
    assert ops_by_type(plan, "create_app") == [{"op": "create_app", "app": "shop"}]
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
    op, sync = ops
    assert op["op"] == "add_field" and op["app"] == "shop"
    assert op["field"]["null"] is True
    # The serializer must learn about the field too, or it stays invisible over the
    # API and writes to it are accepted and then silently discarded.
    assert sync == {
        "op": "sync_serializer",
        "app": "shop",
        "model": "Product",
        "field_name": "price",
        "present": True,
    }


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
    # Every field change carries a matching serializer sync, in both directions.
    assert ops[1] == {
        "op": "sync_serializer", "app": "shop", "model": "Product",
        "field_name": "owner", "present": True,
    }
    assert ops[2] == {"op": "remove_field", "app": "shop", "model": "Product", "field_name": "name"}
    assert ops[3] == {
        "op": "sync_serializer", "app": "shop", "model": "Product",
        "field_name": "name", "present": False,
    }


def test_compile_changes_add_entity_expands_to_full_scaffold():
    ops, _ = compile_changes(
        [{"operation": "add_entity", "app": "shop",
          "entity": {"name": "Order", "fields": [{"name": "total", "type": "decimal"}]}}],
        EXISTING,
        APPS,
        None,
    )
    kinds = [op["op"] for op in ops]
    assert kinds == [
        # add_entity reuses the feature compiler, so it inherits the same
        # ensure-the-app-is-wired step a build gets.
        "create_app",
        "create_model",
        "create_serializer",
        "create_viewset",
        "register_route",
    ]
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


# ---------------------------------------------------------------------------
# Workflows: validation, compilation, change ops
# ---------------------------------------------------------------------------

WORKFLOW_ENTITY = {
    "name": "Order",
    "fields": [{"name": "total", "type": "decimal"}],
    "workflow": {
        "states": ["draft", "submitted", "approved"],
        "transitions": [
            {"name": "submit", "from": "draft", "to": "submitted", "actor": "authenticated"},
            {"name": "approve", "from": ["submitted"], "to": "approved",
             "permission": "IsAdminUser"},
        ],
    },
}


def _workflow_spec(entity=None):
    return {"name": "shop_flow", "app": "shop_flow", "entities": [entity or WORKFLOW_ENTITY]}


def test_workflow_validation_matrix():
    def problems_for(workflow, fields=None, api=None):
        entity = {"name": "Order", "fields": fields or [{"name": "total", "type": "decimal"}],
                  "workflow": workflow}
        if api is not None:
            entity["api"] = api
        return validate_feature_spec(_workflow_spec(entity), [], [])

    base_transitions = [{"name": "submit", "from": "draft", "to": "submitted"}]

    assert problems_for({"states": [], "transitions": base_transitions})
    assert problems_for({"states": ["draft", "draft"], "transitions": base_transitions})
    assert problems_for({"states": ["draft", "submitted"], "initial": "nope",
                         "transitions": base_transitions})
    assert problems_for({"states": ["draft", "submitted"], "transitions": []})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "list", "from": "draft", "to": "submitted"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "total", "from": "draft", "to": "submitted"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "submit", "from": "nope", "to": "submitted"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "submit", "from": "draft", "to": "nope"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "submit", "from": "draft", "to": "submitted",
                                          "actor": "authenticated", "permission": "IsAdminUser"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "submit", "from": "draft", "to": "submitted",
                                          "actor": "wizard"}]})
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": [{"name": "submit", "from": "draft", "to": "submitted",
                                          "permission": "IsWizard"}]})
    # Duplicate transition names.
    assert problems_for({"states": ["draft", "submitted"],
                         "transitions": base_transitions + base_transitions})
    # expose: false + workflow is contradictory.
    assert problems_for({"states": ["draft", "submitted"], "transitions": base_transitions},
                        api={"expose": False})
    # Declared status field must be an enum covering the states.
    assert problems_for(
        {"states": ["draft", "submitted"], "transitions": base_transitions},
        fields=[{"name": "status", "type": "string"}],
    )
    # Valid workflow — no problems.
    assert problems_for({"states": ["draft", "submitted"], "transitions": base_transitions}) == []


def test_workflow_owner_actor_without_owner_field_warns():
    warnings: list[str] = []
    entity = {"name": "Order", "fields": [{"name": "total", "type": "decimal"}],
              "workflow": {"states": ["draft", "done"],
                            "transitions": [{"name": "finish", "from": "draft",
                                             "to": "done", "actor": "owner"}]}}
    assert validate_feature_spec(_workflow_spec(entity), [], [], warnings) == []
    assert any("IsOwner" in w for w in warnings)


def test_workflow_compiles_to_status_field_and_transition_ops():
    plan = compile_feature_spec(_workflow_spec(), [], [])
    model_op = next(op for op in plan["operations"] if op["op"] == "create_model")
    status = next(f for f in model_op["fields"] if f["name"] == "status")
    assert status["type"] == "CharField"
    assert status["choices"] == [["draft", "draft"], ["submitted", "submitted"],
                                 ["approved", "approved"]]
    assert status["default"] == "draft"

    actions = [op for op in plan["operations"] if op["op"] == "add_viewset_action"]
    assert [op["action_name"] for op in actions] == ["submit", "approve"]
    submit, approve = actions
    assert submit["permission"] == "IsAuthenticated"
    assert approve["permission"] == "IsAdminUser"
    assert submit["methods"] == ["post"] and submit["detail"] is True
    assert submit["response_serializer"] == "OrderSerializer"
    assert "ResourceConflictException" in submit["body"]
    assert 'obj.status not in ("draft",)' in submit["body"]
    assert 'obj.status = "submitted"' in submit["body"]
    assert "from zeeb_api.exceptions import ResourceConflictException" in submit["imports"]
    assert "2 workflow transition(s)" in plan["summary"]
    # Transition ops come after the entity's route registration.
    kinds = [op["op"] for op in plan["operations"]]
    assert kinds.index("register_route") < kinds.index("add_viewset_action")


def test_workflow_reuses_declared_enum_status_field():
    entity = {
        "name": "Order",
        "fields": [
            {"name": "total", "type": "decimal"},
            {"name": "status", "type": "enum",
             "values": ["draft", "submitted", "archived"], "default": "draft"},
        ],
        "workflow": {"states": ["draft", "submitted"],
                      "transitions": [{"name": "submit", "from": "draft",
                                       "to": "submitted"}]},
    }
    plan = compile_feature_spec(_workflow_spec(entity), [], [])
    model_op = next(op for op in plan["operations"] if op["op"] == "create_model")
    status_fields = [f for f in model_op["fields"] if f["name"] == "status"]
    assert len(status_fields) == 1  # declared field reused, none synthesized
    assert ["archived", "archived"] in status_fields[0]["choices"]


def test_transition_without_actor_or_permission_inherits():
    entity = {
        "name": "Order",
        "fields": [{"name": "total", "type": "decimal"}],
        "workflow": {"states": ["a", "b"],
                      "transitions": [{"name": "go", "from": "a", "to": "b"}]},
    }
    plan = compile_feature_spec(_workflow_spec(entity), [], [])
    action = next(op for op in plan["operations"] if op["op"] == "add_viewset_action")
    assert "permission" not in action


def test_add_workflow_change_emits_field_and_actions():
    existing = [{"app": "shop", "model": "Order", "fields": ["id", "total"]}]
    ops, warnings = compile_changes(
        [{"operation": "add_workflow", "entity": "Order",
          "workflow": {"states": ["draft", "done"],
                        "transitions": [{"name": "finish", "from": "draft", "to": "done"}]}}],
        existing,
        ["shop"],
        None,
    )
    kinds = [op["op"] for op in ops]
    assert kinds == ["add_field", "add_viewset_action"]
    field_op = ops[0]
    assert field_op["field"]["name"] == "status"
    assert field_op["field"]["default"] == "draft"
    assert ops[1]["action_name"] == "finish"

    # Existing status field: no add_field, but an assumption warning.
    existing_with_status = [
        {"app": "shop", "model": "Order", "fields": ["id", "total", "status"]}
    ]
    ops2, warnings2 = compile_changes(
        [{"operation": "add_workflow", "entity": "Order",
          "workflow": {"states": ["draft", "done"],
                        "transitions": [{"name": "finish", "from": "draft", "to": "done"}]}}],
        existing_with_status,
        ["shop"],
        None,
    )
    assert [op["op"] for op in ops2] == ["add_viewset_action"]
    assert any("assuming existing field 'status'" in w for w in warnings2)


def test_add_transition_change_emits_action_with_warning():
    existing = [{"app": "shop", "model": "Order", "fields": ["id", "status"]}]
    ops, warnings = compile_changes(
        [{"operation": "add_transition", "entity": "Order", "field": "status",
          "transition": {"name": "cancel", "from": ["draft", "submitted"],
                          "to": "cancelled"}}],
        existing,
        ["shop"],
        None,
    )
    assert [op["op"] for op in ops] == ["add_viewset_action"]
    assert 'obj.status not in ("draft", "submitted",)' in ops[0]["body"]
    assert any("cannot be checked" in w for w in warnings)


def test_add_transition_rejects_bad_shapes():
    existing = [{"app": "shop", "model": "Order", "fields": ["id", "status"]}]
    import pytest as _pytest

    from zeeb_agents._utils.errors import AgentError

    with _pytest.raises(AgentError):
        compile_changes(
            [{"operation": "add_transition", "entity": "Order",
              "transition": {"name": "x", "from": "a", "to": "b",
                              "actor": "authenticated", "permission": "IsAdminUser"}}],
            existing, ["shop"], None,
        )
    with _pytest.raises(AgentError):
        compile_changes(
            [{"operation": "add_workflow", "entity": "Order"}],
            existing, ["shop"], None,
        )


# ---------------------------------------------------------------------------
# Convergent reconciliation (re-running a build with an extended spec)
# ---------------------------------------------------------------------------


BLOG_EXISTING = [
    {
        "app": "blog",
        "model": "Post",
        "fields": ["id", "title", "created_at", "updated_at"],
        "field_types": {
            "id": "AutoField",
            "title": "CharField",
            "created_at": "DateTimeField",
            "updated_at": "DateTimeField",
        },
    }
]


def test_compile_reconciles_existing_entity_adds_missing_fields():
    plan = compile_feature_spec(
        spec_with(
            [
                {"name": "title", "type": "string"},
                {"name": "subtitle", "type": "string"},
                {"name": "author", "type": "relation", "target": "User"},
            ]
        ),
        BLOG_EXISTING + EXISTING,
        ["blog", *APPS],
    )
    added = {op["field"]["name"] for op in ops_by_type(plan, "add_field")}
    assert added == {"subtitle"}
    related = {op["rel"]["name"] for op in ops_by_type(plan, "add_relationship")}
    assert related == {"author"}
    # Every reconciled field is also exposed on the serializer, or writes to it
    # are accepted and silently discarded.
    synced = {(op["field_name"], op["present"]) for op in ops_by_type(plan, "sync_serializer")}
    assert synced == {("subtitle", True), ("author", True)}
    # The create steps stay (skip-idempotent), and reconcile ops come after them.
    assert ops_by_type(plan, "create_model")
    kinds = [op["op"] for op in plan["operations"]]
    assert kinds.index("create_serializer") < kinds.index("sync_serializer")
    assert "drift" not in plan
    assert "reconciling 2 field(s)" in plan["summary"]
    assert any("reconciling: 2 missing field(s)" in w for w in plan["warnings"])


def test_compile_reconcile_is_noop_when_spec_matches_disk():
    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "string"}]),
        BLOG_EXISTING,
        ["blog"],
    )
    assert ops_by_type(plan, "add_field") == []
    assert ops_by_type(plan, "sync_serializer") == []
    assert "drift" not in plan
    assert any("already exists" in w for w in plan["warnings"])


def test_compile_reports_drift_without_applying_it():
    plan = compile_feature_spec(
        # 'title' is CharField on disk and text in the spec; created_at/updated_at
        # are not drift because timestamps=True re-declares them.
        spec_with([{"name": "title", "type": "text"}], timestamps=True),
        BLOG_EXISTING,
        ["blog"],
    )
    kinds = {entry["kind"] for entry in plan["drift"]["entries"]}
    assert kinds == {"type_changed"}
    operations = {op["operation"] for op in plan["drift"]["suggested_changes"]}
    assert operations == {"alter_field"}
    # Reported, never applied.
    assert ops_by_type(plan, "alter_field") == []
    assert any("Destructive drift" in w for w in plan["warnings"])


def test_compile_reports_fields_missing_from_spec_as_drift():
    existing = [
        {
            "app": "blog",
            "model": "Post",
            "fields": ["id", "title", "legacy"],
            "field_types": {"title": "CharField", "legacy": "IntegerField"},
        }
    ]
    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "string"}], timestamps=False),
        existing,
        ["blog"],
    )
    entries = plan["drift"]["entries"]
    assert [(e["field"], e["kind"]) for e in entries] == [("legacy", "missing_from_spec")]
    assert plan["drift"]["suggested_changes"] == [
        {"operation": "remove_field", "entity": "Post", "app": "blog", "field_name": "legacy"}
    ]
    assert ops_by_type(plan, "remove_field") == []


def test_compile_skips_type_drift_without_field_types_inventory():
    # Older inventory snapshots carry names only — no types means no type drift,
    # not drift reported against every field.
    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "text"}], timestamps=False),
        [{"app": "blog", "model": "Post", "fields": ["id", "title"]}],
        ["blog"],
    )
    assert "drift" not in plan


# ---------------------------------------------------------------------------
# New change_feature operations
# ---------------------------------------------------------------------------


ORDER_EXISTING = [
    {
        "app": "shop",
        "model": "Order",
        "fields": ["id", "reference", "total"],
        "field_types": {"reference": "CharField", "total": "IntegerField"},
    }
]


def test_alter_field_change_compiles_and_warns_on_type_change():
    ops, warnings = compile_changes(
        [{"operation": "alter_field", "entity": "Order",
          "field": {"name": "total", "type": "decimal", "max_digits": 10,
                     "decimal_places": 2}}],
        ORDER_EXISTING,
        ["shop"],
        None,
    )
    assert [op["op"] for op in ops] == ["alter_field"]
    assert ops[0]["field"]["type"] == "decimal"
    assert any("IntegerField → DecimalField" in w for w in warnings)


def test_alter_field_rejects_unknown_field_with_suggestions():
    with pytest.raises(AgentError) as exc:
        compile_changes(
            [{"operation": "alter_field", "entity": "Order",
              "field": {"name": "referenc", "type": "text"}}],
            ORDER_EXISTING,
            ["shop"],
            None,
        )
    problem = (exc.value.result.data or {})["problems"][0]
    assert problem["code"] == "field_not_found"
    assert problem["suggestions"] == ["reference"]


def test_remove_entity_emits_cleanup_in_reverse_creation_order():
    ops, warnings = compile_changes(
        [{"operation": "remove_entity", "entity": "Order"}],
        ORDER_EXISTING,
        ["shop"],
        None,
    )
    assert [op["op"] for op in ops] == [
        "unregister_route",
        "delete_viewset",
        "delete_serializer",
        "delete_model",
    ]
    assert all(op["app"] == "shop" and op["model"] == "Order" for op in ops)
    assert any("dangling relation" in w for w in warnings)


def test_remove_entity_unpublishes_it_from_the_batch_inventory():
    with pytest.raises(AgentError) as exc:
        compile_changes(
            [
                {"operation": "remove_entity", "entity": "Order"},
                {"operation": "add_field", "entity": "Order",
                 "field": {"name": "note", "type": "string"}},
            ],
            ORDER_EXISTING,
            ["shop"],
            None,
        )
    assert (exc.value.result.data or {})["problems"][0]["code"] == "model_not_found"


def test_set_permissions_and_authentication_compile_to_update_viewset():
    ops, _ = compile_changes(
        [{"operation": "set_permissions", "entity": "Order",
          "permissions": ["IsAdminUser"]}],
        ORDER_EXISTING, ["shop"], None,
    )
    assert ops == [
        {"op": "update_viewset", "app": "shop", "model": "Order",
         "permission": ["IsAdminUser"]}
    ]
    ops, _ = compile_changes(
        [{"operation": "set_authentication", "entity": "Order",
          "authentication": "required"}],
        ORDER_EXISTING, ["shop"], None,
    )
    assert ops[0]["permission"] == ["IsAuthenticated"]


def test_set_permissions_rejects_unknown_class_and_authentication():
    with pytest.raises(AgentError) as exc:
        compile_changes(
            [{"operation": "set_permissions", "entity": "Order",
              "permissions": ["IsAdminUsr"]}],
            ORDER_EXISTING, ["shop"], None,
        )
    problem = (exc.value.result.data or {})["problems"][0]
    assert problem["code"] == "invalid_permission"
    assert "IsAdminUser" in problem["suggestions"]

    with pytest.raises(AgentError) as exc:
        compile_changes(
            [{"operation": "set_authentication", "entity": "Order",
              "authentication": "requird"}],
            ORDER_EXISTING, ["shop"], None,
        )
    assert (exc.value.result.data or {})["problems"][0]["code"] == "invalid_authentication"


def test_add_entity_generates_tests_into_a_per_entity_file():
    ops, _ = compile_changes(
        [{"operation": "add_entity", "app": "shop",
          "entity": {"name": "Refund", "fields": [{"name": "amount", "type": "int"}]}}],
        ORDER_EXISTING, ["shop"], None, tests=True,
    )
    generated = [op for op in ops if op["op"] == "generate_tests"]
    assert [op["filename"] for op in generated] == ["tests/test_shop_refund_generated.py"]
    # Default stays off — field-level changes never regenerate tests.
    ops, _ = compile_changes(
        [{"operation": "add_entity", "app": "shop",
          "entity": {"name": "Refund", "fields": [{"name": "amount", "type": "int"}]}}],
        ORDER_EXISTING, ["shop"], None,
    )
    assert not [op for op in ops if op["op"] == "generate_tests"]


def test_explicit_permissions_reach_the_test_descriptor():
    """The generated tests must be derived from the permission actually applied.

    An explicit ``api.permissions`` used to be invisible to the test scaffold,
    which read ``authentication`` instead — so a gated endpoint got an
    anonymous-GET-expects-200 test that failed against correct code.
    """
    from zeeb_agents.test_scaffold import _render_api_smoke

    plan = compile_feature_spec(
        spec_with(
            [{"name": "title", "type": "string"}],
            # authentication left at its read_only_public default on purpose.
            api={"permissions": ["IsAuthenticated"]},
        ),
        [],
        [],
    )
    (viewset_op,) = ops_by_type(plan, "create_viewset")
    (tests_op,) = ops_by_type(plan, "generate_tests")
    (descriptor,) = tests_op["entities"]

    assert viewset_op["permission"] == ["IsAuthenticated"]
    assert descriptor["permission"] == ["IsAuthenticated"]
    # The endpoint is exercised, but as an authenticated caller — never
    # anonymously, which is what used to fail against correct code.
    smoke = _render_api_smoke(descriptor) or ""
    assert "auth_client" in smoke
    assert "(client, api_prefix)" not in smoke


def test_read_only_public_still_generates_the_anonymous_smoke_test():
    from zeeb_agents.test_scaffold import _render_api_smoke

    plan = compile_feature_spec(
        spec_with([{"name": "title", "type": "string"}]),
        [],
        [],
    )
    (tests_op,) = ops_by_type(plan, "generate_tests")
    (descriptor,) = tests_op["entities"]
    assert descriptor["permission"] == ["IsAuthenticatedOrReadOnly"]
    assert "assert resp.status_code == 200" in (_render_api_smoke(descriptor) or "")


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def test_plan_risk_is_high_when_an_entity_is_dropped():
    from zeeb_agents.feature_spec import _plan_risk

    ops, _ = compile_changes(
        [{"operation": "remove_entity", "entity": "Order"}],
        ORDER_EXISTING, ["shop"], None,
    )
    risk = _plan_risk(ops)
    assert risk == {"level": "high", "destructive": True, "database_changes": True}
    # A field removal stays medium — routine schema churn, not a dropped table.
    ops, _ = compile_changes(
        [{"operation": "remove_field", "entity": "Order", "field_name": "total"}],
        ORDER_EXISTING, ["shop"], None,
    )
    assert _plan_risk(ops)["level"] == "medium"
