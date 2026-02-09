"""Database connection management with async support."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from zeeb_orm.conf.settings import DatabaseConfig, get_settings

# Global connection registry
_connections: dict[str, Database] = {}
_default_alias = "default"

# Context variable for active transaction session
_active_session: ContextVar[AsyncSession | None] = ContextVar("active_session", default=None)


class Database:
    """
    Database connection wrapper supporting both async and sync operations.

    Usage:
        db = Database('postgresql+asyncpg://user:pass@localhost/mydb')
        await db.connect()

        async with db.session() as session:
            result = await session.execute(select(...))

        await db.disconnect()
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        config: DatabaseConfig | None = None,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: int = 30,
        pool_recycle: int = 1800,
        connect_args: dict[str, Any] | None = None,
    ) -> None:
        if config:
            self.config = config
        elif url:
            self.config = DatabaseConfig(
                url=url,
                echo=echo,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                connect_args=connect_args or {},
            )
        else:
            self.config = get_settings().database

        self._async_engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine: Any | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._connected = False

    @property
    def is_async(self) -> bool:
        """Check if the database URL is async."""
        async_drivers = ["asyncpg", "aiomysql", "aiosqlite"]
        return any(driver in self.config.url for driver in async_drivers)

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite (which has pool limitations)."""
        return "sqlite" in self.config.url.lower()

    @property
    def url(self) -> str:
        """Get database URL."""
        return self.config.url

    async def connect(self) -> None:
        """Establish database connection."""
        if self._connected:
            return

        # SQLite doesn't support pool configuration
        pool_kwargs: dict[str, Any] = {}
        if not self.is_sqlite:
            pool_kwargs = {
                "pool_size": self.config.pool_size,
                "max_overflow": self.config.max_overflow,
                "pool_timeout": self.config.pool_timeout,
                "pool_recycle": self.config.pool_recycle,
            }

        if self.is_async:
            self._async_engine = create_async_engine(
                self.config.url,
                echo=self.config.echo,
                connect_args=self.config.connect_args,
                **pool_kwargs,
            )
            self._async_session_factory = async_sessionmaker(
                self._async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        else:
            # Create sync engine for non-async drivers
            self._sync_engine = create_engine(
                self.config.url,
                echo=self.config.echo,
                connect_args=self.config.connect_args,
                **pool_kwargs,
            )
            self._sync_session_factory = sessionmaker(
                self._sync_engine,
                expire_on_commit=False,
            )

        self._connected = True

    async def disconnect(self) -> None:
        """Close database connection."""
        if self._async_engine:
            await self._async_engine.dispose()
            self._async_engine = None
            self._async_session_factory = None

        if self._sync_engine:
            self._sync_engine.dispose()
            self._sync_engine = None
            self._sync_session_factory = None

        self._connected = False

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session."""
        if not self._connected:
            await self.connect()

        if self._async_session_factory:
            async with self._async_session_factory() as session:
                try:
                    yield session
                except Exception:
                    await session.rollback()
                    raise
        else:
            # Wrap sync session in async interface
            session = self._sync_session_factory()  # type: ignore
            try:
                yield _SyncSessionWrapper(session)  # type: ignore
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def get_engine(self) -> AsyncEngine | Any:
        """Get the underlying SQLAlchemy engine."""
        return self._async_engine or self._sync_engine

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """Execute a raw SQL statement."""
        async with self.session() as session:
            if isinstance(statement, str):
                statement = text(statement)
            result = await session.execute(statement, parameters or {})
            await session.commit()
            return result

    async def create_all(self) -> None:
        """Create all tables defined in metadata."""
        from zeeb_orm.models.base import metadata

        if not self._connected:
            await self.connect()

        if self._async_engine:
            async with self._async_engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
        elif self._sync_engine:
            metadata.create_all(self._sync_engine)

    async def drop_all(self) -> None:
        """Drop all tables defined in metadata."""
        from zeeb_orm.models.base import metadata

        if not self._connected:
            await self.connect()

        if self._async_engine:
            async with self._async_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)
        elif self._sync_engine:
            metadata.drop_all(self._sync_engine)


class _SyncSessionWrapper:
    """Wrapper to make sync session work with async interface."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        return self._session.execute(statement, parameters or {})

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()

    async def refresh(self, instance: Any) -> None:
        self._session.refresh(instance)

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    def add_all(self, instances: list[Any]) -> None:
        self._session.add_all(instances)


# Connection management functions


def register_database(db: Database, alias: str = "default") -> None:
    """Register a database connection with an alias."""
    _connections[alias] = db


def get_database(alias: str = "default") -> Database | None:
    """Get a registered database by alias."""
    return _connections.get(alias)


async def get_connection(alias: str | None = None) -> Database:
    """
    Get or create database connection.

    If no alias specified, uses default connection from settings.
    """
    alias = alias or _default_alias

    if alias not in _connections:
        settings = get_settings()
        db = Database(config=settings.database)
        await db.connect()
        _connections[alias] = db

    db = _connections[alias]
    if not db._connected:
        await db.connect()

    return db


async def close_all_connections() -> None:
    """Close all registered database connections."""
    for db in _connections.values():
        await db.disconnect()
    _connections.clear()


# Transaction management


@asynccontextmanager
async def atomic(using: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database transactions.

    Usage:
        async with atomic() as session:
            await User.objects.create(name='John')
            await Post.objects.create(title='Hello')
            # Both committed together or rolled back on error
    """
    db = await get_connection(using)

    async with db.session() as session:
        token = _active_session.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _active_session.reset(token)


def get_active_session() -> AsyncSession | None:
    """Get the active transaction session, if any."""
    return _active_session.get()


@asynccontextmanager
async def get_session(db_alias: str | None = None) -> AsyncGenerator[tuple[AsyncSession, bool], None]:
    """
    Get a database session, reusing the active transaction session if available.

    Returns a tuple of (session, should_commit) where should_commit is False
    if using an active transaction (let atomic() handle the commit).

    Usage:
        async with get_session() as (session, should_commit):
            result = await session.execute(stmt)
            if should_commit:
                await session.commit()
    """
    active = _active_session.get()
    if active is not None:
        # Reuse existing transaction session - don't commit
        yield active, False
    else:
        # Create a new session - caller should commit
        db = await get_connection(db_alias)
        async with db.session() as session:
            yield session, True


# Convenience functions for setup


async def setup_database(url: str, **kwargs: Any) -> Database:
    """
    Quick setup for database connection.

    Usage:
        db = await setup_database('postgresql+asyncpg://localhost/mydb')
    """
    db = Database(url, **kwargs)
    await db.connect()
    register_database(db)
    return db
