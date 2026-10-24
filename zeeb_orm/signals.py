"""Django-style signals for zeeb_orm.

Signals allow decoupled components to get notified when certain actions occur::

    from zeeb_orm.signals import post_save, receiver
    from myapp.models import Post

    @receiver(post_save, sender=Post)
    async def on_post_saved(sender, instance, created, **kwargs):
        if created:
            print(f"New post created: {instance.title}")

Built-in signals:

- :data:`pre_save`  — fired before ``Model.save()``
- :data:`post_save` — fired after ``Model.save()``
- :data:`pre_delete`  — fired before ``Model.delete()``
- :data:`post_delete` — fired after ``Model.delete()``

Transaction compliance
----------------------
- ``pre_save`` fires **before** the database session is opened.  If a
  receiver raises, the save is aborted and nothing reaches the database.
- ``post_save`` fires after the write — after ``session.commit()`` when the
  save opened its own session, before the commit inside an ``atomic()``
  block. A receiver that raises after a commit cannot undo it.
- ``pre_delete`` and ``post_delete`` fire **inside** the delete's
  transaction, as in Django: ``Model.delete()`` and the collector path of
  ``QuerySet.delete()`` open an ``atomic()`` block (or join the active one),
  collect the affected rows, send ``pre_delete`` for each just before its
  row is deleted and ``post_delete`` once the rows are gone. A receiver that
  raises rolls the whole delete back.
- If you need work to happen only after the outermost ``atomic()`` block
  commits, use :func:`zeeb_orm.db.transaction.on_commit` from your receiver.
"""

from __future__ import annotations

import inspect
import weakref
from typing import Any, Callable


def _make_id(target: Any) -> Any:
    """A key identifying ``target`` for as long as it lives.

    A bound method is a new object on every attribute access, so it is keyed
    by its instance and function instead (Django's ``_make_id``).
    """
    if hasattr(target, "__func__") and hasattr(target, "__self__"):
        return (id(target.__self__), id(target.__func__))
    return id(target)


def _resolve(ref: Any) -> Any:
    """The receiver behind ``ref`` (``None`` once a weak reference died)."""
    if isinstance(ref, weakref.ref):  # WeakMethod is a weakref.ref subclass
        return ref()
    return ref


class Signal:
    """A signal dispatcher.

    Receivers can be connected with :meth:`connect` or the :func:`receiver`
    decorator and will be called when :meth:`send` is invoked.

    Args:
        providing_args: Optional list of kwarg names the signal provides
                        (documentation only; not enforced).
    """

    def __init__(self, providing_args: list[str] | None = None) -> None:
        self.providing_args: list[str] = providing_args or []
        # List of (lookup_key, receiver_ref) where lookup_key =
        # (_make_id(receiver) or ("uid", dispatch_uid), sender).
        self._receivers: list[tuple[tuple[Any, type | None], Any]] = []

    def _clear_dead(self) -> None:
        """Drop entries whose weakly referenced receiver was collected.

        Must run before any key comparison: a dead entry keeps the ``id()``
        of a receiver that no longer exists, and CPython reuses addresses, so
        a new receiver could otherwise be taken for a duplicate and silently
        not be connected (or be disconnected by mistake).
        """
        self._receivers = [
            (key, ref) for key, ref in self._receivers if _resolve(ref) is not None
        ]

    @staticmethod
    def _lookup_key(
        receiver: Callable, sender: type | None, dispatch_uid: str | None
    ) -> tuple[Any, type | None]:
        if dispatch_uid:
            return (("uid", dispatch_uid), sender)
        return (_make_id(receiver), sender)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def connect(
        self,
        receiver: Callable,
        sender: type | None = None,
        weak: bool = True,
        dispatch_uid: str | None = None,
    ) -> None:
        """Connect a receiver function to this signal.

        Args:
            receiver: A callable (sync or async) that handles the signal.
            sender: If given, only receive signals from this model class.
                    ``None`` means "any sender".
            weak: Store a weak reference to the receiver.  Set to ``False``
                  if the receiver is a lambda or local function that would
                  otherwise be garbage-collected.
            dispatch_uid: A unique string key to prevent duplicate connections.
        """
        lookup_key = self._lookup_key(receiver, sender, dispatch_uid)

        # Avoid duplicates (among live receivers only, see _clear_dead)
        self._clear_dead()
        for existing_key, _ in self._receivers:
            if existing_key == lookup_key:
                return

        if weak:
            if hasattr(receiver, "__self__"):
                # Bound method
                ref: Any = weakref.WeakMethod(receiver)  # type: ignore[arg-type]
            else:
                ref = weakref.ref(receiver)
        else:
            ref = receiver  # strong reference

        self._receivers.append((lookup_key, ref))

    def disconnect(
        self,
        receiver: Callable,
        sender: type | None = None,
        dispatch_uid: str | None = None,
    ) -> bool:
        """Disconnect a receiver.

        Returns ``True`` if the receiver was found and removed.
        """
        lookup_key = self._lookup_key(receiver, sender, dispatch_uid)

        self._clear_dead()
        before = len(self._receivers)
        self._receivers = [
            (k, r) for k, r in self._receivers if k != lookup_key
        ]
        return len(self._receivers) < before

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _live_receivers(self, sender: type) -> list[Callable]:
        """Return receivers that are alive and match *sender*."""
        self._clear_dead()
        live: list[Callable] = []
        for lookup_key, ref in self._receivers:
            _, registered_sender = lookup_key
            if registered_sender is not None and registered_sender is not sender:
                continue
            func = _resolve(ref)
            if func is not None:
                live.append(func)
        return live

    def has_listeners(self, sender: type) -> bool:
        """Whether any live receiver would run for *sender*.

        Lets callers skip a set-based fast path that cannot fire signals —
        :meth:`zeeb_orm.query.QuerySet.delete` uses it to decide between a
        single DELETE statement and the per-instance collector.
        """
        return bool(self._live_receivers(sender))

    async def send(self, sender: type, **kwargs: Any) -> list[tuple[Callable, Any]]:
        """Fire all connected receivers.

        Both sync and async receivers are supported: whatever a receiver
        returns that is awaitable is awaited — an ``async def`` function or
        method, an object with an ``async def __call__``, a sync function
        returning a coroutine.

        Raises the first exception encountered.

        Returns:
            List of ``(receiver, response)`` tuples.
        """
        responses: list[tuple[Callable, Any]] = []
        for recv in self._live_receivers(sender):
            response = recv(sender=sender, **kwargs)
            if inspect.isawaitable(response):
                response = await response
            responses.append((recv, response))
        return responses

    async def send_robust(
        self, sender: type, **kwargs: Any
    ) -> list[tuple[Callable, Any]]:
        """Fire all receivers, catching exceptions per receiver.

        Unlike :meth:`send`, ``send_robust`` continues calling remaining
        receivers even if one raises.  Exceptions are returned as the
        response value (not re-raised).

        Returns:
            List of ``(receiver, response_or_exception)`` tuples.
        """
        responses: list[tuple[Callable, Any]] = []
        for recv in self._live_receivers(sender):
            try:
                response: Any = recv(sender=sender, **kwargs)
                if inspect.isawaitable(response):
                    response = await response
            except Exception as exc:
                response = exc
            responses.append((recv, response))
        return responses


