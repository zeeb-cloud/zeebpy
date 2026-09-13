#!/usr/bin/env python3
"""
Migrations Example - Zeeb ORM

Demonstrates:
- Creating migrations
- Applying migrations
- Migration CLI commands

This example shows the workflow but doesn't execute migrations
(since we're using in-memory SQLite for demos).

Run: python example_migrations.py
"""

print("""
=" * 60
Zeeb ORM - Migrations Example
=" * 60

Zeeb ORM provides Django-style migrations. No Alembic config files,
no revision hashes — just clean numbered migration files.

WORKFLOW
--------

1. DEFINE YOUR MODELS (in your app):

    from zeeb_orm import Model, fields

    class User(Model):
        name = fields.CharField(max_length=100)
        email = fields.EmailField(unique=True)

        class Meta:
            table_name = 'users'


2. CONFIGURE DATABASE (in your app or settings):

    from zeeb_orm import configure

    configure(database={
        'url': 'postgresql+asyncpg://user:pass@localhost/mydb'
    })


3. CREATE MIGRATION:

    $ python manage.py makemigrations

    This auto-detects model changes and generates:
    migrations/0001_initial.py

    The file looks like:

    import sqlalchemy as sa
    from zeeb_orm.migrations import Migration, operations

    class Migration(Migration):
        initial = True
        dependencies = []
        operations = [
            operations.CreateModel(
                name='User',
                table='users',
                columns=[
                    sa.Column('id', sa.Uuid(), nullable=False),
                    sa.Column('name', sa.String(100), nullable=False),
                    sa.Column('email', sa.String(254), nullable=False, unique=True),
                ],
                primary_key=['id'],
            ),
        ]


4. APPLY MIGRATIONS:

    $ python manage.py migrate

    Running migrations:
      Applying 0001_initial... OK


5. CHECK STATUS:

    $ python manage.py showmigrations

     [X] 0001_initial
     [ ] 0002_add_views_to_posts


6. ROLLBACK (if needed):

    $ python manage.py migrate --rollback 1    # Roll back 1
    $ python manage.py migrate zero             # Roll back all


CLI COMMANDS
------------

    python manage.py makemigrations              Create new migration
    python manage.py makemigrations -n MSG       With custom name
    python manage.py makemigrations --empty      Empty migration (for manual edits)
    python manage.py migrate                     Apply all pending
    python manage.py migrate 0001               Migrate to specific migration
    python manage.py migrate zero               Roll back all
    python manage.py migrate --rollback 1       Roll back N migrations
    python manage.py showmigrations             Show status


PROGRAMMATIC USAGE
------------------

    from zeeb_orm.migrations import (
        makemigrations,
        migrate,
        rollback,
        showmigrations,
        current,
    )

    # Create migration
    name = makemigrations(message='add new model')

    # Apply
    migrate()

    # Check status
    showmigrations()


TIPS
----

1. No 'init' step needed — makemigrations creates the directory
2. Always review generated migrations before applying
3. Keep migrations in version control
4. Don't modify migrations after they're applied to production
5. Create manual migrations with --empty for complex changes
6. Use meaningful migration names
""")

# Demonstrate programmatic migration functions (info only)
if __name__ == "__main__":
    from zeeb_orm.migrations import (
        makemigrations,
        migrate,
        rollback,
        showmigrations,
        current,
    )

    print("\nProgrammatic functions available:")
    print(f"  - makemigrations(message, autogenerate, empty)")
    print(f"  - migrate(target, fake)")
    print(f"  - rollback(steps)")
    print(f"  - showmigrations()")
    print(f"  - current()")
    print("\n✓ See code examples above for usage!")
