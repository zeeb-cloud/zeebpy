"""Dotted ("app.Model") string references and hard-failing model registration.

Regression tests for the ``KeyError('accounts.User')`` failure: LLM-driven
clients write Django-style dotted targets (``ForeignKey("accounts.User")``)
which the flat, class-name-keyed model registry could not resolve — and
``_register_models`` swallowed the error into a warning, leaving
makemigrations to run against an incomplete model state.

Covers:
- resolve_model_ref: bare names, dotted labels, unknown refs
- ForeignKey / ManyToMany / through resolution with dotted targets
- "self" references still work
- _register_models: cross-app dotted refs independent of INSTALLED_APPS order
- _register_models: raises ModelRegistrationError instead of warning
- make_migrations agent: full Lovable scenario (custom user model in
  'accounts', second app referencing "accounts.User")
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_orm import Model, fields
from zeeb_orm.exceptions import ModelRegistrationError
from zeeb_orm.models.base import _model_registry, metadata, resolve_model_ref
from zeeb_orm.migrations.cli import _register_models

# ---------------------------------------------------------------------------
# Test models (module-level, unique names to avoid registry collisions)
# ---------------------------------------------------------------------------


class DottedRefUser(Model):
    email = fields.CharField(max_length=200)

    class Meta:
        table_name = "dottedref_users"


class DottedRefExpense(Model):
    # Django-style dotted target — resolves via the bare-name fallback
    owner = fields.ForeignKey("accounts.DottedRefUser", on_delete="CASCADE")

    class Meta:
        table_name = "dottedref_expenses"


class DottedRefTag(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "dottedref_tags"


class DottedRefReport(Model):
    tags = fields.ManyToMany("taxonomy.DottedRefTag", related_name="reports")

    class Meta:
        table_name = "dottedref_reports"


class DottedRefMembership(Model):
    report = fields.ForeignKey("DottedRefReport", on_delete="CASCADE")
    user = fields.ForeignKey("DottedRefUser", on_delete="CASCADE")

    class Meta:
        table_name = "dottedref_memberships"


class DottedRefTeam(Model):
    # dotted custom through model
    members = fields.ManyToMany(
        "accounts.DottedRefUser", through="app.DottedRefMembership"
    )

    class Meta:
        table_name = "dottedref_teams"


class DottedRefNode(Model):
    parent = fields.ForeignKey("self", on_delete="CASCADE", null=True)

    class Meta:
        table_name = "dottedref_nodes"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore registry, metadata and project-module imports.

    Integration tests import ``apps.*`` packages from scaffolded tmp
    projects and register their models globally; without cleanup they leak
    into other tests (the registry is flat and metadata is shared).
    """
    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    yield
    for name in set(_model_registry) - set(registry_before):
        del _model_registry[name]
    for name in set(metadata.tables) - tables_before:
        metadata.remove(metadata.tables[name])
    for name in set(sys.modules) - modules_before:
        if name == "apps" or name.startswith("apps.") or name == "settings":
            del sys.modules[name]
    sys.path[:] = path_before
    try:
        from zeeb_api.auth.backends import set_project_root

        set_project_root(None)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# resolve_model_ref unit tests
# ---------------------------------------------------------------------------


class TestResolveModelRef:
    def test_bare_name(self):
        assert resolve_model_ref("DottedRefUser") is DottedRefUser

    def test_dotted_label(self):
        assert resolve_model_ref("accounts.DottedRefUser") is DottedRefUser

    def test_deeply_dotted_label(self):
        assert resolve_model_ref("apps.accounts.DottedRefUser") is DottedRefUser

    def test_unknown_ref_raises_keyerror_with_known_models(self):
        with pytest.raises(KeyError) as exc:
            resolve_model_ref("nowhere.Missing")
        assert "not registered" in str(exc.value)
        assert "DottedRefUser" in str(exc.value)

    def test_unknown_bare_ref_raises(self):
        with pytest.raises(KeyError):
            resolve_model_ref("Missing")


class TestDottedFieldTargets:
    def test_fk_dotted_target_resolves(self):
        field = DottedRefExpense._fk_fields[0]
        assert field.get_target_model() is DottedRefUser

    def test_fk_dotted_target_builds_table(self):
        DottedRefUser._get_table()  # FK column resolution needs the target table
        table = DottedRefExpense._get_table()
        fk = next(iter(table.columns["owner_id"].foreign_keys))
        assert fk.column.table.name == "dottedref_users"

    def test_m2m_dotted_target_resolves(self):
        m2m = DottedRefReport._m2m_fields[0]
        assert m2m.get_target_model() is DottedRefTag

    def test_m2m_dotted_through_resolves(self):
        m2m = DottedRefTeam._m2m_fields[0]
        assert m2m.get_target_model() is DottedRefUser
        assert m2m.get_through_model() is DottedRefMembership

    def test_self_reference_still_works(self):
        field = DottedRefNode._fk_fields[0]
        assert field.get_target_model() is DottedRefNode


# ---------------------------------------------------------------------------
# _register_models integration tests (hand-written project scaffold)
# ---------------------------------------------------------------------------


