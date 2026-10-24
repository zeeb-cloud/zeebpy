"""
Migration executor — loads, orders, and runs migrations.

Tracks applied migrations in a ``zeeb_migrations`` table, similar to
Django's ``django_migrations`` table.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from zeeb_orm.db.urls import sync_database_url
from zeeb_orm.migrations.migration import Migration

# ---------------------------------------------------------------------------
# Migration tracking table
# ---------------------------------------------------------------------------

_TRACKING_TABLE = "zeeb_migrations"


def tracking_table() -> Any:
    """The ``zeeb_migrations`` table, defined once and rendered per dialect.

    Built with SQLAlchemy rather than a DDL string so the auto-increment
    primary key is right on every backend: ``INTEGER PRIMARY KEY`` on
    SQLite, ``SERIAL`` on PostgreSQL, ``AUTO_INCREMENT`` on MySQL (whose
    parser rejects SQLite's ``AUTOINCREMENT``). Existing tables created by
    the old DDL have the same columns and are used as they are.
    """
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

    return Table(
        _TRACKING_TABLE,
        MetaData(),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(255), nullable=False, unique=True),
        Column("applied_at", DateTime(), nullable=False),
    )


# ---------------------------------------------------------------------------
# Loading migration files
# ---------------------------------------------------------------------------

def get_migrations_dir(project_root: Path | None = None) -> Path:
    """Return the project's migrations directory (``<root>/migrations``)."""
    if project_root is None:
        project_root = _find_project_root() or Path.cwd()
    return project_root / "migrations"


def _find_project_root() -> Path | None:
    from zeeb_orm.migrations.state import find_project_root
    return find_project_root()


def _resolve_dir(project_root: Path | None, migrations_dir: Path | str | None) -> Path:
    return Path(migrations_dir) if migrations_dir is not None else get_migrations_dir(project_root)


def list_migration_files(migrations_dir: Path) -> list[tuple[str, Path]]:
    """
    Return ``[(name, path), ...]`` sorted by name.

    Migration files match ``NNNN_description.py`` (e.g. ``0001_initial.py``).
    """
    if not migrations_dir.exists():
        return []

    pattern = re.compile(r"^\d{4}_.+\.py$")
    files = []
    for p in sorted(migrations_dir.iterdir()):
        if pattern.match(p.name) and p.is_file():
            files.append((p.stem, p))
    return files


def find_migration_files(migrations_dir: Path) -> list[tuple[str, Path]]:
    """Like :func:`list_migration_files`, but tolerant of the legacy layout.

    ``write_migration`` puts migrations flat into ``migrations/``, and the
    executor, the autodetector and the state module all read them from there.
    Projects scaffolded by older versions additionally got an empty
    ``migrations/versions/`` directory; a few of them have migrations in it.
    The pre-flight checks in ``runserver`` and ``check`` use this so both
    layouts are found (and reported, see :func:`legacy_migration_files`).
    """
    return list_migration_files(migrations_dir) or list_migration_files(
        migrations_dir / "versions"
    )


def legacy_migration_files(migrations_dir: Path) -> list[str]:
    """Migrations under ``migrations/versions/`` that the engine would not see.

    Those whose name is also a flat file (a copy already moved up) are fine.
    """
    flat = {name for name, _ in list_migration_files(migrations_dir)}
    return [
        name
        for name, _ in list_migration_files(migrations_dir / "versions")
        if name not in flat
    ]


def check_layout(migrations_dir: Path) -> None:
    """Refuse to work on a directory whose migrations sit in ``versions/``.

    Raises:
        MigrationLayoutError: ``migrations/versions/`` holds migration files
            that ``migrations/`` does not; they would be silently ignored.
    """
    from zeeb_orm.migrations.state import MigrationLayoutError

    legacy = legacy_migration_files(migrations_dir)
    if legacy:
        raise MigrationLayoutError(
            f"{len(legacy)} migration(s) are in {migrations_dir / 'versions'} "
            f"({', '.join(legacy)}), which migrate and makemigrations do not read. "
            f"Move them up into {migrations_dir} (e.g. "
            f"'mv migrations/versions/*.py migrations/') and run the command again."
        )


def load_migration(path: Path) -> Migration:
    """Import a migration file and return its ``Migration`` instance.

    Raises:
        MigrationLoadError: the file does not load (a syntax error, an
            exception at import) or defines no ``Migration`` class.
    """
    from zeeb_orm.migrations.serializer import MIGRATION_MODULE_PREFIX
    from zeeb_orm.migrations.state import MigrationLoadError

    spec = importlib.util.spec_from_file_location(f"{MIGRATION_MODULE_PREFIX}{path.stem}", path)
    if spec is None or spec.loader is None:
        raise MigrationLoadError(f"Cannot load migration: {path}", path=path)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise MigrationLoadError(
            f"Migration {path.name} is not valid Python ({exc.msg}, line {exc.lineno}).",
            path=path,
        ) from exc
    except Exception as exc:
        raise MigrationLoadError(
            f"Migration {path.name} failed to load ({type(exc).__name__}: {exc}).",
            path=path,
        ) from exc

    # Find the Migration subclass in the module
    for name in dir(module):
        obj = getattr(module, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, Migration)
            and obj is not Migration
        ):
            return obj()

    raise MigrationLoadError(f"No Migration class found in {path}", path=path)


