"""Tests for ForeignKey on_delete variants.

NOTE: SQLite's foreign_keys PRAGMA is OFF by default (verified in
TestAssumptions), so the database itself never cascades/restricts here —
these tests exercise the Python-side Collector logic exclusively.
"""

import pytest
from sqlalchemy.schema import CreateTable

from zeeb_orm import (
    CASCADE,
    DO_NOTHING,
    PROTECT,
    RESTRICT,
    SET_DEFAULT,
    SET_NULL,
    Model,
    ProtectedError,
    RestrictedError,
    close_all_connections,
    configure,
    fields,
    post_delete,
    pre_delete,
    setup_database,
)

# Test Models — separate author models per variant so deletions don't
# trigger unrelated inbound FKs.


class CasAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_cas_authors"


class CasPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(CasAuthor, on_delete=CASCADE, related_name="posts")

    class Meta:
        table_name = "od_cas_posts"


class CasComment(Model):
    body = fields.CharField(max_length=100)
    post = fields.ForeignKey(CasPost, on_delete=CASCADE, related_name="comments")

    class Meta:
        table_name = "od_cas_comments"


class ProtAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_prot_authors"


class ProtBook(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(ProtAuthor, on_delete=PROTECT, related_name="books")

    class Meta:
        table_name = "od_prot_books"


class ResAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_res_authors"


class ResBook(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(ResAuthor, on_delete=CASCADE, related_name="books")

    class Meta:
        table_name = "od_res_books"


class ResReview(Model):
    text = fields.CharField(max_length=100)
    book = fields.ForeignKey(ResBook, on_delete=RESTRICT, related_name="reviews")
    author = fields.ForeignKey(ResAuthor, on_delete=CASCADE, related_name="reviews")

    class Meta:
        table_name = "od_res_reviews"


class SnAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_sn_authors"


class SnPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(
        SnAuthor, on_delete=SET_NULL, null=True, related_name="posts"
    )

    class Meta:
        table_name = "od_sn_posts"


class SdAuthor(Model):
    id = fields.AutoField()
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_sd_authors"


class SdPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(
        SdAuthor, on_delete=SET_DEFAULT, default=1, related_name="posts"
    )

    class Meta:
        table_name = "od_sd_posts"


class DnAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "od_dn_authors"


class DnPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(
        DnAuthor, on_delete=DO_NOTHING, related_name="posts"
    )

    class Meta:
        table_name = "od_dn_posts"


class SrCategory(Model):
    name = fields.CharField(max_length=100)
    parent = fields.ForeignKey(
        "self", on_delete=CASCADE, null=True, related_name="children"
    )

    class Meta:
        table_name = "od_sr_categories"


MODELS = (
    CasAuthor,
    CasPost,
    CasComment,
    ProtAuthor,
    ProtBook,
    ResAuthor,
    ResBook,
    ResReview,
    SnAuthor,
    SnPost,
    SdAuthor,
    SdPost,
    DnAuthor,
    DnPost,
    SrCategory,
)


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


@pytest.fixture(autouse=True)
def clear_signals():
    yield
    for sig in (pre_delete, post_delete):
        sig._receivers.clear()


class TestAssumptions:
    @pytest.mark.asyncio
    async def test_sqlite_fk_pragma_is_off(self, db):
        """The DB does NOT enforce FKs — Python logic is what's exercised."""
        result = await db.execute("PRAGMA foreign_keys")
        assert result.fetchone()[0] == 0


class TestOnDeleteValidation:
    def test_unknown_on_delete_raises(self):
        with pytest.raises(ValueError, match="Unknown on_delete"):
            fields.ForeignKey(CasAuthor, on_delete="EXPLODE")

    def test_set_null_requires_null_true(self):
        with pytest.raises(ValueError, match="null=True"):
            fields.ForeignKey(CasAuthor, on_delete=SET_NULL)

    def test_set_default_requires_default(self):
        with pytest.raises(ValueError, match="default"):
            fields.ForeignKey(CasAuthor, on_delete=SET_DEFAULT)

    def test_valid_combinations_accepted(self):
        fields.ForeignKey(CasAuthor, on_delete=SET_NULL, null=True)
        fields.ForeignKey(CasAuthor, on_delete=SET_DEFAULT, default=1)
        fields.ForeignKey(CasAuthor, on_delete=DO_NOTHING)


class TestDDL:
    """The fix: on_delete constants map to valid SQL ON DELETE actions."""

    def _ddl(self, model):
        return str(CreateTable(model._get_table()).compile())

    @pytest.mark.asyncio
    async def test_cascade_ddl(self, db):
        assert "ON DELETE CASCADE" in self._ddl(CasPost)

    @pytest.mark.asyncio
    async def test_set_null_ddl(self, db):
        ddl = self._ddl(SnPost)
        assert "ON DELETE SET NULL" in ddl
        assert "SET_NULL" not in ddl  # the old bug emitted the raw constant

    @pytest.mark.asyncio
    async def test_set_default_ddl(self, db):
        assert "ON DELETE SET DEFAULT" in self._ddl(SdPost)

    @pytest.mark.asyncio
    async def test_protect_maps_to_restrict_ddl(self, db):
        assert "ON DELETE RESTRICT" in self._ddl(ProtBook)

    @pytest.mark.asyncio
    async def test_do_nothing_emits_no_clause(self, db):
        assert "ON DELETE" not in self._ddl(DnPost)


class TestCascade:
    @pytest.mark.asyncio
    async def test_cascade_deletes_children(self, db):
        author = await CasAuthor.objects.create(name="a")
        await CasPost.objects.create(title="p1", author=author)
        await CasPost.objects.create(title="p2", author=author)

        total, per_model = await author.delete()

        assert total == 3
        assert per_model == {"CasAuthor": 1, "CasPost": 2}
        assert await CasPost.objects.count() == 0

    @pytest.mark.asyncio
    async def test_cascade_multi_level(self, db):
        author = await CasAuthor.objects.create(name="a")
        post = await CasPost.objects.create(title="p", author=author)
        await CasComment.objects.create(body="c1", post=post)
        await CasComment.objects.create(body="c2", post=post)

        total, per_model = await author.delete()

        assert total == 4
        assert per_model == {"CasAuthor": 1, "CasPost": 1, "CasComment": 2}
        assert await CasComment.objects.count() == 0

    @pytest.mark.asyncio
    async def test_cascade_leaves_unrelated_rows(self, db):
        a1 = await CasAuthor.objects.create(name="a1")
        a2 = await CasAuthor.objects.create(name="a2")
        await CasPost.objects.create(title="p1", author=a1)
        keep = await CasPost.objects.create(title="p2", author=a2)

        await a1.delete()

        remaining = await CasPost.objects.all()
        assert [p.pk for p in remaining] == [keep.pk]

    @pytest.mark.asyncio
    async def test_signals_fired_on_cascade(self, db):
        author = await CasAuthor.objects.create(name="a")
        post = await CasPost.objects.create(title="p", author=author)

        events = []

        async def on_pre(sender, instance, **kwargs):
            events.append(("pre", sender.__name__, instance.pk))

        async def on_post(sender, instance, **kwargs):
            events.append(("post", sender.__name__, instance.pk))

        pre_delete.connect(on_pre, weak=False)
        post_delete.connect(on_post, weak=False)

        await author.delete()

        assert ("pre", "CasPost", post.pk) in events
        assert ("post", "CasPost", post.pk) in events
        assert ("pre", "CasAuthor", author.pk) in events
        assert ("post", "CasAuthor", author.pk) in events
        # Leaf-first: the post's pre_delete fires before the author's
        assert events.index(("pre", "CasPost", post.pk)) < events.index(
            ("pre", "CasAuthor", author.pk)
        )

    @pytest.mark.asyncio
    async def test_queryset_delete_routes_through_collector(self, db):
        author = await CasAuthor.objects.create(name="a")
        await CasPost.objects.create(title="p1", author=author)
        await CasPost.objects.create(title="p2", author=author)

        # CasAuthor has a non-DO_NOTHING inbound FK -> Collector path; the
        # count includes cascade-deleted rows.
        deleted = await CasAuthor.objects.filter(pk=author.pk).delete()
        assert deleted == 3
        assert await CasPost.objects.count() == 0

    @pytest.mark.asyncio
    async def test_queryset_delete_fast_path_for_leaf_model(self, db):
        author = await CasAuthor.objects.create(name="a")
        await CasComment.objects.create(
            body="c",
            post=await CasPost.objects.create(title="p", author=author),
        )
        # No model references CasComment -> fast single-statement path
        deleted = await CasComment.objects.all().delete()
        assert deleted == 1


class TestProtect:
    @pytest.mark.asyncio
    async def test_protect_raises_before_any_delete(self, db):
        author = await ProtAuthor.objects.create(name="a")
        book = await ProtBook.objects.create(title="b", author=author)

        with pytest.raises(ProtectedError) as exc:
            await author.delete()

        # Error carries the protected objects and a useful message
        assert book in exc.value.protected_objects
        assert "ProtBook.author" in str(exc.value)
        # Nothing was deleted
        assert await ProtAuthor.objects.count() == 1
        assert await ProtBook.objects.count() == 1
        assert author._state.persisted is True

    @pytest.mark.asyncio
    async def test_protect_allows_delete_without_references(self, db):
        author = await ProtAuthor.objects.create(name="a")
        total, _ = await author.delete()
        assert total == 1


class TestRestrict:
    @pytest.mark.asyncio
    async def test_restrict_raises(self, db):
        author = await ResAuthor.objects.create(name="a")
        book = await ResBook.objects.create(title="b", author=author)
        other_author = await ResAuthor.objects.create(name="other")
        review = await ResReview.objects.create(
            text="r", book=book, author=other_author
        )

        # Deleting just the book: the review RESTRICTs and is not collected
        with pytest.raises(RestrictedError) as exc:
            await book.delete()
        assert review in exc.value.restricted_objects
        assert await ResBook.objects.count() == 1

    @pytest.mark.asyncio
    async def test_restrict_allowed_when_restricting_rows_also_deleted(self, db):
        author = await ResAuthor.objects.create(name="a")
        book = await ResBook.objects.create(title="b", author=author)
        await ResReview.objects.create(text="r", book=book, author=author)

        # Deleting the author cascades to both the book AND the review,
        # so the RESTRICT edge (review -> book) does not block.
        total, per_model = await author.delete()
        assert total == 3
        assert per_model == {"ResAuthor": 1, "ResBook": 1, "ResReview": 1}


class TestSetNull:
    @pytest.mark.asyncio
    async def test_set_null_clears_fk_and_keeps_row(self, db):
        author = await SnAuthor.objects.create(name="a")
        post = await SnPost.objects.create(title="p", author=author)

        total, per_model = await author.delete()

        assert total == 1
        assert per_model == {"SnAuthor": 1}
        survivor = await SnPost.objects.get(pk=post.pk)
        assert survivor.author_id is None

    @pytest.mark.asyncio
    async def test_set_null_only_affects_referencing_rows(self, db):
        a1 = await SnAuthor.objects.create(name="a1")
        a2 = await SnAuthor.objects.create(name="a2")
        await SnPost.objects.create(title="p1", author=a1)
        p2 = await SnPost.objects.create(title="p2", author=a2)

        await a1.delete()

        kept = await SnPost.objects.get(pk=p2.pk)
        assert kept.author_id == a2.pk


class TestSetDefault:
    @pytest.mark.asyncio
    async def test_set_default_resets_fk(self, db):
        default_author = await SdAuthor.objects.create(name="default")
        assert default_author.pk == 1  # matches the field default

        author = await SdAuthor.objects.create(name="real")
        post = await SdPost.objects.create(title="p", author=author)
        assert post.author_id == author.pk

        await author.delete()

        survivor = await SdPost.objects.get(pk=post.pk)
        assert survivor.author_id == 1


class TestDoNothing:
    @pytest.mark.asyncio
    async def test_do_nothing_leaves_rows_untouched(self, db):
        author = await DnAuthor.objects.create(name="a")
        post = await DnPost.objects.create(title="p", author=author)

        total, per_model = await author.delete()

        assert total == 1
        assert per_model == {"DnAuthor": 1}
        # Row survives with a (now dangling) FK — SQLite doesn't enforce it
        survivor = await DnPost.objects.get(pk=post.pk)
        assert survivor.author_id == author.pk


class TestSelfReferential:
    @pytest.mark.asyncio
    async def test_cascade_down_the_tree(self, db):
        root = await SrCategory.objects.create(name="root")
        child = await SrCategory.objects.create(name="child", parent=root)
        grandchild = await SrCategory.objects.create(
            name="grandchild", parent=child
        )

        total, per_model = await root.delete()

        assert total == 3
        assert per_model == {"SrCategory": 3}
        assert await SrCategory.objects.count() == 0
        assert grandchild.pk is not None  # instance still usable after delete

    @pytest.mark.asyncio
    async def test_deleting_leaf_keeps_ancestors(self, db):
        root = await SrCategory.objects.create(name="root")
        child = await SrCategory.objects.create(name="child", parent=root)

        total, _ = await child.delete()

        assert total == 1
        assert await SrCategory.objects.count() == 1
