"""Transaction management utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Context variable for on_commit callback registry
_on_commit_callbacks: ContextVar[list[Callable[[], Any]] | None] = ContextVar(
    "_on_commit_callbacks", default=None
)


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

    Delegates to :func:`zeeb_orm.db.connection.atomic`, so the transaction
    participates in the active-session context (queries inside the block
    reuse the transaction session) and nested blocks become SAVEPOINTs.

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
        self._cm: Any = None

    async def __aenter__(self) -> AsyncSession:
        from zeeb_orm.db.connection import atomic as _atomic

        self._cm = _atomic(self.using)
        return await self._cm.__aenter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        cm, self._cm = self._cm, None
        if cm is None:
            return None
        return await cm.__aexit__(exc_type, exc_val, exc_tb)

    def __call__(self, func: Any) -> Any:
        """Decorator support (a fresh transaction per call)."""
        import functools

        from zeeb_orm.db.connection import atomic as _atomic

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with _atomic(self.using):
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
    """
    callbacks = _on_commit_callbacks.get()
    if callbacks is None:
        raise RuntimeError(
            "on_commit() can only be called inside an atomic() block."
        )
    callbacks.append(func)


def _run_on_commit_callbacks() -> None:
    """Execute all registered on_commit callbacks."""
    import asyncio

    callbacks = _on_commit_callbacks.get()
    if not callbacks:
        return

    for callback in callbacks:
        result = callback()
        # Support async callbacks
        if asyncio.iscoroutine(result):
            asyncio.ensure_future(result)
