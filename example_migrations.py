#!/usr/bin/env python3
"""
Migrations Example - Zeeb ORM

Demonstrates:
- Initializing migrations directory
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

Zeeb ORM uses Alembic for migrations with Django-like commands.

SETUP
-----
First, initialize the migrations directory:

    $ zeeb init

This creates:
    migrations/
    ├── versions/        # Migration files go here
    ├── env.py          # Alembic environment (auto-configured)
    └── script.py.mako  # Migration template


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

    $ zeeb makemigrations -m "create users table"

    This auto-detects model changes and generates:
    migrations/versions/abc123_create_users_table.py


4. REVIEW MIGRATION (optional):

    The generated file looks like:

    def upgrade():
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('email', sa.String(254), nullable=False, unique=True),
        )

    def downgrade():
        op.drop_table('users')


5. APPLY MIGRATIONS:

    $ zeeb migrate

    Or migrate to specific revision:
    $ zeeb migrate abc123


6. CHECK STATUS:

    $ zeeb showmigrations

    Output:
      [X] abc123 - create users table
      [ ] def456 - add user profile

    $ zeeb current
    Current revision: abc123


7. ROLLBACK (if needed):

    $ zeeb rollback        # One step back
    $ zeeb rollback -1     # One step back (explicit)
    $ zeeb rollback base   # All the way back


CLI COMMANDS
------------

    zeeb init                    Initialize migrations directory
    zeeb makemigrations -m MSG   Create new migration
    zeeb makemigrations --empty  Create empty migration (for manual edits)
    zeeb migrate                 Apply all pending migrations
    zeeb migrate REVISION        Migrate to specific revision
    zeeb migrate --sql           Output SQL without executing
    zeeb rollback                Roll back one migration
    zeeb rollback REVISION       Roll back to specific revision
    zeeb showmigrations          Show all migrations and status
    zeeb current                 Show current revision


EXAMPLE SESSION
---------------

    $ cd myproject
    
    $ zeeb init
    Created migrations directory: migrations
    
    $ zeeb makemigrations -m "initial"
    Created new migration: a1b2c3d4e5f6
    
    $ zeeb migrate
    Migrated to: head
    
    $ zeeb showmigrations
    Migrations:
    --------------------------------------------------
      [X] a1b2c3d4e5f6 - initial
    
    # ... make model changes ...
    
    $ zeeb makemigrations -m "add user profile"
    Created new migration: g7h8i9j0k1l2
    
    $ zeeb migrate
    Migrated to: head
    
    $ zeeb showmigrations
    Migrations:
    --------------------------------------------------
      [X] a1b2c3d4e5f6 - initial
      [X] g7h8i9j0k1l2 - add user profile


PROGRAMMATIC USAGE
------------------

You can also use migrations programmatically:

    from zeeb_orm.migrations import (
        init_migrations,
        makemigrations,
        migrate,
        rollback,
        showmigrations,
        current,
    )

    # Initialize
    init_migrations('migrations')

    # Create migration
    revision = makemigrations(message='add new model')

    # Apply
    migrate()

    # Check status
    showmigrations()


TIPS
----

1. Always review generated migrations before applying
2. Keep migrations in version control
3. Don't modify migrations after they're applied to production
4. Use --sql flag to preview SQL before applying
5. Create manual migrations with --empty for complex changes
6. Use meaningful migration messages


For more details, see the README.md or Alembic documentation.
""")

# Demonstrate programmatic migration functions (info only)
if __name__ == "__main__":
    from zeeb_orm.migrations import (
        init_migrations,
        makemigrations,
        migrate,
        rollback,
        showmigrations,
        current,
    )

    print("\nProgrammatic functions available:")
    print(f"  - init_migrations(directory)")
    print(f"  - makemigrations(message, autogenerate, empty)")
    print(f"  - migrate(revision, sql)")
    print(f"  - rollback(revision, sql)")
    print(f"  - showmigrations()")
    print(f"  - current()")
    print("\n✓ See code examples above for usage!")
