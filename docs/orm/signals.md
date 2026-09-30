# Signals

`zeeb_orm` provides a signal system for observing model lifecycle events
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

### Which write paths fire them

| Method | Save signals |
|--------|--------------|
| `Model.save()` | yes |
| `objects.create()` | yes (`created=True`) — it calls `save()` |
| `objects.get_or_create()` | yes, on the create branch only |
| `objects.update_or_create()` | yes, on both branches |
| `objects.bulk_create()` / `bulk_update()` | **no** |
| `QuerySet.update()` | **no** |

The bulk paths are deliberately signal-free: they write set at a time and
never build the instances a receiver would expect. Use `Model.save()` in a
loop when receivers must run.

`Model.delete()` and `QuerySet.delete()` both fire the delete signals per
instance. `QuerySet.delete()` normally collapses to a single DELETE
statement when nothing references the model, but takes the per-instance
route as soon as a receiver is connected — check with
`pre_delete.has_listeners(MyModel)`.

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
        await s.commit()             (skipped inside atomic(): the block commits)
    post_save.send(...)              ← fires AFTER the write

delete():
    async with atomic():             ← joins an active atomic() block instead
        collect related rows
        pre_delete.send(...)         ← per instance, just before its row goes
        ...DELETE...
        post_delete.send(...)        ← per instance, once the rows are gone
    commit
```

**`pre_save`:** If a receiver raises, the exception propagates and the DB
operation is never attempted — nothing is written.

**`post_save`:** Fires after the write — after the commit when `save()` opened
its own session. A receiver exception propagates to the caller but **cannot
roll back** the committed data.

**`pre_delete` / `post_delete`:** Fire inside the delete's transaction. A
receiver that raises rolls the whole delete back (cascades
included).

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

`post_save` fires per-operation, not per-transaction.
`on_commit` defers the callback until the outermost `atomic()` block commits.
Callbacks run after the commit, in registration order; an `async` callback is
awaited before `atomic()` returns. A callback that raises cannot undo the
commit: by default the exception propagates out of the `atomic()` block (and
the remaining callbacks are skipped); register it with
`on_commit(func, robust=True)` to have the error logged instead.

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
