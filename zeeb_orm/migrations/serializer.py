"""
Render migration operations as Python source without losing information.

A migration file is the only record of what a migration does, so writing one
has to be lossless. Every operation is rendered from its constructor arguments
(:meth:`~zeeb_orm.migrations.operations.Operation.deconstruct`), and every
argument through :class:`SourceWriter`, which knows how to spell SQLAlchemy
columns, types and constraints and collects the imports they need.

A value that cannot be spelled faithfully raises
:class:`MigrationSerializationError` instead of being written approximately: a
``DateTime`` that lost its time zone, or a type name that does not exist in the
module it is written against, is worse than no migration at all. Types are
checked by evaluating the rendered source and comparing the result with the
original, DDL included, on every supported dialect.

``RunPython`` callables are written by reference when they live in an
importable module. One defined inside a migration file (which is not importable)
has its source copied into the new file, together with the module-level helpers,
constants and imports it uses — that is how a squash carries data migrations
over.
"""

from __future__ import annotations

import ast
import enum
import importlib
import inspect
import math
import re
import textwrap
import types as pytypes
from decimal import Decimal
from typing import Any

from zeeb_orm.migrations.state import MigrationError

#: Dialects every rendered type is compared on.
_DIALECTS = ("sqlite", "postgresql", "mysql")

#: Names a migration file always binds; nothing else may claim them.
_RESERVED = {"sa", "operations", "Migration"}

#: Module-name prefix :func:`~zeeb_orm.migrations.executor.load_migration` uses.
MIGRATION_MODULE_PREFIX = "migration_"


class MigrationSerializationError(MigrationError):
    """A value cannot be written to a migration file without losing information."""


class Source(str):
    """Python source that is written into the migration file as it is."""


def is_migration_module(module_name: str | None) -> bool:
    """Whether *module_name* is a migration file loaded by the executor."""
    return bool(module_name) and str(module_name).startswith(MIGRATION_MODULE_PREFIX)


