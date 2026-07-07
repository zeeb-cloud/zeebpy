#!/usr/bin/env python3
"""
Full API Example - Zeeb ORM + Zeeb API

Demonstrates:
- ModelSerializer for validation and serialization
- ModelViewSet with full CRUD
- Custom actions with @action decorator
- Unified Query endpoint with Q filters (POST /query/)
- Router auto-generation with OpenAPI schemas
- JWT Authentication with database-backed User model
- User registration and login

Run: python example_api.py
Then visit: http://127.0.0.1:9000/docs
"""

import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends

# Zeeb ORM imports
from zeeb_orm import (
    Model, fields, Q,
    configure, setup_database, close_all_connections,
)
from zeeb_orm.models.base import metadata

# Zeeb API imports
from zeeb_api import (
    ModelSerializer, SerializerMethodField,
    ModelViewSet, ReadOnlyModelViewSet, action,
    DefaultRouter,
    permissions,
    # Exception handling
    install_exception_handlers,
    get_error_responses,
    ZeebException,
    ValidationException,
    ResourceNotFoundException,
    ErrorCode,
    field_error,
    # Authentication
    configure_jwt,
    create_auth_router,
    JWTAuthMiddleware,
    get_current_user,
    get_current_user_optional,
    # User model
    User,
    create_user,
)
from zeeb_api.serializers import CharField, IntegerField, EmailField


# =============================================================================
# MODELS
# =============================================================================

class Author(Model):
    """Author model with UUID ID (default)."""
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    bio = fields.TextField(null=True)
    
    class Meta:
        table_name = "api_authors"
    
    def __str__(self) -> str:
        return self.name


class Book(Model):
    """Book model with ForeignKey and UUID ID (default)."""
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(Author, related_name="books")
    published_year = fields.IntegerField()
    genre = fields.CharField(max_length=50)
    price = fields.DecimalField(max_digits=6, decimal_places=2)
    in_stock = fields.BooleanField(default=True)
    
    class Meta:
        table_name = "api_books"
    
    def __str__(self) -> str:
        return self.title


# =============================================================================
# SERIALIZERS
# =============================================================================

class AuthorSerializer(ModelSerializer):
    """Serializer for Author model."""
    book_count = SerializerMethodField(return_type=int)
    
    class Meta:
        model = Author
        fields = ["id", "name", "email", "bio", "book_count"]
        read_only_fields = ["id"]
    
    def get_book_count(self, obj):
        # Note: In real app, would use annotation/aggregation
        return 0  # Simplified


class BookSerializer(ModelSerializer):
    """Serializer for Book model."""
    author_name = SerializerMethodField()
    
    class Meta:
        model = Book
        fields = ["id", "title", "author_id", "author_name", "published_year", 
                  "genre", "price", "in_stock"]
        read_only_fields = ["id"]
    
    def get_author_name(self, obj):
        # Would need async in real app
        return f"Author #{obj.author_id}"


class BookCreateSerializer(ModelSerializer):
    """Serializer for creating books."""
    
    class Meta:
        model = Book
        fields = ["title", "author_id", "published_year", "genre", "price", "in_stock"]


# =============================================================================
# VIEWSETS
# =============================================================================

class AuthorViewSet(ModelViewSet):
    """
    ViewSet for Author CRUD operations.
    
    Endpoints:
    - POST /authors/query/ - Query authors with Q filters
    - POST /authors/ - Create author
    - GET /authors/{id}/ - Get author
    - PUT /authors/{id}/ - Update author
    - PATCH /authors/{id}/ - Partial update
    - DELETE /authors/{id}/ - Delete author
    - GET /authors/{id}/books/ - Get author's books (custom action)
    """
    queryset = Author.objects
    serializer_class = AuthorSerializer
    
    @action(detail=True, methods=["get"])
    async def books(self, request: Request, id: int = None):
        """Get all books by this author."""
        author = await self.get_object()
        books = await Book.objects.filter(author_id=author.id)
        
        # Serialize manually for simplicity
        return [
            {"id": b.id, "title": b.title, "published_year": b.published_year}
            for b in books
        ]
    
    @action(detail=False, methods=["get"])
    async def popular(self, request: Request):
        """Get popular authors (example custom list action)."""
        # In real app, would sort by book count or ratings
        authors = await self.get_queryset().order_by("name")[:5]
        serializer = self.get_serializer(instance=authors, many=True)
        return serializer.data


