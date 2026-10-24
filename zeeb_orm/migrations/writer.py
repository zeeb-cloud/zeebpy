"""
Migration file writer — generates Django-style migration files.

Produces files like ``0001_initial.py`` with a ``Migration`` class
containing ``dependencies`` and ``operations``. Operations are rendered by
:mod:`zeeb_orm.migrations.serializer`, and every file is checked before it is
written: it must compile, import, and load back into the same operations. A
file that would not is refused with
:class:`~zeeb_orm.migrations.serializer.MigrationSerializationError` — a broken
migration file breaks every later ``migrate`` and ``makemigrations``, because
each run imports all of them.
"""

from __future__ import annotations

import os
import re
import types
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from zeeb_orm.migrations.serializer import (
    MIGRATION_MODULE_PREFIX,
    MigrationSerializationError,
    SourceWriter,
)

if TYPE_CHECKING:
    from zeeb_orm.migrations.operations import Operation


def _slugify(text: str) -> str:
    """Convert text to a migration-name-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text or "auto"


def next_migration_number(migrations_dir: Path) -> int:
    """Determine the next sequential migration number (one past the highest)."""
    from zeeb_orm.migrations.executor import list_migration_files

    numbers = [
        int(match.group(1))
        for name, _ in list_migration_files(migrations_dir)
        if (match := re.match(r"^(\d{4})_", name))
    ]
    return max(numbers) + 1 if numbers else 1


def migration_name(
    migrations_dir: Path,
    operations: Sequence[Operation],
    name: str | None = None,
    initial: bool = False,
) -> str:
    """The name the next migration file gets, e.g. ``"0003_posts_slug"``.

    What :func:`write_migration` writes and what a dry run reports, so the two
    can never disagree.
    """
    number = next_migration_number(migrations_dir)
    if name is not None:
        slug = _slugify(name)
    elif initial or number == 1:
        slug = "initial"
    else:
        slug = _auto_name(operations)
    return f"{number:04d}_{slug}"


def write_migration(
    migrations_dir: Path,
    operations: list[Operation],
    name: str | None = None,
    dependencies: list[str] | None = None,
    initial: bool = False,
) -> Path:
    """
    Generate a new migration file.

    Args:
        migrations_dir: Directory to write the file in.
        operations: List of Operation objects.
        name: Human-readable name (e.g. "add_views_to_posts").
              Auto-generated if None.
        dependencies: List of migration names this depends on. Defaults to
              the latest migration (the last one a fresh database applies).
        initial: Whether this is the initial migration.

    Returns:
        Path to the created migration file.

    Raises:
        MigrationSerializationError: an operation cannot be written faithfully;
            nothing is written.
    """
    from zeeb_orm.migrations.executor import latest_migration

    migrations_dir.mkdir(parents=True, exist_ok=True)

    stem = migration_name(migrations_dir, operations, name=name, initial=initial)
    number = int(stem[:4])

    if dependencies is None:
        latest = latest_migration(migrations_dir)
        dependencies = [latest] if latest else []

    filepath = migrations_dir / f"{stem}.py"
    content, renames = _render(
        migration_name=stem,
        operations=operations,
        dependencies=dependencies,
        initial=initial or number == 1,
    )
    save_migration(filepath, content, operations, renames)
    return filepath


def save_migration(
    path: Path,
    content: str,
    operations: Sequence[Operation],
    renames: dict[tuple[str, str], str] | None = None,
) -> None:
    """Verify *content* against *operations*, then write it atomically to *path*.

    Args:
        renames: The names copied ``RunPython`` functions got in *content*
            (from :func:`_render`), so they compare equal to the originals.
    """
    verify_migration_source(content, path, operations, renames)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _auto_name(operations: Sequence[Operation]) -> str:
    """Generate a name from the operations list."""
    from zeeb_orm.migrations import operations as ops_module

    descriptions = []
    for op in operations[:3]:
        if isinstance(op, ops_module.CreateModel):
            descriptions.append(op.table)
        elif isinstance(op, ops_module.DeleteModel):
            descriptions.append(f"delete_{op.table}")
        elif isinstance(op, ops_module.AddField):
            descriptions.append(f"{op.table}_{op.name}")
        elif isinstance(op, ops_module.RemoveField):
            descriptions.append(f"remove_{op.name}_from_{op.table}")
        elif isinstance(op, ops_module.AlterField):
            descriptions.append(f"alter_{op.name}_on_{op.table}")
        elif isinstance(op, ops_module.RenameField):
            descriptions.append(f"rename_{op.old_name}_to_{op.new_name}_on_{op.table}")
        elif isinstance(op, ops_module.AlterModelTable):
            descriptions.append(f"rename_{op.old_table}_to_{op.new_table}")
        else:
            descriptions.append("auto")

    name = "_".join(descriptions) if descriptions else "auto"
    if len(operations) > 3:
        name += "_and_more"
    return _slugify(name)


def render_migration(
    migration_name: str,
    operations: Sequence[Operation],
    dependencies: Sequence[str],
    initial: bool,
    replaces: Sequence[str] | None = None,
    comment: str | None = None,
    atomic: bool = True,
) -> str:
    """Render the migration file content.

    Args:
        comment: Lines for a ``#`` comment block after the docstring.
        atomic: ``False`` writes ``atomic = False`` (see ``Migration.atomic``).

    Raises:
        MigrationSerializationError: an operation cannot be written faithfully.
    """
    return _render(
        migration_name, operations, dependencies, initial, replaces, comment, atomic
    )[0]


# Kept for callers of the old private name.
_render_migration = render_migration


def _render(
    migration_name: str,
    operations: Sequence[Operation],
    dependencies: Sequence[str],
    initial: bool,
    replaces: Sequence[str] | None = None,
    comment: str | None = None,
    atomic: bool = True,
) -> tuple[str, dict[tuple[str, str], str]]:
    """:func:`render_migration`, plus the names copied functions got."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    writer = SourceWriter()
    rendered = [writer.operation(op) for op in operations]

    # Build description from operations
    descriptions = [op.describe() for op in operations]
    description = ", ".join(descriptions) if descriptions else "Empty migration"

    lines = ['"""', description, "", f"Generated by Zeeb ORM on {timestamp}", '"""', ""]
    if comment:
        lines += [f"# {line}".rstrip() for line in comment.splitlines()]
    lines += ["import sqlalchemy as sa", *writer.imports()]
    lines += ["from zeeb_orm.migrations import Migration, operations", "", ""]
    for definition in writer.definitions():
        lines += [*definition.rstrip("\n").splitlines(), "", ""]

    lines += ["class Migration(Migration):", ""]
    if initial:
        lines += ["    initial = True", ""]
    lines += [f"    dependencies = {list(dependencies)!r}", ""]
    if replaces:
        lines += [f"    replaces = {list(replaces)!r}", ""]
    if not atomic:
        lines += ["    atomic = False", ""]
    lines.append("    operations = [")
    for source in rendered:
        indented = "\n".join(f"        {line}" for line in source.splitlines())
        lines.append(f"{indented},")
    lines += ["    ]", ""]
    return "\n".join(lines), writer.copied_names


