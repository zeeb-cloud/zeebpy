"""
Zeeb ORM - A Django-like ORM wrapper built on SQLAlchemy and Alembic.

Usage:
    from zeeb_orm import Model, fields, Q, F
    from zeeb_orm.db import Database, atomic

    class User(Model):
        name = fields.CharField(max_length=100)
        email = fields.EmailField(unique=True)
        age = fields.IntegerField(null=True)

        class Meta:
            table_name = 'users'
            ordering = ['-created_at']

    # Configure database
    db = await setup_database('postgresql+asyncpg://localhost/mydb')

    # Queries
    users = await User.objects.filter(age__gte=18).order_by('name')
    user = await User.objects.get(email='test@example.com')

    # Complex queries with Q
    results = await User.objects.filter(
        Q(age__gte=18) & (Q(name__startswith='A') | Q(name__startswith='B'))
    )

    # CRUD
    user = await User.objects.create(name='John', email='john@example.com')
    await User.objects.filter(age__lt=18).update(is_minor=True)
"""

from zeeb_orm.conf.settings import Settings, configure, get_settings
from zeeb_orm.exceptions import (
    NON_FIELD_ERRORS,
    FieldDoesNotExist,
    FieldError,
    NotSupportedError,
    ProtectedError,
    RestrictedError,
    ValidationError,
)
from zeeb_orm.db.connection import (
    Database,
    atomic,
    close_all_connections,
    get_connection,
    register_database,
    setup_database,
)
from zeeb_orm.migrations.state import (
    MigrationError,
    MigrationState,
    check_migrations_applied,
    get_migration_state,
    require_migrations,
)
from zeeb_orm.models.base import (
    DoesNotExist,
    Model,
    MultipleObjectsReturned,
    metadata,
)
from zeeb_orm.models.relations import RelationInfo, resolve_relation
from zeeb_orm.models.fields import (
    AutoField,
    BigAutoField,
    BigIntegerField,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    EmailField,
    Field,
    FloatField,
    ForeignKey,
    ForeignKeyField,
    IntegerField,
    JSONField,
    ManyToMany,
    ManyToManyField,
    OneToOne,
    OneToOneField,
    PositiveIntegerField,
    SlugField,
    SmallIntegerField,
    TextField,
    TimeField,
    URLField,
    UUIDAutoField,
    UUIDField,
)
from zeeb_orm.models.manager import Manager
from zeeb_orm.models.options import CheckConstraint, Index, Options, UniqueConstraint
from zeeb_orm.query.expressions import (
    # Base
    Aggregate,
    Expression,
    F,
    Value,
    # Aggregates
    Avg,
    Count,
    Max,
    Min,
    StdDev,
    Sum,
    Variance,
    # Conditional
    Case,
    Coalesce,
    When,
    # Subqueries
    Exists,
    OuterRef,
    Subquery,
    # String functions
    Concat,
    Length,
    Lower,
    Substr,
    Trim,
    Upper,
    # Date/time functions
    Extract,
    Now,
    TruncDate,
    TruncMonth,
    TruncYear,
    # Math functions
    Abs,
    Ceil,
    Floor,
    Greatest,
    Least,
    Round,
)
from zeeb_orm.query.q import Q
from zeeb_orm.query.queryset import Prefetch, QuerySet
from zeeb_orm.signals import (
    Signal,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
    receiver,
)

__version__ = "0.1.0"

__all__ = [
    # Version
    "__version__",
    # Core
    "Model",
    "Manager",
    "QuerySet",
    "metadata",
    "RelationInfo",
    "resolve_relation",
    # Exceptions
    "NON_FIELD_ERRORS",
    "ValidationError",
    "NotSupportedError",
    "ProtectedError",
    "RestrictedError",
    "FieldDoesNotExist",
    "FieldError",
    "DoesNotExist",
    "MultipleObjectsReturned",
    # Fields
    "Field",
    "AutoField",
    "BigAutoField",
    "UUIDAutoField",
    "CharField",
    "TextField",
    "EmailField",
    "SlugField",
    "URLField",
    "IntegerField",
    "SmallIntegerField",
    "BigIntegerField",
    "PositiveIntegerField",
    "FloatField",
    "DecimalField",
    "BooleanField",
    "DateField",
    "TimeField",
    "DateTimeField",
    "JSONField",
    "UUIDField",
    "ForeignKey",
    "ForeignKeyField",
    "OneToOne",
    "OneToOneField",
    "ManyToMany",
    "ManyToManyField",
    # Query
    "Q",
    "F",
    "Prefetch",
    # Expressions & Aggregates
    "Expression",
    "Value",
    "Aggregate",
    "Count",
    "Sum",
    "Avg",
    "Min",
    "Max",
    "StdDev",
    "Variance",
    "Coalesce",
    "Case",
    "When",
    # Subqueries
    "Subquery",
    "OuterRef",
    "Exists",
    # String functions
    "Concat",
    "Lower",
    "Upper",
    "Length",
    "Trim",
    "Substr",
    # Date/time functions
    "Now",
    "Extract",
    "TruncDate",
    "TruncMonth",
    "TruncYear",
    # Math functions
    "Abs",
    "Ceil",
    "Floor",
    "Round",
    "Greatest",
    "Least",
    # Options
    "Options",
    "Index",
    "UniqueConstraint",
    "CheckConstraint",
    # Database
    "Database",
    "setup_database",
    "get_connection",
    "register_database",
    "close_all_connections",
    "atomic",
    # Settings
    "Settings",
    "configure",
    "get_settings",
    # Migrations
    "MigrationError",
    "MigrationState",
    "check_migrations_applied",
    "get_migration_state",
    "require_migrations",
    # Signals
    "Signal",
    "receiver",
    "pre_save",
    "post_save",
    "pre_delete",
    "post_delete",
]

# Provide a fields namespace like Django
from zeeb_orm.models import fields
