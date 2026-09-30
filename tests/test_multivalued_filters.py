"""exclude() / negation and chained filter() across multi-valued relations.

Django semantics pinned here:

* ``exclude(rel__x=v)`` over a reverse FK / M2M removes the objects that have
  *any* related row matching, and keeps objects without related rows. It used
  to negate the condition over a shared LEFT JOIN — returning objects with any
  *non*-matching related row and dropping objects without related rows.
* ``exclude(x=v)`` keeps rows where ``x`` is NULL.
* ``exclude(a, b)`` removes rows matching ``a AND b``.
* ``filter(rel__x=a).filter(rel__x=b)`` matches objects with a related row
  ``a`` and a (possibly different) related row ``b``; it used to share one
  join and could never match.
"""

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import F, Model, Q, close_all_connections, configure, fields, setup_database


class MvAuthor(Model):
    name = fields.CharField(max_length=50)
    age = fields.IntegerField(null=True)

    class Meta:
        table_name = "mv_authors"


class MvTag(Model):
    name = fields.CharField(max_length=50)
    color = fields.CharField(max_length=20, null=True)

    class Meta:
        table_name = "mv_tags"


class MvBook(Model):
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(MvAuthor, null=True, on_delete="SET_NULL", related_name="books")
    published = fields.BooleanField(default=False)
    rating = fields.IntegerField(null=True)
    tags = fields.ManyToMany(MvTag, related_name="books")

    class Meta:
        table_name = "mv_books"


MODELS = (MvAuthor, MvTag, MvBook)


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
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    MvBook.tags.get_through_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    through = metadata.tables.get("mv_books_tags")
    if through is not None:
        metadata.remove(through)
    Settings.reset()


@pytest.fixture
async def world(db):
    ann = await MvAuthor.objects.create(name="Ann", age=30)
    bob = await MvAuthor.objects.create(name="Bob", age=None)
    cy = await MvAuthor.objects.create(name="Cy", age=40)  # no books

    python = await MvTag.objects.create(name="python", color="blue")
    testing = await MvTag.objects.create(name="testing", color="red")
    rust = await MvTag.objects.create(name="rust", color=None)

    b1 = await MvBook.objects.create(title="python", author=ann, published=True, rating=5)
    b2 = await MvBook.objects.create(title="Ann draft", author=ann, published=False)
    b3 = await MvBook.objects.create(title="Bob draft", author=bob, published=False, rating=3)
    await MvBook.objects.create(title="Orphan", author=None, published=True, rating=1)

    await b1.tags.add(python, testing)  # python + testing
    await b2.tags.add(python)  # python only
    await b3.tags.add(rust)  # rust only
    # b4 has no tags
    return {"authors": (ann, bob, cy), "tags": (python, testing, rust)}


def _names(objs):
    return sorted(getattr(o, "name", None) or o.title for o in objs)


class TestExcludeAcrossReverseFk:
    async def test_excludes_objects_with_any_matching_related_row(self, world):
        # Ann has a published book, Bob does not, Cy has no books at all.
        rows = await MvAuthor.objects.exclude(books__published=True)
        assert _names(rows) == ["Bob", "Cy"]

    async def test_negated_q_inside_filter_behaves_like_exclude(self, world):
        rows = await MvAuthor.objects.filter(~Q(books__published=True))
        assert _names(rows) == ["Bob", "Cy"]

    async def test_double_negation_is_has_a_matching_row(self, world):
        rows = await MvAuthor.objects.exclude(~Q(books__published=True))
        assert _names(rows) == ["Ann"]

    async def test_exclude_isnull_keeps_objects_with_related_rows(self, world):
        assert _names(await MvAuthor.objects.exclude(books__isnull=True)) == ["Ann", "Bob"]
        assert _names(await MvAuthor.objects.exclude(books__isnull=False)) == ["Cy"]

    async def test_mixed_with_plain_condition_in_one_exclude(self, world):
        # NOT (age = 30 AND has a published book): only Ann matches both.
        rows = await MvAuthor.objects.exclude(age=30, books__published=True)
        assert _names(rows) == ["Bob", "Cy"]

    async def test_count_and_values_follow_the_same_semantics(self, world):
        qs = MvAuthor.objects.exclude(books__published=True)
        assert await qs.count() == 2
        assert sorted(await qs.values_list("name", flat=True)) == ["Bob", "Cy"]


class TestExcludeAcrossM2M:
    async def test_forward_m2m(self, world):
        rows = await MvBook.objects.exclude(tags__name="python")
        assert _names(rows) == ["Bob draft", "Orphan"]

    async def test_reverse_m2m(self, world):
        rows = await MvTag.objects.exclude(books__published=True)
        assert _names(rows) == ["rust"]

    async def test_exclude_with_f_expression_correlates_to_the_outer_row(self, world):
        # Book "python" carries a tag named like its own title.
        rows = await MvBook.objects.exclude(tags__name=F("title"))
        assert _names(rows) == ["Ann draft", "Bob draft", "Orphan"]

    async def test_exclude_combined_with_a_join_filter(self, world):
        rows = await MvBook.objects.filter(author__name="Ann").exclude(tags__name="testing")
        assert _names(rows) == ["Ann draft"]

    async def test_update_and_delete_with_multi_valued_exclude(self, world):
        updated = await MvBook.objects.exclude(tags__name="python").update(rating=0)
        assert updated == 2
        assert _names(await MvBook.objects.filter(rating=0)) == ["Bob draft", "Orphan"]

        # Neither the rust-only book nor the untagged one has a colored tag.
        deleted = await MvBook.objects.exclude(tags__color__isnull=False).delete()
        assert deleted == 2
        assert _names(await MvBook.objects.all()) == ["Ann draft", "python"]


