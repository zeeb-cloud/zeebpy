"""Failures in ``--json`` mode are envelopes too, and names a project can live with.

Each test pins a defect that used to pass silently:

- ``migrate --json`` hitting a database error, a migration file with a syntax
  error, and ``makemigrations --json`` with a model that does not register all
  printed a traceback and nothing on stdout
- ``makemigrations`` wrote a NOT NULL ``AddField`` that could never apply
- ``startapp class`` reported success and wrote a ``urls.py`` that does not
  parse; ``startproject class`` / ``sqlalchemy`` / ``zeeb_orm`` produced a
  project that cannot import itself or its dependencies
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zeeb_orm.cli.commands.startproject import run_startproject
from zeeb_orm.scaffold import ScaffoldError, project_name_problem
from zeeb_orm.scaffold.wiring import append_router_include, write_python_source

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def manage(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``python manage.py <args>`` in a clean subprocess."""
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def failure(result: subprocess.CompletedProcess) -> dict:
    """stdout as the one failure envelope it must be, with a next command."""
    payload = json.loads(result.stdout)  # raises if stdout is empty or not JSON
    assert set(payload) == {"success", "message", "data"}, payload
    assert payload["success"] is False
    assert result.returncode == 1
    assert payload["data"]["next_command"]
    return payload


@pytest.fixture()
def project(tmp_path) -> Path:
    assert run_startproject("jf_proj", str(tmp_path)) == 0
    return tmp_path / "jf_proj"


def _app(root: Path, name: str, models: str) -> None:
    from zeeb_orm.scaffold.wiring import ensure_installed_app

    app = root / "apps" / name
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "models.py").write_text(models)
    ensure_installed_app(root, name)


_MIGRATION = '''
import sqlalchemy as sa
from zeeb_orm.migrations import Migration, operations


class Migration(Migration):
    dependencies = {deps!r}
    operations = [{ops}]
'''


