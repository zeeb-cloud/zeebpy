"""Tests for window functions, Cast and StringAgg/GroupConcat.

Window functions require SQLite >= 3.25 (any modern Python build) or
MySQL >= 8.0; execution tests here run on SQLite, dialect-specific SQL is
asserted via compile strings.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql

from zeeb_orm import (
    Cast,
    CumeDist,
    DenseRank,
    FieldError,
    FirstValue,
    GroupConcat,
    Lag,
    LastValue,
    Lead,
    Model,
    Ntile,
    PercentRank,
    Rank,
    RowNumber,
    StringAgg,
    Window,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class WinPlayer(Model):
    name = fields.CharField(max_length=100)
    team = fields.CharField(max_length=50)
    score = fields.IntegerField(default=0)

    class Meta:
        table_name = "win_players"


MODELS = (WinPlayer,)


@pytest.fixture
async def db():
    """Set up test database (pattern from tests/test_related_lookups.py)."""
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()

    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")

    for model in MODELS:
        model._get_table()

    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def seeded(db):
    """Two teams with distinct scores."""
    data = [
        ("Ann", "red", 10),
        ("Ben", "red", 30),
        ("Col", "red", 20),
        ("Dan", "blue", 15),
        ("Eve", "blue", 5),
    ]
    for name, team, score in data:
        await WinPlayer.objects.create(name=name, team=team, score=score)
    return data


class TestWindowExecution:
    @pytest.mark.asyncio
    async def test_row_number_partition_order(self, seeded):
        players = await WinPlayer.objects.annotate(
            rn=Window(RowNumber(), partition_by="team", order_by="-score")
        )
        by_name = {p.name: p.rn for p in players}
        assert by_name == {"Ben": 1, "Col": 2, "Ann": 3, "Dan": 1, "Eve": 2}

    @pytest.mark.asyncio
    async def test_rank_and_dense_rank(self, seeded):
        # Add a tie so RANK and DENSE_RANK differ
        await WinPlayer.objects.create(name="Fay", team="blue", score=15)
        players = await WinPlayer.objects.annotate(
            rank=Window(Rank(), order_by="-score"),
            drank=Window(DenseRank(), order_by="-score"),
        )
        by_name = {p.name: (p.rank, p.drank) for p in players}
        assert by_name["Ben"] == (1, 1)
        assert by_name["Col"] == (2, 2)
        # Dan and Fay tie on 15
        assert by_name["Dan"] == by_name["Fay"]
        assert by_name["Dan"][0] == 3
        # After the two-way tie, RANK skips a value but DENSE_RANK doesn't
        assert by_name["Ann"] == (5, 4)

    @pytest.mark.asyncio
    async def test_lag_with_partition_and_order(self, seeded):
        players = await WinPlayer.objects.annotate(
            prev=Window(Lag("score"), partition_by="team", order_by="score")
        )
        by_name = {p.name: p.prev for p in players}
        assert by_name == {"Ann": None, "Col": 10, "Ben": 20, "Eve": None, "Dan": 5}

    @pytest.mark.asyncio
    async def test_lag_with_default(self, seeded):
        players = await WinPlayer.objects.annotate(
            prev=Window(Lag("score", 1, -1), partition_by="team", order_by="score")
        )
        by_name = {p.name: p.prev for p in players}
        assert by_name["Ann"] == -1
        assert by_name["Eve"] == -1
        assert by_name["Ben"] == 20

    @pytest.mark.asyncio
    async def test_lead_with_partition_and_order(self, seeded):
        players = await WinPlayer.objects.annotate(
            nxt=Window(Lead("score"), partition_by="team", order_by="score")
        )
        by_name = {p.name: p.nxt for p in players}
        assert by_name == {"Ann": 20, "Col": 30, "Ben": None, "Eve": 15, "Dan": None}

    @pytest.mark.asyncio
    async def test_first_value_and_ntile(self, seeded):
        players = await WinPlayer.objects.annotate(
            top=Window(FirstValue("name"), partition_by="team", order_by="-score"),
            bucket=Window(Ntile(2), order_by="-score"),
        )
        by_name = {p.name: p.top for p in players}
        assert by_name["Ann"] == "Ben"
        assert by_name["Eve"] == "Dan"
        buckets = {p.bucket for p in players}
        assert buckets == {1, 2}

    @pytest.mark.asyncio
    async def test_percent_rank_cume_dist_last_value_resolve(self, db):
        # Compile-level sanity for the remaining window functions
        for expr_cls in (PercentRank, CumeDist):
            sql = str(select(Window(expr_cls(), order_by="score").resolve(WinPlayer)))
            assert "OVER" in sql
        sql = str(
            select(Window(LastValue("name"), order_by="score").resolve(WinPlayer))
        )
        assert "last_value" in sql


class TestCast:
    @pytest.mark.asyncio
    async def test_cast_to_char_instance(self, seeded):
        players = await WinPlayer.objects.annotate(
            score_text=Cast("score", fields.CharField(max_length=20))
        ).filter(name="Ben")
        assert players[0].score_text == "30"
        assert isinstance(players[0].score_text, str)

    @pytest.mark.asyncio
    async def test_cast_to_field_class(self, seeded):
        players = await WinPlayer.objects.annotate(
            score_float=Cast("score", fields.FloatField)
        ).filter(name="Ann")
        assert players[0].score_float == pytest.approx(10.0)
        assert isinstance(players[0].score_float, float)


class TestStringAgg:
    @pytest.mark.asyncio
    async def test_string_agg_execution(self, seeded):
        result = await WinPlayer.objects.filter(team="red").aggregate(
            names=StringAgg("name", "|")
        )
        assert sorted(result["names"].split("|")) == ["Ann", "Ben", "Col"]

    @pytest.mark.asyncio
    async def test_group_concat_is_alias(self, db):
        assert GroupConcat is StringAgg

    @pytest.mark.asyncio
    async def test_string_agg_compile_postgresql(self, db):
        expr = StringAgg("name", "; ").resolve(WinPlayer)
        sql = str(select(expr).compile(dialect=postgresql.dialect()))
        assert "string_agg(" in sql
        assert "'; '" in sql

    @pytest.mark.asyncio
    async def test_string_agg_compile_mysql(self, db):
        expr = StringAgg("name", "; ").resolve(WinPlayer)
        sql = str(select(expr).compile(dialect=mysql.dialect()))
        assert "GROUP_CONCAT(" in sql
        assert "SEPARATOR '; '" in sql

    @pytest.mark.asyncio
    async def test_string_agg_distinct_compile(self, db):
        expr = StringAgg("team", ", ", distinct=True).resolve(WinPlayer)
        pg_sql = str(select(expr).compile(dialect=postgresql.dialect()))
        assert "string_agg(DISTINCT" in pg_sql
        my_sql = str(select(expr).compile(dialect=mysql.dialect()))
        assert "GROUP_CONCAT(DISTINCT" in my_sql

    @pytest.mark.asyncio
    async def test_string_agg_distinct_execution_sqlite(self, seeded):
        # SQLite uses group_concat(DISTINCT expr) with the default delimiter
        result = await WinPlayer.objects.aggregate(
            teams=StringAgg("team", distinct=True)
        )
        assert sorted(result["teams"].split(",")) == ["blue", "red"]

    @pytest.mark.asyncio
    async def test_delimiter_quote_escaping(self, db):
        expr = StringAgg("name", "'").resolve(WinPlayer)
        sql = str(select(expr).compile(dialect=postgresql.dialect()))
        assert "''''" in sql  # escaped single quote inside quotes


class TestWindowInFilter:
    @pytest.mark.asyncio
    async def test_window_annotation_in_filter_raises(self, db):
        qs = WinPlayer.objects.annotate(
            rn=Window(RowNumber(), partition_by="team", order_by="-score")
        ).filter(rn=1)
        with pytest.raises(FieldError):
            qs._build_select()

    @pytest.mark.asyncio
    async def test_window_annotation_in_exclude_raises(self, db):
        qs = WinPlayer.objects.annotate(
            rn=Window(RowNumber(), order_by="-score")
        ).exclude(rn__gt=1)
        with pytest.raises(FieldError):
            qs._build_select()

    @pytest.mark.asyncio
    async def test_non_window_annotation_in_filter_still_works(self, seeded):
        from zeeb_orm import F

        players = await WinPlayer.objects.annotate(double=F("score") * 2).filter(
            double__gt=40
        )
        assert {p.name for p in players} == {"Ben"}
