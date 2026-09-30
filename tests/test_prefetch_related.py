"""prefetch_related(): manager API off the cache, nested lookups, no silent skips.

It used to replace a to-many accessor with a plain list (``await
author.posts.all()`` then failed), silently skip nested lookups such as
``posts__comments`` and silently ignore unknown names.
"""

import pytest
from sqlalchemy import event

from zeeb_orm import FieldError, Model, close_all_connections, configure, fields, setup_database
from zeeb_orm.query import Prefetch
from zeeb_orm.query.prefetch import PrefetchedRelated


class PfAuthor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "pf_authors"


class PfTag(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "pf_tags"


class PfPost(Model):
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(PfAuthor, related_name="posts")
    tags = fields.ManyToMany(PfTag, related_name="posts")

    class Meta:
        table_name = "pf_posts"
        ordering = ["title"]


class PfComment(Model):
    body = fields.CharField(max_length=50)
    post = fields.ForeignKey(PfPost, related_name="comments")

    class Meta:
        table_name = "pf_comments"


MODELS = (PfAuthor, PfTag, PfPost, PfComment)
TABLES = ("pf_authors", "pf_tags", "pf_posts", "pf_comments", "pf_posts_tags")


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
    PfPost.tags.get_through_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for name in TABLES:
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
def queries(db):
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
async def world(db):
    ann = await PfAuthor.objects.create(name="Ann")
    bob = await PfAuthor.objects.create(name="Bob")
    await PfAuthor.objects.create(name="Cy")
    py = await PfTag.objects.create(name="python")
    web = await PfTag.objects.create(name="web")
    p1 = await PfPost.objects.create(title="a1", author=ann)
    await PfPost.objects.create(title="a2", author=ann)
    p3 = await PfPost.objects.create(title="b1", author=bob)
    await p1.tags.add(web, py)
    await p3.tags.add(py)
    await PfComment.objects.create(body="c1", post=p1)
    await PfComment.objects.create(body="c2", post=p1)
    await PfComment.objects.create(body="c3", post=p3)
    return {"ann": ann, "bob": bob, "python": py, "web": web}


def _by_name(authors):
    return {a.name: a for a in authors}


class TestManagerApiFromCache:
    async def test_all_count_exists_need_no_query(self, world, queries):
        authors = _by_name(await PfAuthor.objects.prefetch_related("posts"))
        queries.clear()
        ann = authors["Ann"]
        assert [p.title for p in await ann.posts.all()] == ["a1", "a2"]
        assert await ann.posts.count() == 2
        assert await ann.posts.exists()
        assert not await authors["Cy"].posts.exists()
        assert queries == []

    async def test_still_a_list(self, world):
        ann = _by_name(await PfAuthor.objects.prefetch_related("posts"))["Ann"]
        assert isinstance(ann.posts, list) and isinstance(ann.posts, PrefetchedRelated)
        assert len(ann.posts) == 2
        assert [p.title for p in ann.posts] == ["a1", "a2"]

    async def test_filter_goes_to_the_database(self, world, queries):
        ann = _by_name(await PfAuthor.objects.prefetch_related("posts"))["Ann"]
        queries.clear()
        rows = await ann.posts.filter(title="a2")
        assert [p.title for p in rows] == ["a2"]
        assert len(queries) == 1

    async def test_write_invalidates_the_cache(self, world):
        ann = _by_name(await PfAuthor.objects.prefetch_related("posts"))["Ann"]
        await ann.posts.create(title="a3")
        assert not isinstance(ann.posts, PrefetchedRelated)
        assert await ann.posts.count() == 3

    async def test_m2m_accessor_keeps_manager_api(self, world, queries):
        posts = await PfPost.objects.prefetch_related("tags")
        queries.clear()
        tags = await posts[0].tags.all()
        assert sorted(t.name for t in tags) == ["python", "web"]
        assert queries == []
        await posts[2].tags.add(world["web"])
        assert sorted(t.name for t in await posts[2].tags.all()) == ["python", "web"]

    async def test_to_attr_is_a_plain_list(self, world):
        authors = await PfAuthor.objects.prefetch_related(
            Prefetch("posts", queryset=PfPost.objects.filter(title="a2"), to_attr="drafts")
        )
        ann = _by_name(authors)["Ann"]
        assert type(ann.drafts) is list
        assert [p.title for p in ann.drafts] == ["a2"]
        assert not isinstance(ann.posts, PrefetchedRelated)

    async def test_custom_queryset_order_is_kept_for_m2m(self, world):
        posts = await PfPost.objects.prefetch_related(
            Prefetch("tags", queryset=PfTag.objects.order_by("-name"))
        )
        assert [t.name for t in posts[0].tags] == ["web", "python"]
        posts = await PfPost.objects.prefetch_related(
            Prefetch("tags", queryset=PfTag.objects.order_by("name"))
        )
        assert [t.name for t in posts[0].tags] == ["python", "web"]

    async def test_reverse_children_know_their_parent(self, world, queries):
        ann = _by_name(await PfAuthor.objects.prefetch_related("posts"))["Ann"]
        queries.clear()
        assert ann.posts[0].author is ann
        assert queries == []


class TestNestedLookups:
    async def test_reverse_then_reverse(self, world, queries):
        queries.clear()
        authors = await PfAuthor.objects.prefetch_related("posts__comments")
        # authors, posts, comments
        assert len(queries) == 3
        ann = _by_name(authors)["Ann"]
        queries.clear()
        bodies = {p.title: sorted(c.body for c in p.comments) for p in ann.posts}
        assert bodies == {"a1": ["c1", "c2"], "a2": []}
        assert await ann.posts[0].comments.count() == 2
        assert queries == []

    async def test_forward_then_reverse(self, world):
        comments = await PfComment.objects.prefetch_related("post__author")
        by_body = {c.body: c for c in comments}
        assert by_body["c3"].post.author.name == "Bob"

    async def test_through_m2m(self, world):
        authors = await PfAuthor.objects.prefetch_related("posts__tags")
        ann = _by_name(authors)["Ann"]
        tags = {p.title: sorted(t.name for t in p.tags) for p in ann.posts}
        assert tags == {"a1": ["python", "web"], "a2": []}

    async def test_shared_prefix_is_fetched_once(self, world, queries):
        queries.clear()
        await PfAuthor.objects.prefetch_related("posts", "posts__comments", "posts__tags")
        # authors, posts, comments, through table, tags
        assert len(queries) == 5

    async def test_prefetch_object_then_nested_uses_its_queryset(self, world):
        authors = await PfAuthor.objects.prefetch_related(
            Prefetch("posts", queryset=PfPost.objects.filter(title="a1")),
            "posts__comments",
        )
        ann = _by_name(authors)["Ann"]
        assert [p.title for p in ann.posts] == ["a1"]
        assert sorted(c.body for c in ann.posts[0].comments) == ["c1", "c2"]

    async def test_select_related_objects_are_reused(self, world, queries):
        queries.clear()
        posts = await PfPost.objects.select_related("author").prefetch_related("author__posts")
        # posts (joined with authors), then the authors' posts — no author refetch
        assert len(queries) == 2
        assert sorted(p.title for p in posts[0].author.posts) == ["a1", "a2"]


class TestInvalidLookups:
    async def test_unknown_name_raises(self, world):
        with pytest.raises(FieldError, match="nope"):
            await PfAuthor.objects.prefetch_related("nope")

    async def test_unknown_nested_name_raises(self, world):
        with pytest.raises(FieldError, match="nope"):
            await PfAuthor.objects.prefetch_related("posts__nope")

    async def test_non_relation_raises(self, world):
        with pytest.raises(FieldError, match="title"):
            await PfPost.objects.prefetch_related("title")
