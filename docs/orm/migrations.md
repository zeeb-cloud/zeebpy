# Migrations

Zeeb uses Alembic for database migrations, providing Django-like commands.

## Overview

Migrations track changes to your models and apply them to the database. The workflow is:

1. **Make changes** to your models
2. **Generate migration** with `makemigrations`
3. **Apply migration** with `migrate`

## Getting Started

### Initialize Migrations

For a new project, initialize the migrations directory:

```bash
python manage.py init
```

This creates:
```
migrations/
├── versions/       # Migration files
├── env.py          # Alembic environment
├── script.py.mako  # Migration template
└── alembic.ini     # Alembic config
```

### Create First Migration

After defining models, generate the initial migration:

```bash
python manage.py makemigrations
```

Output:
```
Checking migrations for installed apps:
  - apps.blog
  - apps.users
  - zeeb_auth

Generating migration...
Created: migrations/versions/001_auto_20240115_1234.py

Operations:
  - Create table: authors
  - Create table: posts
  - Create index: ix_posts_slug
```

### Apply Migrations

```bash
python manage.py migrate
```

Output:
```
Running migrations...
  Applying 001_auto_20240115_1234... OK

Current revision: 001_auto_20240115_1234
```

## Commands

### makemigrations

Generate migrations from model changes.

```bash
# Generate for all apps
python manage.py makemigrations

# Generate with custom message
python manage.py makemigrations -m "add user profile"

# Generate empty migration (for manual edits)
python manage.py makemigrations --empty

# Preview SQL without creating migration
python manage.py makemigrations --sql
```

**Options:**
| Option | Description |
|--------|-------------|
| `-m, --message` | Migration message/description |
| `--empty` | Create empty migration |
| `--sql` | Show SQL that would be generated |
| `--autogenerate` | Auto-detect model changes (default) |

### migrate

Apply migrations to the database.

```bash
# Apply all pending migrations
python manage.py migrate

# Apply to specific revision
python manage.py migrate 001

# Show SQL without applying
python manage.py migrate --sql

# Apply to specific database
python manage.py migrate --database=replica
```

**Options:**
| Option | Description |
|--------|-------------|
| `revision` | Target revision (optional) |
| `--sql` | Show SQL without applying |
| `--database` | Database alias to migrate |

### showmigrations

Display migration status.

```bash
python manage.py showmigrations
```

Output:
```
Migration History:
  [X] 001_auto_20240115_1234 (applied: 2024-01-15 12:34:56)
  [X] 002_add_user_profile (applied: 2024-01-16 09:00:00)
  [ ] 003_add_comments (pending)

Current: 002_add_user_profile
```

**Status indicators:**
- `[X]` - Applied
- `[ ]` - Pending

### rollback

Revert migrations.

```bash
# Rollback one migration
python manage.py rollback

# Rollback to specific revision
python manage.py rollback 001

# Rollback all (dangerous!)
python manage.py rollback base
```

## How Migrations Work

### INSTALLED_APPS

Only models from `INSTALLED_APPS` are included in migrations:

```python
# settings.py
INSTALLED_APPS = [
    "apps.blog",
    "apps.users",
    "apps.orders",
]
```

The `zeeb_auth` app (providing User, Permission models) is automatically included.

### Migration Files

Migrations are stored in `migrations/versions/`:

```python
# migrations/versions/001_auto_20240115_1234.py
"""add posts table

Revision ID: 001
Create Date: 2024-01-15 12:34:56
"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'posts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_posts_title', 'posts', ['title'])


def downgrade():
    op.drop_index('ix_posts_title')
    op.drop_table('posts')
```

### Auto-detection

Zeeb auto-detects these changes:

- **Tables**: Create, drop, rename
- **Columns**: Add, drop, alter type, rename
- **Indexes**: Create, drop
- **Constraints**: Primary key, foreign key, unique, check
- **Defaults**: Add, change, remove

## Common Workflows

### Adding a Field

```python
# 1. Add field to model
class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    views = fields.IntegerField(default=0)  # New field
```

