"""Django semantics of model instances: loading, relations, identity, registry.

Each test pins a defect that used to pass silently:

- a NULL column loaded as the field default, and the next save() wrote it back
- ``await obj.fk`` raised TypeError whenever the relation happened to be cached
- a raw-id assignment (and refresh_from_db) kept serving the old related object
- a misspelt keyword became a plain attribute, and ``update_fields`` typos and
  UPDATEs matching no row were ignored
- unsaved instances all compared equal and changed their hash on save
- same-named models in two apps overwrote each other in the registry,
  reverse-accessor clashes were skipped, ``ForeignKey("self")`` on an abstract
  base resolved to the abstract parent
- related managers and FK loaders ignored the instance's database alias
"""

from __future__ import annotations

import pytest

from zeeb_orm import Database, Model, fields, register_database
from zeeb_orm.exceptions import DatabaseError, FieldError
from zeeb_orm.models.base import (
    AmbiguousModelReferenceError,
    _model_registry,
    model_label,
    resolve_model_ref,
)
from zeeb_orm.models.fields import ForeignKeyLazyLoader
from zeeb_orm.testing import temporary_database


class SmAuthor(Model):
    name = fields.CharField(max_length=100)
    verified = fields.BooleanField(null=True)
    score = fields.IntegerField(null=True, default=0)

    class Meta:
        table_name = "sm_authors"


class SmPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(SmAuthor, related_name="sm_posts", null=True)

    class Meta:
        table_name = "sm_posts"


class SmTag(Model):
    label = fields.CharField(max_length=50)
    posts = fields.ManyToMany(SmPost, related_name="sm_tags")

    class Meta:
        table_name = "sm_tags"


MODELS = (SmAuthor, SmPost, SmTag)


@pytest.fixture
async def db():
    # temporary_database also creates SmTag.posts' auto join table.
    async with temporary_database(*MODELS) as database:
        yield database


async def _raw(sql: str) -> list:
    from sqlalchemy import text

    from zeeb_orm.db.connection import get_connection

    database = await get_connection()
    async with database.session() as session:
        result = await session.execute(text(sql))
        await session.commit()
        return result.fetchall() if result.returns_rows else []


# ---------------------------------------------------------------------------
# 1. Loading never applies defaults
# ---------------------------------------------------------------------------


class TestNullIsNotTheDefault:
    async def test_a_null_column_loads_as_none_and_survives_a_save(self, db):
        author = await SmAuthor.objects.create(name="a", verified=None, score=None)
        stored = await _raw("SELECT verified, score FROM sm_authors")
        assert stored == [(None, None)]

        loaded = await SmAuthor.objects.get(pk=author.pk)
        assert loaded.verified is None
        assert loaded.score is None

        loaded.name = "renamed"
        await loaded.save()
        assert await _raw("SELECT verified, score FROM sm_authors") == [(None, None)]

    async def test_select_related_does_not_apply_defaults_either(self, db):
        author = await SmAuthor.objects.create(name="a", verified=None, score=None)
        await SmPost.objects.create(title="p", author=author)

        post = await SmPost.objects.select_related("author").get(title="p")
        assert post.author.verified is None
        assert post.author.score is None

    def test_the_constructor_applies_defaults_only_to_fields_not_passed(self):
        assert SmAuthor(name="a").verified is False
        assert SmAuthor(name="a").score == 0
        assert SmAuthor(name="a", verified=None, score=None).verified is None
        assert SmAuthor(name="a", verified=None, score=None).score is None

    async def test_a_deferred_field_is_not_overwritten_by_save(self, db):
        author = await SmAuthor.objects.create(name="a", verified=True, score=7)

        partial = await SmAuthor.objects.only("name").get(pk=author.pk)
        partial.name = "b"
        await partial.save()

        assert await _raw("SELECT name, verified, score FROM sm_authors") == [("b", 1, 7)]

        partial.score = 9  # assigning a deferred field makes it saved again
        await partial.save()
        assert await _raw("SELECT score FROM sm_authors") == [(9,)]


# ---------------------------------------------------------------------------
# 2. await obj.fk works whether or not the relation is cached
# ---------------------------------------------------------------------------


