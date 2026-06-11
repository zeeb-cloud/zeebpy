"""Tests for the ManyToMany implementation.

Covers: auto through-table creation (columns, constraints, migration
autodetection), forward/reverse accessors (add/remove/set/clear/count,
pk arguments, duplicate handling, unsaved errors), query traversal
(filter across m2m in both directions, distinct, prefetch_related) and
custom through models (reads work, writes raise NotSupportedError).
"""

import pytest
import sqlalchemy as sa

from zeeb_orm import (
    Model,
    NotSupportedError,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class M2MTag(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "m2m_tags"


class M2MPost(Model):
    title = fields.CharField(max_length=200)
    tags = fields.ManyToMany(M2MTag, related_name="posts")

    class Meta:
        table_name = "m2m_posts"


class M2MColor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "m2m_colors"


class M2MShirt(Model):
    label = fields.CharField(max_length=50)
    colors = fields.ManyToMany(M2MColor)  # no related_name -> m2mshirt_set

    class Meta:
        table_name = "m2m_shirts"


class M2MPerson(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "m2m_persons"


class M2MTeam(Model):
    name = fields.CharField(max_length=50)
    members = fields.ManyToMany(
        M2MPerson, through="M2MMembership", related_name="teams"
    )

    class Meta:
        table_name = "m2m_teams"


class M2MMembership(Model):
    person = fields.ForeignKey(M2MPerson)
    team = fields.ForeignKey(M2MTeam)
    role = fields.CharField(max_length=50, default="member")

    class Meta:
        table_name = "m2m_memberships"


MODELS = (M2MTag, M2MPost, M2MColor, M2MShirt, M2MPerson, M2MTeam, M2MMembership)
AUTO_THROUGH_TABLES = ("m2m_posts_tags", "m2m_shirts_colors")


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

    # Trigger table creation (also creates the auto M2M through tables)
    for model in MODELS:
        model._get_table()

    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    # Remove our tables (incl. through tables) from the global metadata so
    # later-collected tests (e.g. migration autodetection) stay clean.
    for name in [m._meta.db_table for m in MODELS] + list(AUTO_THROUGH_TABLES):
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def seeded(db):
    """Three tags, two posts; post1 has python+testing, post2 has python."""
    t_python = await M2MTag.objects.create(name="python")
    t_testing = await M2MTag.objects.create(name="testing")
    t_rust = await M2MTag.objects.create(name="rust")
    p1 = await M2MPost.objects.create(title="First Post")
    p2 = await M2MPost.objects.create(title="Second Post")
    await p1.tags.add(t_python, t_testing)
    await p2.tags.add(t_python)
    return {
        "tags": {"python": t_python, "testing": t_testing, "rust": t_rust},
        "posts": [p1, p2],
    }


@pytest.fixture
async def team_seeded(db):
    """Custom-through data: Alice leads team A, Bob is a member of A and B."""
    alice = await M2MPerson.objects.create(name="Alice")
    bob = await M2MPerson.objects.create(name="Bob")
    team_a = await M2MTeam.objects.create(name="Team A")
    team_b = await M2MTeam.objects.create(name="Team B")
    await M2MMembership.objects.create(person=alice, team=team_a, role="lead")
    await M2MMembership.objects.create(person=bob, team=team_a)
    await M2MMembership.objects.create(person=bob, team=team_b)
    return {"alice": alice, "bob": bob, "team_a": team_a, "team_b": team_b}


class TestM2MTableCreation:
    @pytest.mark.asyncio
    async def test_through_table_in_metadata(self, db):
        from zeeb_orm.models.base import metadata

        assert "m2m_posts_tags" in metadata.tables
        through = M2MPost.tags.get_through_table()
        assert through is metadata.tables["m2m_posts_tags"]

    @pytest.mark.asyncio
    async def test_through_table_columns(self, db):
        through = M2MPost.tags.get_through_table()
        assert set(through.c.keys()) == {"m2mpost_id", "m2mtag_id"}
        assert M2MPost.tags.get_source_column() == "m2mpost_id"
        assert M2MPost.tags.get_target_column() == "m2mtag_id"

    @pytest.mark.asyncio
    async def test_through_table_foreign_keys_cascade(self, db):
        through = M2MPost.tags.get_through_table()
        fks = {
            list(col.foreign_keys)[0].target_fullname: list(col.foreign_keys)[0]
            for col in through.c
        }
        assert set(fks) == {"m2m_posts.id", "m2m_tags.id"}
        assert all(fk.ondelete == "CASCADE" for fk in fks.values())

    @pytest.mark.asyncio
    async def test_through_table_unique_constraint(self, db):
        through = M2MPost.tags.get_through_table()
        uniques = [
            {col.name for col in c.columns}
            for c in through.constraints
            if isinstance(c, sa.UniqueConstraint)
        ]
        assert {"m2mpost_id", "m2mtag_id"} in uniques

    @pytest.mark.asyncio
    async def test_through_column_types_copied_from_pks(self, db):
        # Default PKs are UUIDs -> through columns must be UUID-typed too
        through = M2MPost.tags.get_through_table()
        pk_type = type(M2MPost._get_table().c["id"].type)
        assert isinstance(through.c["m2mpost_id"].type, pk_type)
        assert isinstance(through.c["m2mtag_id"].type, pk_type)

    @pytest.mark.asyncio
    async def test_migration_autodetect_sees_through_table(self, db, tmp_path):
        from zeeb_orm.migrations.autodetector import detect_changes
        from zeeb_orm.migrations.operations import CreateModel

        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        ops = detect_changes(migrations_dir=str(mig_dir))
        created_tables = [op.table for op in ops if isinstance(op, CreateModel)]
        assert "m2m_posts_tags" in created_tables
        assert "m2m_posts" in created_tables


class TestM2MAccessors:
    @pytest.mark.asyncio
    async def test_add_and_count(self, seeded):
        p1, p2 = seeded["posts"]
        assert await p1.tags.count() == 2
        assert await p2.tags.count() == 1

    @pytest.mark.asyncio
    async def test_all_returns_related(self, seeded):
        p1, _ = seeded["posts"]
        tags = await p1.tags.all()
        assert {t.name for t in tags} == {"python", "testing"}

    @pytest.mark.asyncio
    async def test_filter_on_manager(self, seeded):
        p1, _ = seeded["posts"]
        tags = await p1.tags.filter(name__startswith="py")
        assert [t.name for t in tags] == ["python"]

    @pytest.mark.asyncio
    async def test_add_duplicates_ignored(self, seeded):
        p1, _ = seeded["posts"]
        python = seeded["tags"]["python"]
        await p1.tags.add(python)
        await p1.tags.add(python, python)
        assert await p1.tags.count() == 2

    @pytest.mark.asyncio
    async def test_add_accepts_pk_values(self, seeded):
        _, p2 = seeded["posts"]
        rust = seeded["tags"]["rust"]
        await p2.tags.add(rust.pk)
        tags = await p2.tags.all()
        assert {t.name for t in tags} == {"python", "rust"}

    @pytest.mark.asyncio
    async def test_remove(self, seeded):
        p1, _ = seeded["posts"]
        await p1.tags.remove(seeded["tags"]["testing"])
        assert {t.name for t in await p1.tags.all()} == {"python"}
        # Removing by pk also works; removing a non-linked tag is a no-op
        await p1.tags.remove(seeded["tags"]["python"].pk, seeded["tags"]["rust"])
        assert await p1.tags.count() == 0

    @pytest.mark.asyncio
    async def test_set_diff_based(self, seeded):
        p1, _ = seeded["posts"]
        rust = seeded["tags"]["rust"]
        python = seeded["tags"]["python"]
        await p1.tags.set([python, rust])
        assert {t.name for t in await p1.tags.all()} == {"python", "rust"}

    @pytest.mark.asyncio
    async def test_set_with_clear(self, seeded):
        p1, _ = seeded["posts"]
        await p1.tags.set([seeded["tags"]["rust"]], clear=True)
        assert {t.name for t in await p1.tags.all()} == {"rust"}

    @pytest.mark.asyncio
    async def test_clear(self, seeded):
        p1, p2 = seeded["posts"]
        await p1.tags.clear()
        assert await p1.tags.count() == 0
        # Other post's links are untouched
        assert await p2.tags.count() == 1

    @pytest.mark.asyncio
    async def test_reverse_accessor(self, seeded):
        python = seeded["tags"]["python"]
        posts = await python.posts.all()
        assert {p.title for p in posts} == {"First Post", "Second Post"}

    @pytest.mark.asyncio
    async def test_reverse_add_and_remove(self, seeded):
        rust = seeded["tags"]["rust"]
        p1, _ = seeded["posts"]
        await rust.posts.add(p1)
        assert {t.name for t in await p1.tags.all()} == {"python", "testing", "rust"}
        await rust.posts.remove(p1)
        assert await rust.posts.count() == 0

    @pytest.mark.asyncio
    async def test_default_reverse_accessor_name(self, db):
        shirt = await M2MShirt.objects.create(label="polo")
        red = await M2MColor.objects.create(name="red")
        await shirt.colors.add(red)
        shirts = await red.m2mshirt_set.all()
        assert [s.label for s in shirts] == ["polo"]

    @pytest.mark.asyncio
    async def test_unsaved_instance_raises(self, db):
        post = M2MPost(title="unsaved")
        with pytest.raises(ValueError, match="needs to have a value for field"):
            post.tags  # noqa: B018

    @pytest.mark.asyncio
    async def test_add_unsaved_object_raises(self, seeded):
        p1, _ = seeded["posts"]
        with pytest.raises(ValueError, match="unsaved"):
            await p1.tags.add(M2MTag(name="ghost"))

    @pytest.mark.asyncio
    async def test_create_on_manager_links(self, seeded):
        p1, _ = seeded["posts"]
        tag = await p1.tags.create(name="async")
        assert tag.pk is not None
        assert {t.name for t in await p1.tags.all()} == {"python", "testing", "async"}


class TestM2MQueries:
    @pytest.mark.asyncio
    async def test_forward_filter_traversal(self, seeded):
        posts = await M2MPost.objects.filter(tags__name="testing")
        assert [p.title for p in posts] == ["First Post"]
        posts = await M2MPost.objects.filter(tags__name="python")
        assert {p.title for p in posts} == {"First Post", "Second Post"}

    @pytest.mark.asyncio
    async def test_reverse_filter_traversal(self, seeded):
        tags = await M2MTag.objects.filter(posts__title="First Post")
        assert {t.name for t in tags} == {"python", "testing"}

    @pytest.mark.asyncio
    async def test_filter_by_instance(self, seeded):
        python = seeded["tags"]["python"]
        posts = await M2MPost.objects.filter(tags=python)
        assert {p.title for p in posts} == {"First Post", "Second Post"}

    @pytest.mark.asyncio
    async def test_duplicates_and_distinct(self, seeded):
        # First Post matches via both python and testing -> appears twice
        posts = await M2MPost.objects.filter(tags__name__in=["python", "testing"])
        titles = [p.title for p in posts]
        assert titles.count("First Post") == 2
        assert titles.count("Second Post") == 1

        distinct = await M2MPost.objects.filter(
            tags__name__in=["python", "testing"]
        ).distinct()
        assert sorted(p.title for p in distinct) == ["First Post", "Second Post"]

    @pytest.mark.asyncio
    async def test_prefetch_related_forward(self, seeded):
        posts = await M2MPost.objects.prefetch_related("tags").order_by("title")
        assert isinstance(posts[0].tags, list)
        assert {t.name for t in posts[0].tags} == {"python", "testing"}
        assert {t.name for t in posts[1].tags} == {"python"}

    @pytest.mark.asyncio
    async def test_prefetch_related_reverse(self, seeded):
        tags = await M2MTag.objects.prefetch_related("posts").order_by("name")
        by_name = {t.name: t.posts for t in tags}
        assert {p.title for p in by_name["python"]} == {"First Post", "Second Post"}
        assert {p.title for p in by_name["testing"]} == {"First Post"}
        assert by_name["rust"] == []

    @pytest.mark.asyncio
    async def test_count_with_traversal(self, seeded):
        assert await M2MPost.objects.filter(tags__name="python").count() == 2

    @pytest.mark.asyncio
    async def test_values_with_traversal(self, seeded):
        rows = await M2MPost.objects.filter(title="Second Post").values(
            "title", "tags__name"
        )
        assert rows == [{"title": "Second Post", "tags__name": "python"}]


class TestCustomThrough:
    @pytest.mark.asyncio
    async def test_forward_read(self, team_seeded):
        members = await team_seeded["team_a"].members.all()
        assert {m.name for m in members} == {"Alice", "Bob"}

    @pytest.mark.asyncio
    async def test_reverse_read(self, team_seeded):
        teams = await team_seeded["bob"].teams.all()
        assert {t.name for t in teams} == {"Team A", "Team B"}

    @pytest.mark.asyncio
    async def test_traversal_through_custom_table(self, team_seeded):
        teams = await M2MTeam.objects.filter(members__name="Alice")
        assert [t.name for t in teams] == ["Team A"]
        persons = await M2MPerson.objects.filter(teams__name="Team B")
        assert [p.name for p in persons] == ["Bob"]

    @pytest.mark.asyncio
    async def test_writes_raise_not_supported(self, team_seeded):
        team = team_seeded["team_a"]
        alice = team_seeded["alice"]
        with pytest.raises(NotSupportedError, match="through model"):
            await team.members.add(alice)
        with pytest.raises(NotSupportedError, match="through model"):
            await team.members.remove(alice)
        with pytest.raises(NotSupportedError, match="through model"):
            await team.members.clear()
        with pytest.raises(NotSupportedError, match="through model"):
            await team.members.set([alice])
        # Reverse side too
        with pytest.raises(NotSupportedError, match="through model"):
            await alice.teams.add(team)

    @pytest.mark.asyncio
    async def test_through_model_direct_writes(self, team_seeded):
        alice = team_seeded["alice"]
        team_b = team_seeded["team_b"]
        await M2MMembership.objects.create(person=alice, team=team_b, role="advisor")
        teams = await alice.teams.all()
        assert {t.name for t in teams} == {"Team A", "Team B"}
