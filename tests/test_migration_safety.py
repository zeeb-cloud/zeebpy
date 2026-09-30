"""Migrations that cannot silently lose data or schema.

Each test pins a defect that used to pass silently:

- ``Meta.managed = False`` was parsed and ignored (makemigrations and test
  schemas created the table anyway)
- ``DeleteModel.backward`` was a no-op that reported itself reversible, so a
  rollback "succeeded" without recreating the table
- the ``zeeb_migrations`` DDL used SQLite's ``AUTOINCREMENT`` on MySQL
- a renamed field became RemoveField + AddField without a word, and a schema
  operation failing during state replay was only a warning
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from zeeb_orm import Model, fields
from zeeb_orm.migrations import IrreversibleError
from zeeb_orm.migrations.autodetector import (
    MigrationReplayError,
    detect_changes,
    detect_changes_with_report,
)
from zeeb_orm.migrations.executor import list_migration_files, load_migration, tracking_table
from zeeb_orm.migrations.operations import (
    AddField,
    CreateModel,
    DeleteModel,
    RemoveField,
    RemoveIndex,
    RenameField,
    RunPython,
    RunSQL,
)
from zeeb_orm.migrations.writer import write_migration
from zeeb_orm.models.base import metadata


@pytest.fixture()
def mig_dir(tmp_path):
    path = tmp_path / "migrations"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _clean_metadata():
    before = set(metadata.tables)
    yield
    for name in set(metadata.tables) - before:
        metadata.remove(metadata.tables[name])


def _table(name: str, *columns) -> sa.Table:
    if name in metadata.tables:
        metadata.remove(metadata.tables[name])
    return sa.Table(name, metadata, *columns)


def _mine(ops, table):
    return [op for op in ops if getattr(op, "table", None) == table]


# ---------------------------------------------------------------------------
# 7. Meta.managed = False
# ---------------------------------------------------------------------------


class TestUnmanagedModels:
    def test_makemigrations_ignores_an_unmanaged_model(self, mig_dir):
        class MsLegacyView(Model):
            name = fields.CharField(max_length=50)

            class Meta:
                table_name = "ms_legacy_view"
                managed = False

        MsLegacyView._get_table()
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "ms_legacy_view") == []

    def test_an_unmanaged_model_whose_table_a_migration_created_is_not_dropped(self, mig_dir):
        """Turning managed off must not make makemigrations delete the table."""
        write_migration(
            mig_dir,
            operations=[
                CreateModel(
                    name="MsExternal",
                    table="ms_external",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            name="initial",
            initial=True,
        )

        class MsExternal(Model):
            id = fields.AutoField()

            class Meta:
                table_name = "ms_external"
                managed = False

        MsExternal._get_table()
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "ms_external") == []

    async def test_test_schemas_skip_unmanaged_tables(self):
        from zeeb_orm.testing import temporary_database

        class MsManaged(Model):
            class Meta:
                table_name = "ms_managed"

        class MsUnmanaged(Model):
            class Meta:
                table_name = "ms_unmanaged"
                managed = False

        async with temporary_database(MsManaged, MsUnmanaged) as database:
            async with database._async_engine.connect() as conn:
                names = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert "ms_managed" in names
            assert "ms_unmanaged" not in names

            await database.create_all()
            async with database._async_engine.connect() as conn:
                names = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert "ms_unmanaged" not in names


# ---------------------------------------------------------------------------
# 8. Reversibility is real or refused
# ---------------------------------------------------------------------------


def _migrate(mig_dir, db, **kwargs):
    from zeeb_orm.migrations import executor

    return executor.migrate(database_url=db, project_root=mig_dir.parent, **kwargs)


def _tables(db):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            return set(sa.inspect(conn).get_table_names())
    finally:
        engine.dispose()


class TestReversibility:
    def test_makemigrations_records_what_delete_model_drops(self, mig_dir, tmp_path):
        _table(
            "ms_doomed",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("title", sa.String(80), nullable=False),
        )
        ops = detect_changes(migrations_dir=str(mig_dir))
        write_migration(mig_dir, operations=_mine(ops, "ms_doomed"), initial=True)
        metadata.remove(metadata.tables["ms_doomed"])

        drop = _mine(detect_changes(migrations_dir=str(mig_dir)), "ms_doomed")
        assert len(drop) == 1 and isinstance(drop[0], DeleteModel)
        assert drop[0].reversible
        # The declared type survives, not SQLite's reflected CHAR(32).
        assert isinstance(drop[0].columns[0].type, sa.Uuid)
        write_migration(mig_dir, operations=drop, name="drop")

        db = f"sqlite:///{tmp_path / 'rev.sqlite3'}"
        names = [n for n, _ in list_migration_files(mig_dir)]
        _migrate(mig_dir, db)
        assert "ms_doomed" not in _tables(db)

        # Rolling back the drop recreates the table (its schema; rows are gone).
        _migrate(mig_dir, db, target=names[0])
        assert "ms_doomed" in _tables(db)
        reloaded = load_migration(list_migration_files(mig_dir)[1][1])
        assert reloaded.operations[0].reversible

    def test_a_legacy_delete_model_refuses_to_roll_back(self, mig_dir, tmp_path):
        write_migration(
            mig_dir,
            operations=[
                CreateModel(
                    name="Old",
                    table="ms_old",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            initial=True,
        )
        write_migration(mig_dir, operations=[DeleteModel(name="Old", table="ms_old")], name="drop")
        db = f"sqlite:///{tmp_path / 'legacy.sqlite3'}"
        _migrate(mig_dir, db)

        assert DeleteModel(name="Old", table="ms_old").reversible is False
        with pytest.raises(IrreversibleError, match="Delete model Old"):
            _migrate(mig_dir, db, target="zero")
        # Refused before anything ran: both migrations are still applied.
        from zeeb_orm.migrations import executor

        status = executor.showmigrations(database_url=db, project_root=mig_dir.parent)
        assert all(applied for _name, applied in status)

        # --fake is the explicit way past it.
        assert _migrate(mig_dir, db, target="zero", fake=True)

    def test_irreversible_operations_raise_instead_of_doing_nothing(self):
        engine = sa.create_engine("sqlite://")
        with engine.connect() as conn:
            for op in (
                RunPython(lambda c: None),
                RunSQL("SELECT 1"),
                RemoveField(model_name="M", table="t", name="c"),
                RemoveIndex(model_name="M", table="t", name="ix"),
            ):
                assert op.reversible is False
                with pytest.raises(IrreversibleError):
                    op.backward(conn)
            # The explicit no-ops are reversible and run nothing.
            RunPython(lambda c: None, reverse_code=RunPython.noop).backward(conn)
            RunSQL("SELECT 1", reverse_sql=RunSQL.noop).backward(conn)

    def test_remove_index_with_its_columns_is_reversible(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY, a INTEGER)"))
            conn.execute(sa.text("CREATE UNIQUE INDEX ix_t_a ON t (a)"))
            op = RemoveIndex(model_name="T", table="t", name="ix_t_a", columns=["a"], unique=True)
            op.forward(conn)
            op.backward(conn)
            indexes = sa.inspect(conn).get_indexes("t")
        assert [(i["name"], i["column_names"], bool(i["unique"])) for i in indexes] == [
            ("ix_t_a", ["a"], True)
        ]


# ---------------------------------------------------------------------------
# 9. The tracking table is dialect-correct
# ---------------------------------------------------------------------------


class TestTrackingTableDDL:
    def _ddl(self, dialect) -> str:
        from sqlalchemy.schema import CreateTable

        return str(CreateTable(tracking_table()).compile(dialect=dialect)).upper()

    def test_mysql_gets_auto_increment_not_autoincrement(self):
        from sqlalchemy.dialects import mysql

        ddl = self._ddl(mysql.dialect())
        assert "AUTO_INCREMENT" in ddl
        assert " AUTOINCREMENT" not in ddl
        assert "UNIQUE (NAME)" in ddl

    def test_postgresql_gets_serial(self):
        from sqlalchemy.dialects import postgresql

        ddl = self._ddl(postgresql.dialect())
        assert "SERIAL" in ddl
        assert "AUTOINCREMENT" not in ddl

    def test_sqlite_gets_an_integer_primary_key(self):
        from sqlalchemy.dialects import sqlite

        assert "ID INTEGER NOT NULL" in self._ddl(sqlite.dialect())

    def test_an_existing_tracking_table_keeps_working(self, mig_dir, tmp_path):
        """Tables the old hand-written DDL created are read and written as before."""
        db = f"sqlite:///{tmp_path / 'old.sqlite3'}"
        engine = sa.create_engine(db)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE zeeb_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name VARCHAR(255) NOT NULL UNIQUE, applied_at TIMESTAMP NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO zeeb_migrations (name, applied_at) "
                    "VALUES ('0001_initial', '2026-01-01T00:00:00+00:00')"
                )
            )
        engine.dispose()
        write_migration(mig_dir, operations=[], initial=True)
        write_migration(mig_dir, operations=[], name="second")

        assert _migrate(mig_dir, db) == ["0002_second"]


# ---------------------------------------------------------------------------
# 10. Renames are reported (or kept), replay failures are fatal
# ---------------------------------------------------------------------------


def _initial_with(mig_dir, table, *columns):
    write_migration(
        mig_dir,
        operations=[
            CreateModel(name="Article", table=table, columns=list(columns), primary_key=["id"])
        ],
        initial=True,
    )


class TestRenames:
    def _setup(self, mig_dir):
        _initial_with(
            mig_dir,
            "ms_articles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("body", sa.Text(), nullable=False),
        )
        _table(
            "ms_articles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("content", sa.Text(), nullable=False),
        )

    def test_a_likely_rename_is_reported(self, mig_dir):
        self._setup(mig_dir)
        ops, report = detect_changes_with_report(migrations_dir=str(mig_dir))
        mine = _mine(ops, "ms_articles")
        assert {type(op) for op in mine} == {RemoveField, AddField}
        assert [r.as_dict() for r in report.possible_renames] == [
            {"model": "MsArticles", "table": "ms_articles", "old": "body", "new": "content"}
        ]
        assert "DROPS the data" in report.warnings()[0]

    def test_accepting_renames_writes_rename_field_and_keeps_the_data(self, mig_dir, tmp_path):
        self._setup(mig_dir)
        db = f"sqlite:///{tmp_path / 'rename.sqlite3'}"
        _migrate(mig_dir, db)
        engine = sa.create_engine(db)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO ms_articles (body) VALUES ('kept')"))

        ops, report = detect_changes_with_report(migrations_dir=str(mig_dir), accept_renames=True)
        mine = _mine(ops, "ms_articles")
        assert len(mine) == 1 and isinstance(mine[0], RenameField)
        assert (mine[0].old_name, mine[0].new_name) == ("body", "content")
        assert report.renamed and not report.possible_renames
        write_migration(mig_dir, operations=mine, name="rename")

        _migrate(mig_dir, db)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT content FROM ms_articles")).scalar() == "kept"
        engine.dispose()
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "ms_articles") == []

    def test_different_definitions_are_not_a_rename(self, mig_dir):
        _initial_with(
            mig_dir,
            "ms_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("body", sa.Text(), nullable=False),
        )
        _table(
            "ms_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rating", sa.Integer(), nullable=True),
        )
        _ops, report = detect_changes_with_report(migrations_dir=str(mig_dir))
        assert report.possible_renames == []


class TestReplay:
    def test_a_failing_schema_operation_is_fatal(self, mig_dir):
        write_migration(
            mig_dir,
            operations=[
                AddField(
                    model_name="Ghost",
                    table="ms_no_such_table",
                    name="x",
                    column=sa.Column("x", sa.Integer(), nullable=True),
                )
            ],
            initial=True,
        )
        with pytest.raises(MigrationReplayError, match="Add field x to Ghost"):
            detect_changes(migrations_dir=str(mig_dir))

    def test_run_python_is_skipped_and_said_so(self, mig_dir, tmp_path):
        marker = tmp_path / "ran"
        (mig_dir / "0001_initial.py").write_text(
            "from pathlib import Path\n"
            "from zeeb_orm.migrations import Migration, operations\n\n"
            "def touch(connection):\n"
            f"    Path({str(marker)!r}).write_text('x')\n\n"
            "class Migration(Migration):\n"
            "    initial = True\n"
            "    operations = [operations.RunPython(touch)]\n"
        )
        _ops, report = detect_changes_with_report(migrations_dir=str(mig_dir))
        assert not marker.exists()
        assert any("Run Python touch" in line for line in report.skipped)


class TestMakemigrationsCommand:
    def test_json_reports_possible_renames(self, tmp_path, monkeypatch, capsys):
        from zeeb_orm.cli.commands import migrate as migrate_cmd

        project = tmp_path / "proj"
        (project / "migrations").mkdir(parents=True)
        (project / "manage.py").write_text("")
        monkeypatch.chdir(project)
        monkeypatch.setattr(migrate_cmd, "_register_models", lambda root: None)
        monkeypatch.setattr(migrate_cmd, "get_installed_apps", lambda root: [])

        TestRenames()._setup(project / "migrations")
        code = migrate_cmd.run_makemigrations(None, False, dry_run=True, json_output=True)
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        renames = payload["data"]["possible_renames"]
        expected = {"model": "MsArticles", "table": "ms_articles", "old": "body", "new": "content"}
        assert expected in renames
        assert any("--accept-renames" in w for w in payload["data"]["warnings"])

    def test_the_accept_renames_flag_parses(self):
        from zeeb_orm.cli.main import build_parser

        args = build_parser().parse_args(["makemigrations", "--accept-renames"])
        assert args.accept_renames is True


# ---------------------------------------------------------------------------
# 16. SQLite table rebuilds never cascade
# ---------------------------------------------------------------------------


class TestSqliteRebuilds:
    def test_a_batch_rebuild_keeps_the_rows_that_reference_the_table(self, mig_dir, tmp_path):
        """AlterField rebuilds a SQLite table; referencing rows must survive.

        Dropping the old copy of a table with foreign-key enforcement on fires
        ON DELETE CASCADE on every row pointing at it. The migration engine
        turns enforcement off per connection — here even against a SQLite
        configured to enable it for every connection.
        """
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        from zeeb_orm.migrations.operations import AlterField

        write_migration(
            mig_dir,
            operations=[
                CreateModel(
                    name="Parent",
                    table="ms_parent",
                    columns=[
                        sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column("name", sa.String(10), nullable=True),
                    ],
                    primary_key=["id"],
                ),
                CreateModel(
                    name="Child",
                    table="ms_child",
                    columns=[
                        sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column(
                            "parent_id",
                            sa.Integer(),
                            sa.ForeignKey("ms_parent.id", ondelete="CASCADE"),
                            nullable=False,
                        ),
                    ],
                    primary_key=["id"],
                ),
            ],
            initial=True,
        )
        db = f"sqlite:///{tmp_path / 'rebuild.sqlite3'}"
        _migrate(mig_dir, db)
        engine = sa.create_engine(db)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO ms_parent (id, name) VALUES (1, 'p')"))
            conn.execute(sa.text("INSERT INTO ms_child (id, parent_id) VALUES (1, 1)"))
        engine.dispose()

        write_migration(
            mig_dir,
            operations=[
                AlterField(
                    model_name="Parent",
                    table="ms_parent",
                    name="name",
                    column_type=sa.String(200),
                    old_column_type=sa.String(10),
                )
            ],
            name="widen",
        )

        def _foreign_keys_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(Engine, "connect", _foreign_keys_on)
        try:
            _migrate(mig_dir, db)
        finally:
            event.remove(Engine, "connect", _foreign_keys_on)

        engine = sa.create_engine(db)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM ms_child")).scalar() == 1
        engine.dispose()
