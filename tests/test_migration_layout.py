"""Migration files live flat in ``migrations/``, and every reader agrees.

``write_migration`` has always written ``migrations/0001_initial.py``, but the
scaffolder created ``migrations/versions/`` and both pre-flight checks globbed
it — so a freshly scaffolded project could never start:
``python manage.py runserver`` aborted with "No migrations found!" and
``zeeb check`` reported the same forever.
"""

from pathlib import Path

import pytest

from zeeb_orm.cli.commands.check import check_migrations, find_migrations
from zeeb_orm.cli.commands.runserver import check_migrations_before_start
from zeeb_orm.cli.commands.startproject import run_startproject
from zeeb_orm.migrations.executor import find_migration_files, list_migration_files


@pytest.fixture
def project(tmp_path) -> Path:
    assert run_startproject("demo", str(tmp_path)) == 0
    return tmp_path / "demo"


def write_migration_file(directory: Path, name: str = "0001_initial.py") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("# migration\n")
    return path


def test_startproject_creates_a_flat_migrations_dir(project):
    assert (project / "migrations").is_dir()
    assert (project / "migrations" / ".gitkeep").is_file()
    assert not (project / "migrations" / "versions").exists()


def test_gitignore_covers_the_flat_layout(project):
    gitignore = (project / ".gitignore").read_text()
    assert "migrations/__pycache__/" in gitignore
    assert "migrations/versions/" not in gitignore


def test_find_migration_files_reads_the_flat_layout(project):
    migrations = project / "migrations"
    assert find_migration_files(migrations) == []

    write_migration_file(migrations)
    assert [name for name, _ in find_migration_files(migrations)] == ["0001_initial"]


def test_find_migration_files_falls_back_to_a_legacy_versions_dir(project):
    """Projects scaffolded before the fix may hold migrations under versions/."""
    migrations = project / "migrations"
    write_migration_file(migrations / "versions")

    assert list_migration_files(migrations) == []
    assert [name for name, _ in find_migration_files(migrations)] == ["0001_initial"]


def test_flat_migrations_win_over_a_legacy_versions_dir(project):
    migrations = project / "migrations"
    write_migration_file(migrations, "0002_flat.py")
    write_migration_file(migrations / "versions", "0001_legacy.py")

    assert [name for name, _ in find_migration_files(migrations)] == ["0002_flat"]


def test_runserver_preflight_sees_a_flat_migration(project, capsys):
    # Nothing written yet: the gate refuses at the "are there any files" check.
    assert check_migrations_before_start(project) is False
    assert "No migrations found" in capsys.readouterr().out

    write_migration_file(project / "migrations")
    # The file is now found, so the gate advances to the "is it applied" check
    # and complains about *that* instead. Before the fix it never got here.
    assert check_migrations_before_start(project) is False
    output = capsys.readouterr().out
    assert "No migrations found" not in output
    assert "Unapplied migrations" in output


def test_check_command_finds_flat_migrations(project):
    issues, summary = check_migrations(project)
    assert [issue["message"] for issue in issues] == [
        "No migrations have been created yet."
    ]
    assert issues[0]["next_command"] == "python manage.py makemigrations"
    # The stale "versions/ directory not found" complaint is gone.
    assert not any("versions" in issue["message"] for issue in issues)

    write_migration_file(project / "migrations")
    issues, summary = check_migrations(project)
    assert summary["total"] == 1
    assert len(find_migrations(project)) == 1
    # Found but never applied — which check must report rather than call green.
    assert [issue["next_command"] for issue in issues] == ["python manage.py migrate"]


def test_check_command_still_counts_a_legacy_versions_layout(project):
    write_migration_file(project / "migrations" / "versions")
    _, summary = check_migrations(project)
    assert summary["total"] == 1
    assert len(find_migrations(project)) == 1


def test_run_init_creates_the_flat_layout(tmp_path, monkeypatch):
    from zeeb_orm.cli.commands.migrate import run_init

    (tmp_path / "manage.py").write_text("")
    monkeypatch.chdir(tmp_path)

    assert run_init("migrations") == 0
    assert (tmp_path / "migrations" / ".gitkeep").is_file()
    assert not (tmp_path / "migrations" / "versions").exists()

    # Idempotent.
    assert run_init("migrations") == 0