class BookViewSet(ModelViewSet):
    """
    ViewSet for Book CRUD operations.
    
    Query with Q filters via POST /books/query/
    """
    queryset = Book.objects
    serializer_class = BookSerializer
    
    def get_serializer_class(self):
        if self.action == "create":
            return BookCreateSerializer
        return BookSerializer
    
    @action(detail=False, methods=["get"])
    async def in_stock(self, request: Request):
        """Get only in-stock books."""
        books = await self.get_queryset().filter(in_stock=True)
        serializer = self.get_serializer(instance=books, many=True)
        return serializer.data
    
    @action(detail=True, methods=["post"])
    async def toggle_stock(self, request: Request, id: int = None):
        """Toggle book stock status."""
        book = await self.get_object()
        book.in_stock = not book.in_stock
        await book.save()
        return {"id": book.id, "in_stock": book.in_stock}


# =============================================================================
# ROUTER
# =============================================================================

router = DefaultRouter()
router.register("authors", AuthorViewSet, basename="author")
router.register("books", BookViewSet, basename="book")


# =============================================================================
# APP SETUP
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - setup and teardown."""
    # Startup
    print("Starting up...")
    
    # Configure database
    configure(database={"url": "sqlite+aiosqlite:///example_api.db"})
    db = await setup_database("sqlite+aiosqlite:///example_api.db")
    
    # Reset tables for demo (including User)
    Author._sa_table = None
    Book._sa_table = None
    User._sa_table = None
    metadata.clear()
    Author._get_table()
    Book._get_table()
    User._get_table()
    await db.create_all()
    
    # Create sample data
    await create_sample_data()
    
    # Create demo user
    await create_demo_user()
    
    print("Ready!")
    yield
    
    # Shutdown
    print("Shutting down...")
    await close_all_connections()


async def create_demo_user():
    """Create a demo user for testing."""
    # Check if demo user exists
    existing = await User.objects.filter(email="demo@example.com").first()
    if existing:
        return
    
    # Create demo user
    user = await create_user(
        email="demo@example.com",
        password="demo123",
        first_name="Demo",
        last_name="User",
        is_active=True,
    )
    print(f"Demo user created: {user.email} (password: demo123)")


async def create_sample_data():
    """Create sample authors and books."""
    # Check if data exists
    count = await Author.objects.count()
    if count > 0:
        return
    
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
    
    # Create books
    await Book.objects.create(
        title="The Lord of the Rings",
        author=tolkien,
        published_year=1954,
        genre="Fantasy",
        price=29.99
    )
    await Book.objects.create(
        title="The Hobbit",
        author=tolkien,
        published_year=1937,
        genre="Fantasy",
        price=19.99
    )
    await Book.objects.create(
        title="Harry Potter and the Philosopher's Stone",
        author=rowling,
        published_year=1997,
        genre="Fantasy",
        price=24.99
    )
    await Book.objects.create(
        title="A Game of Thrones",
        author=martin,
        published_year=1996,
        genre="Fantasy",
        price=34.99,
        in_stock=False
    )
    
    print("Sample data created!")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="Zeeb API Example",
        description="Example API using Zeeb ORM and Zeeb API with database-backed authentication",
        version="1.0.0",
        lifespan=lifespan,
    )
    
    # Configure JWT (use a proper secret in production!)
    configure_jwt(
        secret_key="your-super-secret-key-change-in-production",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
    )
    
    # Add JWT middleware - loads user from database
    app.add_middleware(JWTAuthMiddleware, load_user_from_db=True)
    
    # Install standardized exception handlers
    install_exception_handlers(app)
    
    # Create auth router with database-backed authentication
    # No need to provide authenticate function - uses database by default
    auth_router = create_auth_router(
        use_database=True,        # Uses User model from database
        enable_registration=True,  # Enables POST /auth/register
    )
    app.include_router(auth_router)
    
    # Include model routers
    for route in router.routes:
        app.include_router(route, prefix="/api")
    
    # Protected endpoint demo - now receives full User model from DB
    @app.get("/api/protected/")
    async def protected_endpoint(user = Depends(get_current_user)):
        """
        Protected endpoint - requires valid JWT token.
        
        The user object is the full User model from the database.
        
        Use the access_token from /auth/login in the Authorization header:
        Authorization: Bearer <access_token>
        """
        return {
            "message": "You are authenticated!",
            "user_id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        }
    
    # Optional auth endpoint demo
    @app.get("/api/maybe-protected/")
    async def maybe_protected(user = Depends(get_current_user_optional)):
        """
        Endpoint that works with or without authentication.
        """
        if user:
            email = getattr(user, "email", None) or user.claims.get("email", "user")
            return {"message": f"Hello, {email}!"}
        return {"message": "Hello, anonymous user!"}
    
    # Demo endpoint showing exception handling
    @app.get("/api/demo/error/{error_type}")
    async def demo_error(error_type: str):
        """
        Demo endpoint to show standardized error responses.
        
        Try:
        - /api/demo/error/validation - Validation error
        - /api/demo/error/not_found - Not found error
        - /api/demo/error/custom - Custom error with details
        - /api/demo/error/server - Server error
        """
        if error_type == "validation":
            raise ValidationException(
                message="Validation failed",
                details=[
                    field_error("email", ErrorCode.FIELD_REQUIRED, "This field is required"),
                    field_error("age", ErrorCode.FIELD_MIN_VALUE, "Must be at least 18", {"min": 18}),
                ]
            )
        elif error_type == "not_found":
            raise ResourceNotFoundException(
                message="Book not found",
                resource_type="Book",
                resource_id="abc-123"
            )
        elif error_type == "custom":
            raise ZeebException(
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Email already registered",
                details=[
                    field_error("email", ErrorCode.FIELD_UNIQUE_CONSTRAINT, 
                               "This email is already in use")
                ],
                status_code=409
            )
        elif error_type == "server":
            # This will be caught by generic handler
            raise RuntimeError("Simulated server error")
        
        return {"message": f"No error for type: {error_type}"}
    
    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "message": "Zeeb API Example with Database-backed Auth",
            "docs": "/docs",
            "endpoints": {
                "authors_query": "POST /api/authors/query/",
                "authors": "/api/authors/",
                "books_query": "POST /api/books/query/",
                "books": "/api/books/",
            },
            "auth_endpoints": {
                "register": "POST /auth/register (body: {email, password})",
                "login": "POST /auth/login (body: {email, password})",
                "refresh": "POST /auth/refresh (body: {refresh_token})",
                "logout": "POST /auth/logout (requires auth)",
                "me": "GET /auth/me (requires auth)",
            },
            "protected_demo": {
                "protected": "GET /api/protected/ (requires auth - returns full user)",
                "maybe_protected": "GET /api/maybe-protected/ (optional auth)",
            },
            "query_example": {
                "filter": "Q(genre='Fantasy') & Q(price__lte=25)",
                "order_by": ["-published_year"],
                "limit": 20,
                "offset": 0
            },
            "demo_credentials": {
                "email": "demo@example.com",
                "password": "demo123",
                "note": "Or register a new account at /auth/register"
            }
        }
    
    return app


app = create_app()


if __name__ == "__main__":
    print("=" * 60)
    print("Zeeb API Example Server - Database-backed Auth")
    print("=" * 60)
    print("\nStarting server at http://127.0.0.1:9000")
    print("API docs at http://127.0.0.1:9000/docs")
    print("\nAuth Endpoints (database-backed):")
    print("  POST /auth/register         - Register new user")
    print("  POST /auth/login            - Login (email + password)")
    print("  POST /auth/refresh          - Refresh access token")
    print("  POST /auth/logout           - Logout (requires auth)")
    print("  GET  /auth/me               - Get current user (requires auth)")
    print("\nDemo User: demo@example.com / demo123")
    print("Or register a new account at /auth/register")
    print("\nProtected Endpoints:")
    print("  GET /api/protected/          - Requires auth (returns full user)")
    print("  GET /api/maybe-protected/    - Optional auth")
    print("\nModel Endpoints:")
    print("  POST /api/authors/query/     - Query authors with Q filters")
    print("  POST /api/books/query/       - Query books with Q filters")
    print("\nPress Ctrl+C to stop")
    print("=" * 60)
    
    uvicorn.run(app, host="127.0.0.1", port=9000)