def _scaffold_project(
    root: Path,
    apps: dict[str, str],
    installed_apps: list[str],
    auth_user_model: str | None = None,
) -> Path:
    """Write a minimal generated-project layout into *root*."""
    (root / "manage.py").write_text("# test scaffold\n")
    conf = root / "demo"
    conf.mkdir()
    settings = f"INSTALLED_APPS = {installed_apps!r}\n"
    if auth_user_model:
        settings += f'AUTH_USER_MODEL = "{auth_user_model}"\n'
    (conf / "settings.py").write_text(settings)
    apps_dir = root / "apps"
    apps_dir.mkdir()
    (apps_dir / "__init__.py").write_text("")
    for app_name, models_src in apps.items():
        app_dir = apps_dir / app_name
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "models.py").write_text(textwrap.dedent(models_src))
    return root


def test_register_models_cross_app_dotted_ref_any_order(tmp_path):
    """An app listed BEFORE the app it references must still register."""
    root = _scaffold_project(
        tmp_path,
        apps={
            "expenses": """\
                from zeeb_orm import Model, fields

                class RegExpense(Model):
                    owner = fields.ForeignKey("accounts.RegUser", on_delete="CASCADE")

                    class Meta:
                        table_name = "reg_expenses"
            """,
            "accounts": """\
                from zeeb_orm import Model, fields

                class RegUser(Model):
                    email = fields.CharField(max_length=200)

                    class Meta:
                        table_name = "reg_accounts_user"
            """,
        },
        # expenses first: under one-phase registration this order failed
        installed_apps=["apps.expenses", "apps.accounts"],
    )

    _register_models(root)

    assert "reg_expenses" in metadata.tables
    assert "reg_accounts_user" in metadata.tables
    fk = next(iter(metadata.tables["reg_expenses"].columns["owner_id"].foreign_keys))
    assert fk.column.table.name == "reg_accounts_user"


def test_register_models_raises_on_unresolvable_ref(tmp_path):
    """A genuinely unknown target is a hard error, not a warning."""
    root = _scaffold_project(
        tmp_path,
        apps={
            "broken": """\
                from zeeb_orm import Model, fields

                class RegBroken(Model):
                    target = fields.ForeignKey("nowhere.Missing", on_delete="CASCADE")

                    class Meta:
                        table_name = "reg_broken"
            """,
        },
        installed_apps=["apps.broken"],
    )

    with pytest.raises(ModelRegistrationError) as exc:
        _register_models(root)
    assert "RegBroken" in str(exc.value)
    assert "nowhere.Missing" in str(exc.value)


def test_register_models_raises_on_broken_module(tmp_path):
    """A models.py that fails to execute is a hard error, not a warning."""
    root = _scaffold_project(
        tmp_path,
        apps={"crash": "raise RuntimeError('boom at import time')\n"},
        installed_apps=["apps.crash"],
    )

    with pytest.raises(ModelRegistrationError) as exc:
        _register_models(root)
    assert "apps.crash" in str(exc.value)
    assert "boom at import time" in str(exc.value)


def test_register_models_still_warns_on_missing_models_module(tmp_path):
    """An installed app without an importable models module stays skippable."""
    root = _scaffold_project(tmp_path, apps={}, installed_apps=["apps.ghost"])

    with pytest.warns(UserWarning, match="apps.ghost"):
        _register_models(root)


# ---------------------------------------------------------------------------
# Lovable scenario end-to-end (agents scaffold + make_migrations)
# ---------------------------------------------------------------------------


async def test_make_migrations_with_custom_user_model_and_dotted_fk(tmp_path):
    """Custom user model in 'accounts' + cross-app ForeignKey("accounts.User").

    Mirrors the failing spesentool_v1 flow: create_user_model writes
    AUTH_USER_MODEL = "accounts.User"; a second app references the dotted
    label. make_migrations must succeed and emit CreateModel for every table.
    """
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    for app in ("accounts", "expenses"):
        res = await agents.create_app(app, project_id=root)
        assert res.success, res.message

    # create_app scaffolds but does not register — clients add the apps to
    # INSTALLED_APPS themselves (same as the real flow).
    settings_path = root / "demo" / "settings.py"
    settings_path.write_text(
        settings_path.read_text().replace(
            "INSTALLED_APPS = [",
            'INSTALLED_APPS = [\n    "apps.accounts",\n    "apps.expenses",',
            1,
        )
    )

    res = await agents.create_user_model(
        "accounts",
        "User",
        extra_fields=[{"name": "role", "type": "CharField", "max_length": 32}],
        project_id=root,
    )
    assert res.success, res.message
    assert res.data["auth_user_model"] == "accounts.User"

    res = await agents.create_model(
        "expenses",
        "ExpenseReport",
        [
            {"name": "title", "type": "CharField", "max_length": 200},
            {
                "name": "owner",
                "type": "ForeignKey",
                "to": "accounts.User",
                "on_delete": "CASCADE",
            },
        ],
        project_id=root,
    )
    assert res.success, res.message

    res = await agents.make_migrations(project_id=root)
    assert res.success, res.message
    assert res.data["created"] is not None
    operations = "\n".join(res.data["operations"])
    assert "Create model User" in operations
    assert "Create model ExpenseReport" in operations