class TestAwaitingARelation:
    async def test_await_on_a_relation_cached_by_create(self, db):
        author = await SmAuthor.objects.create(name="a")
        post = await SmPost.objects.create(title="p", author=author)

        assert post.author is author  # sync access to the cached object
        assert await post.author is author  # ...and awaiting it, no query

    async def test_await_on_select_related_and_on_a_lazy_relation(self, db):
        author = await SmAuthor.objects.create(name="a")
        await SmPost.objects.create(title="p", author=author)

        joined = await SmPost.objects.select_related("author").get(title="p")
        assert (await joined.author).name == "a"

        lazy = await SmPost.objects.get(title="p")
        assert isinstance(lazy.author, ForeignKeyLazyLoader)
        loaded = await lazy.author
        assert loaded.name == "a"
        # Cached now: plain attribute access and a second await both work.
        assert lazy.author.name == "a"
        assert await lazy.author is loaded

    async def test_reading_an_unloaded_relation_names_the_fix(self, db):
        author = await SmAuthor.objects.create(name="a")
        await SmPost.objects.create(title="p", author=author)
        lazy = await SmPost.objects.get(title="p")

        with pytest.raises(AttributeError, match="await obj.author"):
            _ = lazy.author.name


# ---------------------------------------------------------------------------
# 3. A changed FK id never serves the old related object
# ---------------------------------------------------------------------------


class TestStaleRelationCache:
    async def test_assigning_a_raw_uuid_drops_the_cached_object(self, db):
        first = await SmAuthor.objects.create(name="first")
        second = await SmAuthor.objects.create(name="second")
        post = SmPost(title="p", author=first)
        assert post.author is first

        post.author = second.pk  # a UUID, not an int
        assert isinstance(post.author, ForeignKeyLazyLoader)
        assert (await post.author).name == "second"

        post.author_id = first.pk
        assert (await post.author).name == "first"

    def test_assigning_the_same_id_keeps_the_cache(self):
        author = SmAuthor(name="a", id=__import__("uuid").uuid4())
        post = SmPost(title="p", author=author)
        post.author_id = author.pk
        assert post.author is author

    async def test_an_unloaded_relation_can_be_linked_by_its_id(self, db):
        author = await SmAuthor.objects.create(name="a")
        await SmPost.objects.create(title="p", author=author)
        lazy = await SmPost.objects.get(title="p")
        # Assigning another object's unloaded relation copies the id.
        other = SmPost(title="q", author=lazy.author)
        assert other.author_id == author.pk

    async def test_refresh_from_db_reloads_the_relation(self, db):
        first = await SmAuthor.objects.create(name="first")
        second = await SmAuthor.objects.create(name="second")
        post = await SmPost.objects.create(title="p", author=first)

        await SmPost.objects.filter(pk=post.pk).update(author_id=second.pk)
        await post.refresh_from_db()

        assert post.author_id == second.pk
        assert (await post.author).name == "second"


# ---------------------------------------------------------------------------
# 4. Unknown fields and UPDATEs that hit nothing are errors
# ---------------------------------------------------------------------------


