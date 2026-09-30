"""Values reach the database as bound parameters, never as SQL text.

Pinned surfaces:

* ``annotate(alias="raw string")`` was rendered verbatim via
  ``literal_column``; ``aggregate()`` silently dropped non-expressions.
* ``explain()`` inlined literal binds and sent the string through ``text()``,
  which re-parsed ``:name`` inside a value as a bind parameter.
* ``raw()`` replaced the first N ``?`` characters anywhere, including inside
  string literals.
* ``StringAgg`` inlined its delimiter with only quote doubling — defeated by
  backslash escapes on MySQL.
* ``Extract(lookup_name)`` rendered the name raw into ``EXTRACT(... FROM``.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import (
    Count,
    F,
    Model,
    Value,
    close_all_connections,
    configure,
    fields,
    setup_database,
)
from zeeb_orm.query import Extract
from zeeb_orm.query.expressions import StringAgg
from zeeb_orm.query.queryset import _prepare_raw_sql


class InjNote(Model):
    title = fields.CharField(max_length=80)
    body = fields.CharField(max_length=80, null=True)
    n = fields.IntegerField(default=0)
    created = fields.DateTimeField(null=True)

    class Meta:
        table_name = "inj_notes"


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    InjNote._sa_table = None
    InjNote._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    InjNote._get_table()
    await database.create_all()
    await InjNote.objects.create(title="a:b", body="x", n=1)
    await InjNote.objects.create(title="what?", body=":name", n=2)
    yield database
    await database.drop_all()
    await close_all_connections()
    table = metadata.tables.get("inj_notes")
    if table is not None:
        metadata.remove(table)
    InjNote._sa_table = None
    InjNote._sa_model = None
    Settings.reset()


class TestAnnotateAggregate:
    def test_annotate_rejects_plain_strings(self):
        with pytest.raises(TypeError, match="non-expression"):
            InjNote.objects.annotate(x="1; DROP TABLE inj_notes")

    def test_annotate_rejects_other_non_expressions(self):
        with pytest.raises(TypeError):
            InjNote.objects.annotate(x=5)

    async def test_aggregate_rejects_non_expressions(self, db):
        with pytest.raises(TypeError, match="non-expression"):
            await InjNote.objects.aggregate(total="count(*)")

    async def test_expressions_still_work(self, db):
        rows = await InjNote.objects.annotate(
            label=Value("1; DROP TABLE inj_notes"), double=F("n") * 2
        ).order_by("n")
        assert [(r.label, r.double) for r in rows] == [
            ("1; DROP TABLE inj_notes", 2),
            ("1; DROP TABLE inj_notes", 4),
        ]
        assert await InjNote.objects.aggregate(c=Count("id")) == {"c": 2}


class TestExplain:
    async def test_colon_names_inside_values_stay_values(self, db):
        plan = await InjNote.objects.filter(title=":oops or 1=1", body="it's :x").explain()
        assert "inj_notes" in plan

    async def test_in_lists_are_expanded(self, db):
        plan = await InjNote.objects.filter(title__in=["a:b", "what?"]).explain()
        assert plan


class TestRaw:
    async def test_question_mark_inside_a_literal_is_text(self, db):
        rows = await InjNote.objects.raw(
            "SELECT * FROM inj_notes WHERE title != 'huh?' AND n = ?", [2]
        )
        assert [r.title for r in rows] == ["what?"]

    async def test_colon_inside_a_literal_is_text(self, db):
        rows = await InjNote.objects.raw(
            "SELECT * FROM inj_notes WHERE body = ':name' AND n > ?", [0]
        )
        assert [r.title for r in rows] == ["what?"]
        rows = await InjNote.objects.raw("SELECT * FROM inj_notes WHERE title = 'a:b'")
        assert [r.n for r in rows] == [1]

    async def test_named_params(self, db):
        rows = await InjNote.objects.raw(
            "SELECT * FROM inj_notes WHERE title = :t AND body != ':t'", {"t": "a:b"}
        )
        assert [r.n for r in rows] == [1]

    async def test_values_are_never_sql(self, db):
        rows = await InjNote.objects.raw(
            "SELECT * FROM inj_notes WHERE title = ?", ["x' OR '1'='1"]
        )
        assert rows == []

    async def test_placeholder_count_must_match(self, db):
        with pytest.raises(ValueError, match="placeholder"):
            await InjNote.objects.raw("SELECT * FROM inj_notes WHERE n = ?", [1, 2])

    @pytest.mark.parametrize(
        "sql, params, dialect, expected",
        [
            (
                "SELECT 1 -- what?\nWHERE a = ?",
                [1],
                "postgresql",
                "SELECT 1 -- what?\nWHERE a = :_raw_0",
            ),
            ("SELECT /* ? */ ?", [1], "sqlite", "SELECT /* ? */ :_raw_0"),
            ("SELECT 'it''s ?', ?", [1], "sqlite", "SELECT 'it''s ?', :_raw_0"),
            ("SELECT 'it\\'s ?', ?", [1], "mysql", "SELECT 'it\\'s ?', :_raw_0"),
            ('SELECT "col?" FROM t WHERE a = ?', [1], "postgresql",
             'SELECT "col?" FROM t WHERE a = :_raw_0'),
            ("SELECT x::int FROM t WHERE a = ?", [1], "postgresql",
             "SELECT x::int FROM t WHERE a = :_raw_0"),
            ("SELECT ' :lit' WHERE a = :a", {"a": 1}, "postgresql",
             "SELECT ' \\:lit' WHERE a = :a"),
        ],
    )
    def test_placeholder_translation(self, sql, params, dialect, expected):
        assert _prepare_raw_sql(sql, params, dialect)[0] == expected


class TestStringAgg:
    def test_delimiter_is_bound_on_postgresql_and_sqlite(self):
        InjNote._get_table()
        evil = "', (SELECT 1)) --"
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            compiled = select(StringAgg("title", evil).resolve(InjNote)).compile(dialect=dialect)
            assert "SELECT 1" not in str(compiled)
            assert evil in compiled.params.values()

    def test_mysql_rejects_backslashes(self):
        InjNote._get_table()
        expr = StringAgg("title", "\\' , (SELECT 1)) -- ").resolve(InjNote)
        with pytest.raises(ValueError, match="backslash"):
            str(select(expr).compile(dialect=mysql.dialect()))

    def test_mysql_doubles_quotes_and_percents(self):
        InjNote._get_table()
        expr = StringAgg("title", "'%").resolve(InjNote)
        sql = str(select(expr).compile(dialect=mysql.dialect()))
        assert "SEPARATOR '''%%'" in sql

    async def test_sqlite_execution_with_a_quote_delimiter(self, db):
        result = await InjNote.objects.order_by("n").aggregate(t=StringAgg("title", "' |"))
        assert result["t"] == "a:b' |what?"


class TestExtract:
    def test_unknown_lookup_name_is_rejected(self):
        with pytest.raises(ValueError, match="unsupported lookup_name"):
            Extract("created", "year FROM created) OR 1=1 --")

    @pytest.mark.parametrize(
        "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
    )
    def test_known_names_compile_per_dialect(self, dialect):
        InjNote._get_table()
        for name in ("year", "quarter", "week_day", "iso_year"):
            sql = str(select(Extract("created", name).resolve(InjNote)).compile(dialect=dialect))
            assert "OR 1=1" not in sql