class SourceWriter:
    """Spells values as Python source for one migration file.

    Collects the imports (beyond the ``sa`` / ``operations`` header every file
    has) and the top-level definitions — copied ``RunPython`` functions and
    their helpers — the rendered source relies on.

    Args:
        copy_functions: Copy the source of callables defined in migration
            files. Off, such a callable is rendered by name only (used for
            ``repr`` and for comparing operations, never for writing a file).
        renamed: With ``copy_functions`` off, the names copied callables got
            in a written file (:attr:`copied_names` of the writer that wrote
            it), keyed by ``(module, name)``.
    """

    def __init__(
        self,
        *,
        copy_functions: bool = True,
        renamed: dict[tuple[str, str], str] | None = None,
    ) -> None:
        import sqlalchemy

        from zeeb_orm.migrations import operations as operations_module

        self._copy_functions = copy_functions
        self._renamed = dict(renamed or {})
        self._import_lines: set[str] = set()
        # Names bound at module level of the file being written, for eval.
        self._namespace: dict[str, Any] = {
            "sa": sqlalchemy,
            "operations": operations_module,
        }
        self._definitions: list[str] = []
        # (source module, original name) -> name in the written file
        self._copied: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # What the file needs besides the rendered operations
    # ------------------------------------------------------------------

    def imports(self) -> list[str]:
        """Import lines the rendered source needs, beyond the standard header."""
        return sorted(self._import_lines)

    def definitions(self) -> list[str]:
        """Top-level source blocks (copied functions, constants), in order."""
        return list(self._definitions)

    @property
    def copied_names(self) -> dict[tuple[str, str], str]:
        """``(source module, original name)`` -> the name it has in this file."""
        return dict(self._copied)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def operation(self, op: Any) -> str:
        """Render one operation as a constructor call (no trailing comma)."""
        from zeeb_orm.migrations import operations as operations_module
        from zeeb_orm.migrations.operations import Operation

        cls = type(op)
        try:
            kwargs = op.deconstruct()
        except NotImplementedError:
            # A custom operation predating deconstruct(): trust its own repr.
            if cls.__repr__ is not Operation.__repr__:
                return repr(op).strip()
            raise MigrationSerializationError(
                f"Operation {cls.__name__} defines neither deconstruct() nor "
                f"__repr__(), so it cannot be written to a migration file."
            ) from None

        if getattr(operations_module, cls.__name__, None) is cls:
            ref = f"operations.{cls.__name__}"
        else:
            ref = self._class_ref(cls, what="operation")

        if not kwargs:
            return f"{ref}()"
        lines = [f"{ref}("]
        for key, value in kwargs.items():
            lines.append(textwrap.indent(f"{key}={self._block(value)},", "    "))
        lines.append(")")
        return "\n".join(lines)

    def _block(self, value: Any) -> str:
        """A value, with a list of non-scalars spread over one line per item."""
        if isinstance(value, (list, tuple)) and value and not all(
            _is_scalar(item) for item in value
        ):
            open_, close = ("[", "]") if isinstance(value, list) else ("(", ")")
            body = "".join(f"    {self.value(item)},\n" for item in value)
            return f"{open_}\n{body}{close}"
        return self.value(value)

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    def value(self, value: Any) -> str:
        """Render *value* as a Python expression."""
        import sqlalchemy as sa
        from sqlalchemy.sql.elements import TextClause
        from sqlalchemy.sql.functions import FunctionElement
        from sqlalchemy.types import TypeEngine

        from zeeb_orm.migrations.operations import NO_DEFAULT

        if value is NO_DEFAULT:
            return "operations.NO_DEFAULT"
        if isinstance(value, Source):
            return str(value)
        if value is None or isinstance(value, (bool, int, str, bytes)):
            return repr(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise MigrationSerializationError(
                    f"The float {value!r} cannot be written to a migration file."
                )
            return repr(value)
        if isinstance(value, Decimal):
            self._import("decimal", "import decimal")
            return f"decimal.Decimal({str(value)!r})"
        if isinstance(value, list):
            return "[" + ", ".join(self.value(item) for item in value) + "]"
        if isinstance(value, tuple):
            inner = ", ".join(self.value(item) for item in value)
            return f"({inner},)" if len(value) == 1 else f"({inner})"
        if isinstance(value, (set, frozenset)):
            if not value:
                return "set()"
            return "{" + ", ".join(sorted(self.value(item) for item in value)) + "}"
        if isinstance(value, dict):
            return "{" + ", ".join(
                f"{self.value(k)}: {self.value(v)}" for k, v in value.items()
            ) + "}"
        if isinstance(value, sa.Column):
            return self.column(value)
        if isinstance(value, TypeEngine):
            return self.type_(value)
        if isinstance(value, type) and issubclass(value, TypeEngine):
            return self._class_ref(value, what="type")
        if isinstance(value, sa.schema.Constraint):
            return self.constraint(value)
        if isinstance(value, TextClause):
            return f"sa.text({value.text!r})"
        if isinstance(value, FunctionElement):
            return self._sql_function(value)
        if isinstance(value, enum.Enum):
            return f"{self._class_ref(type(value), what='enum')}.{value.name}"
        if isinstance(value, (pytypes.FunctionType, pytypes.BuiltinFunctionType)):
            return self.callable_(value)
        raise MigrationSerializationError(
            f"Cannot write {type(value).__name__} value {value!r} to a migration file."
        )

    def _sql_function(self, value: Any) -> str:
        """``sa.func.now()``-style SQL functions without arguments."""
        name = getattr(value, "name", None)
        # A ClauseList refuses bool(); test it against None, never for truth.
        arguments = getattr(value, "clauses", None)
        clauses = list(arguments) if arguments is not None else []
        if not name or clauses or not str(name).isidentifier():
            raise MigrationSerializationError(
                f"Cannot write the SQL expression {value} to a migration file; "
                f"use sa.text(...) for a server default with arguments."
            )
        return f"sa.func.{name}()"

    # ------------------------------------------------------------------
    # Columns, foreign keys, constraints
    # ------------------------------------------------------------------

    def column(self, col: Any) -> str:
        """Render a ``sqlalchemy.Column`` as an ``sa.Column(...)`` call.

        ``primary_key`` and ``index`` are left out on purpose: the primary key
        is the operation's own ``primary_key`` argument, and indexes are
        separate ``AddIndex`` operations.
        """
        from sqlalchemy.schema import DefaultClause

        from zeeb_orm.migrations.operations import _scalar_default

        if getattr(col, "computed", None) is not None or getattr(col, "identity", None) is not None:
            raise MigrationSerializationError(
                f"Column '{col.name}' is a computed/identity column, which cannot be "
                f"written to a migration file; create it with RunSQL instead."
            )

        parts = [repr(col.name), self.type_(col.type)]
        for fk in sorted(col.foreign_keys, key=lambda fk: str(fk.target_fullname)):
            parts.append(self._foreign_key(fk))
        if col.key != col.name:
            parts.append(f"key={col.key!r}")
        if not col.nullable:
            parts.append("nullable=False")
        if col.unique:
            parts.append("unique=True")
        # The Python-side default survives into the file: AddField reads it to
        # backfill existing rows when it adds a NOT NULL column.
        default = _scalar_default(col)
        if default is not None:
            parts.append(f"default={self.value(default)}")
        if col.server_default is not None:
            if not isinstance(col.server_default, DefaultClause):
                raise MigrationSerializationError(
                    f"Column '{col.name}' has a server default of type "
                    f"{type(col.server_default).__name__}, which cannot be written "
                    f"to a migration file."
                )
            parts.append(f"server_default={self.value(col.server_default.arg)}")
        # SQLAlchemy's "auto" autoincrement declines for composite, FK-bearing
        # or server-defaulted primary keys; an explicit True must survive.
        if col.autoincrement is True:
            parts.append("autoincrement=True")
        if col.comment:
            parts.append(f"comment={col.comment!r}")
        return f"sa.Column({', '.join(parts)})"

    def _foreign_key(self, fk: Any) -> str:
        args = [repr(str(fk.target_fullname))]
        for attr in ("name", "ondelete", "onupdate", "deferrable", "initially"):
            value = getattr(fk, attr, None)
            if value is not None:
                args.append(f"{attr}={self.value(value)}")
        if getattr(fk, "use_alter", False):
            args.append("use_alter=True")
        return f"sa.ForeignKey({', '.join(args)})"

    def constraint(self, constraint: Any) -> str:
        """Render a table constraint as an ``sa.*Constraint(...)`` call."""
        import sqlalchemy as sa

        extra = []
        for attr in ("deferrable", "initially"):
            value = getattr(constraint, attr, None)
            if value is not None:
                extra.append(f"{attr}={self.value(value)}")
        name = f"name={self.value(constraint.name)}" if constraint.name else None

        def call(ref: str, *args: str) -> str:
            rendered = [*args, *([name] if name else []), *extra]
            return f"{ref}({', '.join(rendered)})"

        if isinstance(constraint, (sa.UniqueConstraint, sa.PrimaryKeyConstraint)):
            ref = (
                "sa.UniqueConstraint"
                if isinstance(constraint, sa.UniqueConstraint)
                else "sa.PrimaryKeyConstraint"
            )
            return call(ref, *(repr(c) for c in constraint_columns(constraint)))
        if isinstance(constraint, sa.CheckConstraint):
            return call("sa.CheckConstraint", repr(str(constraint.sqltext)))
        if isinstance(constraint, sa.ForeignKeyConstraint):
            columns = [element.parent.name if element.parent is not None else None
                       for element in constraint.elements]
            if None in columns:
                columns = list(constraint.column_keys)
            targets = [str(element.target_fullname) for element in constraint.elements]
            for attr in ("ondelete", "onupdate"):
                value = getattr(constraint, attr, None)
                if value is not None:
                    extra.append(f"{attr}={self.value(value)}")
            return call("sa.ForeignKeyConstraint", repr(columns), repr(targets))
        raise MigrationSerializationError(
            f"Cannot write a {type(constraint).__name__} to a migration file."
        )

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------

    def type_(self, sa_type: Any) -> str:
        """Render a SQLAlchemy type, variants included, and verify the result."""
        source = self._type_base(sa_type)
        variants = getattr(sa_type, "_variant_mapping", None) or {}
        for dialect in sorted(variants):
            source += f".with_variant({self._type_base(variants[dialect])}, {dialect!r})"
        self._check_type(sa_type, source)
        return source

    def _type_base(self, sa_type: Any) -> str:
        ref = self._class_ref(type(sa_type), what="type")
        positional, varargs, keywords = _constructor_arguments(sa_type)
        parts = [self.value(v) for v in positional]
        parts += [self.value(v) for v in varargs]
        parts += [f"{key}={self.value(v)}" for key, v in keywords]
        return f"{ref}({', '.join(parts)})"

    def _check_type(self, sa_type: Any, source: str) -> None:
        try:
            rebuilt = eval(source, dict(self._namespace))  # noqa: S307 — our own output
        except Exception as exc:
            raise MigrationSerializationError(
                f"The type {sa_type!r} was rendered as {source}, which does not "
                f"evaluate ({type(exc).__name__}: {exc})."
            ) from exc
        if type_fingerprint(rebuilt) != type_fingerprint(sa_type):
            raise MigrationSerializationError(
                f"The type {sa_type!r} cannot be written to a migration file "
                f"faithfully: {source} builds a different type."
            )

    # ------------------------------------------------------------------
    # Names: classes, modules, callables
    # ------------------------------------------------------------------

    def _import(self, alias: str, line: str, module: Any = None) -> None:
        bound = self._namespace.get(alias)
        if module is None:
            module = importlib.import_module(alias)
        if bound is not None and bound is not module:
            raise MigrationSerializationError(
                f"'{alias}' would mean two different things in the migration file."
            )
        self._namespace[alias] = module
        if alias not in ("sa", "operations"):
            self._import_lines.add(line)

    def _class_ref(self, cls: type, *, what: str) -> str:
        """An expression naming *cls*, importing what it needs."""
        import sqlalchemy

        module_name, qualname = cls.__module__, cls.__qualname__
        if module_name.startswith("sqlalchemy"):
            if getattr(sqlalchemy, qualname, None) is cls:
                return f"sa.{qualname}"
            match = re.match(r"sqlalchemy\.dialects\.(\w+)", module_name)
            if match:
                dialect = match.group(1)
                dialect_module = importlib.import_module(f"sqlalchemy.dialects.{dialect}")
                if getattr(dialect_module, qualname, None) is cls:
                    self._import(
                        dialect, f"from sqlalchemy.dialects import {dialect}", dialect_module
                    )
                    return f"{dialect}.{qualname}"
            if getattr(sqlalchemy.types, qualname, None) is cls:
                return f"sa.types.{qualname}"
        return self._module_ref(module_name, qualname, cls, what=what)

    def _module_ref(self, module_name: str, qualname: str, obj: Any, *, what: str) -> str:
        if "<locals>" in qualname or module_name == "__main__" or is_migration_module(
            module_name
        ):
            raise MigrationSerializationError(
                f"The {what} {qualname} is defined in {module_name}, which a migration "
                f"file cannot import. Move it into an importable module."
            )
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise MigrationSerializationError(
                f"The {what} {qualname} lives in {module_name}, which cannot be "
                f"imported: {exc}"
            ) from exc
        found: Any = module
        for part in qualname.split("."):
            found = getattr(found, part, None)
        if found is not obj:
            raise MigrationSerializationError(
                f"The {what} {qualname} is not reachable as {module_name}.{qualname}."
            )
        top = module_name.split(".")[0]
        if top in _RESERVED or top in self._defined_names():
            raise MigrationSerializationError(
                f"The module {module_name} clashes with a name the migration file uses."
            )
        self._import(top, f"import {module_name}", importlib.import_module(top))
        return f"{module_name}.{qualname}"

    def _defined_names(self) -> set[str]:
        return set(self._copied.values())

    def callable_(self, func: Any) -> str:
        """Reference a ``RunPython`` callable, copying it when it must be."""
        from zeeb_orm.migrations.operations import RunPython

        if func is RunPython.noop:
            return "operations.RunPython.noop"
        qualname = getattr(func, "__qualname__", "")
        module_name = getattr(func, "__module__", None) or ""
        if qualname == "<lambda>" or "<lambda>" in qualname:
            raise MigrationSerializationError(
                "A lambda cannot be written to a migration file; give RunPython a "
                "module-level function."
            )
        if is_migration_module(module_name):
            if not self._copy_functions:
                return self._renamed.get((module_name, func.__name__), func.__name__)
            return self._copy(func)
        return self._module_ref(module_name, qualname, func, what="function")

    # ------------------------------------------------------------------
    # Copying functions out of migration files
    # ------------------------------------------------------------------

    def _free_name(self, name: str) -> str:
        """*name*, or *name* suffixed until nothing in this file uses it."""
        taken = _RESERVED | set(self._namespace) | self._defined_names()
        target, counter = name, 2
        while target in taken:
            target = f"{name}_{counter}"
            counter += 1
        return target

    def _copy(self, func: Any) -> str:
        """Copy a function defined in a migration file into this file.

        Returns the name it has here: the original one, unless something else
        in this file already uses it, in which case it is suffixed.
        """
        module_name, name = func.__module__, func.__name__
        key = (module_name, name)
        if key in self._copied:
            return self._copied[key]
        if "<locals>" in func.__qualname__ or func.__closure__:
            raise MigrationSerializationError(
                f"{func.__qualname__} is a nested function or closure and cannot be "
                f"copied into a migration file; make it module-level."
            )
        try:
            source = textwrap.dedent(inspect.getsource(func))
        except (OSError, TypeError) as exc:
            raise MigrationSerializationError(
                f"The source of {name} (from {module_name}) is not available to copy: {exc}"
            ) from exc
        tree = ast.parse(source)
        definition = tree.body[0] if len(tree.body) == 1 else None
        if (
            not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
            or definition.decorator_list
        ):
            raise MigrationSerializationError(
                f"{name} (from {module_name}) is not a plain function definition and "
                f"cannot be copied into a migration file."
            )

        target = self._free_name(name)
        self._copied[key] = target

        # The body looks its globals up when it runs (they are in co_names);
        # default values are evaluated once, where the function is defined, so
        # their names are read off the source instead. Each is renamed only
        # where it is used: a parameter called TABLE stays the parameter.
        body_renames = self._copy_globals(func, _global_names(func.__code__), module_name)
        default_renames = self._copy_globals(func, _default_names(definition), module_name)
        # Annotations are evaluated where the function is defined too; they are
        # dropped rather than copied, since a data migration does not need them.
        annotated = _strip_annotations(definition)
        if not (body_renames or default_renames or annotated or target != name):
            self._definitions.append(source.rstrip() + "\n")
            return target
        definition.name = target
        for statement in definition.body:
            _Renamer(body_renames).visit(statement)
        arguments = definition.args
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default is not None:
                _Renamer(default_renames).visit(default)
        self._definitions.append(ast.unparse(tree).rstrip() + "\n")
        return target

    def _copy_globals(self, func: Any, names: set[str], module_name: str) -> dict[str, str]:
        """Bring the globals *func* reads under *names* into this file; their renames."""
        renames: dict[str, str] = {}
        for global_name in sorted(names):
            if global_name == func.__name__:
                # Recursion: the copy calls itself under its name here.
                if self._copied[(module_name, global_name)] != global_name:
                    renames[global_name] = self._copied[(module_name, global_name)]
                continue
            if global_name not in func.__globals__ or (
                global_name.startswith("__") and global_name.endswith("__")
            ):
                continue  # a builtin, an attribute name, or a module dunder
            local = self._copy_global(global_name, func.__globals__[global_name], module_name)
            if local != global_name:
                renames[global_name] = local
        return renames

    def _copy_global(self, name: str, value: Any, module_name: str) -> str:
        """Bring a global that a copied function uses into this file; its name here."""
        if isinstance(value, pytypes.ModuleType):
            if value.__name__ == name:
                line = f"import {name}"
            elif value.__name__.rpartition(".")[2] == name:
                parent = value.__name__.rpartition(".")[0]
                line = f"from {parent} import {name}"
            else:
                line = f"import {value.__name__} as {name}"
            self._import(name, line, value)
            return name
        if name in self._namespace and self._namespace[name] is value:
            return name
        if isinstance(value, (pytypes.FunctionType, type)):
            owner = getattr(value, "__module__", "") or ""
            if is_migration_module(owner):
                if isinstance(value, type):
                    raise MigrationSerializationError(
                        f"The class {name} is defined in a migration file and cannot be "
                        f"copied; move it into an importable module."
                    )
                return self._copy(value)
            original = value.__name__
            try:
                module = importlib.import_module(owner)
            except ImportError:
                module = None
            if module is None or getattr(module, original, None) is not value:
                raise MigrationSerializationError(
                    f"A copied function uses {name}, which cannot be imported from "
                    f"{owner}; move it into an importable module."
                )
            if name in _RESERVED or name in self._namespace or name in self._defined_names():
                raise MigrationSerializationError(
                    f"A copied function uses '{name}', which clashes with a name the "
                    f"migration file already uses."
                )
            self._namespace[name] = value
            self._import_lines.add(
                f"from {owner} import {name}"
                if original == name
                else f"from {owner} import {original} as {name}"
            )
            return name
        if _is_scalar(value) or isinstance(value, (list, tuple, dict, set, frozenset)):
            key = (module_name, name)
            if key not in self._copied:
                target = self._free_name(name)
                self._copied[key] = target
                self._definitions.append(f"{target} = {self.value(value)}\n")
            return self._copied[key]
        raise MigrationSerializationError(
            f"A copied function uses the global {name} ({type(value).__name__}), which "
            f"cannot be written to a migration file."
        )


class _Renamer(ast.NodeTransformer):
    """Rename module-level names (definitions and references) in copied code."""

    def __init__(self, renames: dict[str, str]) -> None:
        self.renames = renames

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # noqa: N802
        node.name = self.renames.get(node.name, node.name)
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:  # noqa: N802
        node.name = self.renames.get(node.name, node.name)
        self.generic_visit(node)
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        node.id = self.renames.get(node.id, node.id)
        return node


def _global_names(code: pytypes.CodeType) -> set[str]:
    """Every name *code* (and code nested in it) may look up globally."""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, pytypes.CodeType):
            names |= _global_names(const)
    return names


