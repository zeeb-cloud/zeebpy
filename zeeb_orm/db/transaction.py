"""Transaction management utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncGenerator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TransactionManager:
    """
    Manages database transactions with savepoint support.

    Usage:
        async with TransactionManager(session) as tx:
            # operations here
            async with tx.savepoint():
                # nested operations
                # can be rolled back independently
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._savepoint_count = 0

    async def __aenter__(self) -> TransactionManager:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            await self._session.rollback()
        else:
            await self._session.commit()

    @asynccontextmanager
    async def savepoint(self, name: str | None = None) -> AsyncGenerator[None, None]:
        """Create a savepoint for nested transactions."""
        self._savepoint_count += 1
        savepoint_name = name or f"sp_{self._savepoint_count}"

        async with self._session.begin_nested():
            try:
                yield
            except Exception:
                raise


class Atomic:
    """
    Decorator and context manager for atomic transactions.

    Usage as context manager:
        async with Atomic():
            await User.objects.create(name='John')
            await Post.objects.create(title='Hello')

    Usage as decorator:
        @Atomic()
        async def create_user_with_posts(name: str):
            user = await User.objects.create(name=name)
            await Post.objects.create(author=user, title='First post')
            return user
    """

    def __init__(self, using: str | None = None, savepoint: bool = True) -> None:
        self.using = using
        self.savepoint = savepoint
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Atomic:
        from zeeb_orm.db.connection import get_connection

        db = await get_connection(self.using)
        self._session = await db.session().__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session is None:
            return

        if exc_type is not None:
            await self._session.rollback()
        else:
            await self._session.commit()

        await self._session.__aexit__(exc_type, exc_val, exc_tb)

    def __call__(self, func: Any) -> Any:
        """Decorator support."""
        import functools

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with self:
                return await func(*args, **kwargs)

        return wrapper


# Convenience alias
atomic = Atomic


def on_commit(func: Any, using: str | None = None) -> None:
    """
    Register a callback to be called after the current transaction commits.

    Usage:
        def send_email():
            # This runs after the transaction commits
            pass

        async with atomic():
            await User.objects.create(name='John')
            on_commit(send_email)
    
    Note: This feature requires SQLAlchemy event system integration.
    See: https://docs.sqlalchemy.org/en/20/orm/session_events.html
    """
    raise NotImplementedError(
        "on_commit is not yet implemented. "
        "Consider using SQLAlchemy's after_commit event directly."
    )
