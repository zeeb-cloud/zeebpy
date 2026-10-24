"""The migration engine end to end: files, plans, squashes, and their front ends.

Each class pins a defect that used to pass silently:

- a migration file lost type arguments (``DateTime(timezone=True)`` became
  ``sa.DateTime()``), wrote dialect types it could not import (``sa.JSONB()``),
  and dropped ``RunPython(reverse_code=...)`` / ``RunSQL(reverse_sql=noop)``
- ``AddField``'s backfill DEFAULT stayed in the database, so every later
  ``makemigrations`` produced a no-op ``AlterField`` — forever
- the old column type of an ``AlterField`` came from SQLite reflection
- an unknown ``migrate`` target applied everything; a rollback into a squash
  unapplied all of it; a squash numbered after later migrations broke a fresh
  database; a partly applied squash re-ran on top of its originals; change
  detection replayed a squash *and* its originals
- ``atomic = False`` still ran inside a transaction; nothing stopped two
  ``migrate`` runs from racing
- the optimizer reordered operations past ``RunSQL`` and foreign keys
- ``migrations_dir`` was ignored, the agent layer never heard about likely
  renames, and ``runserver`` missed a pending migration that sorts early
- a ``sa.func.now()`` server default crashed the writer; a copied function's
  default argument bound another file's constant; a nested squash as a target
  crashed; ``--plan`` disagreed with the real run; a squash could share a
  number; a rollback picked its target before taking the lock; MySQL DDL broke
  the savepoint around an atomic migration
"""

from __future__ import annotations

import io
import threading
import types
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from zeeb_orm.migrations import executor
from zeeb_orm.migrations.autodetector import detect_changes, detect_changes_with_report
from zeeb_orm.migrations.executor import (
    get_applied_migrations,
    list_migration_files,
    load_migration,
)
from zeeb_orm.migrations.operations import (
    NO_DEFAULT,
    AddField,
    AlterField,
    CreateModel,
    DeleteModel,
    RenameModel,
    RunPython,
    RunSQL,
)
from zeeb_orm.migrations.optimizer import optimize
from zeeb_orm.migrations.serializer import MigrationSerializationError
from zeeb_orm.migrations.state import MigrationError, UnknownMigrationError
from zeeb_orm.migrations.writer import write_migration
from zeeb_orm.models.base import metadata


@pytest.fixture()
def mig_dir(tmp_path):
    path = tmp_path / "migrations"
    path.mkdir()
    return path


@pytest.fixture()
def db(tmp_path):
    return f"sqlite:///{tmp_path / 'engine.sqlite3'}"


@pytest.fixture(autouse=True)
def _clean_metadata():
    before = set(metadata.tables)
    yield
    for name in set(metadata.tables) - before:
        metadata.remove(metadata.tables[name])


def _model(name: str, *columns) -> sa.Table:
    """Register a model table the autodetector compares against."""
    if name in metadata.tables:
        metadata.remove(metadata.tables[name])
    return sa.Table(name, metadata, *columns)


def _mine(ops, table):
    return [op for op in ops if getattr(op, "table", None) == table]


def _write(mig_dir, filename, body):
    (mig_dir / filename).write_text(body)


def _migrate(mig_dir, db, **kwargs):
    return executor.migrate(database_url=db, migrations_dir=mig_dir, **kwargs)


def _record(db, *names):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            get_applied_migrations(conn)  # creates the tracking table
            for name in names:
                executor.record_migration(conn, name)
            conn.commit()
    finally:
        engine.dispose()


def _recorded(db):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            return get_applied_migrations(conn)
    finally:
        engine.dispose()


def _columns(db, table):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            return {c["name"]: c for c in sa.inspect(conn).get_columns(table)}
    finally:
        engine.dispose()


def _create(table, *columns):
    return CreateModel(name="T", table=table, columns=list(columns), primary_key=["id"])


def _add(table, name, type_=None, **kw):
    kw.setdefault("nullable", True)
    return AddField(
        model_name="T", table=table, name=name, column=sa.Column(name, type_ or sa.Integer(), **kw)
    )


def _id():
    return sa.Column("id", sa.Integer(), primary_key=True)


def _chain(mig_dir, table, n):
    """0001 creates *table*; 0002..n each add a nullable column ``c<i>``."""
    write_migration(mig_dir, [_create(table, _id())], name="initial", initial=True)
    for i in range(2, n + 1):
        write_migration(mig_dir, [_add(table, f"c{i}")], name=f"add_c{i}")
    return [name for name, _ in list_migration_files(mig_dir)]


def _only_op(path):
    ops = list(load_migration(path).operations)
    assert len(ops) == 1
    return ops[0]


# ---------------------------------------------------------------------------
# Migration files keep every type argument, and import what they use
# ---------------------------------------------------------------------------