def _load_all(migrations_dir: Path) -> list[tuple[str, Path, Migration]]:
    """Load every migration file once, in filename order."""
    return [
        (name, path, load_migration(path))
        for name, path in list_migration_files(migrations_dir)
    ]


# ---------------------------------------------------------------------------
# The migration graph
# ---------------------------------------------------------------------------

class MigrationGraph:
    """The migrations in a directory, as a database in a given state sees them.

    Migrations are applied in dependency order — a topological sort of their
    ``dependencies``, file names breaking ties — not merely in file-name
    order, so a squash sorts correctly wherever its file name puts it.

    A squash (a migration with ``replaces``) stands in for the migrations it
    replaces when either all or none of them are applied: those are then not
    applied on their own, and a dependency on any of them means the squash. A
    squash whose replaced migrations are only partly applied cannot be used —
    its operations would re-run on top of a half-built schema — so the
    remaining originals are applied instead, and the squash is recorded once
    they all are.

    Args:
        loaded: ``(name, path, migration)`` for every file, in file-name order.
        recorded: Names in the ``zeeb_migrations`` table.

    Raises:
        MigrationError: a dependency cycle, or a partly applied squash whose
            remaining originals are missing.
    """

    def __init__(
        self,
        loaded: list[tuple[str, Path, Migration]],
        recorded: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        from zeeb_orm.migrations.state import MigrationError

        self.files = [name for name, _, _ in loaded]
        self.migrations = {name: mig for name, _, mig in loaded}
        self.recorded = set(recorded)
        position = {name: i for i, name in enumerate(self.files)}

        #: Squashes standing in for their replaced migrations.
        self.squashes: dict[str, Migration] = {}
        #: Squashes not usable here (partly applied); their originals run instead.
        self.unused_squashes: dict[str, Migration] = {}
        self._remap: dict[str, str] = {}
        for name, mig in self.migrations.items():
            if not mig.replaces:
                continue
            replaced = list(mig.replaces)
            done = [r in self.recorded for r in replaced]
            if all(done) or not any(done):
                self.squashes[name] = mig
                for original in replaced:
                    self._remap[original] = name
                continue
            missing = [r for r in replaced if r not in self.migrations]
            if missing:
                raise MigrationError(
                    f"The squashed migration '{name}' cannot be applied: the database "
                    f"has only some of the migrations it replaces, and "
                    f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} no "
                    f"longer on disk to finish them one by one. Restore "
                    f"{'that file' if len(missing) == 1 else 'those files'} and migrate "
                    f"again."
                )
            self.unused_squashes[name] = mig
            self._remap[name] = replaced[-1]

        nodes = [
            name
            for name in self.files
            if name not in self.unused_squashes
            and self._remap.get(name) not in self.squashes
        ]
        self.order = self._sort(nodes, position)
        self.applied = {name for name in self.order if self.is_applied(name)}

    def resolve(self, name: str) -> str:
        """The node a dependency or target name stands for here."""
        for _ in range(3):  # a squash of squashes remaps at most twice
            name = self._remap.get(name, name)
        return name

    def dependencies(self, name: str) -> list[str]:
        """*name*'s dependencies, mapped onto the nodes of this graph."""
        deps: list[str] = []
        for dep in self.migrations[name].dependencies:
            node = self.resolve(dep)
            if node != name and node not in deps:
                deps.append(node)
        return deps

    def is_applied(self, name: str) -> bool:
        """Whether node *name* counts as applied (a squash: all it replaces is)."""
        if name in self.recorded:
            return True
        squash = self.squashes.get(name)
        return squash is not None and all(r in self.recorded for r in squash.replaces)

    def originals(self, squash: str) -> list[str] | None:
        """The migrations a squash in use replaces, in order — or ``None``
        when any of them is no longer on disk (then it cannot be stepped
        through, only applied or unapplied whole)."""
        replaced = [
            name for name in self.squashes[squash].replaces
            if not (name in self.migrations and self.migrations[name].replaces)
        ]
        if not all(name in self.migrations for name in replaced):
            return None
        return replaced

    def history(self) -> list[str]:
        """The apply order at its finest grain.

        Like :attr:`order`, but a squash in use whose original files are all
        still on disk stands for those originals — so stepping back one
        migration from a squash unapplies its last original, not everything
        it replaces.
        """
        steps: list[str] = []
        for name in self.order:
            originals = self.originals(name) if name in self.squashes else None
            steps.extend(originals if originals else [name])
        return steps

    def in_history_applied(self, name: str) -> bool:
        """Whether a :meth:`history` step is applied."""
        return name in self.applied if name in self.order else name in self.recorded

    def _sort(self, nodes: list[str], position: dict[str, int]) -> list[str]:
        import heapq

        from zeeb_orm.migrations.state import MigrationError

        node_set = set(nodes)
        incoming = {name: [d for d in self.dependencies(name) if d in node_set] for name in nodes}
        dependents: dict[str, list[str]] = {name: [] for name in nodes}
        for name, deps in incoming.items():
            for dep in deps:
                dependents[dep].append(name)
        waiting = {name: len(deps) for name, deps in incoming.items()}
        ready = [(position[name], name) for name in nodes if waiting[name] == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            _, name = heapq.heappop(ready)
            order.append(name)
            for dependent in dependents[name]:
                waiting[dependent] -= 1
                if waiting[dependent] == 0:
                    heapq.heappush(ready, (position[dependent], dependent))
        if len(order) != len(nodes):
            stuck = sorted(set(nodes) - set(order), key=position.__getitem__)
            raise MigrationError(
                f"The migrations {', '.join(stuck)} depend on each other in a cycle; "
                f"fix their dependencies."
            )
        return order

    def target(self, name: str) -> str:
        """Resolve a ``migrate`` target to a step of :meth:`history`.

        Accepts a full migration name, the file name (``.py``), or a unique
        prefix such as ``"0003"``. The last migration a squash replaces means
        the squash (the same schema state). Any other migration a squash in use
        replaces is a valid target while the original files are on disk; the
        database is then moved through those originals.

        Raises:
            UnknownMigrationError: no migration, or more than one, matches; or
                *name* lies inside a squash whose originals are gone.
        """
        from zeeb_orm.migrations.state import UnknownMigrationError

        wanted = name[:-3] if name.endswith(".py") else name
        if wanted in self.migrations:
            match = wanted
        else:
            candidates = [file for file in self.files if file.startswith(wanted)]
            if len(candidates) != 1:
                if candidates:
                    message = (
                        f"Migration '{name}' is ambiguous; it matches "
                        f"{', '.join(candidates)}."
                    )
                else:
                    message = f"No migration named '{name}'."
                raise UnknownMigrationError(
                    message, name=name, available=self.files, candidates=candidates
                )
            match = candidates[0]
        node = self.resolve(match)
        asked = match
        # A squash that a later squash replaces in turn is not a step of its
        # own here; it means the state after the last migration it replaces.
        while match != node and match in self.migrations and self.migrations[match].replaces:
            match = list(self.migrations[match].replaces)[-1]
        squash = self.squashes.get(node)
        if squash is not None and match != node and match != list(squash.replaces)[-1]:
            originals = self.originals(node)
            if originals and match in originals:
                return match
            raise UnknownMigrationError(
                f"'{asked}' is squashed into '{node}', so the database cannot stop "
                f"half-way through it. Migrate to '{node}', or to a migration before "
                f"'{list(squash.replaces)[0]}'.",
                name=name,
                available=self.files,
            )
        return node


def latest_migration(migrations_dir: Path) -> str | None:
    """The migration a new one should depend on: the last one a fresh database applies."""
    loaded = _load_all(migrations_dir)
    if not loaded:
        return None
    return MigrationGraph(loaded).order[-1]


# ---------------------------------------------------------------------------
# Tracking applied migrations
# ---------------------------------------------------------------------------

def _ensure_tracking_table(connection) -> None:
    """Create the zeeb_migrations table if it doesn't exist."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(connection)
    if _TRACKING_TABLE not in inspector.get_table_names():
        tracking_table().create(connection, checkfirst=True)
        connection.commit()


def get_applied_migrations(connection) -> set[str]:
    """Return set of applied migration names."""
    from sqlalchemy import text

    _ensure_tracking_table(connection)
    rows = connection.execute(
        text(f"SELECT name FROM {_TRACKING_TABLE} ORDER BY name")
    ).fetchall()
    return {row[0] for row in rows}


def record_migration(connection, name: str) -> None:
    """Record a migration as applied (``applied_at`` in UTC)."""
    from sqlalchemy import insert

    # Naive UTC: a TIMESTAMP/DATETIME column without a zone accepts it on
    # every backend, where an ISO string with "+00:00" is refused by MySQL.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    connection.execute(insert(tracking_table()).values(name=name, applied_at=now))


def unrecord_migration(connection, name: str) -> None:
    """Remove a migration from the applied list."""
    from sqlalchemy import text

    connection.execute(
        text(f"DELETE FROM {_TRACKING_TABLE} WHERE name = :name"),
        {"name": name},
    )


# ---------------------------------------------------------------------------
# Executing migrations
# ---------------------------------------------------------------------------

class _PlanItem(NamedTuple):
    """One step in a migration plan."""

    name: str
    migration: Migration
    backward: bool = False
    # Forward-only flags that skip running operations:
    fake_initial: bool = False        # initial migration whose tables already exist
    replaces_satisfied: bool = False  # squash whose replaced migrations are all applied


def _build_plan(
    conn,
    graph: MigrationGraph,
    target: str | None,
    fake_initial: bool,
) -> list[_PlanItem]:
    """Compute the ordered list of steps to reach *target*.

    - ``target is None`` → apply everything not yet applied.
    - ``target == "zero"`` → unapply everything applied, newest first.
    - otherwise → afterwards exactly the migrations up to and including
      *target* (in apply order) are applied: later ones are unapplied (newest
      first), missing earlier ones applied.

    A squash that counts as applied (all of its replaced migrations are) but
    is not recorded yet is planned as a ``replaces_satisfied`` step, which
    only records it.

    Raises:
        UnknownMigrationError: *target* matches no migration (or several).
    """

    def forward(name: str) -> _PlanItem | None:
        mig = graph.migrations[name]
        if name in graph.squashes and graph.is_applied(name):
            if name in graph.recorded:
                return None
            return _PlanItem(name=name, migration=mig, replaces_satisfied=True)
        if name in graph.applied:
            return None
        if fake_initial and mig.initial and _tables_exist(conn, mig):
            return _PlanItem(name=name, migration=mig, fake_initial=True)
        return _PlanItem(name=name, migration=mig)

    def backward(names: list[str]) -> list[_PlanItem]:
        return [
            _PlanItem(name=name, migration=graph.migrations[name], backward=True)
            for name in reversed(names)
            if name in graph.applied
        ]

    if target == "zero":
        return backward(graph.order)

    limit = len(graph.order)
    steps: list[_PlanItem] = []
    if target is not None:
        resolved = graph.target(target)
        if resolved not in graph.order:
            return _plan_into_squash(graph, resolved, forward, backward)
        limit = graph.order.index(resolved) + 1
        steps = backward(graph.order[limit:])
    steps += [item for name in graph.order[:limit] if (item := forward(name)) is not None]
    return steps


def _plan_into_squash(graph: MigrationGraph, original: str, forward, backward) -> list[_PlanItem]:
    """Steps to a migration a squash in use replaces, through the original files.

    Applied, the squash is unwound by unapplying its originals after *original*
    (the squash stops counting as applied); not applied, the originals up to
    *original* are applied one by one. Either way the squash is then partly
    applied, and the originals carry on from there.
    """
    squash = graph.resolve(original)
    position = graph.order.index(squash)
    originals = graph.originals(squash) or []
    cut = originals.index(original) + 1
    steps = backward(graph.order[position + 1:])
    if graph.is_applied(squash):
        steps += [
            _PlanItem(name=name, migration=graph.migrations[name], backward=True)
            for name in reversed(originals[cut:])
            if name in graph.recorded
        ]
        return steps
    steps += [item for name in graph.order[:position] if (item := forward(name)) is not None]
    steps += [
        _PlanItem(name=name, migration=graph.migrations[name])
        for name in originals[:cut]
        if name not in graph.recorded
    ]
    return steps


def _validate_dependencies(
    plan: list[_PlanItem], graph: MigrationGraph, check_history: bool = True
) -> None:
    """Ensure each forward step's dependencies are met before it runs.

    A dependency is met if it is applied or scheduled earlier in the plan (a
    dependency on a migration a squash replaces means that squash). An
    applied migration whose dependency is not applied is an inconsistent
    history and refused; a dependency on a migration that no longer exists
    only warns (e.g. a squashed original was deleted).
    """
    import warnings

    from zeeb_orm.migrations.state import MigrationError

    forward = [item for item in plan if not item.backward]
    if not forward:
        return
    known = set(graph.order) | set(graph.migrations)
    unapplied_later = {item.name for item in plan if item.backward}

    def dependencies(name: str) -> list[str]:
        owner = graph.resolve(name)
        if owner == name:
            return graph.dependencies(name)
        # An original of a squash in use, applied on its own: its siblings in
        # the squash are dependencies in their own right.
        siblings = set(graph.migrations[owner].replaces)
        return [
            dep if dep in siblings else graph.resolve(dep)
            for dep in graph.migrations[name].dependencies
        ]

    for name in graph.order if check_history else ():
        if name not in graph.applied or name in unapplied_later:
            continue
        for dep in graph.dependencies(name):
            if dep in known and dep not in graph.applied:
                # `migrate <name> --fake` records what is missing up to *name*
                # and leaves everything after it alone; say exactly what.
                missing = [
                    n for n in graph.order[: graph.order.index(name)]
                    if n not in graph.applied
                ]
                raise MigrationError(
                    f"Inconsistent migration history: '{name}' is applied, but its "
                    f"dependency '{dep}' is not. If the changes of "
                    f"{', '.join(repr(n) for n in missing)} are already in the "
                    f"database, record {'it' if len(missing) == 1 else 'them'} as "
                    f"applied without running anything: `migrate {name} --fake` "
                    f"(this records only {', '.join(missing)}). Otherwise unapply "
                    f"'{name}' first."
                )

    satisfied = (set(graph.applied) | graph.recorded) - unapplied_later
    for item in forward:
        for dep in dependencies(item.name):
            if dep in satisfied:
                continue
            if dep in known:
                raise MigrationError(
                    f"Migration '{item.name}' depends on '{dep}', which is not "
                    f"applied and not scheduled to run before it."
                )
            warnings.warn(
                f"Migration '{item.name}' depends on unknown migration "
                f"'{dep}' (file missing and not covered by a squash); continuing.",
                RuntimeWarning,
                stacklevel=3,
            )
        satisfied.add(item.name)


def _apply_plan(
    conn,
    plan: list[_PlanItem],
    graph: MigrationGraph,
    fake: bool,
    dry_run: bool = False,
) -> list[str]:
    """Execute a plan, recording each step. Returns the names processed.

    With *dry_run*, nothing runs and nothing is recorded, but the names come
    back exactly as a real run would return them — that is what ``--plan``
    shows — except that a faked-initial migration, which runs nothing, is
    left out.
    """
    processed: list[str] = []
    recorded = set(graph.recorded)

    def record(name: str) -> None:
        if name not in recorded:
            if not dry_run:
                record_migration(conn, name)
            recorded.add(name)

    def unrecord(name: str) -> None:
        if not dry_run:
            unrecord_migration(conn, name)
        recorded.discard(name)

    def commit() -> None:
        if not dry_run:
            conn.commit()

    for item in plan:
        mig = item.migration
        if item.backward:
            if not (fake or dry_run):
                _execute(conn, mig, backward=True)
            unrecord(item.name)
            # A squash recorded its replaced migrations as applied; unrecord
            # them together so tracking stays consistent. A replaced original
            # going away means its squash is no longer applied either.
            for replaced_name in mig.replaces:
                unrecord(replaced_name)
            for squash_name, squash in {**graph.squashes, **graph.unused_squashes}.items():
                if item.name in squash.replaces and squash_name in recorded:
                    unrecord(squash_name)
            commit()
            processed.append(item.name)
            continue

        # Forward. Skip running operations when faking, faking the initial
        # (tables exist), or when a squash's replaced migrations are all applied.
        skip_run = fake or item.fake_initial or item.replaces_satisfied
        if not (skip_run or dry_run):
            _execute(conn, mig, backward=False)
        record(item.name)
        # A squash marks its replaced migrations applied too.
        for replaced_name in mig.replaces:
            record(replaced_name)
        commit()
        if not (dry_run and item.fake_initial):
            processed.append(item.name)

        # The last remaining original of a partly applied squash completes it,
        # and the squash is reported as recorded with it.
        for squash_name, squash in graph.unused_squashes.items():
            if (
                squash_name not in recorded
                and item.name in squash.replaces
                and all(r in recorded for r in squash.replaces)
            ):
                record(squash_name)
                commit()
                processed.append(squash_name)

    return processed


class MigrateResult(NamedTuple):
    """What a ``migrate`` run did (or, planned, would do), split by direction."""

    applied: list[str]
    """Migrations applied or recorded (faked, or a squash whose originals were
    all applied), in order."""
    unapplied: list[str]
    """Migrations rolled back, newest first."""
    target: str | None = None
    """The target as resolved (``"0003"`` becomes the full name)."""

    @property
    def names(self) -> list[str]:
        """Every migration processed, in the order processed."""
        return [*self.unapplied, *self.applied]


def migrate(
    target: str | None = None,
    database_url: str | None = None,
    project_root: Path | None = None,
    fake: bool = False,
    fake_initial: bool = False,
    plan: bool = False,
    migrations_dir: Path | str | None = None,
) -> list[str]:
    """
    Apply pending migrations (or migrate to a specific target).

    Args:
        target: Target migration — a full name, a unique prefix such as
                ``"0003"``, ``"zero"`` to roll back everything, or ``None``
                to apply all pending.
        database_url: Database URL. Resolved like ``manage.py migrate`` when
                      None (the project's settings.py, else DATABASE_URL).
        project_root: Project root directory.
        fake: If True, mark as applied without running.
        fake_initial: If True, skip the initial migration when the tables
                      already exist (useful when adopting migrations in an
                      existing project).
        plan: If True, return the list of migrations that *would* be
              applied/unapplied without actually executing anything.
        migrations_dir: The migrations directory, when it is not
                        ``<project_root>/migrations``.

    Returns:
        List of migration names that were (or would be) applied/unapplied, in
        the order processed (rollbacks first). :func:`migrate_with_result`
        tells the two apart.

    Raises:
        UnknownMigrationError: *target* matches no migration, or several.
        MigrationLayoutError: the migrations sit in ``migrations/versions/``.
        IrreversibleError: a rollback would need an irreversible operation.
        MigrationError: inconsistent history, or another ``migrate`` held the
            lock for too long.
    """
    return migrate_with_result(
        target=target,
        database_url=database_url,
        project_root=project_root,
        fake=fake,
        fake_initial=fake_initial,
        plan=plan,
        migrations_dir=migrations_dir,
    ).names


def migrate_with_result(
    target: str | None = None,
    database_url: str | None = None,
    project_root: Path | None = None,
    fake: bool = False,
    fake_initial: bool = False,
    plan: bool = False,
    migrations_dir: Path | str | None = None,
) -> MigrateResult:
    """:func:`migrate`, reporting what was applied and what was unapplied."""
    return _run(
        target=target,
        steps=None,
        database_url=database_url,
        project_root=project_root,
        fake=fake,
        fake_initial=fake_initial,
        plan=plan,
        migrations_dir=migrations_dir,
    )


def _run(
    *,
    target: str | None,
    steps: int | None,
    database_url: str | None,
    project_root: Path | None,
    fake: bool,
    fake_initial: bool,
    plan: bool,
    migrations_dir: Path | str | None,
) -> MigrateResult:
    """Plan and apply a migrate (or, with *steps*, a rollback of that many).

    The graph, the rollback target and the plan are all worked out under the
    migration lock, from the state the run then changes. A ``plan`` run
    changes nothing, so it takes no lock, but it is checked exactly like a
    real run and reports exactly what the real run would do.
    """
    if database_url is None:
        from zeeb_orm.conf.project import resolve_database_url
        database_url = resolve_database_url(project_root or _find_project_root())

    mig_dir = _resolve_dir(project_root, migrations_dir)
    check_layout(mig_dir)
    loaded = _load_all(mig_dir)

    engine = _migration_engine(database_url)
    try:
        with engine.connect() as conn:
            # One migrate at a time: replicas that all run migrate on startup
            # would otherwise each apply the same pending migrations.
            with nullcontext() if plan else _migration_lock(conn, database_url):
                graph = MigrationGraph(loaded, get_applied_migrations(conn))
                if steps is not None:
                    target = _rollback_target(graph, steps)
                    if target is None:
                        return MigrateResult(applied=[], unapplied=[])
                plan_items = _build_plan(conn, graph, target, fake_initial)
                # --fake is how an inconsistent history gets repaired, so it
                # is not refused for having one.
                _validate_dependencies(plan_items, graph, check_history=not fake)
                if not fake:
                    _check_reversible(plan_items)
                processed = _apply_plan(conn, plan_items, graph, fake, dry_run=plan)
                backward = {item.name for item in plan_items if item.backward}
                return MigrateResult(
                    applied=[name for name in processed if name not in backward],
                    unapplied=[name for name in processed if name in backward],
                    target=_resolved(graph, target),
                )
    finally:
        engine.dispose()


def _resolved(graph: MigrationGraph, target: str | None) -> str | None:
    return target if target in (None, "zero") else graph.target(target)


def rollback_target(
    steps: int = 1,
    database_url: str | None = None,
    project_root: Path | None = None,
    migrations_dir: Path | str | None = None,
) -> str | None:
    """The ``migrate`` target that unapplies the last *steps* migrations.

    Counted in apply order (:meth:`MigrationGraph.history`): a squash counts
    as the migrations it replaces while their files are on disk — rolling
    back one step unapplies its last original — and as one migration once
    they are deleted. ``"zero"`` when *steps* covers everything applied,
    ``None`` when nothing is applied (or *steps* is less than one).
    """
    if steps < 1:
        return None
    if database_url is None:
        from zeeb_orm.conf.project import resolve_database_url
        database_url = resolve_database_url(project_root or _find_project_root())

    mig_dir = _resolve_dir(project_root, migrations_dir)
    check_layout(mig_dir)
    loaded = _load_all(mig_dir)
    engine = _migration_engine(database_url)
    try:
        with engine.connect() as conn:
            graph = MigrationGraph(loaded, get_applied_migrations(conn))
    finally:
        engine.dispose()
    return _rollback_target(graph, steps)


def _rollback_target(graph: MigrationGraph, steps: int) -> str | None:
    if steps < 1:
        return None
    applied = [name for name in graph.history() if graph.in_history_applied(name)]
    if not applied:
        return None
    return "zero" if steps >= len(applied) else applied[-(steps + 1)]


def rollback(
    steps: int = 1,
    database_url: str | None = None,
    project_root: Path | None = None,
    fake: bool = False,
    plan: bool = False,
    migrations_dir: Path | str | None = None,
) -> list[str]:
    """Unapply the last *steps* migrations; returns the names unapplied.

    See :func:`rollback_target` for how steps are counted. The one
    implementation behind ``migrate --rollback N``, the programmatic
    ``rollback()`` and the agent layer's ``rollback_migration``;
    :func:`rollback_with_result` also reports the target it rolled back to.
    """
    return rollback_with_result(
        steps,
        database_url=database_url,
        project_root=project_root,
        fake=fake,
        plan=plan,
        migrations_dir=migrations_dir,
    ).unapplied


def rollback_with_result(
    steps: int = 1,
    database_url: str | None = None,
    project_root: Path | None = None,
    fake: bool = False,
    plan: bool = False,
    migrations_dir: Path | str | None = None,
) -> MigrateResult:
    """:func:`rollback`, as a :class:`MigrateResult`.

    The target is worked out under the migration lock, so a migration another
    process applies in the meantime is never unapplied in its place. Nothing
    applied (or *steps* below one) gives an empty result with ``target=None``.
    """
    return _run(
        target=None,
        steps=steps,
        database_url=database_url,
        project_root=project_root,
        fake=fake,
        fake_initial=False,
        plan=plan,
        migrations_dir=migrations_dir,
    )


def _check_reversible(plan: list[_PlanItem]) -> None:
    """Refuse a rollback that would reach an irreversible operation.

    Checked for the whole plan up front: finding out at the third migration
    would leave the database half rolled back.
    """
    from zeeb_orm.migrations.state import IrreversibleError

    blocked = [
        f"{item.name}: {op.describe()}"
        for item in plan
        if item.backward
        for op in item.migration.operations
        if not op.reversible
    ]
    if blocked:
        raise IrreversibleError(
            "Cannot unapply — these operations are irreversible: "
            + "; ".join(blocked)
            + ". Make them reversible (e.g. reverse_sql/reverse_code, or "
            "RunSQL.noop/RunPython.noop), or mark the migrations unapplied "
            "without running them with --fake."
        )


def _migration_engine(database_url: str) -> Any:
    """A sync engine for running migrations.

    On SQLite, foreign-key enforcement is switched off for the migration
    connection (Alembic's recommendation for batch mode): SQLite alters a
    column by rebuilding the table, and with enforcement on, dropping the old
    copy of a referenced table would fire ``ON DELETE CASCADE`` on every row
    pointing at it. The pragma cannot be changed inside a transaction, so it
    is set as each connection opens.
    """
    from sqlalchemy import create_engine, event

    engine = create_engine(sync_database_url(database_url))
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _foreign_keys_off(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=OFF")
            finally:
                cursor.close()

    return engine


#: How long ``migrate`` waits for another one to finish (MySQL; seconds).
LOCK_TIMEOUT = 600

# Any fixed 63-bit number; PostgreSQL advisory locks are keyed by integers.
_PG_LOCK_KEY = 0x7A_65_65_62_6D_69_67  # "zeebmig"


@contextmanager
def _migration_lock(conn, database_url: str) -> Iterator[None]:
    """Hold the database-wide "migrate is running" lock.

    PostgreSQL: a session advisory lock. MySQL: ``GET_LOCK``. SQLite: an
    exclusive OS lock on a ``<database>-migrate.lock`` file beside the
    database (an in-memory database needs none). Every lock is released when
    the process dies, so a crashed migrate never wedges the next one.
    """
    from sqlalchemy import text

    from zeeb_orm.migrations.state import MigrationError

    dialect = conn.dialect.name
    if dialect == "postgresql":
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _PG_LOCK_KEY})
        conn.commit()
        try:
            yield
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _PG_LOCK_KEY})
            conn.commit()
        return
    if dialect in ("mysql", "mariadb"):
        got = conn.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": _TRACKING_TABLE, "timeout": LOCK_TIMEOUT},
        ).scalar()
        conn.commit()
        if got != 1:
            raise MigrationError(
                f"Another migrate has held the migration lock for {LOCK_TIMEOUT}s; "
                f"wait for it to finish and try again."
            )
        try:
            yield
        finally:
            conn.rollback()
            conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _TRACKING_TABLE})
            conn.commit()
        return
    if dialect == "sqlite":
        database = conn.engine.url.database
        if not database or database == ":memory:" or database.startswith("file::memory:"):
            yield
            return
        with _file_lock(Path(f"{database}-migrate.lock")):
            yield
        return
    yield


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """An exclusive, blocking OS-level lock on *path* (released on process exit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ~10s; keep waiting
                    continue
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _execute(conn, mig, backward: bool = False) -> None:
    """Run a migration's operations forward or backward, respecting ``atomic``.

    An atomic migration runs in a savepoint inside the transaction that also
    records it. A non-atomic one (``atomic = False``) runs with the connection
    in autocommit mode, so each statement commits on its own — the only way
    to run statements PostgreSQL refuses inside a transaction block, such as
    ``CREATE INDEX CONCURRENTLY``.

    MySQL commits implicitly before and after every DDL statement, which ends
    any savepoint, so there an atomic migration runs in the plain transaction
    instead: its data statements and the row recording it still commit
    together, but its DDL cannot be rolled back.
    """
    def _body() -> None:
        mig.pre_migrate(conn)
        ops = list(reversed(mig.operations)) if backward else list(mig.operations)
        for op in ops:
            if backward:
                op.backward(conn)
            else:
                op.forward(conn)
        mig.post_migrate(conn)

    if mig.atomic:
        if conn.dialect.name in ("mysql", "mariadb"):
            _body()
            return
        with conn.begin_nested():
            _body()
        return

    # SQLAlchemy 2.0 begins a transaction on a connection's first statement;
    # end it, then switch the DBAPI connection to autocommit for the body.
    conn.commit()
    conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        _body()
    finally:
        conn.commit()
        conn.execution_options(isolation_level=conn.default_isolation_level)


def _tables_exist(conn, mig) -> bool:
    """Return True if all tables created by the initial migration already exist."""
    from sqlalchemy import inspect as sa_inspect

    from zeeb_orm.migrations.operations import CreateModel

    inspector = sa_inspect(conn)
    existing = set(inspector.get_table_names())
    create_ops = [op for op in mig.operations if isinstance(op, CreateModel)]
    if not create_ops:
        return False
    return all(op.table in existing for op in create_ops)


def showmigrations(
    database_url: str | None = None,
    project_root: Path | None = None,
    migrations_dir: Path | str | None = None,
) -> list[tuple[str, bool]]:
    """
    Return list of ``(migration_name, is_applied)`` tuples, in file order.

    A squash counts as applied when every migration it replaces is, even
    before ``migrate`` has recorded it.

    Args:
        database_url: Database URL.
        project_root: Project root directory.
        migrations_dir: The migrations directory, when it is not
                        ``<project_root>/migrations``.

    Returns:
        List of (name, applied) tuples.
    """
    if database_url is None:
        from zeeb_orm.conf.project import resolve_database_url
        database_url = resolve_database_url(project_root or _find_project_root())

    engine = _migration_engine(database_url)

    mig_dir = _resolve_dir(project_root, migrations_dir)
    all_migrations = list_migration_files(mig_dir)

    try:
        with engine.connect() as conn:
            applied = get_applied_migrations(conn)
    finally:
        engine.dispose()

    squashes = _squash_replaces(all_migrations)
    return [
        (
            name,
            name in applied
            or (name in squashes and all(r in applied for r in squashes[name])),
        )
        for name, _ in all_migrations
    ]


def _squash_replaces(files: list[tuple[str, Path]]) -> dict[str, list[str]]:
    """``{squash name: replaced names}``, read without importing the files.

    ``showmigrations`` must keep working when a migration file is broken
    (that is often why someone runs it), so ``replaces`` is read from the
    syntax tree, not by importing.
    """
    import ast

    squashes: dict[str, list[str]] = {}
    for name, path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "replaces"
                        for t in statement.targets
                    )
                ):
                    try:
                        value = ast.literal_eval(statement.value)
                    except ValueError:
                        continue
                    if value:
                        squashes[name] = [str(v) for v in value]
    return squashes
