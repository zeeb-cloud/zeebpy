#!/usr/bin/env python3
"""
Advanced Queries Example - Zeeb ORM

Demonstrates:
- Q objects for complex filtering (AND, OR, NOT)
- F expressions for field references
- Annotations (computed fields per row)
- Aggregations (Count, Sum, Avg, Min, Max)
- String/Math/Date functions
- Ordering, slicing, distinct

Run: python example_queries.py
"""

import asyncio
from zeeb_orm import (
    Model, fields, Q, F,
    # Aggregates
    Count, Sum, Avg, Min, Max,
    # Expressions
    Value, Case, When, Coalesce,
    # String functions
    Concat, Lower, Upper, Length,
    # Math functions
    Round, Abs, Greatest, Least,
    configure, setup_database, close_all_connections,
)
from zeeb_orm.models.base import metadata


# Define Models

class Product(Model):
    """Product model for query demonstrations."""
    name = fields.CharField(max_length=100)
    category = fields.CharField(max_length=50)
    price = fields.DecimalField(max_digits=10, decimal_places=2)
    cost = fields.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock = fields.IntegerField(default=0)
    is_available = fields.BooleanField(default=True)
    rating = fields.FloatField(null=True)

    class Meta:
        table_name = "products"


async def setup_data(db):
    """Create test data."""
    Product._sa_table = None
    Product._sa_model = None
    metadata.clear()
    Product._get_table()
    await db.create_all()

    products = [
        {"name": "Laptop", "category": "Electronics", "price": 999.99, "cost": 600, "stock": 50, "rating": 4.5},
        {"name": "Phone", "category": "Electronics", "price": 699.99, "cost": 400, "stock": 100, "rating": 4.2},
        {"name": "Tablet", "category": "Electronics", "price": 449.99, "cost": 250, "stock": 30, "rating": 4.0},
        {"name": "Headphones", "category": "Electronics", "price": 199.99, "cost": 80, "stock": 200, "rating": 4.8},
        {"name": "T-Shirt", "category": "Clothing", "price": 29.99, "cost": 10, "stock": 500, "rating": 4.1},
        {"name": "Jeans", "category": "Clothing", "price": 59.99, "cost": 25, "stock": 300, "rating": 4.3},
        {"name": "Sneakers", "category": "Clothing", "price": 89.99, "cost": 40, "stock": 150, "rating": 4.6},
        {"name": "Book", "category": "Books", "price": 14.99, "cost": 5, "stock": 1000, "rating": 4.9},
        {"name": "Notebook", "category": "Books", "price": 9.99, "cost": 3, "stock": 2000, "rating": 4.0},
        {"name": "Pen Set", "category": "Office", "price": 19.99, "cost": 8, "stock": 500, "is_available": False, "rating": 3.5},
    ]

    for p in products:
        await Product.objects.create(**p)

    print(f"✓ Created {len(products)} products\n")