class TestUnknownFieldsAndLostUpdates:
    def test_a_misspelt_keyword_raises(self):
        with pytest.raises(TypeError, match="titel"):
            SmPost(titel="typo")

    async def test_create_with_a_misspelt_keyword_raises(self, db):
        with pytest.raises(TypeError, match="titel"):
            await SmPost.objects.create(titel="typo")
        assert await SmPost.objects.count() == 0

    def test_settable_properties_and_fk_ids_are_accepted(self):
        import uuid

        pk = uuid.uuid4()
        author_id = uuid.uuid4()
        post = SmPost(pk=pk, title="p", author_id=author_id)
        assert post.id == pk
        assert post.author_id == author_id

    async def test_update_fields_rejects_unknown_names(self, db):
        post = await SmPost.objects.create(title="p")
        with pytest.raises(ValueError, match="titel"):
            await post.save(update_fields=["titel"])

    async def test_update_fields_accepts_a_fk_attname(self, db):
        author = await SmAuthor.objects.create(name="a")
        post = await SmPost.objects.create(title="p")
        post.author = author
        await post.save(update_fields=["author_id"])
        assert (await SmPost.objects.get(pk=post.pk)).author_id == author.pk

    async def test_an_empty_update_fields_saves_nothing(self, db):
        post = await SmPost.objects.create(title="p")
        post.title = "changed"
        await post.save(update_fields=[])
        assert (await SmPost.objects.get(pk=post.pk)).title == "p"

    async def test_update_fields_on_a_deleted_row_raises(self, db):
        post = await SmPost.objects.create(title="p")
        await SmPost.objects.filter(pk=post.pk).delete()
        post.title = "changed"
        with pytest.raises(DatabaseError, match="did not affect any rows"):
            await post.save(update_fields=["title"])

    async def test_a_plain_save_of_a_deleted_row_inserts_it_again(self, db):
        from zeeb_orm.signals import post_save

        post = await SmPost.objects.create(title="p")
        await SmPost.objects.filter(pk=post.pk).delete()

        seen = []

        async def receiver(sender, instance, created, **kwargs):
            seen.append(created)

        post_save.connect(receiver, sender=SmPost, weak=False)
        try:
            post.title = "back"
            await post.save()
        finally:
            post_save.disconnect(receiver, sender=SmPost)

        assert (await SmPost.objects.get(pk=post.pk)).title == "back"
        assert seen == [True]

    async def test_update_fields_on_an_unsaved_instance_without_pk_raises(self):
        with pytest.raises(ValueError, match="no primary key"):
            await SmPost(title="p").save(update_fields=["title"])


# ---------------------------------------------------------------------------
# 5. Equality and hashing follow Django
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_unsaved_instances_are_only_equal_to_themselves(self):
        a, b = SmPost(title="x"), SmPost(title="x")
        assert a != b
        assert a == a

    def test_an_unsaved_instance_is_unhashable(self):
        with pytest.raises(TypeError, match="unhashable"):
            hash(SmPost(title="x"))

    async def test_a_saved_instance_hashes_by_pk_and_stays_in_its_set(self, db):
        post = await SmPost.objects.create(title="p")
        bucket = {post}
        post.title = "changed"
        await post.save()
        assert post in bucket
        assert await SmPost.objects.get(pk=post.pk) in bucket

    def test_different_models_with_the_same_pk_differ(self):
        import uuid

        pk = uuid.uuid4()
        assert SmPost(pk=pk, title="x") != SmAuthor(pk=pk, name="x")


# ---------------------------------------------------------------------------
# 6. The registry is app-qualified; relations resolve per concrete model
# ---------------------------------------------------------------------------


def _widget(label: str, name: str = "SmWidget") -> type[Model]:
    """A model class called ``name`` in app ``label``."""
    meta = type("Meta", (), {"app_label": label, "table_name": f"sm_{label}_{name.lower()}"})
    return type(name, (Model,), {"__module__": __name__, "Meta": meta})


