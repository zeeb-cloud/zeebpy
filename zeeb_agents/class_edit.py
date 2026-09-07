"""Surgical edits to one class in a generated file — methods and attributes.

The per-object tools create whole artifacts and the field tools edit one
field; what was missing was the layer in between: overriding ``get_queryset``
on a ViewSet, adding ``validate_title`` to a serializer, giving a model a
``__str__`` or a ``Meta.ordering``. Each of those used to mean rewriting the
whole file with ``write_file`` — and losing the migration detection and route
wiring the structured tools provide.

Two verbs cover it: :func:`set_class_method` adds, replaces or removes one
method; :func:`set_class_attribute` sets or unsets one class attribute (or one
``Meta`` key). Both locate the class by name across the app's generated files,
splice by the parser's exact span, and leave everything else in the file
untouched.
"""

from __future__ import annotations

import ast
import asyncio
import re
import textwrap
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    class_exists,
    ensure_import,
    remove_method_from_class,
    set_class_attribute_in_block,
    set_method_in_class,
)
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier

#: The generated files a class may live in, in lookup order.
CLASS_FILES = ("models.py", "serializers.py", "views.py", "permissions.py", "filters.py")


def _locate_class(app_dir: Path, app: str, class_name: str, file: str | None) -> tuple[Path, str]:
    """Return ``(path, project-relative path)`` of the file defining *class_name*."""
    if file is not None:
        path = app_dir / file
        if not path.is_file():
            raise AgentError(
                f"apps/{app}/{file} does not exist.", code="file_not_found", missing=file
            )
        candidates = [file]
    else:
        candidates = list(CLASS_FILES)
    known: list[str] = []
    for filename in candidates:
        path = app_dir / filename
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if class_exists(content, class_name):
            return path, f"apps/{app}/{filename}"
        known += re.findall(r"^class (\w+)", content, re.MULTILINE)
    raise AgentError(
        f"No class '{class_name}' in {', '.join(candidates)} of app '{app}'.",
        code="model_not_found",
        suggestions=close_matches(class_name, known),
    )


def _require_parseable(path: Path, rel: str) -> str:
    """Read *path*, failing with a pointer at the syntax error when it does not parse."""
    content = path.read_text(encoding="utf-8")
    try:
        ast.parse(content)
    except SyntaxError as exc:
        raise AgentError(
            f"{rel} does not parse (line {exc.lineno}: {exc.msg}) — repair it with "
            "edit_file before editing it structurally.",
            code="invalid_input",
            file=rel,
            line=exc.lineno,
        ) from exc
    return content


def _add_imports(path: Path, imports: list[str] | None) -> list[str]:
    """Add each import line once; return the ones that were actually new."""
    added: list[str] = []
    for line in imports or []:
        before = path.read_text(encoding="utf-8")
        ensure_import(path, line)
        if path.read_text(encoding="utf-8") != before:
            added.append(line)
    return added