def receiver(
    signal: Signal | list[Signal],
    sender: type | None = None,
    weak: bool = True,
    dispatch_uid: str | None = None,
) -> Callable:
    """Decorator to connect a receiver function to one or more signals.

    Args:
        signal: A :class:`Signal` instance or list of instances.
        sender: If given, only receive from this model class.
        weak: Store a weak reference (default ``True``).
        dispatch_uid: Unique key to prevent duplicate connections.

    Usage::

        @receiver(post_save, sender=MyModel)
        async def my_handler(sender, instance, created, **kwargs):
            ...

        @receiver([pre_save, post_save])
        async def any_save(sender, instance, **kwargs):
            ...
    """
    def _decorator(func: Callable) -> Callable:
        signals = signal if isinstance(signal, list) else [signal]
        for sig in signals:
            sig.connect(func, sender=sender, weak=weak, dispatch_uid=dispatch_uid)
        return func

    return _decorator


# ---------------------------------------------------------------------------
# Built-in signal instances
# ---------------------------------------------------------------------------

pre_save = Signal(providing_args=["instance", "created", "update_fields"])
"""Sent at the start of :meth:`~zeeb_orm.models.base.Model.save`.

Keyword arguments:

- ``sender`` — the model class
- ``instance`` — the model instance being saved
- ``created`` — ``True`` if this is a new record (INSERT), ``False`` for UPDATE
- ``update_fields`` — list of field names being updated, or ``None``
"""

post_save = Signal(providing_args=["instance", "created", "update_fields"])
"""Sent at the end of :meth:`~zeeb_orm.models.base.Model.save`, after commit.

Same keyword arguments as :data:`pre_save`.
"""

pre_delete = Signal(providing_args=["instance"])
"""Sent for every instance :meth:`~zeeb_orm.models.base.Model.delete` removes.

Fires inside the delete's transaction, just before the instance's row is
deleted (cascaded rows included); raising rolls the delete back.

Keyword arguments:

- ``sender`` — the model class
- ``instance`` — the model instance being deleted
"""

post_delete = Signal(providing_args=["instance"])
"""Sent for every deleted instance once the rows are gone.

Still inside the delete's transaction (Django parity); use
:func:`~zeeb_orm.db.transaction.on_commit` for work that must wait for the
commit.

The instance ``pk`` is still set so receivers can identify what was deleted.
Same keyword arguments as :data:`pre_delete`.
"""
