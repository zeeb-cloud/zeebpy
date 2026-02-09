"""Tests for Zeeb ORM core functionality."""

import pytest
import asyncio
from datetime import datetime

from zeeb_orm import (
    Model,
    fields,
    Q,
    F,
    Count,
    Sum,
    Avg,
    configure,
    setup_database,
    close_all_connections,
)


# Test Models


class User(Model):
    """Test user model."""

    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    age = fields.IntegerField(null=True)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "test_users"
        ordering = ["-created_at"]


class Post(Model):
    """Test post model."""

    title = fields.CharField(max_length=200)
    content = fields.TextField()
    author = fields.ForeignKey(User, related_name="posts")
    published = fields.BooleanField(default=False)
    view_count = fields.IntegerField(default=0)

    class Meta:
        table_name = "test_posts"


# Test Q Objects


class TestQObjects:
    """Tests for Q object functionality."""

    def test_simple_q(self):
        """Test simple Q object creation."""
        q = Q(name="John")
        assert q.children == [("name", "John")]

    def test_q_and(self):
        """Test Q AND operation."""
        q = Q(name="John") & Q(age=25)
        assert len(q.children) == 2

    def test_q_or(self):
        """Test Q OR operation."""
        q = Q(name="John") | Q(name="Jane")
        assert len(q.children) == 2

    def test_q_not(self):
        """Test Q NOT operation."""
        q = ~Q(deleted=True)
        assert q.negated is True

    def test_complex_q(self):
        """Test complex Q combination."""
        q = Q(age__gte=18) & (Q(name__startswith="A") | Q(name__startswith="B"))
        assert len(q.children) == 2


# Test F Expressions


class TestFExpressions:
    """Tests for F expression functionality."""

    def test_f_creation(self):
        """Test F object creation."""
        f = F("field_name")
        assert f.field_name == "field_name"

    def test_f_arithmetic(self):
        """Test F arithmetic operations."""
        expr = F("count") + 1
        assert expr.operator == "+"
        assert expr.rhs == 1

        expr = F("price") * F("quantity")
        assert expr.operator == "*"

    def test_f_subtraction(self):
        """Test F subtraction."""
        expr = F("total") - F("discount")
        assert expr.operator == "-"


# Test Field Types


class TestFields:
    """Tests for field types."""

    def test_char_field(self):
        """Test CharField."""
        field = fields.CharField(max_length=100)
        assert field.max_length == 100

    def test_integer_field(self):
        """Test IntegerField."""
        field = fields.IntegerField(default=0)
        assert field.default == 0

    def test_boolean_field(self):
        """Test BooleanField."""
        field = fields.BooleanField(default=True)
        assert field.default is True

    def test_datetime_field_auto_now(self):
        """Test DateTimeField auto_now options."""
        field = fields.DateTimeField(auto_now=True)
        assert field.auto_now is True

        field = fields.DateTimeField(auto_now_add=True)
        assert field.auto_now_add is True

    def test_foreign_key_field(self):
        """Test ForeignKey field."""
        field = fields.ForeignKey(User, related_name="posts")
        assert field.related_name == "posts"
        assert field.on_delete == "CASCADE"

    def test_field_options(self):
        """Test field common options."""
        field = fields.CharField(
            max_length=100,
            null=True,
            unique=True,
            index=True,
            default="test",
        )
        assert field.null is True
        assert field.unique is True
        assert field.index is True
        assert field.default == "test"


# Test Model


class TestModel:
    """Tests for Model class."""

    def test_model_meta(self):
        """Test model Meta options."""
        assert User._meta.db_table == "test_users"
        assert User._meta.ordering == ["-created_at"]

    def test_model_fields(self):
        """Test model field collection."""
        fields_list = User._meta.get_fields()
        field_names = [f.name for f in fields_list]
        assert "id" in field_names  # Auto PK
        assert "name" in field_names
        assert "email" in field_names

    def test_model_pk(self):
        """Test model primary key."""
        assert User._meta.pk_name == "id"
        assert User._meta.pk is not None

    def test_model_instance_creation(self):
        """Test creating model instance."""
        user = User(name="John", email="john@example.com", age=25)
        assert user.name == "John"
        assert user.email == "john@example.com"
        assert user.age == 25

    def test_model_repr(self):
        """Test model string representation."""
        user = User(name="John")
        assert "User" in repr(user)

    def test_model_equality(self):
        """Test model equality."""
        user1 = User(name="John")
        setattr(user1, "_field_id", 1)
        user2 = User(name="Jane")
        setattr(user2, "_field_id", 1)
        user3 = User(name="John")
        setattr(user3, "_field_id", 2)

        assert user1 == user2  # Same PK
        assert user1 != user3  # Different PK


# Test QuerySet