class TestLosslessFiles:
    def test_a_sql_function_server_default_is_written(self):
        from zeeb_orm.migrations.serializer import SourceWriter

        column = sa.Column("at", sa.DateTime(), server_default=sa.func.now())
        assert "server_default=sa.func.now()" in SourceWriter().column(column)
        with pytest.raises(MigrationSerializationError, match="sa.text"):
            SourceWriter().column(
                sa.Column("slug", sa.String(), server_default=sa.func.lower("X"))
            )

    def test_makemigrations_loads_each_existing_migration_once(self, mig_dir, monkeypatch):
        from zeeb_orm.migrations.autogen import make_migration

        names = _chain(mig_dir, "ll_once", 3)
        _model("ll_once", _id(), *(sa.Column(f"c{i}", sa.Integer()) for i in (2, 3, 4)))
        loads: list[str] = []
        real = executor.load_migration

        def counting(path):
            loads.append(Path(path).stem)
            return real(path)

        monkeypatch.setattr(executor, "load_migration", counting)
        draft = make_migration(mig_dir)
        assert draft.path is not None
        assert sorted(loads) == names
        assert load_migration(draft.path).dependencies == [names[-1]]

    def test_datetime_keeps_its_time_zone(self, mig_dir):
        path = write_migration(
            mig_dir, [_add("lf_events", "at", sa.DateTime(timezone=True))], name="at"
        )
        column = _only_op(path).column
        assert column.type.timezone is True
        assert str(column.type.compile(dialect=postgresql.dialect())) == (
            "TIMESTAMP WITH TIME ZONE"
        )

    def test_a_dialect_type_is_written_with_its_import(self, mig_dir):
        path = write_migration(
            mig_dir,
            [_create("lf_docs", _id(), sa.Column("body", postgresql.JSONB()))],
            initial=True,
        )
        text = path.read_text()
        assert "from sqlalchemy.dialects import postgresql" in text
        assert "postgresql.JSONB(" in text
        body = _only_op(path).columns[1]
        assert isinstance(body.type, postgresql.JSONB)

    def test_enum_values_and_nested_types_survive(self, mig_dir):
        path = write_migration(
            mig_dir,
            [
                _create(
                    "lf_posts",
                    _id(),
                    sa.Column("status", sa.Enum("draft", "published", name="post_status")),
                    sa.Column("tags", postgresql.ARRAY(sa.String(20))),
                    sa.Column("price", sa.Numeric(10, 2)),
                )
            ],
            initial=True,
        )
        status, tags, price = _only_op(path).columns[1:]
        assert status.type.enums == ["draft", "published"]
        assert status.type.name == "post_status"
        assert str(tags.type.compile(dialect=postgresql.dialect())) == "VARCHAR(20)[]"
        assert (price.type.precision, price.type.scale) == (10, 2)

    def test_a_type_that_cannot_be_written_is_refused_and_nothing_is_written(self, mig_dir):
        class LocalType(sa.types.TypeDecorator):
            impl = sa.String
            cache_ok = True

        with pytest.raises(MigrationSerializationError, match="LocalType"):
            write_migration(mig_dir, [_add("lf_bad", "x", LocalType(10))], name="bad")
        assert list(mig_dir.iterdir()) == []

    def test_run_sql_noop_reverse_stays_reversible(self, mig_dir):
        path = write_migration(
            mig_dir, [RunSQL("SELECT 1", reverse_sql=RunSQL.noop)], name="sql"
        )
        op = _only_op(path)
        assert op.reverse_sql == "" and op.reversible
        assert "reverse_sql=operations.RunSQL.noop" in path.read_text()

    def test_a_lambda_is_refused(self, mig_dir):
        with pytest.raises(MigrationSerializationError, match="lambda"):
            write_migration(mig_dir, [RunPython(lambda conn: None)], name="code")

    def test_dropping_a_server_default_is_spelled_explicitly(self, mig_dir):
        op = AlterField(
            model_name="T", table="t", name="n",
            server_default=NO_DEFAULT, old_server_default="0",
        )
        path = write_migration(mig_dir, [op], name="drop_default")
        assert _only_op(path).server_default is NO_DEFAULT
        assert "server_default=operations.NO_DEFAULT" in path.read_text()


# ---------------------------------------------------------------------------
# Squashes carry RunPython code over
# ---------------------------------------------------------------------------


_DATA_MIGRATION = '''
import sqlalchemy as sa
from zeeb_orm.migrations import Migration, operations

LABEL = {label!r}


def _insert(conn, value):
    conn.execute(sa.text("INSERT INTO sq_log (msg) VALUES (:m)"), {{"m": value}})


def forwards(conn):
    _insert(conn, LABEL)


def backwards(conn):
    conn.execute(sa.text("DELETE FROM sq_log WHERE msg = :m"), {{"m": LABEL}})


class Migration(Migration):
    dependencies = {deps!r}
    operations = [operations.RunPython(forwards, reverse_code=backwards)]
'''


