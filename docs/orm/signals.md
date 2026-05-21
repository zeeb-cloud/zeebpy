# Signals

`zeeb_orm` provides a Django-style signal system for observing model lifecycle events
without subclassing. Signals decouple the notification from the action — any number of
receivers can react to a `save()` or `delete()` without the model knowing about them.

---

## Built-in signals

| Signal | Fires | kwargs |
|--------|-------|--------|
| `pre_save` | Before any DB operation in `save()` | `instance`, `created`, `update_fields` |
| `post_save` | After the DB commit in `save()` | `instance`, `created`, `update_fields` |
| `pre_delete` | Before any DB operation in `delete()` | `instance` |
| `post_delete` | After the DB commit in `delete()` | `instance` |

`created` is `True` for `INSERT`, `False` for `UPDATE`.

---

## Connecting receivers

### Using the `@receiver` decorator (recommended)

```python
from zeeb_orm.signals import receiver, post_save
from myapp.models import Article

@receiver(post_save, sender=Article)
async def on_article_saved(sender, instance, created, **kwargs):
    if created:
        print(f"New article: {instance.title}")
```

### Using `Signal.connect()`

```python
from zeeb_orm.signals import post_save
from myapp.models import Article

async def my_handler(sender, instance, created, **kwargs):
    ...

post_save.connect(my_handler, sender=Article)
```

### Disconnecting

```python
post_save.disconnect(my_handler, sender=Article)
```

---

## Sender filtering

Pass `sender=MyModel` to receive signals only for that model.
Omit `sender` (or pass `None`) to receive signals from **all** senders.

```python
@receiver(pre_save)          # fires for every model
async def audit_all(sender, instance, **kwargs): ...

@receiver(pre_save, sender=Article)   # fires only for Article
async def audit_article(sender, instance, **kwargs): ...
```

---

## Sync and async receivers

Both sync and async receivers are supported:

```python
@receiver(post_save, sender=Article)
def sync_handler(sender, instance, **kwargs):   # plain def — called synchronously
    cache.invalidate(instance.pk)

@receiver(post_save, sender=Article)
async def async_handler(sender, instance, **kwargs):   # async def — awaited
    await notify_subscribers(instance)
```

---

## Preventing duplicate connections

Use `dispatch_uid` to ensure a receiver is only registered once, even if the module
is imported multiple times:

```python
post_save.connect(my_handler, sender=Article, dispatch_uid="article_post_save_notify")
```

---

## Signal API

### `Signal`

```python
class Signal:
    def connect(
        receiver,
        sender=None,
        weak=True,
        dispatch_uid=None,
    ) -> None: ...

    def disconnect(
        receiver=None,
        sender=None,
        dispatch_uid=None,
    ) -> bool: ...

    async def send(sender, **kwargs) -> list[tuple[callable, Any]]: ...
    async def send_robust(sender, **kwargs) -> list[tuple[callable, Any | Exception]]: ...
```

`send()` raises on the first receiver exception, aborting remaining receivers.  
`send_robust()` catches per-receiver exceptions and returns them as `(receiver, Exception)` tuples.

### `receiver(signal, sender=None, weak=True, dispatch_uid=None)`

Decorator that calls `signal.connect(func, ...)` when applied.

---

## Transaction compliance

The signal hooks respect the ORM's session lifecycle:

```
save():
    pre_save.send(...)               ← fires BEFORE session opens
    async with db.session() as s:
        ...INSERT/UPDATE...
        await s.commit()
    post_save.send(...)              ← fires AFTER commit

delete():
    pre_delete.send(...)             ← fires BEFORE session opens
    async with db.session() as s:
        ...DELETE...
        await s.commit()
    post_delete.send(...)            ← fires AFTER commit
```

**`pre_*` signals:** If a receiver raises, the exception propagates and the DB operation
is never attempted — nothing is written.

**`post_*` signals:** Fire after the commit. The data is already in the DB.
A receiver exception propagates to the caller but **cannot roll back** the committed data.

### Using `on_commit` for post-transaction safety

If you need a side-effect to run only after the outermost `atomic()` block commits,
call `on_commit()` from inside your receiver:

```python
from zeeb_orm.db.transaction import on_commit

@receiver(post_save, sender=Order)
async def on_order_saved(sender, instance, created, **kwargs):
    if created:
        on_commit(lambda: send_confirmation_email(instance.email))
```

`post_save` fires per-operation (like Django), not per-transaction.
`on_commit` defers the callback until the outermost `atomic()` block commits.

---

## Custom signals

You can create your own signals for any event:

```python
from zeeb_orm.signals import Signal

user_activated = Signal()

# somewhere in your code
await user_activated.send(sender=User, instance=user)

# in a receiver
@receiver(user_activated, sender=User)
async def on_activation(sender, instance, **kwargs):
    await send_welcome_email(instance)
```

---

## Weak references

By default, receivers are stored as weak references. This means if the function or
bound method is garbage-collected, it is silently pruned from the receiver list.

To keep a reference alive regardless of scope, pass `weak=False`:

```python
post_save.connect(my_handler, sender=Article, weak=False)
```

---

## Scaffolding receivers with `zeeb_agents`

The `zeeb_agents` package provides helpers to create and manage signal receivers:

```python
from zeeb_agents import create_signal_receiver, list_signal_receivers

# Create a new receiver stub
await create_signal_receiver(
    app="blog",
    signal_name="post_save",
    model_name="Article",
    function_name="on_article_saved",
)

# List all receivers in an app
result = await list_signal_receivers(app="blog")
print(result.data["receivers"])
```

See [Agent Functions — Signals](../cli/agents.md#signals-scaffolding) for the full API.