```bash
# 2. Generate migration
python manage.py makemigrations -m "add views to posts"

# 3. Apply
python manage.py migrate
```

### Removing a Field

```python
# 1. Remove field from model
class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    # views field removed
```

```bash
# 2. Generate migration
python manage.py makemigrations -m "remove views from posts"

# 3. Apply
python manage.py migrate
```

### Renaming a Field

Zeeb can't auto-detect renames. Use an empty migration:

```bash
# 1. Generate empty migration
python manage.py makemigrations --empty -m "rename title to headline"
```

```python
# 2. Edit migration manually
def upgrade():
    op.alter_column('posts', 'title', new_column_name='headline')

def downgrade():
    op.alter_column('posts', 'headline', new_column_name='title')
```

```bash
# 3. Update model
# 4. Apply
python manage.py migrate
```

### Adding a Relationship

```python
# 1. Add ForeignKey
class Post(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(
        "Author",
        on_delete="CASCADE",
        null=True,  # Allow null for existing rows
    )
```

```bash
# 2. Generate and apply
python manage.py makemigrations -m "add author to posts"
python manage.py migrate
```

### Data Migrations

For data transformations, create an empty migration:

```bash
python manage.py makemigrations --empty -m "populate slug from title"
```

```python
# Edit migration
from alembic import op
from sqlalchemy import text

def upgrade():
    # Populate slugs for existing posts
    connection = op.get_bind()
    connection.execute(text("""
        UPDATE posts 
        SET slug = LOWER(REPLACE(title, ' ', '-'))
        WHERE slug IS NULL
    """))

def downgrade():
    pass  # Data migration - no downgrade
```

### Squashing Migrations

Combine multiple migrations into one:

```bash
# Not directly supported - manually:
# 1. Ensure all migrations are applied in production
# 2. Delete migration files (keep first and last)
# 3. Reset migration history in database
# 4. Generate fresh migration from current state
```

## Working with Multiple Databases

### Configure Databases

```python
# settings.py
DATABASES = {
    "default": {
        "url": "postgresql+asyncpg://localhost/myapp",
    },
    "replica": {
        "url": "postgresql+asyncpg://localhost/myapp_replica",
    },
}
```

### Migrate Specific Database

```bash
# Migrate default
python manage.py migrate

# Migrate replica
python manage.py migrate --database=replica
```

## Troubleshooting

### Migration Conflicts

When multiple developers create migrations:

```
ERROR: Multiple heads detected: 002_add_users, 002_add_posts
```

Resolution:

```bash
# Create merge migration
python manage.py makemigrations --merge

# Or manually set down_revision in one migration
# down_revision = '002_add_users'
```

### Schema Mismatch

When database differs from migrations:

```bash
# Check current state
python manage.py showmigrations

# Force to specific revision (dangerous!)
# Edit alembic_version table directly
```

### Pending Migrations Error

```
ERROR: Unapplied migrations detected. Run 'python manage.py migrate'
```

This appears when starting the server with pending migrations. Apply them:

```bash
python manage.py migrate
```

Or disable the check in settings:

```python
# settings.py
CHECK_MIGRATIONS_ON_STARTUP = False
```

## Best Practices

### 1. Always Review Generated Migrations

```bash
# Generate
python manage.py makemigrations

# Review the file before applying
cat migrations/versions/003_*.py
```

### 2. Use Descriptive Messages

```bash
# Good
python manage.py makemigrations -m "add email verification fields to users"

# Bad
python manage.py makemigrations -m "update"
```

### 3. Test Migrations

```bash
# Apply
python manage.py migrate

# Rollback
python manage.py rollback

# Apply again
python manage.py migrate
```

### 4. Keep Migrations Small

- One logical change per migration
- Easier to review and rollback

### 5. Don't Edit Applied Migrations

Once a migration is applied in production, don't modify it. Create a new migration instead.

### 6. Commit Migrations to Version Control

Migrations are part of your codebase. Commit them with the model changes.

## Next Steps

- [Models](models.md) - Define models
- [Fields](fields.md) - Field types
- [CLI Commands](../cli/commands.md) - All commands