@agent_function
async def set_class_method(
    app: str,
    class_name: str,
    method_name: str,
    source: str | None = None,
    file: str | None = None,
    remove: bool = False,
    imports: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Add, replace or remove one method on a model, serializer, viewset or permission class.

    *source* is the whole method — decorators, ``def`` line and body — at any
    indentation. An existing method of that name is replaced in place; a new
    one is appended to the class. This is how a ViewSet gets a
    ``get_queryset`` / ``perform_create`` override, a serializer a
    ``validate_<field>``, a model a ``__str__`` or ``save`` — without touching
    the rest of the file. To change only the statements of a generated
    ``@action``/route/hook/task, prefer ``edit_function``, which keeps the
    decorator and signature for you.

    Args:
        app: App directory name.
        class_name: The class to edit (``"PostViewSet"``, ``"PostSerializer"``,
            ``"Post"``).
        method_name: The method to add, replace or remove.
        source: Full method source, e.g. ``"def get_queryset(self):\\n    return
            Post.objects.filter(published=True)"``. Must define exactly
            ``method_name``. Required unless ``remove`` is true.
        file: Which generated file holds the class (``"views.py"``). Omit to
            search ``models.py``, ``serializers.py``, ``views.py``,
            ``permissions.py``, ``filters.py`` in that order.
        remove: Remove the method instead of writing one.
        imports: Import lines the method needs (``"from datetime import
            date"``); each is added once, at the top of the file.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name.
        file (str): project-relative file that was edited.
        class_name (str): the class that was edited.
        method (str): the method name.
        action (str): ``"added"``, ``"replaced"``, ``"removed"``, or
            ``"skipped"`` (removing a method that was not there).
        imports_added (list[str]): the import lines that were not already present.

    Notes:
        - Fails with ``invalid_input`` when ``source`` does not parse or does
          not define exactly ``method_name``, when neither ``source`` nor
          ``remove`` is given, or when the file itself does not parse (repair
          it with ``edit_file`` first).
        - Fails with ``model_not_found`` (with close-match ``suggestions``)
          when no generated file of the app defines the class.
    """
    root = project_root
    ensure_identifier(class_name, "class name")
    ensure_identifier(method_name, "method name")
    if remove and source is not None:
        return fail("Pass either source or remove=True, not both.", code="invalid_input")
    if not remove and not (source or "").strip():
        return fail(
            "source is required — the full method to add or replace — unless remove=True.",
            code="invalid_input",
        )
    app_dir = ensure_app_exists(app, root)
    if source is not None:
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError as exc:
            return fail(
                f"source does not parse (line {exc.lineno}: {exc.msg}).",
                code="invalid_input",
            )
        defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len(tree.body) != 1 or len(defs) != 1 or defs[0].name != method_name:
            return fail(
                f"source must define exactly one method named '{method_name}'.",
                code="invalid_input",
            )

    def _edit() -> tuple[str, str, list[str]]:
        path, rel = _locate_class(app_dir, app, class_name, file)
        content = _require_parseable(path, rel)
        if remove:
            updated = remove_method_from_class(content, class_name, method_name)
            if updated is None:
                return rel, "skipped", []
            path.write_text(updated, encoding="utf-8")
            return rel, "removed", []
        result = set_method_in_class(content, class_name, method_name, source or "")
        if result is None:  # pragma: no cover - the class was just located
            raise AgentError(f"'{class_name}' not found in {rel}", code="model_not_found")
        updated, action = result
        path.write_text(updated, encoding="utf-8")
        return rel, action, _add_imports(path, imports)

    rel, action, imports_added = await asyncio.to_thread(_edit)
    verb = {
        "added": "Added",
        "replaced": "Replaced",
        "removed": "Removed",
        "skipped": "No such method; nothing to do —",
    }[action]
    return AgentResult(
        success=True,
        message=f"{verb} {class_name}.{method_name} in {rel}",
        data={
            "app": app,
            "file": rel,
            "class_name": class_name,
            "method": method_name,
            "action": action,
            "imports_added": imports_added,
        },
    )


@agent_function
async def set_class_attribute(
    app: str,
    class_name: str,
    attribute: str,
    value: str | None = None,
    file: str | None = None,
    meta: bool = False,
    remove: bool = False,
    imports: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Set or unset one class attribute — or one ``Meta`` key — on a generated class.

    Covers what ``update_viewset``'s fixed option list does not: ``queryset``,
    ``filterset_class``, ``filter_backends``, ``ordering``, a serializer's
    ``Meta.extra_kwargs``, a model's ``Meta.db_table`` or ``Meta.ordering``.
    ``value`` is Python source and is written verbatim, so a queryset
    expression works as well as a literal. With ``meta=True`` the attribute
    goes on the class's inner ``Meta`` — created when the class has none.

    Args:
        app: App directory name.
        class_name: The class to edit.
        attribute: The attribute name.
        value: Python source for the value, e.g. ``'["-created_at"]'`` or
            ``'Post.objects.filter(published=True)'``. Required unless
            ``remove`` is true.
        file: Which generated file holds the class; omit to search the app's
            generated files (see ``set_class_method``).
        meta: Target the inner ``class Meta`` instead of the class body.
        remove: Remove the attribute instead of setting it. Removing the last
            ``Meta`` key removes the ``Meta`` class.
        imports: Import lines the value needs; each is added once.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name.
        file (str): project-relative file that was edited.
        class_name (str): the class that was edited.
        attribute (str): the attribute name.
        action (str): ``"set"`` (new), ``"replaced"``, ``"removed"``, or
            ``"skipped"`` (removing an attribute that was not there).
        meta (bool): whether the inner ``Meta`` was targeted.
        imports_added (list[str]): the import lines that were not already present.

    Notes:
        - Fails with ``invalid_input`` when ``value`` is not a valid Python
          expression, when neither ``value`` nor ``remove`` is given, or when
          the file does not parse (repair it with ``edit_file`` first).
        - Fails with ``model_not_found`` (with ``suggestions``) when no
          generated file of the app defines the class.
        - Field definitions are attributes too, but ``alter_field`` /
          ``remove_field`` know about migrations — use those for fields.
    """
    root = project_root
    ensure_identifier(class_name, "class name")
    ensure_identifier(attribute, "attribute name")
    if remove and value is not None:
        return fail("Pass either value or remove=True, not both.", code="invalid_input")
    if not remove and not (value or "").strip():
        return fail(
            "value is required — Python source for the attribute — unless remove=True.",
            code="invalid_input",
        )
    app_dir = ensure_app_exists(app, root)
    if value is not None:
        try:
            ast.parse(textwrap.dedent(value).strip(), mode="eval")
        except SyntaxError as exc:
            return fail(
                f"value is not a valid Python expression ({exc.msg}).", code="invalid_input"
            )

    def _edit() -> tuple[str, str, list[str]]:
        path, rel = _locate_class(app_dir, app, class_name, file)
        content = _require_parseable(path, rel)
        result = set_class_attribute_in_block(
            content,
            class_name,
            attribute,
            None if remove else textwrap.dedent(value or "").strip(),
            nested="Meta" if meta else None,
        )
        if result is None:  # pragma: no cover - the class was just located
            raise AgentError(f"'{class_name}' not found in {rel}", code="model_not_found")
        updated, action = result
        if action == "skipped":
            return rel, action, []
        path.write_text(updated, encoding="utf-8")
        return rel, action, _add_imports(path, imports)

    rel, action, imports_added = await asyncio.to_thread(_edit)
    where = f"{class_name}.Meta.{attribute}" if meta else f"{class_name}.{attribute}"
    verb = {
        "set": "Set",
        "replaced": "Replaced",
        "removed": "Removed",
        "skipped": "No such attribute; nothing to do —",
    }[action]
    return AgentResult(
        success=True,
        message=f"{verb} {where} in {rel}",
        data={
            "app": app,
            "file": rel,
            "class_name": class_name,
            "attribute": attribute,
            "action": action,
            "meta": meta,
            "imports_added": imports_added,
        },
    )
