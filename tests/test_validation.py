"""Tests for model/field validation: Field.validate, validators,
full_clean and save/create enforcement."""

import pytest

from zeeb_orm import (
    Model,
    ValidationError,
    close_all_connections,
    configure,
    fields,
    setup_database,
    validators,
)

# Test Models


class ValProduct(Model):
    name = fields.CharField(max_length=10)
    status = fields.CharField(
        max_length=20,
        choices=[("draft", "Draft"), ("live", "Live")],
        default="draft",
    )
    stock = fields.PositiveIntegerField(default=0)
    note = fields.CharField(max_length=100, null=True)

    class Meta:
        table_name = "val_products"


class ValCleanProduct(ValProduct):
    """Adds a custom async clean() hook."""

    class Meta:
        table_name = "val_clean_products"

    async def clean(self):
        if self.name == "forbidden":
            raise ValidationError({"name": "This name is forbidden."})


MODELS = (ValProduct, ValCleanProduct)


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


# Field.validate (no DB)


class TestFieldValidate:
    def test_null_false_rejects_none(self):
        field = fields.CharField(max_length=10)
        with pytest.raises(ValidationError) as exc:
            field.validate(None)
        assert "null" in str(exc.value)

    def test_null_true_accepts_none(self):
        fields.CharField(max_length=10, null=True).validate(None)

    def test_none_skipped_for_primary_key(self):
        fields.UUIDAutoField().validate(None)

    def test_none_skipped_for_auto_now_fields(self):
        fields.DateTimeField(auto_now_add=True).validate(None)
        fields.DateTimeField(auto_now=True).validate(None)

    def test_none_skipped_for_fields_with_default(self):
        # Value is filled at insert time — None must not fail validation.
        fields.IntegerField(default=7).validate(None)

    def test_choices_accept_valid(self):
        field = fields.CharField(max_length=10, choices=[("a", "A"), ("b", "B")])
        field.validate("a")

    def test_choices_reject_invalid(self):
        field = fields.CharField(max_length=10, choices=[("a", "A"), ("b", "B")])
        with pytest.raises(ValidationError) as exc:
            field.validate("c")
        assert "valid choice" in str(exc.value)

    def test_charfield_max_length(self):
        field = fields.CharField(max_length=3)
        field.validate("abc")
        with pytest.raises(ValidationError):
            field.validate("abcd")

    def test_email_field_default_validator(self):
        field = fields.EmailField()
        field.validate("user@example.com")
        with pytest.raises(ValidationError):
            field.validate("not-an-email")

    def test_url_field_default_validator(self):
        field = fields.URLField()
        field.validate("https://example.com/path")
        with pytest.raises(ValidationError):
            field.validate("nope")

    def test_slug_field_default_validator(self):
        field = fields.SlugField()
        field.validate("a-valid_slug-123")
        with pytest.raises(ValidationError):
            field.validate("not a slug!")

    def test_positive_integer_fields(self):
        for cls in (
            fields.PositiveIntegerField,
            fields.PositiveSmallIntegerField,
            fields.PositiveBigIntegerField,
        ):
            field = cls()
            field.validate(0)
            field.validate(10)
            with pytest.raises(ValidationError):
                field.validate(-1)

    def test_custom_validators_run(self):
        field = fields.IntegerField(
            validators=[validators.MaxValueValidator(100)]
        )
        field.validate(100)
        with pytest.raises(ValidationError):
            field.validate(101)

    def test_multiple_errors_collected(self):
        field = fields.CharField(
            max_length=3,
            choices=[("ok", "OK")],
        )
        with pytest.raises(ValidationError) as exc:
            field.validate("toolong")
        # Both the choice error and the max_length error are reported
        assert len(exc.value.messages) == 2


# Validators (no DB)


class TestValidators:
    def test_min_value(self):
        validators.MinValueValidator(5)(5)
        with pytest.raises(ValidationError):
            validators.MinValueValidator(5)(4)

    def test_max_value(self):
        validators.MaxValueValidator(5)(5)
        with pytest.raises(ValidationError):
            validators.MaxValueValidator(5)(6)

    def test_min_length(self):
        validators.MinLengthValidator(3)("abc")
        with pytest.raises(ValidationError):
            validators.MinLengthValidator(3)("ab")

    def test_max_length(self):
        validators.MaxLengthValidator(3)("abc")
        with pytest.raises(ValidationError):
            validators.MaxLengthValidator(3)("abcd")

    def test_regex_validator(self):
        v = validators.RegexValidator(r"^\d+$")
        v("12345")
        with pytest.raises(ValidationError):
            v("12a45")

    def test_regex_validator_inverse_match(self):
        v = validators.RegexValidator(r"forbidden", inverse_match=True)
        v("allowed text")
        with pytest.raises(ValidationError):
            v("forbidden text")

    def test_email_validator(self):
        v = validators.EmailValidator()
        v("a@b.co")
        for bad in ("plain", "a@b", "a b@c.de", "@no-local.com"):
            with pytest.raises(ValidationError):
                v(bad)

    def test_validate_ipv46_address(self):
        validators.validate_ipv46_address("127.0.0.1")
        validators.validate_ipv46_address("::1")
        validators.validate_ipv46_address("2001:db8::8a2e:370:7334")
        with pytest.raises(ValidationError):
            validators.validate_ipv46_address("999.0.0.1")
        with pytest.raises(ValidationError):
            validators.validate_ipv46_address("not-an-ip")

    def test_validate_ipv4_and_ipv6(self):
        validators.validate_ipv4_address("10.0.0.1")
        with pytest.raises(ValidationError):
            validators.validate_ipv4_address("::1")
        validators.validate_ipv6_address("::1")
        with pytest.raises(ValidationError):
            validators.validate_ipv6_address("10.0.0.1")

    def test_validation_error_message_and_code(self):
        with pytest.raises(ValidationError) as exc:
            validators.MinValueValidator(0)(-1)
        assert exc.value.code == "min_value"
        assert "greater than or equal to 0" in exc.value.messages[0]


