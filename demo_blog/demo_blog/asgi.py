"""
demo_blog ASGI application.

FastAPI app factory for the project.
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager

from zeeb_orm import setup_database, close_all_connections, check_migrations_applied, MigrationError
from zeeb_api.logging import configure_logging, get_logger

# Import settings
from demo_blog.settings import (
    DATABASE, API_TITLE, API_VERSION, API_PREFIX, DEBUG,
    CORS_ALLOW_ORIGINS, MIDDLEWARE, ENFORCE_MIGRATIONS, LOGGING,
)
from demo_blog.urls import get_routes

# Configure logging from settings (with rotation at midnight)
configure_logging(
    level=LOGGING.get("level", "INFO"),
    json_logs=LOGGING.get("json_logs", False),
    log_file=LOGGING.get("log_file"),
    log_rotation=LOGGING.get("log_rotation", True),
    log_retention_days=LOGGING.get("log_retention_days", 30),
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - setup and teardown."""
    logger.info("Starting application", api_title=API_TITLE, debug=DEBUG)
    
    # Check migrations before startup (like Django)
    try:
        check_migrations_applied(raise_on_pending=True)
    except MigrationError as e:
        separator = "=" * 60
        if ENFORCE_MIGRATIONS and not DEBUG:
            logger.error("Unapplied migrations detected", error=str(e))
            print(f"\n{separator}")
            print("ERROR: Unapplied migrations detected!")
            print(str(e))
            print("\nRun the following commands:")
            print("  python manage.py init           # If not already done")
            print("  python manage.py makemigrations  # Create migrations")
            print("  python manage.py migrate         # Apply migrations")
            print(f"{separator}\n")
            raise SystemExit(1)
        else:
            # In DEBUG mode, warn but continue
            logger.warning("Unapplied migrations detected", error=str(e))
            print(f"\n{separator}")
            print("WARNING: Unapplied migrations detected!")
            print(str(e))
            print("\nRun the following commands:")
            print("  python manage.py init           # If not already done")
            print("  python manage.py makemigrations  # Create migrations")
            print("  python manage.py migrate         # Apply migrations")
            print(f"{separator}\n")
    
    # Startup
    await setup_database(DATABASE["url"])
    logger.info("Database connected")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application")
    await close_all_connections()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        debug=DEBUG,
        lifespan=lifespan,
    )

    # Add CORS middleware if configured
    if CORS_ALLOW_ORIGINS:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOW_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include routes with API prefix
    for route in get_routes():
        app.include_router(route, prefix=API_PREFIX)

    logger.info("Application configured", routes=len(get_routes()))
    return app


# Create app instance
app = create_app()