def _default_names(definition: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names the default values of *definition*'s parameters read."""
    arguments = definition.args
    return {
        node.id
        for default in [*arguments.defaults, *arguments.kw_defaults]
        if default is not None
        for node in ast.walk(default)
        if isinstance(node, ast.Name)
    }


def _strip_annotations(definition: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Remove *definition*'s own annotations; whether it had any."""
    arguments = definition.args
    every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    every += [arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None]
    found = definition.returns is not None or any(arg.annotation is not None for arg in every)
    definition.returns = None
    for arg in every:
        arg.annotation = None
    return found


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str, bytes, Decimal))


def constraint_columns(constraint: Any) -> list[str]:
    """Column names of a (possibly unbound) Unique/PrimaryKey constraint."""
    columns = [col.key if hasattr(col, "key") else str(col) for col in constraint.columns]
    if columns:
        return columns
    pending = getattr(constraint, "_pending_colargs", ())
    return [col.name if hasattr(col, "name") else str(col) for col in pending]


# ---------------------------------------------------------------------------
# Type introspection
# ---------------------------------------------------------------------------


def _constructor_arguments(
    sa_type: Any,
) -> tuple[list[Any], list[Any], list[tuple[str, Any]]]:
    """The arguments that rebuild *sa_type*: positional, ``*args``, keywords.

    The same association SQLAlchemy's own ``repr`` of a type uses — each
    ``__init__`` parameter read back from the same-named attribute — with
    keyword-only parameters included and private (``_``-prefixed) ones left
    out. Keywords equal to their default are omitted.
    """
    import sqlalchemy as sa
    from sqlalchemy.sql.sqltypes import SchemaType

    missing = object()
    to_inspect: list[Any] = [type(sa_type)]
    additional: list[tuple[str, Any]] = []
    omit: set[str] = set()
    if isinstance(sa_type, sa.Enum):
        to_inspect = [sa.Enum, SchemaType]
        additional = [
            ("native_enum", True),
            ("create_constraint", False),
            ("length", getattr(sa_type, "_default_length", missing)),
        ]
        # ``schema`` is DDL (the type's namespace) and stays; ``metadata`` is
        # the in-process MetaData the type was bound to.
        omit = {"metadata"}

    positional_names: list[str] = []
    varargs_name: str | None = None
    keywords: dict[str, Any] = {}
    for index, cls in enumerate(to_inspect):
        try:
            spec = inspect.getfullargspec(cls.__init__)
        except TypeError:
            continue
        defaults = spec.defaults or ()
        args = spec.args[1:]
        required = args[: len(args) - len(defaults)] if defaults else args
        if index == 0:
            positional_names.extend(required)
            varargs_name = spec.varargs
        else:
            keywords.update((arg, missing) for arg in required)
        keywords.update(zip(args[len(args) - len(defaults):], defaults))
        for arg in spec.kwonlyargs:
            keywords[arg] = (spec.kwonlydefaults or {}).get(arg, missing)
    keywords.update(additional)

    positional = [getattr(sa_type, name, None) for name in positional_names]
    varargs = list(getattr(sa_type, varargs_name, ()) or ()) if varargs_name else []
    rendered: list[tuple[str, Any]] = []
    for name, default in keywords.items():
        if name.startswith("_") or name in omit or name in positional_names:
            continue
        try:
            value = getattr(sa_type, name, missing)
        except Exception:
            continue
        if value is missing:
            continue
        if value is None and type(default).__name__ == "_NoArg":
            continue  # SQLAlchemy's "not given" sentinel, stored as None
        try:
            same = value == default
        except Exception:
            same = False
        if same is not True:
            rendered.append((name, value))
    return positional, varargs, rendered


_dialect_cache: dict[str, Any] = {}


def _dialect(name: str) -> Any:
    if name not in _dialect_cache:
        from sqlalchemy.dialects import registry

        _dialect_cache[name] = registry.load(name)()
    return _dialect_cache[name]


def type_ddl(sa_type: Any) -> tuple[str, ...]:
    """The DDL *sa_type* compiles to on each supported dialect (variants applied)."""
    ddl = []
    for name in _DIALECTS:
        try:
            ddl.append(str(sa_type.compile(dialect=_dialect(name))))
        except Exception as exc:
            ddl.append(f"<{type(exc).__name__}>")
    return tuple(ddl)


def type_fingerprint(sa_type: Any) -> tuple:
    """What a type *is* to a migration: its class, its variants, and its DDL.

    Python-side options that never reach the database (``Uuid(as_uuid=...)``,
    an ``Enum``'s Python class) are deliberately not part of it.
    """
    variants = getattr(sa_type, "_variant_mapping", None) or {}
    return (
        type(sa_type),
        tuple(sorted((d, type_fingerprint(v)) for d, v in variants.items())),
        type_ddl(sa_type),
    )