class TestSquashCopiesCode:
    def _setup(self, mig_dir):
        write_migration(
            mig_dir,
            [_create("sq_log", _id(), sa.Column("msg", sa.String(50)))],
            initial=True,
        )
        _write(mig_dir, "0002_one.py", _DATA_MIGRATION.format(label="one", deps=["0001_initial"]))
        _write(mig_dir, "0003_two.py", _DATA_MIGRATION.format(label="two", deps=["0002_one"]))

    def _rows(self, db):
        engine = sa.create_engine(db)
        try:
            with engine.connect() as conn:
                return sorted(r[0] for r in conn.execute(sa.text("SELECT msg FROM sq_log")))
        finally:
            engine.dispose()

    def test_functions_and_their_helpers_are_copied_and_run(self, mig_dir, db, capsys):
        from zeeb_orm.migrations.cli import squashmigrations

        self._setup(mig_dir)
        name = squashmigrations("0001_initial", "0003_two", migrations_dir=str(mig_dir))
        assert name is not None, capsys.readouterr().out
        squash = mig_dir / f"{name}.py"
        for original in ("0001_initial.py", "0002_one.py", "0003_two.py"):
            (mig_dir / original).unlink()

        ops = list(load_migration(squash).operations)
        code_ops = [op for op in ops if isinstance(op, RunPython)]
        assert len(code_ops) == 2
        assert all(op.reverse_code is not None for op in code_ops)
        # The two files' same-named functions and constants did not collide.
        assert len({op.code.__name__ for op in code_ops}) == 2

        _migrate(mig_dir, db)
        assert self._rows(db) == ["one", "two"]
        _migrate(mig_dir, db, target="zero")
        assert "sq_log" not in sa.inspect(sa.create_engine(db)).get_table_names()


    def test_a_default_argument_keeps_its_own_files_constant(self, mig_dir, db, capsys):
        from zeeb_orm.migrations.cli import squashmigrations

        self._setup(mig_dir)
        # A function whose default argument reads LABEL — evaluated where it is
        # defined, so not in its code's names — and that is annotated.
        _write(mig_dir, "0003_two.py", _DATA_MIGRATION.format(
            label="two", deps=["0002_one"]
        ).replace(
            "def forwards(conn):\n    _insert(conn, LABEL)",
            "def forwards(conn: sa.engine.Connection, label=LABEL) -> None:\n"
            "    _insert(conn, label)",
        ))
        name = squashmigrations("0001_initial", "0003_two", migrations_dir=str(mig_dir))
        assert name is not None, capsys.readouterr().out
        for original in ("0001_initial.py", "0002_one.py", "0003_two.py"):
            (mig_dir / original).unlink()

        _migrate(mig_dir, db)
        assert self._rows(db) == ["one", "two"]


# ---------------------------------------------------------------------------
# The backfill default is not a schema change, and defaults can be dropped
# ---------------------------------------------------------------------------


class TestServerDefaults:
    def test_adding_a_not_null_field_with_a_default_converges(self, mig_dir, db):
        write_migration(mig_dir, [_create("sd_posts", _id())], initial=True)
        _model(
            "sd_posts",
            _id(),
            sa.Column("status", sa.String(20), nullable=False, default="draft"),
        )
        ops = _mine(detect_changes(migrations_dir=str(mig_dir)), "sd_posts")
        assert [type(op) for op in ops] == [AddField]
        write_migration(mig_dir, ops, name="status")

        engine = sa.create_engine(db)
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE sd_posts (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("INSERT INTO sd_posts (id) VALUES (1)"))
        engine.dispose()
        _migrate(mig_dir, db, fake_initial=True)

        # No phantom AlterField for the backfill DEFAULT, on this run or any later one.
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "sd_posts") == []

    def test_the_backfill_default_is_dropped_where_the_database_can(self, monkeypatch):
        from alembic.runtime.migration import MigrationContext

        buffer = io.StringIO()
        offline = MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
        )
        monkeypatch.setattr(MigrationContext, "configure", classmethod(lambda cls, conn: offline))

        op = _add("sd_pg", "status", sa.String(20), nullable=False, default="draft")
        op.forward(types.SimpleNamespace(dialect=postgresql.dialect()))
        sql = buffer.getvalue()
        assert "ADD COLUMN status VARCHAR(20) DEFAULT 'draft' NOT NULL" in sql
        assert "ALTER COLUMN status DROP DEFAULT" in sql

    def test_a_server_default_can_be_removed_and_restored(self, mig_dir, db):
        write_migration(
            mig_dir,
            [_create("sd_counts", _id(), sa.Column("n", sa.Integer(), server_default="0"))],
            initial=True,
        )
        _model("sd_counts", _id(), sa.Column("n", sa.Integer()))
        ops = _mine(detect_changes(migrations_dir=str(mig_dir)), "sd_counts")
        assert len(ops) == 1
        assert ops[0].server_default is NO_DEFAULT and ops[0].old_server_default == "0"
        write_migration(mig_dir, ops, name="drop_default")

        _migrate(mig_dir, db)
        assert _columns(db, "sd_counts")["n"]["default"] is None
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "sd_counts") == []

        _migrate(mig_dir, db, target="0001_initial")
        # Restored as the literal it was, not as a twice-quoted string.
        assert _columns(db, "sd_counts")["n"]["default"] == "'0'"


# ---------------------------------------------------------------------------
# Column changes are detected against what the migrations declare
# ---------------------------------------------------------------------------