# full_clean / clean_fields / clean


class TestFullClean:
    @pytest.mark.asyncio
    async def test_full_clean_passes_for_valid_instance(self, db):
        product = ValProduct(name="ok", status="live")
        await product.full_clean()

    @pytest.mark.asyncio
    async def test_clean_fields_collects_per_field_errors(self, db):
        product = ValProduct(name="waytoolongname", status="bogus", stock=-5)
        with pytest.raises(ValidationError) as exc:
            product.clean_fields()
        errors = exc.value.message_dict
        assert set(errors) == {"name", "status", "stock"}

    @pytest.mark.asyncio
    async def test_clean_fields_exclude(self, db):
        product = ValProduct(name="waytoolongname", status="live")
        product.clean_fields(exclude=["name"])  # no error

    @pytest.mark.asyncio
    async def test_full_clean_merges_clean_hook_errors(self, db):
        product = ValCleanProduct(name="forbidden", status="bogus")
        with pytest.raises(ValidationError) as exc:
            await product.full_clean()
        errors = exc.value.message_dict
        assert "This name is forbidden." in errors["name"]
        assert "status" in errors

    @pytest.mark.asyncio
    async def test_full_clean_exclude(self, db):
        product = ValProduct(name="waytoolongname", status="live")
        await product.full_clean(exclude=["name"])


# Enforcement on save()/create()/bulk_create()


class TestSaveValidation:
    @pytest.mark.asyncio
    async def test_create_rejects_invalid_choice(self, db):
        with pytest.raises(ValidationError):
            await ValProduct.objects.create(name="ok", status="bogus")
        assert await ValProduct.objects.count() == 0

    @pytest.mark.asyncio
    async def test_create_opt_out(self, db):
        product = await ValProduct.objects.create(
            name="ok", status="bogus", validate=False
        )
        assert product.pk is not None
        assert await ValProduct.objects.count() == 1

    @pytest.mark.asyncio
    async def test_save_rejects_max_length_violation(self, db):
        product = ValProduct(name="waytoolongname")
        with pytest.raises(ValidationError):
            await product.save()
        assert await ValProduct.objects.count() == 0

    @pytest.mark.asyncio
    async def test_save_opt_out(self, db):
        product = ValProduct(name="waytoolongname")
        await product.save(validate=False)  # SQLite ignores VARCHAR length
        assert product.pk is not None

    @pytest.mark.asyncio
    async def test_save_null_enforcement(self, db):
        product = ValProduct()  # name is null=False without default
        with pytest.raises(ValidationError) as exc:
            await product.save()
        assert "name" in exc.value.message_dict

    @pytest.mark.asyncio
    async def test_fields_with_defaults_pass_validation(self, db):
        # status/stock have defaults; note is nullable; pk is auto
        product = await ValProduct.objects.create(name="ok")
        assert product.status == "draft"
        assert product.stock == 0

    @pytest.mark.asyncio
    async def test_save_update_fields_excludes_other_fields(self, db):
        product = await ValProduct.objects.create(name="ok", status="live")
        product.name = "waytoolongname"  # invalid, but not being updated
        product.stock = 5
        await product.save(update_fields=["stock"])  # must not raise

        fetched = await ValProduct.objects.get(pk=product.pk)
        assert fetched.stock == 5
        assert fetched.name == "ok"  # name was not written

    @pytest.mark.asyncio
    async def test_save_update_fields_validates_updated_fields(self, db):
        product = await ValProduct.objects.create(name="ok")
        product.name = "waytoolongname"
        with pytest.raises(ValidationError):
            await product.save(update_fields=["name"])

    @pytest.mark.asyncio
    async def test_bulk_create_skips_validation_by_default(self, db):
        objs = [ValProduct(name="waytoolongname", status="bogus")]
        created = await ValProduct.objects.bulk_create(objs)
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_bulk_create_opt_in_validation(self, db):
        objs = [ValProduct(name="ok"), ValProduct(name="waytoolongname")]
        with pytest.raises(ValidationError):
            await ValProduct.objects.bulk_create(objs, validate=True)
        # Validation runs before ANY insert
        assert await ValProduct.objects.count() == 0
