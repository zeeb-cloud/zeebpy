"""Transaction management utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Context variable for on_commit callback registry: (callback, robust) pairs
_on_commit_callbacks: ContextVar[list[tuple[Callable[[], Any], bool]] | None] = ContextVar(
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


def on_commit(func: Any, using: str | None = None, robust: bool = False) -> None:
    """
    Register a callback to be called after the current transaction commits.

    Django semantics: callbacks run in registration order, once, after the
    outermost ``atomic()`` block has committed and released its session
    (so a callback that queries or writes runs outside the transaction).
    A callback may be a coroutine function (or return an awaitable); it is
    awaited before ``atomic()`` returns — never scheduled and forgotten.

    The transaction is already committed when callbacks run, so a failing
    callback cannot roll anything back:

    - ``robust=False`` (default): the exception propagates out of the
      ``atomic()`` block and the remaining callbacks are skipped.
    - ``robust=True``: the exception is logged (logger
      ``"zeeb_orm.db.transaction"``) and the next callback runs.

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
    callbacks.append((func, robust))


async def _run_on_commit_callbacks(callbacks: list[Any] | None = None) -> None:
    """Run registered on_commit callbacks after a commit (see :func:`on_commit`)."""
    import inspect
    import logging

    if callbacks is None:
        callbacks = _on_commit_callbacks.get()
    if not callbacks:
        return

    logger = logging.getLogger("zeeb_orm.db.transaction")
    pending = list(callbacks)
    callbacks.clear()
    for entry in pending:
        callback, robust = entry if isinstance(entry, tuple) else (entry, False)
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            if not robust:
                raise
            logger.exception("on_commit callback %r failed", callback)