class TestDeclaredState:
    def test_a_lost_time_zone_is_detected_and_repaired(self, mig_dir):
        # A file written before type arguments survived: sa.DateTime().
        _write(mig_dir, "0001_initial.py", (
            "import sqlalchemy as sa\n"
            "from zeeb_orm.migrations import Migration, operations\n\n"
            "class Migration(Migration):\n"
            "    initial = True\n"
            "    dependencies = []\n"
            "    operations = [operations.CreateModel(name='E', table='ds_events', columns=[\n"
            "        sa.Column('id', sa.Integer(), nullable=False),\n"
            "        sa.Column('at', sa.DateTime(), nullable=False),\n"
            "    ], primary_key=['id'])]\n"
        ))
        _model("ds_events", _id(), sa.Column("at", sa.DateTime(timezone=True), nullable=False))

        ops = _mine(detect_changes(migrations_dir=str(mig_dir)), "ds_events")
        assert len(ops) == 1 and isinstance(ops[0], AlterField)
        assert ops[0].column_type.timezone is True
        assert ops[0].old_column_type.timezone is False
        write_migration(mig_dir, ops, name="tz")
        assert _mine(detect_changes(migrations_dir=str(mig_dir)), "ds_events") == []

    def test_the_old_type_is_the_declared_one_not_the_reflected_one(self, mig_dir):
        write_migration(
            mig_dir, [_create("ds_tokens", _id(), sa.Column("key", sa.Uuid()))], initial=True
        )
        _model("ds_tokens", _id(), sa.Column("key", sa.String(64)))
        (op,) = _mine(detect_changes(migrations_dir=str(mig_dir)), "ds_tokens")
        # SQLite reflects a Uuid as CHAR(32); rolling back on PostgreSQL needs UUID.
        assert isinstance(op.old_column_type, sa.Uuid)
        assert op.reversible


# ---------------------------------------------------------------------------
# Targets: unknown ones are refused, prefixes resolve
# ---------------------------------------------------------------------------


class TestTargets:
    def test_an_unknown_target_applies_nothing(self, mig_dir, db):
        _chain(mig_dir, "tg_rows", 2)
        with pytest.raises(UnknownMigrationError) as raised:
            _migrate(mig_dir, db, target="0002_add_c2_typo")
        assert raised.value.available == ["0001_initial", "0002_add_c2"]
        assert _recorded(db) == set()

    def test_a_unique_prefix_resolves_and_an_ambiguous_one_is_refused(self, mig_dir, db):
        names = _chain(mig_dir, "tg_more", 3)
        assert _migrate(mig_dir, db, target="0002") == names[:2]
        with pytest.raises(UnknownMigrationError, match="ambiguous") as raised:
            _migrate(mig_dir, db, target="000")
        assert raised.value.candidates == names


# ---------------------------------------------------------------------------
# Squashes: order, partial application, rollback, change detection
# ---------------------------------------------------------------------------


def _squash(mig_dir, start, end):
    from zeeb_orm.migrations.cli import squashmigrations

    name = squashmigrations(start, end, migrations_dir=str(mig_dir))
    assert name is not None
    return name


class TestSquashes:
    def test_rolling_back_one_step_from_a_squash_unapplies_its_last_original(self, mig_dir, db):
        names = _chain(mig_dir, "sq_rows", 3)
        squash = _squash(mig_dir, names[0], names[2])
        assert _migrate(mig_dir, db) == [squash]

        assert executor.rollback(1, database_url=db, migrations_dir=mig_dir) == [names[2]]
        columns = _columns(db, "sq_rows")
        assert "c2" in columns and "c3" not in columns
        assert _recorded(db) == {names[0], names[1]}

        # The originals carry on from there, and complete the squash.
        assert _migrate(mig_dir, db) == [names[2], squash]
        assert _recorded(db) == {*names, squash}

    def test_without_its_originals_a_squash_is_one_step(self, mig_dir, db):
        names = _chain(mig_dir, "sq_whole", 3)
        squash = _squash(mig_dir, names[0], names[2])
        _migrate(mig_dir, db)
        for name in names:
            (mig_dir / f"{name}.py").unlink()
        assert executor.rollback_target(1, database_url=db, migrations_dir=mig_dir) == "zero"
        assert executor.rollback(1, database_url=db, migrations_dir=mig_dir) == [squash]

    def test_a_squash_never_runs_after_a_migration_that_depends_on_it(self, mig_dir, db):
        names = _chain(mig_dir, "sq_mid", 4)
        squash = _squash(mig_dir, names[0], names[2])
        # 0004 is taken, so it comes after the last one: numbers stay unique,
        # and the dependency graph still runs it before 0004.
        assert squash.startswith("0005_")
        numbers = [name[:4] for name, _ in list_migration_files(mig_dir)]
        assert len(numbers) == len(set(numbers))
        assert _migrate(mig_dir, db) == [squash, names[3]]
        # "0005" names the squash alone (it runs before 0004, so 0004 would go).
        planned = executor.migrate_with_result(
            database_url=db, migrations_dir=mig_dir, target="0005", plan=True
        )
        assert (planned.target, planned.unapplied) == (squash, [names[3]])
        assert {"c2", "c3", "c4"} <= set(_columns(db, "sq_mid"))

    def test_a_partly_applied_squash_finishes_with_its_originals(self, mig_dir, db):
        names = _chain(mig_dir, "sq_part", 3)
        _migrate(mig_dir, db, target=names[1])
        squash = _squash(mig_dir, names[0], names[2])

        # The squash is recorded with its last original, and reported as such.
        result = executor.migrate_with_result(database_url=db, migrations_dir=mig_dir)
        assert result.applied == [names[2], squash]
        assert _recorded(db) == {*names, squash}
        assert "c3" in _columns(db, "sq_part")

    def test_change_detection_replays_a_squash_instead_of_its_originals(self, mig_dir):
        names = _chain(mig_dir, "sq_detect", 3)
        _squash(mig_dir, names[0], names[2])
        _model("sq_detect", _id(), *(sa.Column(f"c{i}", sa.Integer()) for i in (2, 3)))
        # Replaying both used to fail with "duplicate column name".
        ops, _report = detect_changes_with_report(migrations_dir=str(mig_dir))
        assert _mine(ops, "sq_detect") == []

    def test_a_squash_inside_a_later_squash_is_a_target(self, mig_dir, db):
        names = _chain(mig_dir, "sq_nested", 4)
        inner = _squash(mig_dir, names[0], names[1])
        outer = _squash(mig_dir, names[0], inner)  # inner sorts after 0004
        assert inner in load_migration(mig_dir / f"{outer}.py").replaces
        _migrate(mig_dir, db)

        # The inner squash means the state after its last original.
        result = executor.migrate_with_result(
            database_url=db, migrations_dir=mig_dir, target=inner
        )
        assert result.unapplied == [names[3], names[2]]
        assert {"c2"} <= set(_columns(db, "sq_nested"))
        assert "c3" not in _columns(db, "sq_nested")

    def test_targeting_inside_a_squash_without_originals_is_refused(self, mig_dir, db):
        names = _chain(mig_dir, "sq_gone", 3)
        squash = _squash(mig_dir, names[0], names[2])
        _migrate(mig_dir, db)
        for name in names:
            (mig_dir / f"{name}.py").unlink()
        (mig_dir / f"{names[1]}.py").write_text("")  # a name, but no migration
        (mig_dir / f"{names[1]}.py").unlink()
        with pytest.raises(UnknownMigrationError):
            _migrate(mig_dir, db, target=names[1])
        assert _recorded(db) >= {squash}