def verify_migration_source(
    content: str,
    path: Path,
    operations: Sequence[Operation],
    renames: dict[tuple[str, str], str] | None = None,
) -> None:
    """Check that *content* loads back into *operations*.

    The file is compiled and executed in a throwaway module (never imported
    under its real name), and each operation it defines must render exactly as
    the original does.

    Raises:
        MigrationSerializationError: it does not compile, does not run, or
            loads into different operations.
    """
    try:
        code = compile(content, str(path), "exec")
    except SyntaxError as exc:
        raise MigrationSerializationError(
            f"{path.name} would not be valid Python ({exc.msg}, line {exc.lineno}); "
            f"it was not written."
        ) from exc

    module = types.ModuleType(f"{MIGRATION_MODULE_PREFIX}{path.stem}__check")
    module.__file__ = str(path)
    try:
        exec(code, module.__dict__)  # noqa: S102 — our own rendering
    except Exception as exc:
        raise MigrationSerializationError(
            f"{path.name} would fail to import ({type(exc).__name__}: {exc}); "
            f"it was not written."
        ) from exc

    from zeeb_orm.migrations.migration import Migration

    classes = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Migration) and obj is not Migration
    ]
    if len(classes) != 1:
        raise MigrationSerializationError(f"{path.name} does not define one Migration class.")
    loaded = list(classes[0]().operations)

    def spelled(op: Operation) -> str:
        return SourceWriter(copy_functions=False, renamed=renames or {}).operation(op)

    if len(loaded) != len(operations):
        raise MigrationSerializationError(
            f"{path.name} would hold {len(loaded)} operation(s) instead of "
            f"{len(operations)}; it was not written."
        )
    for original, reloaded in zip(operations, loaded):
        if spelled(original) != spelled(reloaded):
            raise MigrationSerializationError(
                f"{original.describe()!r} does not survive being written to {path.name}: "
                f"it loads back as {spelled(reloaded)!r}; the file was not written."
            )
