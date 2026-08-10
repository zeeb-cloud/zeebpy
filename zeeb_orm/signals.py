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
- ``pre_*`` signals fire **before** the database session is opened.  If a
  receiver raises an exception the save/delete is aborted and nothing reaches
  the database.
- ``post_*`` signals fire **after** ``session.commit()``.  Data is in the
  database at that point; exceptions in receivers propagate but cannot roll
  back the already-committed write.
- If you need work to happen only after the outermost ``atomic()`` block
  commits, use :func:`zeeb_orm.db.transaction.on_commit` from your receiver.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any, Callable


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
        # List of (lookup_key, receiver_ref) where lookup_key = (id(receiver), sender)
        self._receivers: list[tuple[tuple[int, type | None], Any]] = []

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
        if dispatch_uid:
            lookup_key = (hash(dispatch_uid), sender)
        else:
            lookup_key = (id(receiver), sender)

        # Avoid duplicates
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
        if dispatch_uid:
            lookup_key = (hash(dispatch_uid), sender)
        else:
            lookup_key = (id(receiver), sender)

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
        live: list[Callable] = []
        dead_keys: list[tuple[int, type | None]] = []

        for lookup_key, ref in self._receivers:
            _, registered_sender = lookup_key

            # Filter by sender
            if registered_sender is not None and registered_sender is not sender:
                continue

            # Resolve weak ref
            if callable(ref) and not isinstance(ref, (weakref.ref, weakref.WeakMethod)):
                func = ref  # strong reference
            else:
                func = ref()  # resolve weak ref
                if func is None:
                    dead_keys.append(lookup_key)
                    continue

            live.append(func)

        # Prune dead weak refs
        if dead_keys:
            self._receivers = [
                (k, r) for k, r in self._receivers if k not in dead_keys
            ]

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

        Both sync and async receivers are supported.  Async receivers are
        awaited; sync receivers are called directly.

        Raises the first exception encountered.

        Returns:
            List of ``(receiver, response)`` tuples.
        """
        responses: list[tuple[Callable, Any]] = []
        for recv in self._live_receivers(sender):
            if asyncio.iscoroutinefunction(recv):
                response = await recv(sender=sender, **kwargs)
            else:
                response = recv(sender=sender, **kwargs)
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
                if asyncio.iscoroutinefunction(recv):
                    response: Any = await recv(sender=sender, **kwargs)
                else:
                    response = recv(sender=sender, **kwargs)
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
"""Sent at the start of :meth:`~zeeb_orm.models.base.Model.delete`.

Keyword arguments:

- ``sender`` — the model class
- ``instance`` — the model instance being deleted
"""

post_delete = Signal(providing_args=["instance"])
"""Sent after :meth:`~zeeb_orm.models.base.Model.delete` commits.

The instance ``pk`` is still set so receivers can identify what was deleted.
Same keyword arguments as :data:`pre_delete`.
"""
