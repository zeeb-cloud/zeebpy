"""Unit tests for the generated-test scaffolding (``zeeb_agents.test_scaffold``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zeeb_agents.test_scaffold import generate_tests

ORDER_ENTITY = {
    "name": "Order",
    "exposed": True,
    "authentication": "read_only_public",
    "prefix": "orders",
    "fields": [
        {"name": "total", "type": "decimal"},
        {"name": "note", "type": "string", "null": True, "blank": True},
        {"name": "status", "type": "CharField", "choices": [["draft", "draft"]],
         "default": "draft", "max_length": 5},
    ],
}


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "settings.py").write_text("DEBUG = True\n")
    (tmp_path / "apps").mkdir()
    return tmp_path


async def test_generate_tests_writes_suite(fake_project: Path):
    res = await generate_tests("shop", [ORDER_ENTITY], project_id=fake_project)
    assert res.success, res.message
    assert set(res.data["created"]) == {
        "pytest.ini", "tests/__init__.py", "tests/conftest.py",
        "tests/test_shop_generated.py",
    }
    conftest = (fake_project / "tests" / "conftest.py").read_text()
    assert 'SETTINGS_MODULE = "demo.settings"' in conftest
    assert "DATABASE_URL" in conftest

    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    # Required decimal gets a sample; nullable and defaulted fields are omitted.
    assert "Order.objects.create(total='9.99')" in generated
    assert "note=" not in generated
    assert "status=" not in generated
    assert 'resp = await client.get(f"{api_prefix}/orders")' in generated

    import ast

    ast.parse(generated)
    ast.parse(conftest)


async def test_generate_tests_never_overwrites(fake_project: Path):
    first = await generate_tests("shop", [ORDER_ENTITY], project_id=fake_project)
    assert first.success
    marker = "# user edited\n"
    test_file = fake_project / "tests" / "test_shop_generated.py"
    test_file.write_text(marker)

    second = await generate_tests("shop", [ORDER_ENTITY], project_id=fake_project)
    assert second.success
    assert second.data["created"] == []
    assert "tests/test_shop_generated.py" in second.data["skipped"]
    assert test_file.read_text() == marker


async def test_authenticated_entities_are_exercised_with_a_token(fake_project: Path):
    """An auth-gated endpoint is tested as an authenticated user, not skipped.

    It used to generate no endpoint test at all, which made ``verified: true``
    mean "the ORM works" for every secured entity.
    """
    entity = {**ORDER_ENTITY, "authentication": "required", "permission": ["IsAuthenticated"]}
    res = await generate_tests("shop", [entity], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    assert 'await auth_client.get(f"{api_prefix}/orders")' in generated
    # The anonymous client must never be used for a read on a gated endpoint.
    assert 'resp = await client.get(f"{api_prefix}/orders")' not in generated
    # ...and the gate itself is asserted.
    assert "test_order_rejects_anonymous_create" in generated
    assert "assert resp.status_code in (401, 403)" in generated
    assert "orm_roundtrip" in generated


async def test_unsatisfiable_permission_generates_no_endpoint_test(fake_project: Path):
    """No synthesized identity satisfies IsOwner, so no endpoint test is written."""
    entity = {**ORDER_ENTITY, "permission": ["IsOwner"]}
    res = await generate_tests("shop", [entity], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    assert "endpoint_lists" not in generated
    assert "crud_over_http" not in generated
    assert "orm_roundtrip" in generated  # ORM layer still exercised


async def test_withheld_operations_are_never_exercised(fake_project: Path):
    """A spec that withholds delete must not generate a DELETE assertion."""
    entity = {
        **ORDER_ENTITY,
        "permission": ["AllowAny"],
        "operations": ["list", "retrieve", "create"],
    }
    res = await generate_tests("shop", [entity], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    assert "crud_over_http" in generated
    assert "client.delete(" not in generated
    assert "client.patch(" not in generated
    # The contract test must not demand methods the endpoint does not serve.
    assert "'delete'" not in generated
    assert "'post'" in generated


async def test_conftest_imports_every_installed_app_before_building_the_schema(
    fake_project: Path,
):
    """A project with a custom user model must not fail at create_all.

    Models only register when their module is imported, and the framework's auth
    tables carry a foreign key to the user table — so without this the whole
    generated suite errored at fixture setup on any bootstrapped project.
    """
    res = await generate_tests("shop", [ORDER_ENTITY], project_id=fake_project)
    assert res.success
    conftest = (fake_project / "tests" / "conftest.py").read_text()
    assert "_import_all_models" in conftest
    assert "INSTALLED_APPS" in conftest


async def test_generated_requests_use_canonical_slashless_paths(fake_project: Path):
    """Writes must target the canonical path — a trailing-slash POST 405s.

    zeeb_api serves the trailing-slash variant for reads only, so a generated
    ``POST /orders/`` returned METHOD_NOT_ALLOWED instead of exercising anything.
    """
    entity = {**ORDER_ENTITY, "permission": ["AllowAny"]}
    res = await generate_tests("shop", [entity], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    assert '/orders"' in generated
    assert '/orders/"' not in generated


async def test_required_internal_fk_creates_target_first(fake_project: Path):
    category = {
        "name": "Category", "exposed": True, "authentication": "public",
        "prefix": "categories",
        "fields": [{"name": "name", "type": "string", "max_length": 50}],
    }
    post = {
        "name": "Post", "exposed": True, "authentication": "public",
        "prefix": "posts",
        "fields": [
            {"name": "title", "type": "string", "max_length": 50},
            {"name": "category", "type": "fk", "to": "Category"},
        ],
    }
    res = await generate_tests("blog", [category, post], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_blog_generated.py").read_text()
    assert "category = await Category.objects.create(name='sample')" in generated
    assert "category_id=category.id" in generated


async def test_workflow_transition_test_generated(fake_project: Path):
    entity = {
        **ORDER_ENTITY,
        "authentication": "public",
        "workflow": {
            "field": "status",
            "initial": "draft",
            "transitions": [
                {"name": "submit", "from_states": ["draft"], "to": "submitted",
                 "permission": None},
                {"name": "approve", "from_states": ["submitted"], "to": "approved",
                 "permission": "IsAdminUser"},
            ],
        },
    }
    res = await generate_tests("shop", [entity], project_id=fake_project)
    assert res.success
    generated = (fake_project / "tests" / "test_shop_generated.py").read_text()
    assert "test_order_submit_transition_conflict" in generated
    assert "assert again.status_code == 409" in generated
    assert "approve" not in generated.replace("test_order_submit", "")  # permissioned one skipped


def test_pytest_summary_parser_handles_all_orderings():
    from zeeb_agents.testing import _parse_pytest_output

    assert _parse_pytest_output("== 3 failed in 1.20s ==") == {
        "passed": 0, "failed": 3, "errors": 0, "skipped": 0,
    }
    assert _parse_pytest_output("== 2 failed, 5 passed, 1 skipped in 0.5s ==") == {
        "passed": 5, "failed": 2, "errors": 0, "skipped": 1,
    }
    assert _parse_pytest_output("== 4 passed in 0.1s ==") == {
        "passed": 4, "failed": 0, "errors": 0, "skipped": 0,
    }
    assert _parse_pytest_output("== 1 error in 0.1s ==") == {
        "passed": 0, "failed": 0, "errors": 1, "skipped": 0,
    }
    assert _parse_pytest_output("no summary here") == {
        "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
    }
