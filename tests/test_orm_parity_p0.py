"""Regression tests for the ORM parity P0 fixes.

Covers the formerly silent failure modes:
- Aggregate(filter=Q(...)) applied (was accepted and ignored)
- Case/When routed through the shared lookup machinery (was a reduced,
  silently-skipping lookup subset)
- Subquery/OuterRef/Exists correlation (was a no-op)
- distinct(*fields) raising instead of silently degrading to plain DISTINCT
- values_list() with no fields returning all columns (was empty tuples)
- first()/last() deterministic via effective ordering
- bulk_create(ignore_conflicts=True) honored (was silently ignored)
- nested atomic() as SAVEPOINTs + on_commit gated to the outermost commit
- reads/saves inside atomic() joining the transaction session
- using() strict alias resolution honored by instance writes
- temporal lookup-value coercion (ISO strings)
- IntegrityError translation
"""

import datetime

import pytest

from zeeb_orm import (
    Database,
    Model,
    Q,
    close_all_connections,
    configure,
    fields,
    register_database,
    setup_database,
)
from zeeb_orm.exceptions import (
    ConnectionDoesNotExist,
    FieldError,
    IntegrityError,
    NotSupportedError,
)
from zeeb_orm.query.expressions import (
    Case,
    Count,
    Exists,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)


class PpAuthor(Model):
    name = fields.CharField(max_length=100)
    active = fields.BooleanField(default=True)

    class Meta:
        table_name = "pp_authors"


class PpPost(Model):
    title = fields.CharField(max_length=200)
    views = fields.IntegerField(default=0)
    author = fields.ForeignKey(PpAuthor, related_name="posts", null=True)
    created_at = fields.DateTimeField(null=True)

    class Meta:
        table_name = "pp_posts"


class PpSlot(Model):
    code = fields.CharField(max_length=20, unique=True)

    class Meta:
        table_name = "pp_slots"


class PpOrdered(Model):
    id = fields.AutoField()
    n = fields.IntegerField(default=0)

    class Meta:
        table_name = "pp_ordered"


class PpMetaOrdered(Model):
    id = fields.AutoField()
    n = fields.IntegerField(default=0)

    class Meta:
        table_name = "pp_meta_ordered"
        ordering = ["n"]


MODELS = (PpAuthor, PpPost, PpSlot, PpOrdered, PpMetaOrdered)


@pytest.fixture
async def db():
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


async def _seed_posts(author=None):
    await PpPost.objects.create(title="Alpha", views=150, author=author)
    await PpPost.objects.create(title="beta", views=50, author=author)
    await PpPost.objects.create(title="Gamma", views=5, author=author)


class TestAggregateFilter:
    async def test_count_with_filter(self, db):
        await _seed_posts()
        res = await PpPost.objects.aggregate(
            total=Count("id"),
            hot=Count("id", filter=Q(views__gte=100)),
        )
        assert res["total"] == 3
        assert res["hot"] == 1

    async def test_count_star_with_filter(self, db):
        await _seed_posts()
        res = await PpPost.objects.aggregate(warm=Count(filter=Q(views__range=(10, 99))))
        assert res["warm"] == 1

    async def test_sum_with_filter(self, db):
        await _seed_posts()
        res = await PpPost.objects.aggregate(
            hot_views=Sum("views", filter=Q(views__gte=50)),
            all_views=Sum("views"),
        )
        assert res["hot_views"] == 200
        assert res["all_views"] == 205

    async def test_filter_requires_q(self, db):
        with pytest.raises(TypeError):
            await PpPost.objects.aggregate(bad=Count("id", filter="views > 3"))

    def test_alias_kwarg_removed(self):
        with pytest.raises(TypeError):
            Count("id", alias="x")


class TestCaseWhen:
    async def test_full_lookup_set(self, db):
        await _seed_posts()
        rows = await PpPost.objects.annotate(
            tier=Case(
                When(views__gte=100, then=Value("hot")),
                When(views__range=(10, 99), then=Value("warm")),
                default=Value("cold"),
            )
        ).order_by("-views")
        assert [r.tier for r in rows] == ["hot", "warm", "cold"]

    async def test_istartswith_lookup(self, db):
        await _seed_posts()
        rows = await PpPost.objects.annotate(
            a_title=Case(
                When(title__istartswith="a", then=Value(1)),
                default=Value(0),
            )
        ).order_by("-views")
        assert [r.a_title for r in rows] == [1, 0, 0]

    async def test_positional_q_condition(self, db):
        await _seed_posts()
        rows = await PpPost.objects.annotate(
            flagged=Case(
                When(Q(views__lt=10) | Q(title="Alpha"), then=Value(1)),
                default=Value(0),
            )
        ).order_by("-views")
        assert [r.flagged for r in rows] == [1, 0, 1]

    async def test_case_tuple_q_condition(self, db):
        await _seed_posts()
        rows = await PpPost.objects.annotate(
            cheap=Case(
                (Q(views__lte=50), Value("low")),
                default=Value("high"),
            )
        ).order_by("-views")
        assert [r.cheap for r in rows] == ["high", "low", "low"]

    async def test_unknown_field_raises(self, db):
        await _seed_posts()
        qs = PpPost.objects.annotate(
            x=Case(When(bogus=1, then=Value(1)), default=Value(0))
        )
        with pytest.raises(FieldError):
            await qs

    async def test_unknown_lookup_raises(self, db):
        await _seed_posts()
        qs = PpPost.objects.annotate(
            x=Case(When(views__frobnicate=1, then=Value(1)), default=Value(0))
        )
        with pytest.raises(FieldError):
            await qs

    def test_when_rejects_non_q_positional(self):
        with pytest.raises(TypeError):
            When("views > 3", then=Value(1))

    def test_when_requires_condition(self):
        with pytest.raises(ValueError):
            When(then=Value(1))


