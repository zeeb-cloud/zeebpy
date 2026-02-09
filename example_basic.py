#!/usr/bin/env python3
"""
Basic CRUD Example - Zeeb ORM

Demonstrates:
- Model definition
- Database setup
- Create, Read, Update, Delete operations
- Basic filtering and ordering

Run: python example_basic.py
"""

import asyncio
from zeeb_orm import Model, fields, configure, setup_database, close_all_connections


# Define Models

class User(Model):
    """User model with basic fields."""
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    age = fields.IntegerField(null=True)
    is_active = fields.BooleanField(default=True)
    bio = fields.TextField(null=True)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "users"
        ordering = ["-created_at"]


async def main():
    print("=" * 60)
    print("Zeeb ORM - Basic CRUD Example")
    print("=" * 60)

    # Setup database (SQLite in-memory for demo)
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")

    # Create tables
    User._sa_table = None  # Reset for fresh start
    User._sa_model = None
    User._get_table()
    await db.create_all()
    print("\n✓ Database initialized\n")

    # =========================================
    # CREATE
    # =========================================
    print("--- CREATE ---")

    # Create single user
    alice = await User.objects.create(
        name="Alice Smith",
        email="alice@example.com",
        age=28,
        bio="Software engineer"
    )
    print(f"Created: {alice} (id={alice.id})")

    # Create more users
    bob = await User.objects.create(name="Bob Jones", email="bob@example.com", age=35)
    charlie = await User.objects.create(name="Charlie Brown", email="charlie@example.com", age=22)
    diana = await User.objects.create(name="Diana Ross", email="diana@example.com", age=45, is_active=False)

    print(f"Created: {bob}, {charlie}, {diana}")

    # =========================================
    # READ
    # =========================================
    print("\n--- READ ---")

    # Get all users
    all_users = await User.objects.all()
    print(f"All users ({len(all_users)}):")
    for user in all_users:
        print(f"  - {user.name} ({user.email}), age={user.age}, active={user.is_active}")

    # Get single user by ID
    user = await User.objects.get(id=alice.id)
    print(f"\nGet by ID: {user.name}")

    # Get single user by email
    user = await User.objects.get(email="bob@example.com")
    print(f"Get by email: {user.name}")

    # Filter users
    active_users = await User.objects.filter(is_active=True)
    print(f"\nActive users: {[u.name for u in active_users]}")

    # Filter with lookups
    young_users = await User.objects.filter(age__lt=30)
    print(f"Users under 30: {[u.name for u in young_users]}")

    adults = await User.objects.filter(age__gte=25, age__lte=40)
    print(f"Users 25-40: {[u.name for u in adults]}")

    # First and last
    first = await User.objects.order_by("name").first()
    last = await User.objects.order_by("name").last()
    print(f"\nFirst by name: {first.name}, Last by name: {last.name}")

    # Count and exists
    count = await User.objects.count()
    print(f"\nTotal users: {count}")

    exists = await User.objects.filter(email="alice@example.com").exists()
    print(f"Alice exists: {exists}")

    # =========================================
    # UPDATE
    # =========================================
    print("\n--- UPDATE ---")

    # Update via model instance
    alice.age = 29
    alice.bio = "Senior software engineer"
    await alice.save()
    print(f"Updated Alice: age={alice.age}, bio={alice.bio}")

    # Bulk update
    updated = await User.objects.filter(age__lt=30).update(is_active=True)
    print(f"Bulk updated {updated} users to active")

    # =========================================
    # DELETE
    # =========================================
    print("\n--- DELETE ---")

    # Delete via model instance
    await diana.delete()
    print(f"Deleted Diana")

    # Delete via queryset
    # deleted = await User.objects.filter(is_active=False).delete()
    # print(f"Deleted {deleted} inactive users")

    # Final count
    final_count = await User.objects.count()
    print(f"\nFinal user count: {final_count}")

    # =========================================
    # GET_OR_CREATE / UPDATE_OR_CREATE
    # =========================================
    print("\n--- GET_OR_CREATE ---")

    user, created = await User.objects.get_or_create(
        email="eve@example.com",
        defaults={"name": "Eve Wilson", "age": 30}
    )
    print(f"get_or_create: {user.name}, created={created}")

    user, created = await User.objects.get_or_create(
        email="eve@example.com",
        defaults={"name": "Different Name", "age": 99}
    )
    print(f"get_or_create again: {user.name}, created={created}")

    user, created = await User.objects.update_or_create(
        email="eve@example.com",
        defaults={"name": "Eve W.", "age": 31}
    )
    print(f"update_or_create: {user.name}, age={user.age}, created={created}")

    # Cleanup
    await close_all_connections()
    print("\n✓ Done!")


if __name__ == "__main__":
    asyncio.run(main())