# ---------------------------------------------------------------------------
# Ordering and history
# ---------------------------------------------------------------------------


_EMPTY = '''
from zeeb_orm.migrations import Migration, operations

class Migration(Migration):
    dependencies = {deps!r}
    operations = []
'''


class TestOrdering:
    def test_migrations_apply_in_dependency_order(self, mig_dir, db):
        _write(mig_dir, "0001_first.py", _EMPTY.format(deps=["0002_second"]))
        _write(mig_dir, "0002_second.py", _EMPTY.format(deps=[]))
        assert _migrate(mig_dir, db) == ["0002_second", "0001_first"]

    def test_a_dependency_cycle_is_refused(self, mig_dir, db):
        _write(mig_dir, "0001_a.py", _EMPTY.format(deps=["0002_b"]))
        _write(mig_dir, "0002_b.py", _EMPTY.format(deps=["0001_a"]))
        with pytest.raises(MigrationError, match="cycle"):
            _migrate(mig_dir, db)

    def test_an_inconsistent_history_is_refused(self, mig_dir, db):
        _write(mig_dir, "0001_a.py", _EMPTY.format(deps=[]))
        _write(mig_dir, "0002_b.py", _EMPTY.format(deps=["0001_a"]))
        _write(mig_dir, "0003_c.py", _EMPTY.format(deps=["0002_b"]))
        _record(db, "0002_b")
        with pytest.raises(MigrationError, match="Inconsistent migration history"):
            _migrate(mig_dir, db)
        # --fake is the repair, and is not refused.
        assert _migrate(mig_dir, db, fake=True) == ["0001_a", "0003_c"]

    def test_the_repair_it_suggests_records_only_the_missing_dependency(self, mig_dir, db):
        _write(mig_dir, "0001_a.py", _EMPTY.format(deps=[]))
        _write(mig_dir, "0002_b.py", _EMPTY.format(deps=["0001_a"]))
        _write(mig_dir, "0003_c.py", _EMPTY.format(deps=["0002_b"]))
        _record(db, "0002_b")
        with pytest.raises(MigrationError, match="`migrate 0002_b --fake`") as raised:
            _migrate(mig_dir, db)
        assert "records only 0001_a)" in str(raised.value)

        # Previewed, then run: 0003_c stays pending, to run for real.
        assert _migrate(mig_dir, db, target="0002_b", fake=True, plan=True) == ["0001_a"]
        assert _migrate(mig_dir, db, target="0002_b", fake=True) == ["0001_a"]
        assert _recorded(db) == {"0001_a", "0002_b"}
        assert _migrate(mig_dir, db) == ["0003_c"]


class TestPlans:
    """``--plan`` reports what the real run does, and refuses what it refuses."""

    def test_a_fake_plan_is_not_refused_for_an_inconsistent_history(self, mig_dir, db):
        _write(mig_dir, "0001_a.py", _EMPTY.format(deps=[]))
        _write(mig_dir, "0002_b.py", _EMPTY.format(deps=["0001_a"]))
        _record(db, "0002_b")
        assert _migrate(mig_dir, db, fake=True, plan=True) == ["0001_a"]
        assert _recorded(db) == {"0002_b"}

    def test_a_plan_through_an_irreversible_operation_is_refused(self, mig_dir, db):
        from zeeb_orm.migrations.state import IrreversibleError

        write_migration(mig_dir, [_create("pl_rows", _id())], initial=True)
        write_migration(mig_dir, [RunSQL("INSERT INTO pl_rows (id) VALUES (1)")], name="seed")
        _migrate(mig_dir, db)
        with pytest.raises(IrreversibleError):
            _migrate(mig_dir, db, target="0001", plan=True)
        # Faked, nothing runs backward, so the plan (and the run) are allowed.
        assert _migrate(mig_dir, db, target="0001", plan=True, fake=True) == ["0002_seed"]

    def test_a_plan_reports_the_squash_its_last_original_completes(self, mig_dir, db):
        names = _chain(mig_dir, "pl_part", 3)
        _migrate(mig_dir, db, target=names[1])
        squash = _squash(mig_dir, names[0], names[2])
        planned = executor.migrate_with_result(
            database_url=db, migrations_dir=mig_dir, plan=True
        )
        assert planned.applied == [names[2], squash]
        assert _recorded(db) == {names[0], names[1]}