class TestSubqueryOuterRef:
    async def test_correlated_scalar_subquery(self, db):
        a1 = await PpAuthor.objects.create(name="ann")
        a2 = await PpAuthor.objects.create(name="bob")
        await PpPost.objects.create(title="a-low", views=1, author=a1)
        await PpPost.objects.create(title="a-high", views=9, author=a1)
        await PpPost.objects.create(title="b-only", views=5, author=a2)

        top_post = (
            PpPost.objects.filter(author_id=OuterRef("id"))
            .order_by("-views")
            .values("title")[:1]
        )
        rows = await PpAuthor.objects.annotate(top=Subquery(top_post)).order_by("name")
        assert [(r.name, r.top) for r in rows] == [("ann", "a-high"), ("bob", "b-only")]

    async def test_exists_annotation(self, db):
        a1 = await PpAuthor.objects.create(name="haspost")
        await PpAuthor.objects.create(name="nopost")
        await PpPost.objects.create(title="t", views=1, author=a1)

        rows = await PpAuthor.objects.annotate(
            has_posts=Exists(PpPost.objects.filter(author_id=OuterRef("id")))
        ).order_by("name")
        assert [(r.name, bool(r.has_posts)) for r in rows] == [
            ("haspost", True),
            ("nopost", False),
        ]

    async def test_subquery_requires_single_column(self, db):
        qs = PpAuthor.objects.annotate(
            bad=Subquery(PpPost.objects.filter(author_id=OuterRef("id")))
        )
        with pytest.raises(ValueError):
            await qs


class TestQuickFixes:
    async def test_distinct_fields_raises(self, db):
        with pytest.raises(NotSupportedError):
            PpPost.objects.distinct("title")

    async def test_distinct_plain_still_works(self, db):
        await _seed_posts()
        rows = await PpPost.objects.values_list("views", flat=True).distinct()
        assert sorted(rows) == [5, 50, 150]

    async def test_values_list_no_fields_returns_all_columns(self, db):
        await PpAuthor.objects.create(name="full")
        rows = await PpAuthor.objects.values_list()
        assert len(rows) == 1
        row = rows[0]
        assert len(row) == len(PpAuthor._meta.local_fields)
        assert "full" in row

    async def test_values_list_flat_multiple_fields_raises(self, db):
        with pytest.raises(TypeError):
            PpAuthor.objects.values_list("id", "name", flat=True)

    async def test_last_unordered_uses_pk_order(self, db):
        for n in (1, 2, 3):
            await PpOrdered.objects.create(n=n)
        first = await PpOrdered.objects.first()
        last = await PpOrdered.objects.last()
        assert first.n == 1
        assert last.n == 3

    async def test_last_respects_meta_ordering(self, db):
        for n in (2, 3, 1):
            await PpMetaOrdered.objects.create(n=n)
        first = await PpMetaOrdered.objects.first()
        last = await PpMetaOrdered.objects.last()
        assert first.n == 1
        assert last.n == 3


class TestBulkCreateIgnoreConflicts:
    async def test_conflicts_skipped(self, db):
        await PpSlot.objects.bulk_create([PpSlot(code="a"), PpSlot(code="b")])
        res = await PpSlot.objects.bulk_create(
            [PpSlot(code="a"), PpSlot(code="c")], ignore_conflicts=True
        )
        assert len(res) == 2
        codes = sorted(await PpSlot.objects.values_list("code", flat=True))
        assert codes == ["a", "b", "c"]

    async def test_conflict_without_flag_raises(self, db):
        await PpSlot.objects.bulk_create([PpSlot(code="a")])
        with pytest.raises(IntegrityError):
            await PpSlot.objects.bulk_create([PpSlot(code="a")])