class TestMigrationCommandEnvelopes:
    def test_migrate_reports_a_database_error(self, project):
        assert manage(project, "makemigrations").returncode == 0
        first = sorted((project / "migrations").glob("0001_*.py"))[0].stem
        (project / "migrations" / "0002_broken.py").write_text(
            _MIGRATION.format(
                deps=[first], ops='operations.RunSQL("SELECT * FROM no_such_table")'
            )
        )
        payload = failure(manage(project, "migrate", "--json"))
        assert "no_such_table" in payload["message"]
        assert payload["data"]["next_command"] == "python manage.py showmigrations"
        # 0001 was applied before 0002 failed.
        assert payload["data"]["state_changed"] is True
        assert payload["data"]["error_code"] == "partial_failure"

    def test_a_migration_with_a_syntax_error(self, project):
        (project / "migrations" / "0001_broken.py").write_text("class Migration(:\n")
        for command in ("makemigrations", "migrate"):
            payload = failure(manage(project, command, "--json"))
            assert "0001_broken.py" in payload["message"], command
            assert payload["data"]["next_command"].startswith("python -m py_compile"), command

    def test_makemigrations_with_a_model_that_does_not_register(self, project):
        _app(project, "broken", "raise RuntimeError('models.py is broken')\n")
        payload = failure(manage(project, "makemigrations", "--json"))
        assert "broken" in payload["message"]
        assert payload["data"]["next_command"] == "python manage.py check"

    def test_a_not_null_field_without_a_default_is_refused(self, project):
        models = (
            "from zeeb_orm import Model, fields\n\n\n"
            "class Note(Model):\n"
            "    title = fields.CharField(max_length=20)\n{extra}\n"
            "    class Meta:\n        table_name = 'jf_note'\n"
        )
        _app(project, "notes", models.format(extra=""))
        assert manage(project, "makemigrations").returncode == 0
        (project / "apps" / "notes" / "models.py").write_text(
            models.format(extra="    rank = fields.IntegerField()\n")
        )
        before = sorted(p.name for p in (project / "migrations").glob("*.py"))
        payload = failure(manage(project, "makemigrations", "--json"))
        assert payload["data"]["fields"] == ["Note.rank"]
        assert "null=True" in payload["data"]["next_command"]
        assert sorted(p.name for p in (project / "migrations").glob("*.py")) == before

    def test_an_unhandled_error_in_any_json_command_is_an_envelope(self, monkeypatch, capsys):
        import importlib

        from zeeb_orm.cli.commands import inspect

        cli_main = importlib.import_module("zeeb_orm.cli.main")

        def boom(**_kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(inspect, "run_showurls", boom)
        monkeypatch.setattr(sys, "argv", ["manage.py", "showurls", "--json"])
        assert cli_main.main() == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert payload["data"]["next_command"]
        assert payload["data"]["exception_type"] == "RuntimeError"


class TestNotNullRenameHint:
    def test_a_not_null_rename_points_at_accept_renames(self, tmp_path):
        import sqlalchemy as sa

        from zeeb_orm.migrations.autodetector import NotNullFieldError
        from zeeb_orm.migrations.autogen import make_migration
        from zeeb_orm.migrations.operations import CreateModel
        from zeeb_orm.migrations.writer import write_migration
        from zeeb_orm.models.base import metadata

        mig = tmp_path / "migrations"
        mig.mkdir()
        pk = sa.Column("id", sa.Integer(), primary_key=True)
        write_migration(
            mig,
            [CreateModel(
                name="Doc", table="jf_docs",
                columns=[pk, sa.Column("body", sa.Text(), nullable=False)], primary_key=["id"],
            )],
            initial=True,
        )
        sa.Table(
            "jf_docs", metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("content", sa.Text(), nullable=False),
        )
        with pytest.raises(NotNullFieldError, match="--accept-renames"):
            make_migration(mig)
        assert make_migration(mig, accept_renames=True).changed


class TestNames:
    def test_startapp_refuses_a_keyword_and_leaves_urls_parsing(self, project):
        urls = next(project.glob("*/urls.py"))
        before = urls.read_text()
        result = manage(project, "startapp", "class", "--json")
        payload = failure(result)
        assert payload["data"]["error_code"] == "invalid_identifier"
        assert "keyword" in payload["message"]
        assert not (project / "apps" / "class").exists()
        assert urls.read_text() == before

    def test_a_soft_keyword_is_a_fine_app_name(self, project):
        assert manage(project, "startapp", "match").returncode == 0
        compile(next(project.glob("*/urls.py")).read_text(), "urls.py", "exec")

    @pytest.mark.parametrize(
        "name", ["class", "sqlalchemy", "zeeb_orm", "fastapi", "json", "tests", "apps"]
    )
    def test_startproject_refuses_a_name_that_shadows_something(self, tmp_path, capsys, name):
        assert run_startproject(name, str(tmp_path), json_output=True) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["error_code"] == "invalid_identifier"
        assert not (tmp_path / name).exists()

    def test_ordinary_project_names_are_fine(self):
        assert project_name_problem("blog") is None
        assert project_name_problem("my_shop") is None

    def test_the_wiring_writer_refuses_source_that_does_not_parse(self, tmp_path):
        urls = tmp_path / "urls.py"
        urls.write_text("router = object()\n")
        with pytest.raises(ScaffoldError) as info:
            append_router_include(urls, "router.include(", import_stmt=None)
        assert info.value.code == "invalid_input"
        assert urls.read_text() == "router = object()\n"
        with pytest.raises(ScaffoldError):
            write_python_source(urls, "def (:\n")
        assert urls.read_text() == "router = object()\n"
        assert not list(tmp_path.glob(".*.tmp"))

    def test_the_wiring_helpers_refuse_a_keyword_app(self, project):
        from zeeb_orm.scaffold.wiring import ensure_app_urls_included, ensure_installed_app

        for helper in (ensure_installed_app, ensure_app_urls_included):
            with pytest.raises(ScaffoldError) as info:
                helper(project, "class")
            assert info.value.code == "invalid_identifier"