class TestRegistry:
    def test_same_named_models_in_two_apps_coexist(self):
        shop, blog = _widget("shop"), _widget("blog")

        assert _model_registry["shop.SmWidget"] is shop
        assert _model_registry["blog.SmWidget"] is blog
        assert resolve_model_ref("shop.SmWidget") is shop
        assert resolve_model_ref("blog.SmWidget") is blog
        assert resolve_model_ref("apps.shop.SmWidget") is shop
        with pytest.raises(AmbiguousModelReferenceError, match="shop.SmWidget"):
            resolve_model_ref("SmWidget")
        # A bare name from inside an app resolves to that app's model.
        assert resolve_model_ref("SmWidget", relative_to=blog) is blog

    def test_a_bare_fk_target_resolves_within_its_own_app(self):
        store = _widget("store")

        class Order(Model):
            widget = fields.ForeignKey("SmWidget", on_delete="CASCADE")

            class Meta:
                app_label = "store"
                table_name = "sm_store_order"

        _widget("lab")
        assert Order._fk_fields[0].get_target_model() is store

    def test_a_project_model_shadows_a_framework_model_of_the_same_name(self):
        meta = type("Meta", (), {"table_name": "sm_framework_gizmo"})
        framework = type(
            "SmGizmo", (Model,), {"__module__": "zeeb_api.fakeapp.models", "Meta": meta}
        )
        project = _widget("accounts", "SmGizmo")

        assert model_label(framework) == "fakeapp.SmGizmo"
        assert resolve_model_ref("SmGizmo") is project

    def test_a_reverse_accessor_clash_raises(self):
        class Owner(Model):
            class Meta:
                table_name = "sm_clash_owner"

        class Car(Model):
            owner = fields.ForeignKey(Owner, related_name="vehicles")

            class Meta:
                table_name = "sm_clash_car"

        with pytest.raises(FieldError, match="Owner.vehicles"):

            class Boat(Model):
                owner = fields.ForeignKey(Owner, related_name="vehicles")

                class Meta:
                    table_name = "sm_clash_boat"

        # The first relation keeps its accessor; nothing is left pending.
        from zeeb_orm.models.relations import _pending_relations

        assert Owner.vehicles.related_model is Car
        assert not [p for p in _pending_relations if p[0].__name__ == "Boat"]

    def test_a_reverse_accessor_may_not_shadow_a_field(self):
        class Shelf(Model):
            books = fields.IntegerField(default=0)

            class Meta:
                table_name = "sm_clash_shelf"

        with pytest.raises(FieldError, match="Shelf.books"):

            class Book(Model):
                shelf = fields.ForeignKey(Shelf, related_name="books")

                class Meta:
                    table_name = "sm_clash_book"

    def test_class_placeholder_gives_each_subclass_its_own_accessor(self):
        class Person(Model):
            class Meta:
                table_name = "sm_ph_person"

        class Owned(Model):
            owner = fields.ForeignKey(Person, related_name="%(class)s_items")

            class Meta:
                abstract = True

        class Pen(Owned):
            class Meta:
                table_name = "sm_ph_pen"

        class Cup(Owned):
            class Meta:
                table_name = "sm_ph_cup"

        assert Person.pen_items.related_model is Pen
        assert Person.cup_items.related_model is Cup

    def test_self_on_an_abstract_base_is_the_concrete_subclass(self):
        class TreeNode(Model):
            parent = fields.ForeignKey("self", null=True, related_name="%(class)s_children")
            links = fields.ManyToMany("self", related_name="%(class)s_linked")

            class Meta:
                abstract = True

        class Folder(TreeNode):
            class Meta:
                table_name = "sm_self_folder"

        class Category(TreeNode):
            class Meta:
                table_name = "sm_self_category"

        assert Folder._fk_fields[0].get_target_model() is Folder
        assert Category._fk_fields[0].get_target_model() is Category
        assert Folder.parent is Folder._fk_fields[0]
        assert Category._m2m_fields[0].get_target_model() is Category
        assert Category._m2m_fields[0].get_through_table_name() == "sm_self_category_links"
        assert Folder.folder_children.related_model is Folder


# ---------------------------------------------------------------------------
# 17. Relations stay on the instance's database
# ---------------------------------------------------------------------------


class TestRelationsFollowTheAlias:
    @pytest.fixture
    async def other(self, db):
        other = Database("sqlite+aiosqlite:///:memory:")
        await other.connect()
        engine = other._async_engine
        tables = [m._get_table() for m in MODELS] + [SmTag._m2m_fields[0].get_through_table()]
        async with engine.begin() as conn:
            from zeeb_orm.models.base import metadata

            await conn.run_sync(lambda c: metadata.create_all(c, tables=tables))
        register_database(other, "other")
        yield other
        from zeeb_orm.db.connection import _connections

        await _connections.pop("other").disconnect()

    async def test_fk_loader_reverse_manager_and_m2m_read_the_other_database(self, other):
        author = await SmAuthor.objects.using("other").create(name="elsewhere")
        post = await SmPost.objects.using("other").create(title="p", author=author)
        tag = await SmTag.objects.using("other").create(label="t")
        await tag.posts.add(post)

        # Nothing of this exists on the default database.
        assert await SmAuthor.objects.count() == 0

        loaded = await SmPost.objects.using("other").get(pk=post.pk)
        assert (await loaded.author).name == "elsewhere"

        loaded_author = await SmAuthor.objects.using("other").get(pk=author.pk)
        assert [p.title for p in await loaded_author.sm_posts.all()] == ["p"]

        loaded_tag = await SmTag.objects.using("other").get(pk=tag.pk)
        assert [p.title for p in await loaded_tag.posts.all()] == ["p"]
        await loaded_tag.posts.clear()
        assert await loaded_tag.posts.count() == 0