class TestAtomicNesting:
    async def test_inner_rollback_keeps_outer(self, db):
        from zeeb_orm import atomic

        async with atomic():
            await PpAuthor.objects.create(name="outer")
            try:
                async with atomic():
                    await PpAuthor.objects.create(name="inner")
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            # Savepoint rolled back: inner gone, outer intact, txn usable
            names = sorted(a.name for a in await PpAuthor.objects.all())
            assert names == ["outer"]
        assert await PpAuthor.objects.count() == 1

    async def test_outer_rollback_discards_inner_commit(self, db):
        from zeeb_orm import atomic

        with pytest.raises(RuntimeError):
            async with atomic():
                await PpAuthor.objects.create(name="outer")
                async with atomic():
                    await PpAuthor.objects.create(name="inner")
                raise RuntimeError("boom")
        assert await PpAuthor.objects.count() == 0

    async def test_on_commit_gated_to_outermost(self, db):
        from zeeb_orm import atomic
        from zeeb_orm.db.transaction import on_commit

        fired: list[str] = []
        async with atomic():
            on_commit(lambda: fired.append("outer"))
            async with atomic():
                on_commit(lambda: fired.append("inner"))
            assert fired == []
        assert fired == ["outer", "inner"]

    async def test_on_commit_discarded_on_savepoint_rollback(self, db):
        from zeeb_orm import atomic
        from zeeb_orm.db.transaction import on_commit

        fired: list[str] = []
        async with atomic():
            on_commit(lambda: fired.append("outer"))
            try:
                async with atomic():
                    on_commit(lambda: fired.append("inner"))
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        assert fired == ["outer"]


class TestReadsAndSavesJoinTransaction:
    async def test_reads_see_uncommitted_writes(self, db):
        from zeeb_orm import atomic

        async with atomic():
            await PpAuthor.objects.create(name="txn")
            assert await PpAuthor.objects.count() == 1
            fetched = await PpAuthor.objects.get(name="txn")
            assert fetched.name == "txn"

    async def test_get_or_create_idempotent_inside_atomic(self, db):
        from zeeb_orm import atomic

        async with atomic():
            obj1, created1 = await PpAuthor.objects.get_or_create(name="solo")
            obj2, created2 = await PpAuthor.objects.get_or_create(name="solo")
        assert created1 is True
        assert created2 is False
        assert obj1.pk == obj2.pk
        assert await PpAuthor.objects.count() == 1

    async def test_instance_save_joins_transaction(self, db):
        from zeeb_orm import atomic

        with pytest.raises(RuntimeError):
            async with atomic():
                author = PpAuthor(name="doomed")
                await author.save()
                raise RuntimeError("boom")
        assert await PpAuthor.objects.count() == 0


class TestUsingAlias:
    async def test_unknown_alias_raises(self, db):
        with pytest.raises(ConnectionDoesNotExist):
            await PpAuthor.objects.using("nope").count()

    async def test_registered_alias_isolated_and_honored_by_save(self, db):
        other = Database("sqlite+aiosqlite:///:memory:")
        await other.connect()
        await other.create_all()
        register_database(other, "other")

        author = await PpAuthor.objects.using("other").create(name="alt")
        assert author._state.db_alias == "other"
        assert await PpAuthor.objects.using("other").count() == 1
        assert await PpAuthor.objects.count() == 0

        # save() targets the alias the instance was created/loaded from
        author.name = "alt2"
        await author.save()
        fetched = await PpAuthor.objects.using("other").get(pk=author.pk)
        assert fetched.name == "alt2"
        assert await PpAuthor.objects.count() == 0


class TestTemporalCoercion:
    def test_lookup_condition_coerces_iso_datetime(self):
        from zeeb_orm.query.queryset import lookup_to_condition

        cond = lookup_to_condition(
            PpPost, "created_at__gte", "2026-01-15T10:30:00"
        )
        assert isinstance(cond.right.value, datetime.datetime)
        assert cond.right.value == datetime.datetime(2026, 1, 15, 10, 30)

    def test_lookup_condition_coerces_date_only_string(self):
        from zeeb_orm.query.queryset import lookup_to_condition

        cond = lookup_to_condition(PpPost, "created_at__lt", "2026-01-15")
        assert isinstance(cond.right.value, datetime.datetime)
        assert cond.right.value == datetime.datetime(2026, 1, 15, 0, 0)

    async def test_filter_with_iso_string(self, db):
        await PpPost.objects.create(
            title="old", created_at=datetime.datetime(2026, 1, 1, 12, 0)
        )
        await PpPost.objects.create(
            title="new", created_at=datetime.datetime(2026, 6, 1, 12, 0)
        )
        rows = await PpPost.objects.filter(created_at__gte="2026-03-01T00:00:00")
        assert [r.title for r in rows] == ["new"]

    def test_non_temporal_values_untouched(self):
        from zeeb_orm.query.queryset import lookup_to_condition

        cond = lookup_to_condition(PpPost, "title__gte", "2026-01-15")
        assert cond.right.value == "2026-01-15"


class TestIntegrityErrorTranslation:
    async def test_unique_violation_translated(self, db):
        await PpSlot.objects.create(code="dup")
        with pytest.raises(IntegrityError) as excinfo:
            await PpSlot.objects.create(code="dup")
        # Original driver error preserved for debugging
        assert excinfo.value.__cause__ is not None

    async def test_translated_inside_atomic(self, db):
        from zeeb_orm import atomic

        await PpSlot.objects.create(code="dup")
        with pytest.raises(IntegrityError):
            async with atomic():
                await PpSlot.objects.create(code="dup")
        # Transaction rolled back cleanly, connection still usable
        assert await PpSlot.objects.count() == 1
