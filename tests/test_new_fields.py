"""Tests for the new field types: BinaryField, DurationField,
GenericIPAddressField, PositiveSmallIntegerField, PositiveBigIntegerField."""

import datetime

import pytest

from zeeb_orm import (
    Model,
    ValidationError,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class NfRecord(Model):
    payload = fields.BinaryField(null=True)
    small_payload = fields.BinaryField(max_length=16, null=True)
    duration = fields.DurationField(null=True)
    ip = fields.GenericIPAddressField(null=True)
    ipv4_only = fields.GenericIPAddressField(protocol="IPv4", null=True)
    ipv6_only = fields.GenericIPAddressField(protocol="IPv6", null=True)
    small_count = fields.PositiveSmallIntegerField(default=0)
    big_count = fields.PositiveBigIntegerField(default=0)

    class Meta:
        table_name = "nf_records"


MODELS = (NfRecord,)


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


class TestBinaryField:
    @pytest.mark.asyncio
    async def test_round_trip(self, db):
        blob = b"\x00\x01\x02\xffhello"
        record = await NfRecord.objects.create(payload=blob)
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.payload == blob

    @pytest.mark.asyncio
    async def test_max_length_validation(self, db):
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(small_payload=b"x" * 17)
        record = await NfRecord.objects.create(small_payload=b"x" * 16)
        assert record.pk is not None

    def test_column_type(self):
        from sqlalchemy import LargeBinary

        field = fields.BinaryField(max_length=32)
        col_type = field.get_column_type()
        assert isinstance(col_type, LargeBinary)
        assert col_type.length == 32


class TestDurationField:
    @pytest.mark.asyncio
    async def test_round_trip(self, db):
        delta = datetime.timedelta(days=2, hours=3, minutes=4, seconds=5)
        record = await NfRecord.objects.create(duration=delta)
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.duration == delta

    @pytest.mark.asyncio
    async def test_round_trip_microseconds(self, db):
        delta = datetime.timedelta(seconds=1, microseconds=123456)
        record = await NfRecord.objects.create(duration=delta)
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.duration == delta

    @pytest.mark.asyncio
    async def test_negative_duration(self, db):
        delta = datetime.timedelta(hours=-5)
        record = await NfRecord.objects.create(duration=delta)
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.duration == delta


class TestGenericIPAddressField:
    @pytest.mark.asyncio
    async def test_round_trip_ipv4_and_ipv6(self, db):
        record = await NfRecord.objects.create(ip="192.168.0.1")
        assert (await NfRecord.objects.get(pk=record.pk)).ip == "192.168.0.1"

        record = await NfRecord.objects.create(ip="2001:db8::8a2e:370:7334")
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.ip == "2001:db8::8a2e:370:7334"

    @pytest.mark.asyncio
    async def test_invalid_address_rejected(self, db):
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(ip="not-an-ip")

    @pytest.mark.asyncio
    async def test_protocol_ipv4(self, db):
        await NfRecord.objects.create(ipv4_only="10.0.0.1")
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(ipv4_only="::1")

    @pytest.mark.asyncio
    async def test_protocol_ipv6(self, db):
        await NfRecord.objects.create(ipv6_only="::1")
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(ipv6_only="10.0.0.1")

    def test_invalid_protocol_rejected(self):
        with pytest.raises(ValueError):
            fields.GenericIPAddressField(protocol="IPvX")

    def test_column_type_is_string_39(self):
        from sqlalchemy import String

        col_type = fields.GenericIPAddressField().get_column_type()
        assert isinstance(col_type, String)
        assert col_type.length == 39


class TestPositiveIntegerVariants:
    @pytest.mark.asyncio
    async def test_round_trip(self, db):
        record = await NfRecord.objects.create(
            small_count=42, big_count=9_999_999_999
        )
        fetched = await NfRecord.objects.get(pk=record.pk)
        assert fetched.small_count == 42
        assert fetched.big_count == 9_999_999_999

    @pytest.mark.asyncio
    async def test_negative_values_rejected(self, db):
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(small_count=-1)
        with pytest.raises(ValidationError):
            await NfRecord.objects.create(big_count=-1)

    def test_column_types(self):
        from sqlalchemy import BigInteger, SmallInteger

        assert fields.PositiveSmallIntegerField().get_column_type() is SmallInteger
        assert fields.PositiveBigIntegerField().get_column_type() is BigInteger


class TestMigrationAutodetect:
    """Autodetect round-trip: the new field types survive
    makemigrations -> replay -> no further changes."""

    def test_autodetect_round_trip(self, db, tmp_path):
        from zeeb_orm.migrations.autodetector import detect_changes
        from zeeb_orm.migrations.operations import CreateModel
        from zeeb_orm.migrations.writer import write_migration

        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()

        # 1. Detect: our table should be reported as a new model
        ops = detect_changes(migrations_dir=str(mig_dir))
        create_ops = [
            o for o in ops if isinstance(o, CreateModel) and o.table == "nf_records"
        ]
        assert len(create_ops) == 1
        col_names = {c.name for c in create_ops[0].columns}
        assert {
            "payload",
            "small_payload",
            "duration",
            "ip",
            "small_count",
            "big_count",
        } <= col_names

        # 2. Write the migration and detect again: no changes for our table
        write_migration(
            mig_dir, operations=create_ops, name="initial", initial=True
        )
        ops_after = detect_changes(migrations_dir=str(mig_dir))
        assert not any(
            isinstance(o, CreateModel) and o.table == "nf_records"
            for o in ops_after
        )