class TestRollbackUnderTheLock:
    def test_a_migration_applied_meanwhile_is_the_one_rolled_back(
        self, mig_dir, db, monkeypatch
    ):
        from contextlib import contextmanager

        for name, deps in (("0001_a", []), ("0002_b", ["0001_a"]), ("0003_c", ["0002_b"])):
            _write(mig_dir, f"{name}.py", _EMPTY.format(deps=deps))
        _migrate(mig_dir, db, target="0002_b")

        lock = executor._migration_lock

        @contextmanager
        def another_replica_got_there_first(conn, url):
            # Another process applies 0003 while this rollback waits for the lock.
            _record(db, "0003_c")
            with lock(conn, url):
                yield

        monkeypatch.setattr(executor, "_migration_lock", another_replica_got_there_first)
        result = executor.rollback_with_result(1, database_url=db, migrations_dir=mig_dir)
        assert result.unapplied == ["0003_c"]
        assert result.target == "0002_b"
        assert _recorded(db) == {"0001_a", "0002_b"}

    def test_nothing_applied_is_an_empty_result(self, mig_dir, db):
        _write(mig_dir, "0001_a.py", _EMPTY.format(deps=[]))
        result = executor.rollback_with_result(1, database_url=db, migrations_dir=mig_dir)
        assert result == executor.MigrateResult(applied=[], unapplied=[], target=None)


# ---------------------------------------------------------------------------
# atomic = False, and one migrate at a time
# ---------------------------------------------------------------------------


_PROBE = '''
import sqlalchemy as sa
from zeeb_orm.migrations import Migration, operations


def probe(conn):
    conn.execute(sa.text("CREATE TABLE IF NOT EXISTS at_probe (x INTEGER)"))
    conn.execute(sa.text("INSERT INTO at_probe (x) VALUES (1)"))
    if conn.connection.dbapi_connection.in_transaction:
        raise RuntimeError("inside a transaction")


class Migration(Migration):
    dependencies = []
    atomic = {atomic}
    operations = [operations.RunPython(probe, reverse_code=operations.RunPython.noop)]
'''


class TestTransactions:
    def test_a_non_atomic_migration_runs_outside_a_transaction(self, mig_dir, db):
        _write(mig_dir, "0001_probe.py", _PROBE.format(atomic=False))
        assert _migrate(mig_dir, db) == ["0001_probe"]

    def test_the_probe_does_see_an_atomic_migrations_transaction(self, mig_dir, db):
        _write(mig_dir, "0001_probe.py", _PROBE.format(atomic=True))
        with pytest.raises(RuntimeError, match="inside a transaction"):
            _migrate(mig_dir, db)

    def test_concurrent_migrate_runs_apply_each_migration_once(self, mig_dir, db):
        _write(mig_dir, "0001_slow.py", (
            "import time\n"
            "import sqlalchemy as sa\n"
            "from zeeb_orm.migrations import Migration, operations\n\n"
            "def slow(conn):\n"
            "    time.sleep(0.5)\n"
            "    conn.execute(sa.text('CREATE TABLE lk_once (x INTEGER)'))\n\n"
            "class Migration(Migration):\n"
            "    dependencies = []\n"
            "    operations = [operations.RunPython(slow)]\n"
        ))
        results: list = []

        def run():
            try:
                results.append(_migrate(mig_dir, db))
            except Exception as exc:  # pragma: no cover — the failure being guarded
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(map(str, results)) == ["['0001_slow']", "[]"]


# ---------------------------------------------------------------------------
# The optimizer never reorders past what it cannot see through
# ---------------------------------------------------------------------------


class TestOptimizer:
    def test_create_and_delete_do_not_cancel_across_run_sql(self):
        ops = [
            _create("op_a", _id()),
            RunSQL("INSERT INTO archive SELECT * FROM op_a"),
            DeleteModel(name="A", table="op_a"),
        ]
        assert optimize(ops) == ops

    def test_create_and_delete_do_not_cancel_across_a_foreign_key(self):
        child = _create(
            "op_child", _id(), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("op_parent.id"))
        )
        ops = [_create("op_parent", _id()), child, DeleteModel(name="P", table="op_parent")]
        assert optimize(ops) == ops

    def test_a_foreign_key_column_is_not_fused_ahead_of_its_target(self):
        fk = sa.Column("b_id", sa.Integer(), sa.ForeignKey("op_b.id"))
        ops = [_create("op_a", _id()), _create("op_b", _id()), _add("op_a", "b_id", fk.type)]
        ops[2].column = fk
        assert len(optimize(ops)) == 3

    def test_rename_model_is_a_barrier(self):
        ops = [
            _create("op_old", _id()),
            RenameModel(old_name="O", new_name="N", old_table="op_old", new_table="op_new"),
            DeleteModel(name="O", table="op_old"),
        ]
        assert optimize(ops) == ops

    def test_unrelated_operations_still_reduce(self):
        ops = [_create("op_x", _id()), _create("op_y", _id()), _add("op_x", "n")]
        reduced = optimize(ops)
        assert len(reduced) == 2
        assert [c.name for c in reduced[0].columns] == ["id", "n"]


