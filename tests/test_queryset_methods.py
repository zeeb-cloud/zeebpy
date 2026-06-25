"""Tests for QuerySet methods: in_bulk, iterator, combinators,
select_for_update and explain."""

import pytest
from sqlalchemy.dialects import postgresql

from zeeb_orm import (
    Model,
    NotSupportedError,
    TransactionManagementError,
    atomic,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class QmItem(Model):
    name = fields.CharField(max_length=100)
    sku = fields.CharField(max_length=50, unique=True)
    qty = fields.IntegerField(default=0)

    class Meta:
        table_name = "qm_items"


MODELS = (QmItem,)


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
    items = []
    for i in range(1, 6):
        items.append(
            await QmItem.objects.create(name=f"item{i}", sku=f"SKU-{i}", qty=i)
        )
    return items


# in_bulk


class TestInBulk:
    @pytest.mark.asyncio
    async def test_in_bulk_all(self, seeded):
        result = await QmItem.objects.in_bulk()
        assert len(result) == 5
        for pk, obj in result.items():
            assert obj.pk == pk

    @pytest.mark.asyncio
    async def test_in_bulk_subset(self, seeded):
        wanted = [seeded[0].pk, seeded[2].pk]
        result = await QmItem.objects.in_bulk(wanted)
        assert set(result.keys()) == set(wanted)
        assert result[seeded[0].pk].name == "item1"

    @pytest.mark.asyncio
    async def test_in_bulk_empty_list(self, seeded):
        assert await QmItem.objects.in_bulk([]) == {}

    @pytest.mark.asyncio
    async def test_in_bulk_unique_field(self, seeded):
        result = await QmItem.objects.in_bulk(["SKU-1", "SKU-4"], field_name="sku")
        assert set(result.keys()) == {"SKU-1", "SKU-4"}
        assert result["SKU-4"].qty == 4

    @pytest.mark.asyncio
    async def test_in_bulk_non_unique_field_raises(self, seeded):
        with pytest.raises(ValueError, match="unique"):
            await QmItem.objects.in_bulk(["item1"], field_name="name")

    @pytest.mark.asyncio
    async def test_in_bulk_unknown_field_raises(self, seeded):
        with pytest.raises(ValueError, match="no field"):
            await QmItem.objects.in_bulk([1], field_name="bogus")

    @pytest.mark.asyncio
    async def test_in_bulk_respects_filters(self, seeded):
        result = await QmItem.objects.filter(qty__gte=4).in_bulk()
        assert len(result) == 2


# iterator


class TestIterator:
    @pytest.mark.asyncio
    async def test_iterator_yields_all_in_order(self, seeded):
        names = []
        async for item in QmItem.objects.order_by("qty").iterator(chunk_size=2):
            assert isinstance(item, QmItem)
            names.append(item.name)
        assert names == ["item1", "item2", "item3", "item4", "item5"]

    @pytest.mark.asyncio
    async def test_iterator_larger_set_chunking(self, db):
        objs = [QmItem(name=f"bulk{i}", sku=f"B-{i}", qty=i) for i in range(250)]
        await QmItem.objects.bulk_create(objs)

        seen = set()
        count = 0
        async for item in QmItem.objects.iterator(chunk_size=100):
            count += 1
            seen.add(item.sku)
        assert count == 250
        assert len(seen) == 250

    @pytest.mark.asyncio
    async def test_iterator_with_filter(self, seeded):
        qtys = [i.qty async for i in QmItem.objects.filter(qty__gt=3).iterator()]
        assert sorted(qtys) == [4, 5]

    @pytest.mark.asyncio
    async def test_iterator_with_prefetch_related_raises(self, seeded):
        with pytest.raises(NotSupportedError):
            QmItem.objects.prefetch_related("anything").iterator()

    @pytest.mark.asyncio
    async def test_iterator_invalid_chunk_size(self, seeded):
        with pytest.raises(ValueError):
            QmItem.objects.iterator(chunk_size=0)


# Combinators


class TestCombinators:
    @pytest.mark.asyncio
    async def test_union_deduplicates(self, seeded):
        # qty < 3 -> {1, 2}; qty in (2, 4) -> {2, 4}; union -> {1, 2, 4}
        qs = QmItem.objects.filter(qty__lt=3).union(
            QmItem.objects.filter(qty__in=[2, 4])
        )
        results = await qs
        assert sorted(i.qty for i in results) == [1, 2, 4]
        for obj in results:
            assert isinstance(obj, QmItem)

    @pytest.mark.asyncio
    async def test_union_all_keeps_duplicates(self, seeded):
        qs = QmItem.objects.filter(qty__lt=3).union(
            QmItem.objects.filter(qty__in=[2, 4]), all=True
        )
        results = await qs
        assert sorted(i.qty for i in results) == [1, 2, 2, 4]

    @pytest.mark.asyncio
    async def test_intersection(self, seeded):
        qs = QmItem.objects.filter(qty__lt=4).intersection(
            QmItem.objects.filter(qty__gt=2)
        )
        results = await qs
        assert [i.qty for i in results] == [3]

    @pytest.mark.asyncio
    async def test_difference(self, seeded):
        qs = QmItem.objects.filter(qty__lt=4).difference(
            QmItem.objects.filter(qty=2)
        )
        results = await qs
        assert sorted(i.qty for i in results) == [1, 3]

    @pytest.mark.asyncio
    async def test_union_multiple_querysets(self, seeded):
        qs = QmItem.objects.filter(qty=1).union(
            QmItem.objects.filter(qty=3),
            QmItem.objects.filter(qty=5),
        )
        results = await qs
        assert sorted(i.qty for i in results) == [1, 3, 5]

    @pytest.mark.asyncio
    async def test_order_by_after_union(self, seeded):
        qs = (
            QmItem.objects.filter(qty__lt=3)
            .union(QmItem.objects.filter(qty__gt=3))
            .order_by("-qty")
        )
        results = await qs
        assert [i.qty for i in results] == [5, 4, 2, 1]

    @pytest.mark.asyncio
    async def test_order_by_after_union_db_column(self, seeded):
        # Ordering by a real field still resolves to its column.
        qs = (
            QmItem.objects.filter(qty=1)
            .union(QmItem.objects.filter(qty=5))
            .order_by("name")
        )
        assert [i.qty for i in await qs] == [1, 5]

    @pytest.mark.asyncio
    async def test_order_by_after_union_rejects_unknown_field(self, seeded):
        # An unknown order-by name must not be rendered verbatim into SQL via
        # literal_column (SQL-injection sink); it is rejected like the
        # non-combined path.
        from zeeb_orm.exceptions import FieldError

        qs = QmItem.objects.filter(qty=1).union(
            QmItem.objects.filter(qty=5)
        ).order_by("1)) UNION SELECT sku FROM qm_items --")
        with pytest.raises(FieldError):
            await qs

    @pytest.mark.asyncio
    async def test_slicing_after_union(self, seeded):
        qs = (
            QmItem.objects.filter(qty__lt=3)
            .union(QmItem.objects.filter(qty__gt=3))
            .order_by("qty")[1:3]
        )
        results = await qs
        assert [i.qty for i in results] == [2, 4]

    @pytest.mark.asyncio
    async def test_values_after_union(self, seeded):
        rows = await (
            QmItem.objects.filter(qty=1)
            .union(QmItem.objects.filter(qty=5))
            .order_by("qty")
            .values("name", "qty")
        )
        assert rows == [
            {"name": "item1", "qty": 1},
            {"name": "item5", "qty": 5},
        ]

    @pytest.mark.asyncio
    async def test_values_list_after_union(self, seeded):
        rows = await (
            QmItem.objects.filter(qty=1)
            .union(QmItem.objects.filter(qty=5))
            .order_by("-qty")
            .values_list("name", flat=True)
        )
        assert rows == ["item5", "item1"]

    @pytest.mark.asyncio
    async def test_count_after_union(self, seeded):
        qs = QmItem.objects.filter(qty__lt=3).union(
            QmItem.objects.filter(qty__gt=4)
        )
        assert await qs.count() == 3

    @pytest.mark.asyncio
    async def test_post_combination_restrictions(self, seeded):
        qs = QmItem.objects.filter(qty=1).union(QmItem.objects.filter(qty=2))
        with pytest.raises(NotSupportedError):
            qs.filter(name="x")
        with pytest.raises(NotSupportedError):
            qs.exclude(name="x")
        with pytest.raises(NotSupportedError):
            qs.distinct()
        with pytest.raises(NotSupportedError):
            qs.annotate(x=None)
        with pytest.raises(NotSupportedError):
            qs.select_related("x")
        with pytest.raises(NotSupportedError):
            qs.prefetch_related("x")
        with pytest.raises(NotSupportedError):
            qs.only("name")
        with pytest.raises(NotSupportedError):
            qs.defer("name")
        with pytest.raises(NotSupportedError):
            await qs.update(qty=0)
        with pytest.raises(NotSupportedError):
            await qs.delete()

    @pytest.mark.asyncio
    async def test_union_requires_queryset(self, seeded):
        with pytest.raises(TypeError):
            QmItem.objects.filter(qty=1).union("not a queryset")

    @pytest.mark.asyncio
    async def test_union_sql_uses_explicit_columns(self, db):
        qs = QmItem.objects.filter(qty=1).union(QmItem.objects.filter(qty=2))
        sql = str(qs._build_select())
        assert "UNION" in sql
        # Explicit per-column select, not SELECT *
        assert "qm_items.name" in sql
        assert "qm_items.sku" in sql


# select_for_update


class TestSelectForUpdate:
    @pytest.mark.asyncio
    async def test_sqlite_raises_not_supported(self, seeded):
        async with atomic():
            with pytest.raises(NotSupportedError):
                await QmItem.objects.select_for_update().filter(qty=1)

    @pytest.mark.asyncio
    async def test_no_transaction_raises_transaction_management_error(self, seeded):
        # Validation order: dialect first, then transaction - exercise the
        # transaction check directly for a non-SQLite dialect.
        qs = QmItem.objects.select_for_update()
        with pytest.raises(TransactionManagementError):
            qs._validate_for_update("postgresql")

    @pytest.mark.asyncio
    async def test_compiled_sql_postgresql(self, db):
        qs = QmItem.objects.filter(qty=1).select_for_update()
        stmt = qs._apply_for_update(qs._build_select())
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in sql

    @pytest.mark.asyncio
    async def test_compiled_sql_postgresql_skip_locked(self, db):
        qs = QmItem.objects.select_for_update(skip_locked=True)
        stmt = qs._apply_for_update(qs._build_select())
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in sql

    @pytest.mark.asyncio
    async def test_compiled_sql_postgresql_nowait(self, db):
        qs = QmItem.objects.select_for_update(nowait=True)
        stmt = qs._apply_for_update(qs._build_select())
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE NOWAIT" in sql

    @pytest.mark.asyncio
    async def test_compiled_sql_of_self(self, db):
        qs = QmItem.objects.select_for_update(of=("self",))
        stmt = qs._apply_for_update(qs._build_select())
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE OF qm_items" in sql

    @pytest.mark.asyncio
    async def test_flags_survive_clone(self, db):
        qs = QmItem.objects.select_for_update(skip_locked=True).order_by("qty")
        assert qs._for_update == {"nowait": False, "skip_locked": True, "of": ()}


# explain


class TestExplain:
    @pytest.mark.asyncio
    async def test_explain_returns_plan_string(self, seeded):
        plan = await QmItem.objects.filter(name="item1").explain()
        assert isinstance(plan, str)
        assert plan  # non-empty
        assert "qm_items" in plan

    @pytest.mark.asyncio
    async def test_explain_full_table(self, seeded):
        plan = await QmItem.objects.all().explain()
        assert "SCAN" in plan.upper()

    @pytest.mark.asyncio
    async def test_explain_combined_query(self, seeded):
        qs = QmItem.objects.filter(qty=1).union(QmItem.objects.filter(qty=2))
        plan = await qs.explain()
        assert isinstance(plan, str)
        assert plan
