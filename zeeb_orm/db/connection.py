"""Database connection management with async support."""

from __future__ import annotations

import asyncio
import logging
import warnings
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncGenerator

from sqlalchemy import create_engine, event, text
from sqlalchemy import exc as _sa_exc
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from zeeb_orm.conf.settings import DatabaseConfig, get_settings
from zeeb_orm.db.urls import sync_database_url

logger = logging.getLogger("zeeb_orm.db")

# Global connection registry
_connections: dict[str, Database] = {}
_default_alias = "default"

#: Disconnects scheduled from synchronous code (``register_database`` while a
#: loop runs). Held here so the task is not garbage-collected mid-flight.
_pending_disposals: set[asyncio.Task[Any]] = set()

#: DB-API drivers that run on asyncio. Used when SQLAlchemy cannot tell us
#: (a third-party dialect it does not ship).
_ASYNC_DRIVERS = frozenset(
    {"asyncpg", "aiomysql", "asyncmy", "aiosqlite", "psycopg_async", "aioodbc"}
)

# Context variables for the active transaction session and its database alias
_active_session: ContextVar[AsyncSession | None] = ContextVar("active_session", default=None)
_active_session_alias: ContextVar[str | None] = ContextVar(
    "active_session_alias", default=None
)


class Database:
    """
    Database connection wrapper supporting both async and sync operations.

    An async URL (``postgresql+asyncpg``, ``mysql+aiomysql``/``asyncmy``,
    ``sqlite+aiosqlite``) gets an ``AsyncEngine``. A synchronous URL is
    supported on purpose — for scripts and tests that already have one — but
    every statement then runs *on the event loop thread* and blocks it;
    :meth:`connect` says so with a ``RuntimeWarning``. Servers should use an
    async driver.

    SQLite connections get ``PRAGMA foreign_keys=ON``, so foreign keys and
    their ``ON DELETE`` actions are enforced as on every other backend.

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
        pool_pre_ping: bool = True,
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
                pool_pre_ping=pool_pre_ping,
                connect_args=connect_args or {},
            )
        else:
            self.config = get_settings().database

        self._async_engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine: Any | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._connected = False

    def _parsed_url(self) -> Any:
        try:
            return make_url(self.config.url)
        except _sa_exc.ArgumentError:
            return None

    @property
    def is_async(self) -> bool:
        """Whether the URL names an asyncio driver.

        Decided from the parsed URL's dialect, not by searching the URL text:
        a database *named* ``aiosqlite_data`` on a sync driver is not async,
        and ``mysql+asyncmy`` is.
        """
        url = self._parsed_url()
        if url is None:
            return False
        try:
            return bool(url.get_dialect().is_async)
        except Exception:
            return url.get_driver_name() in _ASYNC_DRIVERS

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite (which has pool limitations)."""
        url = self._parsed_url()
        return url is not None and url.get_backend_name() == "sqlite"

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
                "pool_pre_ping": self.config.pool_pre_ping,
            }

        if self.is_async:
            self._async_engine = create_async_engine(
                self.config.url,
                echo=self.config.echo,
                connect_args=self.config.connect_args,
                **pool_kwargs,
            )
            if self.is_sqlite:
                enable_sqlite_foreign_keys(self._async_engine.sync_engine)
            self._async_session_factory = async_sessionmaker(
                self._async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        else:
            warnings.warn(
                f"Database URL {self._redacted_url()!r} uses a synchronous driver: "
                "every query will block the event loop. Use an async driver "
                "(postgresql+asyncpg, mysql+aiomysql, sqlite+aiosqlite) for a server.",
                RuntimeWarning,
                stacklevel=2,
            )
            # Create sync engine for non-async drivers, with the driver named:
            # a bare scheme gets SQLAlchemy's default, which is not ours to pick.
            self._sync_engine = create_engine(
                sync_database_url(self.config.url),
                echo=self.config.echo,
                connect_args=self.config.connect_args,
                **pool_kwargs,
            )
            if self.is_sqlite:
                enable_sqlite_foreign_keys(self._sync_engine)
            self._sync_session_factory = sessionmaker(
                self._sync_engine,
                expire_on_commit=False,
            )

        self._connected = True

    def _redacted_url(self) -> str:
        url = self._parsed_url()
        return url.render_as_string(hide_password=True) if url is not None else "<invalid>"

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
        """Create the tables defined in metadata.

        Tables of ``Meta.managed = False`` models are skipped: they belong to
        some other schema, and creating them here would hide a missing table
        in tests that production would hit.
        """
        from zeeb_orm.models.sa_builder import managed_tables, metadata

        if not self._connected:
            await self.connect()

        def _create(conn: Any) -> None:
            metadata.create_all(conn, tables=managed_tables())

        if self._async_engine:
            async with self._async_engine.begin() as conn:
                await conn.run_sync(_create)
        elif self._sync_engine:
            with self._sync_engine.begin() as conn:
                _create(conn)

    async def drop_all(self) -> None:
        """Drop the tables defined in metadata (unmanaged ones are left alone)."""
        from zeeb_orm.models.sa_builder import managed_tables, metadata

        if not self._connected:
            await self.connect()

        def _drop(conn: Any) -> None:
            metadata.drop_all(conn, tables=managed_tables())

        if self._async_engine:
            async with self._async_engine.begin() as conn:
                await conn.run_sync(_drop)
        elif self._sync_engine:
            with self._sync_engine.begin() as conn:
                _drop(conn)


class _SyncSessionTransactionWrapper:
    """Async facade over a sync SessionTransaction (savepoint)."""

    def __init__(self, tx: Any) -> None:
        self._tx = tx

    async def commit(self) -> None:
        self._tx.commit()

    async def rollback(self) -> None:
        self._tx.rollback()


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

    async def begin_nested(self) -> _SyncSessionTransactionWrapper:
        return _SyncSessionTransactionWrapper(self._session.begin_nested())

    async def stream(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """Server-side streaming, so ``QuerySet.iterator()`` works on a sync URL."""
        result = self._session.execute(
            statement, parameters or {}, execution_options={"stream_results": True}
        )
        return _SyncStreamResult(result)

    async def refresh(self, instance: Any) -> None:
        self._session.refresh(instance)

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    def add_all(self, instances: list[Any]) -> None:
        self._session.add_all(instances)


class _SyncStreamResult:
    """Async facade over a sync streaming ``Result`` (``partitions()`` only)."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def partitions(self, size: int | None = None) -> AsyncGenerator[Any, None]:
        for partition in self._result.partitions(size):
            yield partition


def enable_sqlite_foreign_keys(engine: Any) -> None:
    """Turn on ``PRAGMA foreign_keys`` for every connection of a SQLite engine.

    SQLite ships with foreign-key enforcement off, per connection. Without
    this, a SQLite database accepts rows pointing at nothing and ignores the
    ``ON DELETE CASCADE`` of every relation — including the auto-created
    many-to-many join tables. ``engine`` is a sync ``Engine`` (for an
    ``AsyncEngine``, pass its ``sync_engine``).
    """

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


class _SerializedSession:
    """An ``AsyncSession`` shared by an ``atomic()`` block, one operation at a time.

    The active session lives in a ``ContextVar``, and tasks inherit context:
    ``asyncio.gather(...)`` inside ``atomic()`` runs several queries on the
    same session concurrently, which SQLAlchemy forbids (and which corrupts
    the connection state when it is not caught). Every awaited operation is
    therefore taken under a per-session lock. The lock is held per call, not
    per ``get_session()`` block, so code that queries while handling a
    result (prefetching, signal receivers) never waits on itself.

    Everything else is delegated to the session unchanged.
    """

    _SERIALIZED = frozenset(
        {
            "execute",
            "scalar",
            "scalars",
            "get",
            "get_one",
            "merge",
            "flush",
            "commit",
            "rollback",
            "refresh",
            "delete",
            "run_sync",
            "connection",
        }
    )

    def __init__(self, session: Any) -> None:
        self._session = session
        self._lock = asyncio.Lock()

    @property
    def session(self) -> Any:
        """The wrapped session (bypasses the lock — use with care)."""
        return self._session

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._session, name)
        if name not in self._SERIALIZED or not callable(attr):
            return attr
        lock = self._lock

        async def serialized(*args: Any, **kwargs: Any) -> Any:
            async with lock:
                return await attr(*args, **kwargs)

        serialized.__name__ = name
        return serialized

    def begin_nested(self) -> _SerializedNested:
        return _SerializedNested(self._session, self._lock)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            result = await self._session.stream(*args, **kwargs)
        return _SerializedStream(result, self._lock)

    def __repr__(self) -> str:
        return f"<serialized {self._session!r}>"


class _SerializedNested:
    """``begin_nested()`` of a :class:`_SerializedSession` (await or ``async with``)."""

    def __init__(self, session: Any, lock: asyncio.Lock) -> None:
        self._session = session
        self._lock = lock
        self._tx: Any = None

    async def _begin(self) -> _SerializedNested:
        async with self._lock:
            self._tx = await self._session.begin_nested()
        return self

    def __await__(self) -> Any:
        return self._begin().__await__()

    async def commit(self) -> None:
        async with self._lock:
            await self._tx.commit()

    async def rollback(self) -> None:
        async with self._lock:
            await self._tx.rollback()

    async def __aenter__(self) -> _SerializedNested:
        return await self._begin()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()


class _SerializedStream:
    """A streamed result whose fetches take the session lock one at a time."""

    def __init__(self, result: Any, lock: asyncio.Lock) -> None:
        self._result = result
        self._lock = lock

    def __getattr__(self, name: str) -> Any:
        return getattr(self._result, name)

    async def partitions(self, size: int | None = None) -> AsyncGenerator[Any, None]:
        source = self._result.partitions(size)
        while True:
            async with self._lock:
                try:
                    partition = await source.__anext__()
                except StopAsyncIteration:
                    return
            yield partition


# Connection management functions


def _dispose_later(db: Database) -> None:
    """Disconnect ``db`` from synchronous code without losing the engine.

    Inside a running loop the disconnect is scheduled (and the task kept
    referenced until it finishes); outside one, a sync engine is disposed
    directly and an async engine's pool is dropped without awaiting its
    connections, which is all that can be done without a loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        task = loop.create_task(db.disconnect())
        _pending_disposals.add(task)

        def _done(finished: asyncio.Task[Any]) -> None:
            _pending_disposals.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.warning(
                    "Disposing a replaced database connection failed",
                    exc_info=finished.exception(),
                )

        task.add_done_callback(_done)
        return
    if db._sync_engine is not None:
        db._sync_engine.dispose()
    if db._async_engine is not None:
        db._async_engine.sync_engine.dispose(close=False)
    db._async_engine = db._sync_engine = None
    db._async_session_factory = db._sync_session_factory = None
    db._connected = False


def _replace_connection(db: Database, alias: str) -> Database | None:
    """Register ``db`` under ``alias``; return the one it replaced, if orphaned.

    The replaced ``Database`` is returned only when no other alias still
    refers to it — that is the one the caller must dispose.
    """
    old = _connections.get(alias)
    _connections[alias] = db
    if old is None or old is db or any(other is old for other in _connections.values()):
        return None
    return old


def register_database(db: Database, alias: str = "default") -> None:
    """Register a database connection with an alias.

    Replacing an alias disposes the engine it held (unless that same
    ``Database`` is still registered under another alias), so re-registering
    never leaks a connection pool. Called inside a running event loop, the
    disposal is scheduled on it; :func:`setup_database` awaits it instead.
    """
    old = _replace_connection(db, alias)
    if old is not None:
        _dispose_later(old)


def _forget_settings_connection(config: DatabaseConfig) -> None:
    """Called by ``zeeb_orm.conf.configure()``: drop a now-stale default.

    The default connection :func:`get_connection` creates lazily is built
    from the settings in force at that moment. Once :func:`configure` points
    the settings elsewhere, that connection is discarded (and disposed) so
    the next query follows the new settings. A default registered explicitly
    (``setup_database``/``register_database``) is the caller's and stays.
    """
    db = _connections.get(_default_alias)
    if db is None or not getattr(db, "_from_settings", False):
        return
    if db.config == config:
        return
    del _connections[_default_alias]
    if not any(other is db for other in _connections.values()):
        _dispose_later(db)


def get_database(alias: str = "default") -> Database | None:
    """Get a registered database by alias."""
    return _connections.get(alias)


async def get_connection(alias: str | None = None) -> Database:
    """
    Get or create database connection.

    The default connection is created lazily from settings. Any other alias
    must have been registered via register_database() first - an unknown
    alias raises ConnectionDoesNotExist instead of silently falling back to
    the default database.
    """
    alias = alias or _default_alias

    if alias not in _connections:
        if alias != _default_alias:
            from zeeb_orm.exceptions import ConnectionDoesNotExist

            raise ConnectionDoesNotExist(
                f"The database connection {alias!r} doesn't exist. Register "
                "it with register_database(Database(...), alias=...) before "
                "using it."
            )
        settings = get_settings()
        db = Database(config=settings.database)
        db._from_settings = True
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

    Nested ``atomic()`` blocks on the same database become SAVEPOINTs: an
    inner block's failure rolls back to its savepoint without dooming the
    outer transaction, and an inner block's success only releases the
    savepoint - nothing is durable until the outermost block commits.

    ``on_commit`` callbacks run once, after the outermost commit and after
    the transaction's session is released — so a callback that queries or
    writes gets its own autocommitting session. Callbacks registered inside
    a rolled-back savepoint block are discarded. A callback that raises does
    not undo the commit: see :func:`zeeb_orm.db.transaction.on_commit`.

    The session is shared by everything running in the block — including
    tasks started with ``asyncio.gather()`` — and serialises their
    statements, since one session cannot run two at once.

    Usage:
        async with atomic() as session:
            await User.objects.create(name='John')
            await Post.objects.create(title='Hello')
            # Both committed together or rolled back on error
    """
    from zeeb_orm.db.transaction import _on_commit_callbacks, _run_on_commit_callbacks

    alias = using or _default_alias
    active = _active_session.get()

    if active is not None and _active_session_alias.get() == alias:
        # Nested block on the same database -> SAVEPOINT on the same session
        callbacks = _on_commit_callbacks.get()
        cb_mark = len(callbacks) if callbacks is not None else 0
        nested = await active.begin_nested()
        try:
            yield active
            await nested.commit()
        except Exception:
            await nested.rollback()
            # Discard callbacks registered inside the rolled-back block
            if callbacks is not None:
                del callbacks[cb_mark:]
            raise
        return

    db = await get_connection(alias)
    callbacks: list[Any] = []

    async with db.session() as raw_session:
        session = _SerializedSession(raw_session)
        token = _active_session.set(session)
        alias_token = _active_session_alias.set(alias)
        cb_token = _on_commit_callbacks.set(callbacks)
        try:
            yield session
            try:
                await session.commit()
            except _sa_exc.IntegrityError as exc:
                from zeeb_orm.exceptions import IntegrityError

                raise IntegrityError(str(exc.orig)) from exc
        except Exception:
            await session.rollback()
            raise
        finally:
            _on_commit_callbacks.reset(cb_token)
            _active_session_alias.reset(alias_token)
            _active_session.reset(token)

    # Committed, and the session is closed: nothing below can roll it back.
    await _run_on_commit_callbacks(callbacks)


def get_active_session_alias() -> str | None:
    """The alias of the active ``atomic()`` block, if any."""
    return _active_session_alias.get() if _active_session.get() is not None else None


_ANY_ALIAS: Any = object()


def get_active_session(using: str | None = _ANY_ALIAS) -> AsyncSession | None:
    """Get the active ``atomic()`` session, if any.

    With no argument, the session of whichever database the innermost
    ``atomic()`` block is on. With ``using`` (``None`` meaning the default
    alias), only a session on that database — a transaction open on another
    database is not one a write to ``using`` may join.
    """
    session = _active_session.get()
    if session is None or using is _ANY_ALIAS:
        return session
    return session if _active_session_alias.get() == (using or _default_alias) else None


@asynccontextmanager
async def get_session(db_alias: str | None = None) -> AsyncGenerator[tuple[AsyncSession, bool], None]:
    """
    Get a database session, reusing the active transaction session if available.

    Returns a tuple of (session, should_commit) where should_commit is False
    if using an active transaction (let atomic() handle the commit).

    The active session is only reused when it belongs to the same database
    alias; statements against a different database open their own session.

    Database integrity violations (unique/foreign-key/check/NOT NULL) raised
    inside the block surface as zeeb_orm.exceptions.IntegrityError.

    Usage:
        async with get_session() as (session, should_commit):
            result = await session.execute(stmt)
            if should_commit:
                await session.commit()
    """
    alias = db_alias or _default_alias
    active = _active_session.get()
    try:
        if active is not None and _active_session_alias.get() == alias:
            # Reuse existing transaction session - don't commit
            yield active, False
        else:
            # Create a new session - caller should commit
            db = await get_connection(alias)
            async with db.session() as session:
                yield session, True
    except _sa_exc.IntegrityError as exc:
        from zeeb_orm.exceptions import IntegrityError

        raise IntegrityError(str(exc.orig)) from exc


# Convenience functions for setup


async def setup_database(url: str, **kwargs: Any) -> Database:
    """
    Quick setup for database connection.

    Registers the database as the default alias, disconnecting the one it
    replaces (unless another alias still uses it).

    Usage:
        db = await setup_database('postgresql+asyncpg://localhost/mydb')
    """
    db = Database(url, **kwargs)
    await db.connect()
    old = _replace_connection(db, _default_alias)
    if old is not None:
        # The default this replaces is closed, not leaked.
        await old.disconnect()
    return db