# ---------------------------------------------------------------------------
# Front ends: migrations_dir, the agent layer, the runserver gate
# ---------------------------------------------------------------------------


class TestFrontEnds:
    def test_the_programmatic_api_honours_migrations_dir(self, tmp_path, monkeypatch, capsys):
        from zeeb_orm.migrations import cli

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        names = _chain(elsewhere, "fe_rows", 2)
        project = tmp_path / "project"
        (project / "migrations").mkdir(parents=True)
        (project / "manage.py").write_text("")
        monkeypatch.chdir(project)
        db = f"sqlite:///{tmp_path / 'fe.sqlite3'}"
        monkeypatch.setattr(cli, "_get_database_url", lambda root=None: db)

        cli.migrate(migrations_dir=str(elsewhere))
        assert _recorded(db) == set(names)
        cli.rollback(1, migrations_dir=str(elsewhere))
        assert _recorded(db) == {names[0]}
        assert cli.current(migrations_dir=str(elsewhere)) == names[0]

    def test_runserver_sees_a_pending_migration_that_sorts_early(self, tmp_path, monkeypatch):
        from zeeb_orm.cli.commands.runserver import check_migrations_before_start

        project = tmp_path / "project"
        mig = project / "migrations"
        mig.mkdir(parents=True)
        (project / "manage.py").write_text("")
        for name in ("0001_a", "0002_b", "0003_c"):
            _write(mig, f"{name}.py", _EMPTY.format(deps=[]))
        db = f"sqlite:///{tmp_path / 'rs.sqlite3'}"
        monkeypatch.setenv("DATABASE_URL", db)
        _record(db, "0001_a", "0003_c")  # 0002 merged in later
        assert check_migrations_before_start(project) is False

    def test_the_legacy_layout_is_refused_not_ignored(self, tmp_path, monkeypatch, capsys):
        from zeeb_orm.cli.commands.check import check_migrations
        from zeeb_orm.cli.commands.runserver import check_migrations_before_start
        from zeeb_orm.migrations.state import MigrationLayoutError

        project = tmp_path / "project"
        legacy = project / "migrations" / "versions"
        legacy.mkdir(parents=True)
        (project / "manage.py").write_text("")
        write_migration(legacy, [_create("lg_rows", _id())], initial=True)
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lg.sqlite3'}")

        assert check_migrations_before_start(project) is False
        assert "migrations/versions/" in capsys.readouterr().out
        issues, _ = check_migrations(project)
        assert issues and "versions" in issues[0]["message"]
        with pytest.raises(MigrationLayoutError):
            executor.migrate(project_root=project)

    async def test_the_agent_layer_reports_likely_renames(self, tmp_path, monkeypatch):
        import zeeb_agents as agents
        from zeeb_orm.migrations import cli

        res = await agents.create_project("demo", directory=str(tmp_path))
        assert res.success, res.message
        root = tmp_path / "demo"
        mig = root / "migrations"
        monkeypatch.setattr(cli, "_register_models", lambda root=None: None)
        write_migration(
            mig,
            [_create("ag_articles", _id(), sa.Column("body", sa.Text()))],
            initial=True,
        )
        _model("ag_articles", _id(), sa.Column("content", sa.Text()))

        res = await agents.make_migrations(project_id=root)
        assert res.success, res.message
        assert {"model": "T", "table": "ag_articles", "old": "body", "new": "content"} in (
            res.data["possible_renames"]
        )
        assert "accept_renames=True" in res.message
        Path(mig / res.data["created"]).unlink()

        res = await agents.make_migrations(project_id=root, accept_renames=True)
        assert res.success, res.message
        assert "Rename field body to content on T" in res.data["operations"]
        assert res.data["possible_renames"] == []


# ---------------------------------------------------------------------------
# On PostgreSQL — set ZEEB_TEST_POSTGRES_URL (e.g.
# postgresql+psycopg2://postgres:pw@127.0.0.1:5432/zeeb) to run these; the database is
# emptied before each test.
# ---------------------------------------------------------------------------