class TestExcludeKeepsNullRows:
    async def test_nullable_column(self, world):
        assert _names(await MvAuthor.objects.exclude(age=30)) == ["Bob", "Cy"]
        assert _names(await MvAuthor.objects.filter(~Q(age__gt=35))) == ["Ann", "Bob"]

    async def test_nullable_column_with_transform_like_lookups(self, world):
        rows = await MvBook.objects.exclude(rating__in=[5, 3])
        assert _names(rows) == ["Ann draft", "Orphan"]

    async def test_forward_fk_traversal_keeps_objects_without_the_relation(self, world):
        rows = await MvBook.objects.exclude(author__name="Ann")
        assert _names(rows) == ["Bob draft", "Orphan"]

    async def test_nullable_fk_column(self, world):
        ann = world["authors"][0]
        rows = await MvBook.objects.exclude(author=ann)
        assert _names(rows) == ["Bob draft", "Orphan"]

    async def test_f_reference_to_a_nullable_column(self, world):
        # rating = rating is NULL for unrated books; excluding it keeps them.
        rows = await MvBook.objects.exclude(rating=F("rating"))
        assert _names(rows) == ["Ann draft"]

    async def test_exclude_none_is_is_not_null(self, world):
        assert _names(await MvAuthor.objects.exclude(age=None)) == ["Ann", "Cy"]

    async def test_exclude_args_mean_not_of_the_conjunction(self, world):
        # NOT (published AND rating = 1): only the orphan matches both.
        rows = await MvBook.objects.exclude(Q(published=True), rating=1)
        assert _names(rows) == ["Ann draft", "Bob draft", "python"]


class TestChainedFilterAcrossMultiValued:
    async def test_second_filter_gets_its_own_m2m_join(self, world):
        rows = await MvBook.objects.filter(tags__name="python").filter(tags__name="testing")
        assert _names(rows) == ["python"]

    async def test_one_filter_call_refers_to_the_same_related_row(self, world):
        # No single tag is both named python and red.
        assert await MvBook.objects.filter(tags__name="python", tags__color="red").count() == 0
        rows = await MvBook.objects.filter(tags__name="python").filter(tags__color="red")
        assert _names(rows) == ["python"]

    async def test_reverse_fk(self, world):
        # Ann has a published book and (another) unpublished one.
        rows = await MvAuthor.objects.filter(books__published=True).filter(books__published=False)
        assert _names(rows) == ["Ann"]
        same_row = await MvAuthor.objects.filter(
            books__published=True, books__published__in=[False]
        )
        assert same_row == []

    async def test_single_valued_joins_stay_shared(self, world):
        qs = MvBook.objects.filter(author__name="Ann").filter(author__age=30)
        sql = str(qs._build_select().compile(dialect=sqlite.dialect()))
        assert sql.count("JOIN mv_authors") == 1
        assert _names(await qs) == ["Ann draft", "python"]

    async def test_ordering_and_values_reuse_the_filter_join(self, world):
        qs = MvBook.objects.filter(tags__name="python").values_list("title", "tags__name")
        assert sorted(await qs) == [("Ann draft", "python"), ("python", "python")]


class TestCompiledSql:
    """The exclude subquery and NULL guards render on every dialect."""

    @pytest.mark.parametrize(
        "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
    )
    def test_multi_valued_exclude_is_a_pk_subquery(self, dialect):
        MvBook.tags.get_through_table()
        qs = MvBook.objects.exclude(tags__name="python")
        sql = str(qs._build_select().compile(dialect=dialect))
        assert "NOT IN (SELECT" in sql
        assert "_mv_mv_books" in sql
        # The outer statement no longer joins the tags at all.
        assert "LEFT OUTER JOIN mv_tags" not in sql.split("NOT IN")[0]

    @pytest.mark.parametrize(
        "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
    )
    def test_nullable_exclude_guards_null(self, dialect):
        qs = MvAuthor.objects.exclude(age=30)
        sql = str(qs._build_select().compile(dialect=dialect))
        assert "age IS NOT NULL" in sql

    def test_non_nullable_exclude_has_no_guard(self):
        qs = MvAuthor.objects.exclude(name="Ann")
        sql = str(qs._build_select().compile(dialect=postgresql.dialect()))
        assert "IS NOT NULL" not in sql

    def test_chained_m2m_filters_join_twice(self):
        MvBook.tags.get_through_table()
        qs = MvBook.objects.filter(tags__name="a").filter(tags__name="b")
        sql = str(qs._build_select().compile(dialect=postgresql.dialect()))
        assert sql.count("JOIN mv_tags AS") == 2
        assert "_sr_tags_tags_2" in sql