class TestQuerySet:
    """Tests for QuerySet."""

    def test_queryset_filter(self):
        """Test QuerySet filter method."""
        qs = User.objects.filter(name="John")
        assert len(qs._filters) == 1

    def test_queryset_exclude(self):
        """Test QuerySet exclude method."""
        qs = User.objects.exclude(deleted=True)
        assert len(qs._excludes) == 1

    def test_queryset_chaining(self):
        """Test QuerySet method chaining."""
        qs = User.objects.filter(active=True).filter(age__gte=18).exclude(banned=True)
        assert len(qs._filters) == 2
        assert len(qs._excludes) == 1

    def test_queryset_order_by(self):
        """Test QuerySet ordering."""
        qs = User.objects.order_by("-created_at", "name")
        assert qs._order_by == ["-created_at", "name"]

    def test_queryset_slicing(self):
        """Test QuerySet slicing."""
        qs = User.objects.all()[:10]
        assert qs._limit == 10

        qs = User.objects.all()[5:15]
        assert qs._offset == 5
        assert qs._limit == 10

    def test_queryset_distinct(self):
        """Test QuerySet distinct."""
        qs = User.objects.distinct()
        assert "*" in qs._distinct_fields

    def test_queryset_select_related(self):
        """Test QuerySet select_related."""
        qs = Post.objects.select_related("author")
        assert "author" in qs._select_related

    def test_queryset_clone(self):
        """Test QuerySet immutability."""
        qs1 = User.objects.filter(active=True)
        qs2 = qs1.filter(age__gte=18)
        assert qs1 is not qs2
        assert len(qs1._filters) == 1
        assert len(qs2._filters) == 2


# Integration Tests (require database)


@pytest.fixture
async def db():
    """Set up test database."""
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata
    
    Settings.reset()  # Clear any previous settings
    
    # Reset tables for fresh tests
    User._sa_table = None
    User._sa_model = None
    Post._sa_table = None
    Post._sa_model = None
    metadata.clear()
    
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    
    # Trigger table creation for our models
    User._get_table()
    Post._get_table()
    
    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    Settings.reset()


@pytest.mark.asyncio
async def test_create_and_get(db):
    """Test creating and retrieving objects."""
    user = await User.objects.create(
        name="John Doe",
        email="john@example.com",
        age=30,
    )

    assert user.id is not None
    assert user.name == "John Doe"

    fetched = await User.objects.get(id=user.id)
    assert fetched.name == "John Doe"


@pytest.mark.asyncio
async def test_filter_query(db):
    """Test filtering objects."""
    await User.objects.create(name="Alice", email="alice@example.com", age=25)
    await User.objects.create(name="Bob", email="bob@example.com", age=30)
    await User.objects.create(name="Charlie", email="charlie@example.com", age=35)

    users = await User.objects.filter(age__gte=30)
    assert len(users) == 2


@pytest.mark.asyncio
async def test_q_filter(db):
    """Test Q object filtering."""
    await User.objects.create(name="Alice", email="alice@example.com", age=25)
    await User.objects.create(name="Bob", email="bob@example.com", age=30)

    users = await User.objects.filter(Q(name="Alice") | Q(name="Bob"))
    assert len(users) == 2

    users = await User.objects.filter(Q(age__gte=25) & Q(age__lt=30))
    assert len(users) == 1


@pytest.mark.asyncio
async def test_update_objects(db):
    """Test updating objects."""
    user = await User.objects.create(name="John", email="john@example.com", age=25)

    await User.objects.filter(id=user.id).update(age=26)

    updated = await User.objects.get(id=user.id)
    assert updated.age == 26


@pytest.mark.asyncio
async def test_delete_objects(db):
    """Test deleting objects."""
    user = await User.objects.create(name="John", email="john@example.com")

    count = await User.objects.filter(id=user.id).delete()
    assert count == 1

    exists = await User.objects.filter(id=user.id).exists()
    assert exists is False


@pytest.mark.asyncio
async def test_count_and_exists(db):
    """Test count and exists methods."""
    await User.objects.create(name="Alice", email="alice@example.com")
    await User.objects.create(name="Bob", email="bob@example.com")

    count = await User.objects.count()
    assert count == 2

    exists = await User.objects.filter(name="Alice").exists()
    assert exists is True

    exists = await User.objects.filter(name="Unknown").exists()
    assert exists is False


@pytest.mark.asyncio
async def test_first_and_last(db):
    """Test first and last methods."""
    await User.objects.create(name="Alice", email="alice@example.com")
    await User.objects.create(name="Bob", email="bob@example.com")

    first = await User.objects.order_by("name").first()
    assert first.name == "Alice"

    last = await User.objects.order_by("name").last()
    assert last.name == "Bob"


@pytest.mark.asyncio
async def test_get_or_create(db):
    """Test get_or_create method."""
    user, created = await User.objects.get_or_create(
        email="new@example.com",
        defaults={"name": "New User"},
    )
    assert created is True
    assert user.name == "New User"

    user2, created2 = await User.objects.get_or_create(
        email="new@example.com",
        defaults={"name": "Different Name"},
    )
    assert created2 is False
    assert user2.name == "New User"


@pytest.mark.asyncio
async def test_model_save(db):
    """Test model save method."""
    user = User(name="John", email="john@example.com")
    await user.save()

    assert user.id is not None
    assert user._state.persisted is True

    user.name = "Jane"
    await user.save()

    fetched = await User.objects.get(id=user.id)
    assert fetched.name == "Jane"


@pytest.mark.asyncio
async def test_model_delete(db):
    """Test model delete method."""
    user = await User.objects.create(name="John", email="john@example.com")
    user_id = user.id

    await user.delete()

    exists = await User.objects.filter(id=user_id).exists()
    assert exists is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
