"""ModelSerializer honours what docs/api/serializers.md says it does.

- ``validate_<field>`` and ``validate(attrs)`` hooks never ran;
- ``Meta.extra_kwargs`` was ignored (even ``help_text``, the one key read);
- DRF-style fields declared on a ``ModelSerializer`` were silently dropped
  (only the marker fields - SerializerMethodField etc. - counted);
- a subclass lost the fields its parent declared;
- validation errors were keyed by the top-level field only (``address`` for
  ``address.zip``).
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ImproperlyConfigured, ValidationError
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import (
    CharField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
)
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database


class ParityAccount(Model):
    username = fields.CharField(max_length=50)
    nickname = fields.CharField(max_length=50, null=True)
    secret = fields.CharField(max_length=100, default="unset")
    level = fields.IntegerField(default=1)

    class Meta:
        table_name = "parity_accounts"


# --------------------------------------------------------------------------- #
# validate_<field> / validate(attrs)
# --------------------------------------------------------------------------- #


class HookedSerializer(ModelSerializer):
    class Meta:
        model = ParityAccount
        fields = ["id", "username", "nickname", "level"]

    def validate_username(self, value):
        if value.lower() in ("admin", "root"):
            raise ValidationError("This username is reserved")
        return value.lower()

    def validate(self, attrs):
        if attrs.get("nickname") == attrs.get("username"):
            raise ValidationError({"nickname": ["Must differ from the username"]})
        attrs["level"] = 5
        return attrs


def test_field_hook_transforms_the_value():
    ser = HookedSerializer(data={"username": "Alice"})
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["username"] == "alice"


def test_field_hook_rejects_with_a_field_error():
    ser = HookedSerializer(data={"username": "ADMIN"})
    assert not ser.is_valid()
    assert ser.errors == {"username": ["This username is reserved"]}
    with pytest.raises(ValidationError):
        HookedSerializer(data={"username": "root"}).is_valid(raise_exception=True)


def test_object_hook_sees_and_returns_attrs():
    ok = HookedSerializer(data={"username": "bob", "nickname": "bobby"})
    assert ok.is_valid(), ok.errors
    assert ok.validated_data["level"] == 5

    bad = HookedSerializer(data={"username": "bob", "nickname": "bob"})
    assert not bad.is_valid()
    assert bad.errors == {"nickname": ["Must differ from the username"]}


def test_hooks_run_only_for_fields_present_on_partial_update():
    ser = HookedSerializer(instance=object(), data={"nickname": "x"}, partial=True)
    assert ser.is_valid(), ser.errors
    assert "username" not in ser.validated_data


class AsyncHooked(ModelSerializer):
    class Meta:
        model = ParityAccount
        fields = ["id", "username"]

    async def validate_username(self, value):
        if value == "taken":
            raise ValidationError("This username is already registered")
        return value


async def test_async_hooks_run_under_ais_valid():
    assert await AsyncHooked(data={"username": "free"}).ais_valid()
    taken = AsyncHooked(data={"username": "taken"})
    assert not await taken.ais_valid()
    assert taken.errors == {"username": ["This username is already registered"]}


def test_async_hook_under_sync_is_valid_is_a_loud_error():
    with pytest.raises(TypeError, match="ais_valid"):
        AsyncHooked(data={"username": "x"}).is_valid()


def test_a_hook_bug_is_not_turned_into_a_validation_error():
    class Buggy(ModelSerializer):
        class Meta:
            model = ParityAccount
            fields = ["id", "username"]

        def validate_username(self, value):
            return value.nope  # AttributeError: a bug, not bad input

    with pytest.raises(AttributeError):
        Buggy(data={"username": "x"}).is_valid()


# --------------------------------------------------------------------------- #
# Meta.extra_kwargs
# --------------------------------------------------------------------------- #


class ExtraKwargsSerializer(ModelSerializer):
    class Meta:
        model = ParityAccount
        fields = ["id", "username", "nickname", "secret", "level"]
        extra_kwargs = {
            "secret": {"write_only": True, "help_text": "Never returned"},
            "level": {"read_only": True},
            "nickname": {"required": True, "max_length": 5},
            "username": {"help_text": "Login name"},
        }


def test_extra_kwargs_write_only_and_read_only():
    response = ExtraKwargsSerializer.ResponseSchema.model_fields
    request = ExtraKwargsSerializer.RequestSchema.model_fields
    assert "secret" not in response and "secret" in request
    assert "level" in response and "level" not in request


def test_extra_kwargs_required_and_constraints():
    missing = ExtraKwargsSerializer(data={"username": "a"})
    assert not missing.is_valid()
    assert "nickname" in missing.errors
    too_long = ExtraKwargsSerializer(data={"username": "a", "nickname": "toolong"})
    assert not too_long.is_valid()
    assert "nickname" in too_long.errors


def test_extra_kwargs_help_text_reaches_the_schema():
    props = ExtraKwargsSerializer.RequestSchema.model_json_schema()["properties"]
    assert props["username"]["description"] == "Login name"
    assert props["secret"]["description"] == "Never returned"


def test_unknown_extra_kwargs_warn():
    with pytest.warns(UserWarning, match="style"):

        class Odd(ModelSerializer):
            class Meta:
                model = ParityAccount
                fields = ["id", "username"]
                extra_kwargs = {"username": {"style": {"input_type": "text"}}}


# --------------------------------------------------------------------------- #
# DRF-style fields declared on a ModelSerializer
# --------------------------------------------------------------------------- #


class DeclaredSerializer(ModelSerializer):
    secret = CharField(write_only=True, max_length=8)
    shout = CharField(source="username", read_only=True)

    class Meta:
        model = ParityAccount
        fields = ["id", "username", "secret", "shout"]


def test_declared_fields_are_honoured():
    response = DeclaredSerializer.ResponseSchema.model_fields
    request = DeclaredSerializer.RequestSchema.model_fields
    assert "secret" not in response  # write_only now applies
    assert "secret" in request
    assert "shout" in response and "shout" not in request
    too_long = DeclaredSerializer(data={"username": "a", "secret": "123456789"})
    assert not too_long.is_valid()
    assert "secret" in too_long.errors


def test_declared_field_reads_its_source():
    account = ParityAccount(username="carol")
    assert DeclaredSerializer(instance=account).data["shout"] == "carol"


def test_writable_declared_field_is_saved_under_its_source():
    class Renamed(ModelSerializer):
        nick = CharField(source="nickname", required=False)

        class Meta:
            model = ParityAccount
            fields = ["id", "username", "nick"]

    ser = Renamed(data={"username": "eve", "nick": "evie"})
    assert ser.is_valid(), ser.errors
    assert ser.validated_data == {"username": "eve", "nickname": "evie"}
    assert Renamed(instance=ParityAccount(username="eve", nickname="evie")).data["nick"] == "evie"


def test_a_method_field_may_return_a_model_instance():
    """Model instances are awaitable in the ORM (they resolve to themselves);
    a get_<field> returning one returns a value, not a coroutine."""
    import inspect

    class AwaitableThing(Model):
        name = fields.CharField(max_length=10)

        class Meta:
            table_name = "awaitable_things"

        def __await__(self):
            return self
            yield  # pragma: no cover - makes this a generator

    thing = AwaitableThing(name="t")
    assert inspect.isawaitable(thing)

    class WithThing(ModelSerializer):
        thing = SerializerMethodField(return_type=object)

        class Meta:
            model = ParityAccount
            fields = ["id", "thing"]

        def get_thing(self, obj):
            return thing

    assert WithThing(instance=ParityAccount(username="fay")).data["thing"] is thing


def test_declared_field_missing_from_meta_fields_is_refused():
    with pytest.raises(ImproperlyConfigured, match="secret"):

        class Unlisted(ModelSerializer):
            secret = CharField(write_only=True)

            class Meta:
                model = ParityAccount
                fields = ["id", "username"]


def test_all_fields_include_declared_ones():
    class Everything(ModelSerializer):
        loud = SerializerMethodField()

        class Meta:
            model = ParityAccount
            fields = "__all__"

        def get_loud(self, obj):
            return obj.username.upper()

    assert "loud" in Everything.ResponseSchema.model_fields


# --------------------------------------------------------------------------- #
# Inheritance and error paths
# --------------------------------------------------------------------------- #


def test_subclass_inherits_declared_fields():
    class Base(ModelSerializer):
        label = SerializerMethodField()

        class Meta:
            model = ParityAccount
            fields = ["id", "username", "label"]

        def get_label(self, obj):
            return f"#{obj.username}"

    class Child(Base):
        class Meta(Base.Meta):
            pass

    assert "label" in Child._declared_fields
    assert Child(instance=ParityAccount(username="dan")).data["label"] == "#dan"


def test_errors_keep_the_full_field_path():
    class Address(BaseModel):
        zip: int

    class Order(Serializer):
        class Schema(BaseModel):
            address: Address
            items: list[int]

    Order.RequestSchema = Order.Schema
    ser = Order(data={"address": {"zip": "abc"}, "items": [1, "x"]})
    assert not ser.is_valid()
    assert set(ser.errors) == {"address.zip", "items.1"}


def test_the_dead_serializers_base_module_is_gone():
    import importlib.util

    assert importlib.util.find_spec("zeeb_api.serializers.base") is None


# --------------------------------------------------------------------------- #
# End to end: the viewsets run the (async) hooks
# --------------------------------------------------------------------------- #


class AccountViewSet(ModelViewSet):
    queryset = ParityAccount.objects
    serializer_class = AsyncHooked


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    ParityAccount._sa_table = None
    ParityAccount._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    ParityAccount._get_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    table = metadata.tables.get(ParityAccount._meta.db_table)
    if table is not None:
        metadata.remove(table)
    ParityAccount._sa_table = None
    ParityAccount._sa_model = None
    Settings.reset()


async def test_viewset_create_runs_async_hooks(db):
    router = SimpleRouter()
    router.register("accounts", AccountViewSet)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/accounts", json={"username": "fresh"})
        taken = await client.post("/accounts", json={"username": "taken"})
    assert ok.status_code == 201, ok.text
    assert taken.status_code == 400, taken.text
    assert taken.json()["error"]["details"][0]["field"] == "username"