@pytest.fixture()
def pg():
    import os

    url = os.environ.get("ZEEB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("ZEEB_TEST_POSTGRES_URL is not set")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()
    return url


class TestOnPostgres:
    def test_a_time_zone_reaches_the_database(self, mig_dir, pg):
        write_migration(
            mig_dir,
            [_create("pg_events", _id(), sa.Column("at", sa.DateTime(timezone=True)))],
            initial=True,
        )
        _migrate(mig_dir, pg)
        at = _columns(pg, "pg_events")["at"]["type"]
        assert at.timezone is True

    def test_the_backfill_default_fills_rows_and_is_dropped(self, mig_dir, pg):
        write_migration(mig_dir, [_create("pg_posts", _id())], initial=True)
        _migrate(mig_dir, pg)
        engine = sa.create_engine(pg)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO pg_posts (id) VALUES (1)"))
        write_migration(
            mig_dir,
            [_add("pg_posts", "status", sa.String(20), nullable=False, default="draft")],
            name="status",
        )
        _migrate(mig_dir, pg)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT status FROM pg_posts")).scalar() == "draft"
        engine.dispose()
        assert _columns(pg, "pg_posts")["status"]["default"] is None

    def test_create_index_concurrently_runs_in_a_non_atomic_migration(self, mig_dir, pg):
        write_migration(mig_dir, [_create("pg_slugs", _id(), sa.Column("slug", sa.Text()))],
                        initial=True)
        _write(mig_dir, "0002_index.py", (
            "from zeeb_orm.migrations import Migration, operations\n\n"
            "class Migration(Migration):\n"
            "    dependencies = ['0001_initial']\n"
            "    atomic = False\n"
            "    operations = [operations.RunSQL(\n"
            "        'CREATE INDEX CONCURRENTLY pg_slugs_slug ON pg_slugs (slug)',\n"
            "        reverse_sql='DROP INDEX CONCURRENTLY pg_slugs_slug',\n"
            "    )]\n"
        ))
        assert _migrate(mig_dir, pg) == ["0001_initial", "0002_index"]
        assert _migrate(mig_dir, pg, target="0001_initial") == ["0002_index"]

    def test_concurrent_migrate_runs_apply_each_migration_once(self, mig_dir, pg):
        _write(mig_dir, "0001_slow.py", (
            "import time\n"
            "import sqlalchemy as sa\n"
            "from zeeb_orm.migrations import Migration, operations\n\n"
            "def slow(conn):\n"
            "    time.sleep(0.5)\n"
            "    conn.execute(sa.text('CREATE TABLE pg_once (x INTEGER)'))\n\n"
            "class Migration(Migration):\n"
            "    dependencies = []\n"
            "    operations = [operations.RunPython(slow)]\n"
        ))
        results: list = []

        def run():
            try:
                results.append(_migrate(mig_dir, pg))
            except Exception as exc:  # pragma: no cover — the failure being guarded
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(map(str, results)) == ["['0001_slow']", "[]"]

    def test_an_altered_server_default_rolls_back_as_it_was(self, mig_dir, pg):
        write_migration(
            mig_dir,
            [_create("pg_counts", _id(), sa.Column("n", sa.Integer(), server_default="0"))],
            initial=True,
        )
        _model("pg_counts", _id(), sa.Column("n", sa.Integer(), server_default="5"))
        ops = _mine(detect_changes(migrations_dir=str(mig_dir)), "pg_counts")
        write_migration(mig_dir, ops, name="five")
        _migrate(mig_dir, pg)
        assert "5" in _columns(pg, "pg_counts")["n"]["default"]
        _migrate(mig_dir, pg, target="0001_initial")
        assert "0" in _columns(pg, "pg_counts")["n"]["default"]


# ---------------------------------------------------------------------------
# On MySQL — set ZEEB_TEST_MYSQL_URL (e.g.
# mysql+pymysql://root:pw@127.0.0.1:3306/zeeb) to run these; the database is
# emptied before each test.
# ---------------------------------------------------------------------------


@pytest.fixture()
def my():
    import os

    url = os.environ.get("ZEEB_TEST_MYSQL_URL")
    if not url:
        pytest.skip("ZEEB_TEST_MYSQL_URL is not set")
    engine = sa.create_engine(url)
    database = engine.url.database
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS `{database}`"))
        conn.execute(sa.text(f"CREATE DATABASE `{database}`"))
    engine.dispose()
    return url


class TestOnMySQL:
    def test_an_atomic_migration_with_ddl_is_applied_and_recorded(self, mig_dir, my):
        # DDL commits implicitly on MySQL, which ended the savepoint the
        # migration ran in: the migration ran, then failed to be recorded.
        write_migration(
            mig_dir,
            [_create("my_posts", _id(), sa.Column("title", sa.String(100)))],
            initial=True,
        )
        write_migration(
            mig_dir,
            [
                _add("my_posts", "views", sa.Integer()),
                RunSQL(
                    "INSERT INTO my_posts (id, title) VALUES (1, 'a')",
                    reverse_sql="DELETE FROM my_posts WHERE id = 1",
                ),
            ],
            name="views",
        )
        assert _migrate(mig_dir, my) == ["0001_initial", "0002_views"]
        assert _recorded(my) == {"0001_initial", "0002_views"}
        assert "views" in _columns(my, "my_posts")

        assert executor.rollback(1, database_url=my, migrations_dir=mig_dir) == ["0002_views"]
        assert "views" not in _columns(my, "my_posts")
        assert _migrate(mig_dir, my) == ["0002_views"]

    def test_concurrent_migrate_runs_apply_each_migration_once(self, mig_dir, my):
        write_migration(mig_dir, [_create("my_once", _id())], initial=True)
        results: list = []

        def run():
            try:
                results.append(_migrate(mig_dir, my))
            except Exception as exc:  # pragma: no cover — the failure being guarded
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(map(str, results)) == ["['0001_initial']", "[]"]
