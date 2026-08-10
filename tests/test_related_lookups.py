"""Tests for related-field traversal in filters, ordering, values and writes.

Includes pinning tests (TestPinned) for behavior that existed before the
JoinContext refactor: select_related, prefetch_related, only/defer, annotate.
"""

import pytest

from zeeb_orm import (
    F,
    FieldError,
    Model,
    Q,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class LkAuthor(Model):
    name = fields.CharField(max_length=100)
    age = fields.IntegerField(null=True)

    class Meta:
        table_name = "lk_authors"


class LkProfile(Model):
    author = fields.OneToOne(LkAuthor, related_name="profile")
    bio = fields.TextField(null=True)

    class Meta:
        table_name = "lk_profiles"


class LkPost(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(LkAuthor, related_name="posts")
    views = fields.IntegerField(default=0)
    published = fields.BooleanField(default=False)

    class Meta:
        table_name = "lk_posts"


class LkLookupNamed(Model):
    """Carries a field named like a lookup operator (parse_path only)."""

    range = fields.IntegerField(default=0)

    class Meta:
        table_name = "lk_lookup_named"


class LkRef(Model):
    target = fields.ForeignKey(LkLookupNamed, related_name="refs")

    class Meta:
        table_name = "lk_refs"


MODELS = (LkAuthor, LkProfile, LkPost)


@pytest.fixture
async def db():
    """Set up test database (pattern from tests/test_orm.py)."""
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()  # Clear any previous settings

    # Reset tables for fresh tests
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")

    # Trigger table creation for our models
    for model in MODELS:
        model._get_table()

    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    # Remove our tables from the global metadata so later-collected tests
    # (e.g. migration autodetection) don't see them as pending models.
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def seeded(db):
    """Two authors with profiles; three posts."""
    alice = await LkAuthor.objects.create(name="Alice", age=30)
    bob = await LkAuthor.objects.create(name="Bob", age=25)
    await LkProfile.objects.create(author=alice, bio="loves rust and sqlite")
    await LkProfile.objects.create(author=bob, bio="pythonista")
    p1 = await LkPost.objects.create(
        title="Alice One", author=alice, views=5, published=True
    )
    p2 = await LkPost.objects.create(
        title="Alice Two", author=alice, views=20, published=False
    )
    p3 = await LkPost.objects.create(
        title="Bob One", author=bob, views=15, published=True
    )
    return {"alice": alice, "bob": bob, "posts": [p1, p2, p3]}


# Pinning tests for pre-existing behavior


class TestPinned:
    """Pin select_related/prefetch_related/only/defer/annotate behavior."""

    @pytest.mark.asyncio
    async def test_select_related_hydrates_author(self, seeded):
        posts = await LkPost.objects.select_related("author").filter(
            title="Alice One"
        )
        assert len(posts) == 1
        cached = getattr(posts[0], "_cache_author", None)
        assert cached is not None
        assert isinstance(cached, LkAuthor)
        assert cached.name == "Alice"
        # FK attribute access returns the cached instance, not a lazy loader
        assert posts[0].author is cached

    @pytest.mark.asyncio
    async def test_select_related_sql_single_left_join(self, db):
        stmt = LkPost.objects.select_related("author")._build_select()
        sql = str(stmt)
        assert sql.count("JOIN") == 1
        assert "LEFT OUTER JOIN" in sql
        # Alias / label scheme used for hydration
        assert "_sr_author_author" in sql
        assert "_sr_author_name" in sql

    @pytest.mark.asyncio
    async def test_prefetch_related_forward(self, seeded):
        posts = await LkPost.objects.prefetch_related("author").filter(
            title="Bob One"
        )
        assert len(posts) == 1
        cached = getattr(posts[0], "_cache_author", None)
        assert cached is not None
        assert cached.name == "Bob"

    @pytest.mark.asyncio
    async def test_prefetch_related_reverse(self, seeded):
        authors = await LkAuthor.objects.prefetch_related("posts").filter(
            name="Alice"
        )
        assert len(authors) == 1
        assert hasattr(authors[0], "posts")
        assert len(authors[0].posts) == 2

    @pytest.mark.asyncio
    async def test_only_fields(self, seeded):
        posts = await LkPost.objects.only("title").filter(title="Alice One")
        assert len(posts) == 1
        assert posts[0].title == "Alice One"
        assert posts[0].id is not None  # PK always included

    @pytest.mark.asyncio
    async def test_defer_fields(self, seeded):
        posts = await LkPost.objects.defer("views").filter(title="Alice One")
        assert len(posts) == 1
        assert posts[0].title == "Alice One"
        assert posts[0].published is True

    @pytest.mark.asyncio
    async def test_annotate_expression(self, seeded):
        posts = await LkPost.objects.annotate(views_plus=F("views") + 1).filter(
            title="Alice Two"
        )
        assert len(posts) == 1
        assert posts[0].views_plus == 21

    @pytest.mark.asyncio
    async def test_order_by_plain_field(self, seeded):
        posts = await LkPost.objects.order_by("-views")
        assert [p.views for p in posts] == [20, 15, 5]


# Related-field traversal


class TestForwardTraversal:
    @pytest.mark.asyncio
    async def test_filter_fk_field(self, seeded):
        posts = await LkPost.objects.filter(author__name="Alice")
        assert {p.title for p in posts} == {"Alice One", "Alice Two"}

    @pytest.mark.asyncio
    async def test_filter_fk_field_with_lookup(self, seeded):
        posts = await LkPost.objects.filter(author__age__gte=30)
        assert {p.title for p in posts} == {"Alice One", "Alice Two"}
        posts = await LkPost.objects.filter(author__name__icontains="bo")
        assert {p.title for p in posts} == {"Bob One"}

    @pytest.mark.asyncio
    async def test_exclude_fk_field(self, seeded):
        posts = await LkPost.objects.exclude(author__name="Alice")
        assert {p.title for p in posts} == {"Bob One"}

    @pytest.mark.asyncio
    async def test_q_combined_traversal(self, seeded):
        posts = await LkPost.objects.filter(
            Q(author__name="Bob") | Q(views__gt=15)
        )
        assert {p.title for p in posts} == {"Bob One", "Alice Two"}

    @pytest.mark.asyncio
    async def test_two_hop_traversal(self, seeded):
        # forward FK (author) then reverse O2O (profile)
        posts = await LkPost.objects.filter(author__profile__bio__contains="rust")
        assert {p.title for p in posts} == {"Alice One", "Alice Two"}

    @pytest.mark.asyncio
    async def test_filter_by_related_instance(self, seeded):
        # Terminal relation: compares the local FK column, no JOIN needed
        posts = await LkPost.objects.filter(author=seeded["bob"])
        assert {p.title for p in posts} == {"Bob One"}
        sql = str(LkPost.objects.filter(author=seeded["bob"])._build_select())
        assert "JOIN" not in sql

    @pytest.mark.asyncio
    async def test_get_with_traversal(self, seeded):
        post = await LkPost.objects.get(author__name="Bob")
        assert post.title == "Bob One"

    @pytest.mark.asyncio
    async def test_count_with_traversal(self, seeded):
        assert await LkPost.objects.filter(author__name="Alice").count() == 2


class TestReverseTraversal:
    @pytest.mark.asyncio
    async def test_reverse_fk_filter(self, seeded):
        authors = await LkAuthor.objects.filter(posts__views__gt=16)
        assert [a.name for a in authors] == ["Alice"]

    @pytest.mark.asyncio
    async def test_reverse_fk_duplicates_and_distinct(self, seeded):
        # Alice has two posts with views >= 5 -> she appears twice (Django parity)
        authors = await LkAuthor.objects.filter(posts__views__gte=5)
        names = [a.name for a in authors]
        assert names.count("Alice") == 2
        assert names.count("Bob") == 1

        distinct_authors = await LkAuthor.objects.filter(
            posts__views__gte=5
        ).distinct()
        assert sorted(a.name for a in distinct_authors) == ["Alice", "Bob"]

    @pytest.mark.asyncio
    async def test_reverse_o2o_filter(self, seeded):
        authors = await LkAuthor.objects.filter(profile__bio__contains="python")
        assert [a.name for a in authors] == ["Bob"]

    @pytest.mark.asyncio
    async def test_reverse_default_set_accessor(self, seeded):
        # LkProfile has related_name="profile"; default `_set` only applies
        # to FKs without a related_name (none here) -> unknown name errors.
        with pytest.raises(FieldError):
            await LkAuthor.objects.filter(lkprofile_set__bio="x")

    @pytest.mark.asyncio
    async def test_count_with_reverse_join_counts_join_rows(self, seeded):
        # Two of Alice's + one of Bob's posts match -> 3 join rows
        assert await LkAuthor.objects.filter(posts__views__gte=5).count() == 3


class TestTraversalOrderBy:
    @pytest.mark.asyncio
    async def test_order_by_related_field(self, seeded):
        posts = await LkPost.objects.order_by("author__name", "views")
        assert [p.title for p in posts] == ["Alice One", "Alice Two", "Bob One"]

    @pytest.mark.asyncio
    async def test_order_by_related_field_desc(self, seeded):
        posts = await LkPost.objects.order_by("-author__name", "-views")
        assert [p.title for p in posts] == ["Bob One", "Alice Two", "Alice One"]


class TestTraversalValues:
    @pytest.mark.asyncio
    async def test_values_with_path(self, seeded):
        rows = await LkPost.objects.filter(published=True).values(
            "title", "author__name"
        )
        assert sorted(rows, key=lambda r: r["title"]) == [
            {"title": "Alice One", "author__name": "Alice"},
            {"title": "Bob One", "author__name": "Bob"},
        ]

    @pytest.mark.asyncio
    async def test_values_list_with_path(self, seeded):
        rows = await LkPost.objects.order_by("views").values_list(
            "author__name", flat=True
        )
        assert rows == ["Alice", "Bob", "Alice"]

    @pytest.mark.asyncio
    async def test_values_list_tuples_with_path(self, seeded):
        rows = await LkPost.objects.order_by("-views").values_list(
            "title", "author__name"
        )
        assert rows == [
            ("Alice Two", "Alice"),
            ("Bob One", "Bob"),
            ("Alice One", "Alice"),
        ]


class TestTraversalUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_with_traversal(self, seeded):
        updated = await LkPost.objects.filter(author__name="Alice").update(
            published=True
        )
        assert updated == 2
        assert await LkPost.objects.filter(published=True).count() == 3

    @pytest.mark.asyncio
    async def test_delete_with_traversal(self, seeded):
        deleted = await LkPost.objects.filter(author__name="Bob").delete()
        assert deleted == 1
        assert await LkPost.objects.count() == 2
        assert {p.title for p in await LkPost.objects.all()} == {
            "Alice One",
            "Alice Two",
        }

    @pytest.mark.asyncio
    async def test_update_with_reverse_traversal(self, seeded):
        # Authors who have a post with views > 16 -> Alice only
        updated = await LkAuthor.objects.filter(posts__views__gt=16).update(age=99)
        assert updated == 1
        alice = await LkAuthor.objects.get(name="Alice")
        assert alice.age == 99


class TestTraversalWithSelectRelated:
    @pytest.mark.asyncio
    async def test_filter_and_select_related_share_single_join(self, seeded):
        qs = LkPost.objects.select_related("author").filter(author__name="Alice")
        sql = str(qs._build_select())
        # select_related and the filter traversal must share ONE JOIN
        assert sql.count("JOIN") == 1
        assert "LEFT OUTER JOIN" in sql

        posts = await qs
        assert len(posts) == 2
        for p in posts:
            cached = getattr(p, "_cache_author", None)
            assert cached is not None
            assert cached.name == "Alice"


class TestCreateWithRelatedInstance:
    @pytest.mark.asyncio
    async def test_create_caches_the_related_instance(self, seeded):
        # create() used to rewrite any kwarg carrying a .pk into "<key>_id",
        # which stored the id but threw away the descriptor's instance cache.
        post = await LkPost.objects.create(title="Fresh", author=seeded["alice"])
        assert post.author_id == seeded["alice"].pk
        assert getattr(post, "_cache_author", None) is seeded["alice"]
        # ...so reading the FK needs no query and no await
        assert post.author is seeded["alice"]

    @pytest.mark.asyncio
    async def test_create_accepts_a_raw_fk_id(self, seeded):
        post = await LkPost.objects.create(
            title="By id", author_id=seeded["bob"].pk
        )
        assert post.author_id == seeded["bob"].pk


class TestTerminalRelationLookups:
    """A relation may be followed directly by a lookup operator.

    ``author__in`` / ``author__isnull`` resolve like the bare terminal
    relation ``author=obj`` does: the local FK column for forward FK/O2O
    (no JOIN), the related model's PK for reverse accessors.
    """

    @pytest.mark.asyncio
    async def test_forward_fk_in_lookup(self, seeded):
        posts = await LkPost.objects.filter(
            author__in=[seeded["alice"], seeded["bob"]]
        )
        assert len(posts) == 3
        # Terminal relation: still the local FK column, no JOIN
        sql = str(
            LkPost.objects.filter(author__in=[seeded["alice"]])._build_select()
        )
        assert "JOIN" not in sql

    @pytest.mark.asyncio
    async def test_forward_fk_in_lookup_with_raw_pks(self, seeded):
        posts = await LkPost.objects.filter(author__in=[seeded["bob"].pk])
        assert [p.title for p in posts] == ["Bob One"]

    @pytest.mark.asyncio
    async def test_forward_fk_isnull_lookup(self, seeded):
        assert await LkPost.objects.filter(author__isnull=True).count() == 0
        assert await LkPost.objects.filter(author__isnull=False).count() == 3

    @pytest.mark.asyncio
    async def test_two_hop_terminal_relation_with_lookup(self, seeded):
        # LkProfile -> author -> (terminal) : the second hop collapses to the
        # FK column on lk_profiles, so only one JOIN is needed.
        profiles = await LkProfile.objects.filter(
            author__in=[seeded["alice"]]
        )
        assert [p.bio for p in profiles] == ["loves rust and sqlite"]

    @pytest.mark.asyncio
    async def test_reverse_relation_isnull(self, seeded):
        carol = await LkAuthor.objects.create(name="Carol", age=41)
        authors = await LkAuthor.objects.filter(profile__isnull=True)
        assert [a.name for a in authors] == ["Carol"]
        assert carol.pk == authors[0].pk

    @pytest.mark.asyncio
    async def test_reverse_relation_in_lookup(self, seeded):
        profile = await LkProfile.objects.filter(bio__contains="python").first()
        authors = await LkAuthor.objects.filter(profile__in=[profile])
        assert [a.name for a in authors] == ["Bob"]

    @pytest.mark.asyncio
    async def test_fk_column_name_after_relation_hop(self, seeded):
        # `author_id` is the db_column of the `author` field; reachable both
        # ways, before and after a relation hop.
        alice_id = seeded["alice"].pk
        assert await LkProfile.objects.filter(author_id=alice_id).count() == 1
        posts = await LkPost.objects.filter(author__profile__author_id=alice_id)
        assert sorted(p.title for p in posts) == ["Alice One", "Alice Two"]

    @pytest.mark.asyncio
    async def test_fk_column_name_with_lookup_after_hop(self, seeded):
        ids = [seeded["alice"].pk, seeded["bob"].pk]
        posts = await LkPost.objects.filter(author__profile__author_id__in=ids)
        assert len(posts) == 3

    def test_field_wins_over_lookup_name(self):
        # A field literally named like a lookup operator must be read as the
        # field, not as a terminal-relation lookup.
        from zeeb_orm.query.q import parse_path

        assert parse_path(LkRef, "target__range") == (
            ["target"],
            "range",
            None,
            "exact",
        )
        assert parse_path(LkRef, "target__range__gte") == (
            ["target"],
            "range",
            None,
            "gte",
        )
        # ...while a name that is only a lookup still collapses the relation
        assert parse_path(LkRef, "target__in") == ([], "target_id", None, "in")

    def test_reverse_scan_tolerates_registry_growth(self):
        # resolve_relation scans the model registry for reverse accessors, and
        # resolving a string-referenced FK target can import a module and
        # register further models mid-scan. Iterating the live dict raised
        # "dictionary changed size during iteration".
        from zeeb_orm.models import base as base_mod
        from zeeb_orm.models.relations import resolve_relation

        counter = iter(range(100))

        class LazyFk:
            """An FK whose target resolution registers another model."""

            name = "late"
            db_column = "late_id"
            related_name = "lates"

            def get_target_model(self):
                base_mod._model_registry[f"_Late{next(counter)}"] = LkPost
                return LkPost

        class Carrier:
            _fk_fields = [LazyFk()]
            _m2m_fields = []

        original = dict(base_mod._model_registry)
        try:
            # Carrier first, so its FK resolves before the real match is found.
            base_mod._model_registry.clear()
            base_mod._model_registry["_Carrier"] = Carrier
            base_mod._model_registry.update(original)
            assert resolve_relation(LkAuthor, "posts") is not None
        finally:
            base_mod._model_registry.clear()
            base_mod._model_registry.update(original)

    def test_transform_on_relation_is_rejected(self):
        # `year` is a datetime transform, but a relation is not a datetime.
        from zeeb_orm.query.q import parse_path

        with pytest.raises(FieldError) as exc:
            parse_path(LkRef, "target__year")
        assert "year" in str(exc.value)
        assert "LkLookupNamed" in str(exc.value)


class TestTraversalErrors:
    @pytest.mark.asyncio
    async def test_unknown_field_after_relation(self, db):
        with pytest.raises(FieldError) as exc:
            LkPost.objects.filter(author__bogus="x")._build_select()
        assert "bogus" in str(exc.value)
        assert "LkAuthor" in str(exc.value)

    @pytest.mark.asyncio
    async def test_unknown_trailing_lookup(self, db):
        with pytest.raises(FieldError):
            LkPost.objects.filter(author__name__bogus="x")._build_select()

    @pytest.mark.asyncio
    async def test_too_many_parts(self, db):
        with pytest.raises(FieldError):
            LkPost.objects.filter(author__name__year__gte__extra=1)._build_select()
