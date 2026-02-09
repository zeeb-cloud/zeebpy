#!/usr/bin/env python3
"""
Relationships Example - Zeeb ORM

Demonstrates:
- ForeignKey relationships
- Django-like FK access: post.author (lazy), post.author_id (raw ID)
- Reverse relations: user.posts.all()
- Creating related objects

Run: python example_relationships.py
"""

import asyncio
from zeeb_orm import (
    Model, fields,
    configure, setup_database, close_all_connections,
)
from zeeb_orm.models.base import metadata


# Define Models with Relationships

class Author(Model):
    """Author model."""
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    bio = fields.TextField(null=True)

    class Meta:
        table_name = "authors"
    
    def __str__(self) -> str:
        return self.name


class Book(Model):
    """Book model with ForeignKey to Author."""
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(Author, related_name="books")
    published_year = fields.IntegerField()
    genre = fields.CharField(max_length=50)
    price = fields.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        table_name = "books"

    def __str__(self) -> str:
        return self.title


class Review(Model):
    """Review model with ForeignKey to Book."""
    book = fields.ForeignKey(Book, related_name="reviews")
    reviewer_name = fields.CharField(max_length=100)
    rating = fields.IntegerField()  # 1-5
    comment = fields.TextField(null=True)

    class Meta:
        table_name = "reviews"


async def setup_data(db):
    """Create test data."""
    # Reset tables
    Author._sa_table = None
    Author._sa_model = None
    Book._sa_table = None
    Book._sa_model = None
    Review._sa_table = None
    Review._sa_model = None
    metadata.clear()

    # Register tables
    Author._get_table()
    Book._get_table()
    Review._get_table()

    await db.create_all()
    print("✓ Tables created\n")

    # Create authors
    tolkien = await Author.objects.create(
        name="J.R.R. Tolkien",
        email="tolkien@example.com",
        bio="English writer and philologist"
    )
    rowling = await Author.objects.create(
        name="J.K. Rowling",
        email="rowling@example.com",
        bio="British author"
    )
    martin = await Author.objects.create(
        name="George R.R. Martin",
        email="martin@example.com",
        bio="American novelist"
    )

    # Create books - Django style: pass model instance directly!
    lotr = await Book.objects.create(
        title="The Lord of the Rings",
        author=tolkien,  # <-- Django-like: pass model instance
        published_year=1954,
        genre="Fantasy",
        price=29.99
    )
    hobbit = await Book.objects.create(
        title="The Hobbit",
        author=tolkien,
        published_year=1937,
        genre="Fantasy",
        price=19.99
    )
    hp1 = await Book.objects.create(
        title="Harry Potter and the Philosopher's Stone",
        author=rowling,
        published_year=1997,
        genre="Fantasy",
        price=24.99
    )
    hp2 = await Book.objects.create(
        title="Harry Potter and the Chamber of Secrets",
        author=rowling,
        published_year=1998,
        genre="Fantasy",
        price=24.99
    )
    got = await Book.objects.create(
        title="A Game of Thrones",
        author=martin,
        published_year=1996,
        genre="Fantasy",
        price=34.99
    )

    # Create reviews
    await Review.objects.create(book=lotr, reviewer_name="Alice", rating=5, comment="Masterpiece!")
    await Review.objects.create(book=lotr, reviewer_name="Bob", rating=5, comment="Epic!")
    await Review.objects.create(book=lotr, reviewer_name="Charlie", rating=4, comment="Long but great")
    await Review.objects.create(book=hp1, reviewer_name="Diana", rating=5, comment="Magical!")
    await Review.objects.create(book=hp1, reviewer_name="Eve", rating=4, comment="Great for kids")
    await Review.objects.create(book=got, reviewer_name="Frank", rating=4, comment="Complex and gripping")

    print("✓ Test data created\n")
    return {"tolkien": tolkien, "rowling": rowling, "martin": martin}


async def main():
    print("=" * 60)
    print("Zeeb ORM - Relationships Example (Django-like)")
    print("=" * 60)

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    authors = await setup_data(db)

    # =========================================
    # DJANGO-LIKE FK ACCESS
    # =========================================
    print("--- DJANGO-LIKE FK ACCESS ---")

    book = await Book.objects.first()
    print(f"Book: {book.title}")
    print(f"  book.author_id = {book.author_id}  (raw FK ID)")
    
    # Lazy load the related object with await
    author = await book.author
    print(f"  book.author = {author.name}  (await fetches Author object)")

    # =========================================
    # REVERSE RELATIONS
    # =========================================
    print("\n--- REVERSE RELATIONS ---")

    tolkien = authors["tolkien"]
    
    # Django-like: user.posts.all() equivalent
    tolkien_books = await tolkien.books.all()
    print(f"Tolkien's books via reverse relation (author.books.all()):")
    for b in tolkien_books:
        print(f"  - {b.title}")

    # Filter on reverse relation
    fantasy_by_tolkien = await tolkien.books.filter(genre="Fantasy")
    print(f"\nFiltered reverse relation: {len(fantasy_by_tolkien)} fantasy books")

    # =========================================
    # QUERYING WITH FK
    # =========================================
    print("\n--- QUERYING WITH FK ---")

    # Filter by FK ID
    tolkien_books = await Book.objects.filter(author_id=tolkien.id)
    print(f"Books by Tolkien (filter by author_id): {[b.title for b in tolkien_books]}")

    # Get books and their authors
    print("\nAll books with authors:")
    all_books = await Book.objects.all()
    for book in all_books:
        author = await book.author  # Lazy load
        print(f"  - {book.title} by {author.name}")

    # =========================================
    # VALUES AND VALUES_LIST
    # =========================================
    print("\n--- VALUES & VALUES_LIST ---")

    # values() returns dicts
    book_dicts = await Book.objects.values("title", "published_year")
    print("values('title', 'published_year'):")
    for d in book_dicts[:3]:
        print(f"  {d}")

    # values_list() returns tuples
    book_tuples = await Book.objects.values_list("title", "published_year")
    print("\nvalues_list('title', 'published_year'):")
    for t in book_tuples[:3]:
        print(f"  {t}")

    # values_list(flat=True) for single field
    titles = await Book.objects.values_list("title", flat=True)
    print(f"\nvalues_list('title', flat=True): {titles[:3]}")

    # =========================================
    # CREATE VIA REVERSE RELATION
    # =========================================
    print("\n--- CREATE VIA REVERSE RELATION ---")

    # Create book via author.books.create()
    new_book = await tolkien.books.create(
        title="The Silmarillion",
        published_year=1977,
        genre="Fantasy",
        price=22.99
    )
    print(f"Created via reverse relation: {new_book.title}")

    # Verify
    tolkien_book_count = await tolkien.books.count()
    print(f"Tolkien now has {tolkien_book_count} books")

    # =========================================
    # SUMMARY
    # =========================================
    print("\n--- SUMMARY ---")

    author_count = await Author.objects.count()
    book_count = await Book.objects.count()
    review_count = await Review.objects.count()

    print(f"Authors: {author_count}")
    print(f"Books: {book_count}")
    print(f"Reviews: {review_count}")

    await close_all_connections()
    print("\n✓ Done!")


if __name__ == "__main__":
    asyncio.run(main())
