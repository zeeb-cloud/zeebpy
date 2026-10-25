"""One target project at a time per process.

The agent layer works on projects by path and imports their code into its own
process: a project's ``settings.py`` runs, its ``apps.*`` models land in the
ORM's model registry, its root goes on ``sys.path``. All of that is process
global, and every generated project names its packages ``apps.*`` — so in a
long-lived server serving two projects, the second project's tool call would
read the first project's models, and whatever ``DATABASE_URL`` the server
itself was started with would become every project's database.

:func:`project_scope` is what the ``@agent_function`` decorator runs each call
in:

- calls on the *same* project run concurrently; a call on another project
  waits until they are done (a switch never happens under a running call);
- on a switch, the previous project's imported code and models are dropped
  (:func:`~zeeb_agents._utils.project.forget_loaded_project_code`), and the
  cached user model and auth project root are reset;
- the call evaluates settings inside :func:`zeeb_api.conf.env.isolated_env`,
  so the project's ``.env`` is its only environment.

A nested agent call on the same project joins the running scope.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger("zeeb_agents")

# The project whose code this process has imported, if any.
_loaded_root: Path | None = None

# The project of the scope this task is running in (nested calls join it).
_current: ContextVar[Path | None] = ContextVar("zeeb_agents_project_scope", default=None)


class _Gate:
    """Admission for one event loop: the project being served, and how many calls."""

    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.root: Path | None = None
        self.active = 0


_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate] = (
    weakref.WeakKeyDictionary()
)


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = _Gate()
    return gate


def _switch_to(root: Path) -> None:
    """Make *root* the project whose code is loaded, dropping the previous one's."""
    global _loaded_root
    previous = _loaded_root
    _loaded_root = root
    if previous is None or previous == root:
        return
    from zeeb_agents._utils.project import forget_loaded_project_code

    forget_loaded_project_code(previous)
    try:
        from zeeb_api.auth.backends import clear_user_model_cache, set_project_root

        set_project_root(None)
        clear_user_model_cache()
    except ImportError:  # pragma: no cover — zeeb_api ships in the same wheel
        pass


@asynccontextmanager
async def project_scope(root: Path | None) -> AsyncIterator[None]:
    """Run an agent call on *root* with the process to itself (see module docstring)."""
    if root is None:
        yield
        return
    root = Path(root).resolve()
    current = _current.get()
    if current is not None:
        if current != root:
            # Waiting here would deadlock on our own outer call; proceed, and
            # say so — the outer call's project is replaced under it.
            logger.warning(
                "agent call on %s nested inside a call on %s; the projects share the process",
                root,
                current,
            )
            _switch_to(root)
        yield
        return

    from zeeb_api.conf.env import isolated_env

    gate = _gate()
    async with gate.condition:
        await gate.condition.wait_for(lambda: gate.active == 0 or gate.root == root)
        gate.root = root
        gate.active += 1
    token = _current.set(root)
    try:
        _switch_to(root)
        with isolated_env():
            yield
    finally:
        _current.reset(token)
        async with gate.condition:
            gate.active -= 1
            gate.condition.notify_all()


def loaded_project_root() -> Path | None:
    """The project whose code this process has imported (introspection, tests)."""
    return _loaded_root
