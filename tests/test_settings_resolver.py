"""One resolver for a project's root, settings module and database.

Four copies of "find settings.py" disagreed (sorted or not, ``apps/`` skipped
or not), none read the ``[tool.zeeb] settings_module`` key ``startproject``
writes, ``migrate`` printed a warning into its ``--json`` stdout and fell back
to ``sqlite:///db.sqlite3`` when settings.py failed to import, and the
migration state reported "everything pending" for a database it could not
read. These tests pin the single behaviour.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from zeeb_orm.conf.project import (
    SettingsImportError,
    find_project_root,
    find_settings_module,
    load_settings_module,
    resolve_database_url,
)


def _settings(root, package, body="INSTALLED_APPS = []\n"):
    (root / package).mkdir(parents=True, exist_ok=True)
    (root / package / "settings.py").write_text(textwrap.dedent(body))


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "manage.py").write_text("")
    (root / "migrations").mkdir()
    return root


class TestDiscovery:
    def test_the_pyproject_key_wins_over_the_scan(self, project):
        _settings(project, "aaa", "DATABASE = {'url': 'sqlite:///aaa.db'}\n")
        _settings(project, "proj", "DATABASE = {'url': 'sqlite:///proj.db'}\n")
        (project / "pyproject.toml").write_text(
            '[tool.zeeb]\nframework = "zeebpy"\nsettings_module = "proj.settings"\n'
        )
        assert find_settings_module(project) == "proj.settings"
        assert resolve_database_url(project) == "sqlite:///proj.db"

    def test_the_scan_is_sorted_and_skips_apps(self, project):
        _settings(project, "apps")
        _settings(project, "zzz")
        _settings(project, "mmm")
        assert find_settings_module(project) == "mmm.settings"

    def test_a_declared_module_that_does_not_exist_falls_back_to_the_scan(self, project):
        _settings(project, "real")
        (project / "pyproject.toml").write_text('[tool.zeeb]\nsettings_module = "gone.settings"\n')
        assert find_settings_module(project) == "real.settings"

    def test_a_broken_settings_module_raises(self, project):
        _settings(project, "proj", "raise RuntimeError('bad env')\n")
        with pytest.raises(SettingsImportError, match="bad env") as info:
            load_settings_module(project)
        assert info.value.module == "proj.settings"
        with pytest.raises(SettingsImportError):
            resolve_database_url(project)

    def test_no_settings_uses_the_orm_configuration(self, project, monkeypatch):
        from zeeb_orm.conf.settings import Settings

        monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
        monkeypatch.setattr(Settings, "_instance", None)
        assert load_settings_module(project) is None
        assert resolve_database_url(project) == "sqlite:///from-env.db"

    def test_manage_py_further_up_wins_over_a_nested_migrations_dir(self, project):
        nested = project / "apps" / "blog"
        (nested / "migrations").mkdir(parents=True)
        assert find_project_root(nested, allow_migrations_dir=True) == project
        assert find_project_root(nested) == project

    def test_migrations_dir_is_only_a_fallback_when_asked(self, tmp_path):
        (tmp_path / "loose" / "migrations").mkdir(parents=True)
        assert find_project_root(tmp_path / "loose") is None
        assert find_project_root(tmp_path / "loose", allow_migrations_dir=True) == (
            tmp_path / "loose"
        )


class TestCommandsUseTheResolver:
    def test_migrate_json_reports_broken_settings_as_one_object(self, project, monkeypatch, capsys):
        from zeeb_orm.cli.commands.migrate import run_migrate

        _settings(project, "proj", "raise RuntimeError('SECRET_KEY missing')\n")
        monkeypatch.chdir(project)

        code = run_migrate(None, None, False, json_output=True)
        out = capsys.readouterr().out

        assert code == 1
        payload = json.loads(out)  # exactly one JSON object, nothing else
        assert payload["success"] is False
        assert "SECRET_KEY missing" in payload["message"]
        assert payload["data"]["next_command"] == "python manage.py check"
        # No database was invented for the failed settings.
        assert not (project / "db.sqlite3").exists()

    def test_showmigrations_and_makemigrations_refuse_too(self, project, monkeypatch, capsys):
        from zeeb_orm.cli.commands.migrate import run_makemigrations, run_showmigrations

        _settings(project, "proj", "import no_such_module\n")
        monkeypatch.chdir(project)

        assert run_showmigrations(json_output=True) == 1
        assert json.loads(capsys.readouterr().out)["success"] is False
        assert run_makemigrations(None, False, json_output=True) == 1
        assert "no_such_module" in json.loads(capsys.readouterr().out)["message"]

    def test_migrate_uses_the_declared_settings(self, project, monkeypatch, capsys):
        from zeeb_orm.cli.commands.migrate import run_migrate

        db = project / "declared.sqlite3"
        _settings(project, "aaa", "DATABASE = {'url': 'sqlite:///wrong.sqlite3'}\n")
        _settings(project, "proj", f"DATABASE = {{'url': 'sqlite:///{db}'}}\n")
        (project / "pyproject.toml").write_text('[tool.zeeb]\nsettings_module = "proj.settings"\n')
        monkeypatch.chdir(project)

        assert run_migrate(None, None, False, json_output=True) == 0
        assert db.exists()
        assert not (project / "wrong.sqlite3").exists()

    def test_the_programmatic_api_reads_the_same_database(self, project, monkeypatch):
        from zeeb_orm.migrations.cli import _get_database_url

        _settings(project, "proj", "DATABASE = {'url': 'sqlite:///settings.db'}\n")
        monkeypatch.chdir(project)
        assert _get_database_url() == "sqlite:///settings.db"

    def test_runserver_serves_the_settings_packages_asgi(self, project):
        from zeeb_orm.cli.commands.runserver import find_asgi_app

        for package in ("aaa", "proj"):
            (project / package).mkdir(exist_ok=True)
            (project / package / "asgi.py").write_text("app = None\n")
        _settings(project, "proj")
        (project / "pyproject.toml").write_text('[tool.zeeb]\nsettings_module = "proj.settings"\n')
        assert find_asgi_app(project) == "proj.asgi:app"

    def test_check_reports_the_import_error(self, project, monkeypatch, capsys):
        from zeeb_orm.cli.commands.check import run_check

        _settings(project, "proj", "DATABASE = {}\nINSTALLED_APPS = []\nraise ValueError('boom')\n")
        monkeypatch.chdir(project)
        run_check(json_output=True)
        issues = json.loads(capsys.readouterr().out)["data"]["issues"]
        assert any("boom" in issue["message"] for issue in issues)


class TestMigrationState:
    def test_an_unreadable_database_is_an_error_not_all_pending(self, project):
        from zeeb_orm.migrations.state import MigrationStateError, get_migration_state
        from zeeb_orm.migrations.writer import write_migration

        write_migration(project / "migrations", operations=[], initial=True)
        unreachable = f"sqlite:///{project / 'no' / 'such' / 'dir' / 'db.sqlite3'}"

        with pytest.raises(MigrationStateError, match="Could not read the applied migrations"):
            get_migration_state(project, db_url=unreachable)
