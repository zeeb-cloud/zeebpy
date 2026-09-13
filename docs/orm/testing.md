# Testing code that uses the ORM

Mocking a queryset is the obvious way to test code that talks to the database,
and it hides exactly the bugs worth finding.

A stub whose `filter()` returns `self` agrees with every query you give it. A
missing tenant scope, a misspelled field, an inverted comparison and a correct
query all produce the same answer. A stub whose `update()` returns a fixed row
count is worse, because in a conditional update that row count *is* the
decision:

```python
# Admission: the row count says whether this caller got the last unit.
granted = await Quota.objects.filter(tenant_id=tid, used__lt=limit).update(used=F("used") + 1)
```

Against a stub, `granted` is whatever the stub was seeded with. The test passes
whether the predicate is right, wrong, or absent. Asserting on the call
arguments instead does not help — the assertion and the code under test then
share the same misunderstanding of what the query means.

`zeeb_orm.testing` gives you a real database cheaply enough that there is no
reason to mock one.

## Usage

```python
import pytest
from zeeb_orm.testing import temporary_database

from myapp.models import Tenant, UsageEvent


@pytest.fixture
async def db():
    async with temporary_database(Tenant, UsageEvent) as database:
        yield database


async def test_the_last_unit_is_granted_once(db):
    tenant = await Tenant.objects.create(name="Acme")
    await Quota.objects.create(tenant_id=tenant.id, used=9, ceiling=10)

    granted = await Quota.objects.filter(tenant_id=tenant.id, used__lt=10).update(used=10)
    refused = await Quota.objects.filter(tenant_id=tenant.id, used__lt=10).update(used=11)

    assert (granted, refused) == (1, 0)
```

Pass every model whose table is involved, including the targets of foreign
keys: only the tables you name are created, so a missing referent fails at DDL
time on a backend that enforces them.

The default database is in-memory SQLite. It needs no running service and costs
milliseconds, so a real-database test belongs in the ordinary suite rather than
a separate slow lane.

## Isolation

Each `temporary_database` block starts with empty tables and restores the global
ORM state on exit — the settings singleton, the connection registry, and the
tables it created. Two blocks in the same process do not see each other's rows,
so tests cannot become order-dependent.

Table *definitions* are deliberately left in the process-global metadata. They
are built once and reused: clearing them is what produces
`Table 'x' is already defined for this MetaData instance` on the second block,
and it also breaks suites that re-import a models module to isolate same-named
`apps` packages.

## Running against PostgreSQL

Set `ZEEB_TEST_DATABASE_URL` and the same tests run against a real server:

```bash
ZEEB_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/test pytest
```

Worth doing in CI, because SQLite will not reproduce:

- partial and expression indexes
- `ON CONFLICT` / upsert semantics
- advisory locks and `SELECT FOR UPDATE`
- genuine concurrent transactions and isolation levels

A test that depends on any of those should say so, rather than passing on
SQLite for the wrong reason:

```python
from zeeb_orm.testing import requires_postgres


@requires_postgres()
async def test_upsert_is_atomic(db):
    ...
```

## What this does not cover

Tables come from the model definitions, so anything only a migration creates
does not exist here: a raw `RunSQL` index, a check constraint, a trigger.

If a constraint is load-bearing, declare it on the model's `Meta` so it is part
of the schema this harness builds and the migration merely materialises it. A
constraint that lives only in a migration cannot be tested this way, and the
gap between "the model says unique" and "the database enforces unique" is a
real and expensive one — it is how a first-of-month race produces two rows that
a later `.get()` turns into `MultipleObjectsReturned`.