async def main():
    print("=" * 60)
    print("Zeeb ORM - Advanced Queries Example")
    print("=" * 60)

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    await setup_data(db)

    # =========================================
    # Q OBJECTS - Complex Filtering
    # =========================================
    print("--- Q OBJECTS ---")

    # Simple Q
    electronics = await Product.objects.filter(Q(category="Electronics"))
    print(f"Electronics: {[p.name for p in electronics]}")

    # AND - Multiple conditions
    expensive_electronics = await Product.objects.filter(
        Q(category="Electronics") & Q(price__gte=500)
    )
    print(f"Expensive electronics (>=500): {[p.name for p in expensive_electronics]}")

    # OR - Either condition
    books_or_office = await Product.objects.filter(
        Q(category="Books") | Q(category="Office")
    )
    print(f"Books or Office: {[p.name for p in books_or_office]}")

    # NOT - Exclude
    not_electronics = await Product.objects.filter(~Q(category="Electronics"))
    print(f"Not electronics: {[p.name for p in not_electronics]}")

    # Complex nested Q
    complex_filter = await Product.objects.filter(
        Q(is_available=True) & (
            Q(price__lt=50) | Q(rating__gte=4.5)
        )
    )
    print(f"Available AND (cheap OR highly rated): {[p.name for p in complex_filter]}")

    # =========================================
    # LOOKUP EXPRESSIONS
    # =========================================
    print("\n--- LOOKUP EXPRESSIONS ---")

    # contains / icontains
    with_book = await Product.objects.filter(name__contains="Book")
    print(f"Name contains 'Book': {[p.name for p in with_book]}")

    # startswith / endswith
    starts_with_p = await Product.objects.filter(name__startswith="P")
    print(f"Name starts with 'P': {[p.name for p in starts_with_p]}")

    # in
    specific = await Product.objects.filter(category__in=["Electronics", "Books"])
    print(f"Category in [Electronics, Books]: {[p.name for p in specific]}")

    # range
    mid_price = await Product.objects.filter(price__range=(20, 100))
    print(f"Price 20-100: {[p.name for p in mid_price]}")

    # isnull
    with_rating = await Product.objects.filter(rating__isnull=False)
    print(f"Has rating: {len(with_rating)} products")

    # =========================================
    # ANNOTATIONS - Computed Fields
    # =========================================
    print("\n--- ANNOTATIONS ---")

    # Simple annotation: calculate profit margin
    products_with_margin = await Product.objects.annotate(
        profit=F("price") - F("cost")
    ).order_by("-profit")[:5]
    
    print("Top 5 products by profit:")
    for p in products_with_margin:
        print(f"  - {p.name}: profit=${p.profit:.2f}")

    # Annotate with string function
    products_with_lower = await Product.objects.annotate(
        name_lower=Lower("name")
    ).values("name", "name_lower")[:3]
    
    print("\nLowercase names:")
    for p in products_with_lower:
        print(f"  - {p['name']} -> {p['name_lower']}")

    # Annotate with conditional (Case/When)
    products_with_tier = await Product.objects.annotate(
        price_tier=Case(
            When(price__gte=500, then=Value("Premium")),
            When(price__gte=100, then=Value("Mid-range")),
            default=Value("Budget")
        )
    ).values("name", "price", "price_tier")[:6]
    
    print("\nPrice tiers:")
    for p in products_with_tier:
        print(f"  - {p['name']}: ${p['price']} ({p['price_tier']})")

    # Annotate with Coalesce (handle nulls)
    products_with_rating = await Product.objects.annotate(
        safe_rating=Coalesce("rating", Value(0.0))
    ).values("name", "rating", "safe_rating")[:3]
    
    print("\nCoalesce rating (null -> 0):")
    for p in products_with_rating:
        print(f"  - {p['name']}: rating={p['rating']} -> safe={p['safe_rating']}")

    # Order by annotation
    by_profit = await Product.objects.annotate(
        profit=F("price") - F("cost")
    ).order_by("-profit").values("name", "profit")[:3]
    
    print("\nOrder by computed profit:")
    for p in by_profit:
        print(f"  - {p['name']}: ${p['profit']:.2f}")

    # =========================================
    # ORDERING
    # =========================================
    print("\n--- ORDERING ---")

    # Ascending
    by_price = await Product.objects.order_by("price")
    print(f"By price (asc): {[(p.name, float(p.price)) for p in by_price[:3]]}")

    # Descending
    by_price_desc = await Product.objects.order_by("-price")
    print(f"By price (desc): {[(p.name, float(p.price)) for p in by_price_desc[:3]]}")

    # Multiple fields
    by_category_price = await Product.objects.order_by("category", "-price")
    print(f"By category, then price desc: {[(p.category, p.name) for p in by_category_price[:5]]}")

    # =========================================
    # SLICING
    # =========================================
    print("\n--- SLICING ---")

    # First N
    first_3 = await Product.objects.order_by("name")[:3]
    print(f"First 3 by name: {[p.name for p in first_3]}")

    # Skip + Limit
    page_2 = await Product.objects.order_by("name")[3:6]
    print(f"Items 4-6 by name: {[p.name for p in page_2]}")

    # =========================================
    # AGGREGATIONS
    # =========================================
    print("\n--- AGGREGATIONS ---")

    # Count
    count = await Product.objects.count()
    print(f"Total products: {count}")

    available_count = await Product.objects.filter(is_available=True).count()
    print(f"Available products: {available_count}")

    # Aggregate multiple values
    stats = await Product.objects.aggregate(
        total=Count("*"),
        avg_price=Avg("price"),
        min_price=Min("price"),
        max_price=Max("price"),
        total_stock=Sum("stock"),
    )
    print(f"Stats: {stats}")

    # Aggregate with filter
    electronics_stats = await Product.objects.filter(category="Electronics").aggregate(
        count=Count("*"),
        avg_rating=Avg("rating"),
    )
    print(f"Electronics stats: {electronics_stats}")

    # =========================================
    # COMBINING EVERYTHING
    # =========================================
    print("\n--- COMBINED QUERIES ---")

    # Complex real-world query
    featured = await Product.objects.filter(
        Q(is_available=True) & Q(rating__gte=4.0)
    ).exclude(
        category="Office"
    ).order_by("-rating", "price")[:5]

    print("Featured products (available, rating>=4, not office, top 5 by rating):")
    for p in featured:
        print(f"  - {p.name} ({p.category}): ${p.price}, rating={p.rating}")

    # Annotate + filter + order
    high_margin = await Product.objects.annotate(
        margin=F("price") - F("cost")
    ).filter(
        margin__gt=100
    ).order_by("-margin").values("name", "price", "cost", "margin")
    
    print("\nHigh margin products (margin > $100):")
    for p in high_margin:
        print(f"  - {p['name']}: price=${p['price']}, cost=${p['cost']}, margin=${p['margin']:.2f}")

    await close_all_connections()
    print("\n✓ Done!")


if __name__ == "__main__":
    asyncio.run(main())
