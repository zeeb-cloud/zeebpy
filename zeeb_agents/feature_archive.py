"""Take a feature's code out of the tree, and put it back exactly as it was.

Deactivating a feature has to remove it from the running application without
losing anything: not the code, not the hand edits made to it since it was
generated, and above all not the data. This module does the file surgery for
that, and its inverse.

Two decisions shape everything here:

**Models stay.** Only the API layer is archived — viewsets, serializers, route
handlers, hooks, tasks, permission classes, tests. ``models.py`` is never
touched, so the app keeps its tables registered, the schema is unchanged, and
``make_migrations`` has nothing to say. Deactivating is therefore free and
instantly reversible, and no path through it can drop a table. Removing data is
what ``delete_feature`` is for, and it says so.

**Surgery, not directory moves.** Features may share an app, so archiving cannot
move ``apps/<app>/``. Each artifact is lifted out of its file individually, by
the ownership record in the manifest, leaving the other features in that file
untouched. The imports other modules hold of a lifted class go with it (and come
back with it), and :func:`find_dependents` refuses the surgery up front when
code that stays would still use what leaves — a shared app must keep importing.

Fragments are stored verbatim under ``.zeeb/archive/<feature>/`` as readable
source files, each with the import lines it needs, so restoring is a copy back
rather than a regeneration — a feature comes back with the edits it left with.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from zeeb_agents._utils.code_gen import (
    append_block,
    ensure_import,
    extract_class_block,
    extract_function_block,
    imported_bindings,
    imports_referenced_by,
    referenced_names,
    remove_class_block,
    remove_imported_names,
    remove_route_function,
    restore_import_statement,
    write_source,
)
from zeeb_agents._utils.project import get_app_path
from zeeb_agents.feature_manifest import archive_path, split_ref

RECORD_NAME = "archive.json"

#: Which project file each artifact kind lives in, relative to ``apps/<app>/``.
_HOST_FILE = {
    "viewset": "views.py",
    "serializer": "serializers.py",
    "action": "views.py",
    "endpoint": "views.py",
    "hook": "signals.py",
    "task": "tasks.py",
    "rule": "permissions.py",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app_file(root: Path, app: str, filename: str) -> Path:
    return get_app_path(app, root) / filename


def _record_path(value: object) -> str:
    """A path read from an archive record, with POSIX separators.

    Records written on Windows before paths were stored POSIX-style carry
    backslashes; ``Path`` on POSIX would read those as one file name.
    """
    return str(value or "").replace("\\", "/")


def _fragment_file(root: Path, feature: str, kind: str, name: str) -> Path:
    return archive_path(root, feature) / kind / f"{name}.py"


def _cut(
    root: Path,
    feature: str,
    app: str,
    filename: str,
    kind: str,
    name: str,
    *,
    is_class: bool,
) -> dict | None:
    """Lift one class/function out of an app file into the archive.

    Returns a fragment record, or ``None`` when the artifact is not in the file
    — already gone is a skip, not a failure, so a re-run stays idempotent.
    """
    host = _app_file(root, app, filename)
    if not host.is_file():
        return None
    content = host.read_text(encoding="utf-8")
    block = (
        extract_class_block(content, name)
        if is_class
        else extract_function_block(content, name)
    )
    if block is None:
        return None

    imports = imports_referenced_by(content, block)
    target = _fragment_file(root, feature, kind, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block, encoding="utf-8")

    updated = (
        remove_class_block(content, name)
        if is_class
        else remove_route_function(content, name)
    )
    if updated is not None:
        write_source(host, updated)

    return {
        "kind": kind,
        "name": name,
        "app": app,
        "file": f"apps/{app}/{filename}",
        # POSIX separators: archive.json is committed project state and must
        # read the same on every OS.
        "fragment": target.relative_to(archive_path(root, feature)).as_posix(),
        "imports": imports,
        "is_class": is_class,
    }


def planned_cuts(artifacts: dict) -> list[tuple[str, str, str, str, bool]]:
    """The class/function cuts archiving *artifacts* makes, in order.

    Each is ``(app, filename, kind, name, is_class)``.
    """
    cuts: list[tuple[str, str, str, str, bool]] = []
    for ref in artifacts.get("viewsets") or []:
        app, name = split_ref(ref)
        if app and name:
            cuts.append((app, "views.py", "viewset", name, True))
    for ref in artifacts.get("serializers") or []:
        app, name = split_ref(ref)
        if app and name:
            cuts.append((app, "serializers.py", "serializer", name, True))
    for func in artifacts.get("functions") or []:
        kind = func.get("kind")
        app = func.get("app")
        name = func.get("name")
        filename = _HOST_FILE.get(kind or "")
        if not (app and name and filename):
            continue
        if kind == "action":
            # An action is a method inside its viewset's body, so it leaves the
            # tree with the viewset — archiving it separately would be a
            # second, failing cut of code that is no longer there.
            continue
        cuts.append((app, filename, kind, name, kind == "rule"))
    return cuts


#: Top-level project directories that hold no importable application code.
_NOT_CODE_DIRS = frozenset({"tests", "migrations", "logs"})
#: Directories, at any depth, that are never the project's own code.
_NEVER_CODE_DIRS = frozenset({"__pycache__", "node_modules", "venv", "site-packages"})


def _code_files(root: Path) -> list[Path]:
    """The project's importable ``.py`` files: the apps and the project package."""
    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if len(parts) < 2 or parts[0] in _NOT_CODE_DIRS:
            continue
        if any(part in _NEVER_CODE_DIRS or part.startswith(".") for part in parts):
            continue
        out.append(path)
    return out


def _package_of(root: Path, path: Path) -> str:
    """The package a module file's relative imports resolve against."""
    return ".".join(path.relative_to(root).parts[:-1])


def _host_module(app: str, filename: str) -> str:
    return f"apps.{app}.{Path(filename).stem}"


def find_dependents(
    root: Path, artifacts: dict, *, ignore: set[tuple[str, str]] | None = None
) -> list[dict]:
    """Code that stays in the tree but uses something archiving would remove.

    Read-only, and run before anything moves: when another feature's class
    reads ``PostSerializer`` (a nested serializer, a subclass, a viewset
    pointing at it), taking ``PostSerializer`` away would leave that module
    failing at import — and the app-mate the surgery was meant to spare goes
    down with it.

    *ignore* holds ``(project-relative file, name)`` pairs whose use the
    caller removes anyway (the ``router.register`` lines of the routes it
    unregisters). Returns ``{"file", "name"}`` dicts.
    """
    cuts = planned_cuts(artifacts)
    targets = {(_host_module(app, filename), name) for app, filename, _k, name, _c in cuts}
    by_host: dict[str, list[tuple[str, bool]]] = {}
    for app, filename, _kind, name, is_class in cuts:
        by_host.setdefault(f"apps/{app}/{filename}", []).append((name, is_class))
    ignore = ignore or set()
    found: list[dict] = []
    for path in _code_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        bound: set[str] = set()
        for name, is_class in by_host.get(rel, []):
            updated = (
                remove_class_block(content, name)
                if is_class
                else remove_route_function(content, name)
            )
            if updated is not None:
                content = updated
                bound.add(name)
        bound.update(
            local
            for module, name, local in imported_bindings(content, _package_of(root, path))
            if (module, name) in targets
        )
        if not bound:
            continue
        for name in sorted(bound & referenced_names(content)):
            if (rel, name) not in ignore:
                found.append({"file": rel, "name": name})
    return found


def _trim_imports(root: Path, targets: set[tuple[str, str]]) -> list[dict]:
    """Remove every module-level import of an archived symbol; return the records."""
    if not targets:
        return []
    removed: list[dict] = []
    for path in _code_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated, records = remove_imported_names(content, _package_of(root, path), targets)
        if not records:
            continue
        write_source(path, updated)
        rel = path.relative_to(root).as_posix()
        removed.extend({"file": rel, **record} for record in records)
    return removed


def _prune_host_imports(root: Path, used_by_cut: dict[str, set[str]]) -> list[dict]:
    """Drop project-local imports only the lifted blocks used, from their hosts.

    ``from .models import Post`` stays behind in ``views.py`` when
    ``PostViewSet`` leaves; unused, it fails the project's own lint. Only
    imports of project modules (``apps.*``) are pruned — the scaffold's
    framework imports carry their own ``noqa`` and stay. Recorded like the
    other removed imports, so the restore puts them back in place.
    """
    removed: list[dict] = []
    for rel, used in used_by_cut.items():
        host = root / rel
        if not host.is_file():
            continue
        content = host.read_text(encoding="utf-8")
        package = _package_of(root, host)
        still_used = referenced_names(content)
        unused = {
            (module, name)
            for module, name, local in imported_bindings(content, package)
            if module.startswith("apps.") and local in used and local not in still_used
        }
        if not unused:
            continue
        updated, records = remove_imported_names(content, package, unused)
        if records:
            write_source(host, updated)
            removed.extend({"file": rel, **record} for record in records)
    return removed


def archive_artifacts(
    root: Path,
    feature: str,
    artifacts: dict,
    keep_tests: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Archive every API-layer artifact *feature* owns; return the archive record.

    ``models`` is read from the ownership record but deliberately never acted on
    — see the module docstring. Routes are unregistered by the caller (that is an
    agent-level operation), and are recorded here so the restore knows what to
    put back.

    Imports of the archived classes/functions left behind in other modules
    (``from .serializers import PostSerializer`` in ``views.py``) go too, and
    are recorded under ``imports_removed`` so the remaining app still imports
    and the restore puts each statement back exactly. Test files in
    *keep_tests* — also owned by another active feature — stay where they are
    and are listed under ``tests_kept``.
    """
    fragments: list[dict] = []
    targets: set[tuple[str, str]] = set()
    used_by_cut: dict[str, set[str]] = {}
    for app, filename, kind, name, is_class in planned_cuts(artifacts):
        fragment = _cut(root, feature, app, filename, kind, name, is_class=is_class)
        if fragment:
            fragments.append(fragment)
            targets.add((_host_module(app, filename), name))
            block = (_fragment_file(root, feature, kind, name)).read_text(encoding="utf-8")
            used_by_cut.setdefault(fragment["file"], set()).update(referenced_names(block))

    # Two passes, each recorded with its number: the restore undoes the later
    # pass first, so every record's anchors are back when it is replayed.
    imports_removed = [{**r, "pass": 0} for r in _trim_imports(root, targets)]
    imports_removed.extend({**r, "pass": 1} for r in _prune_host_imports(root, used_by_cut))

    tests: list[dict] = []
    tests_kept: list[str] = []
    for rel in artifacts.get("tests") or []:
        source = root / rel
        if not source.is_file():
            continue
        if rel in keep_tests:
            tests_kept.append(rel)
            continue
        target = archive_path(root, feature) / "tests" / Path(rel).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        tests.append({"path": rel, "fragment": f"tests/{Path(rel).name}"})

    # A re-run (a delete that has to be finished) cuts nothing new; what the
    # first run archived stays on the record.
    previous = read_record(root, feature) or {}
    record = {
        "feature": feature,
        "archived_at": _now(),
        "fragments": [*(previous.get("fragments") or []), *fragments],
        "imports_removed": [*(previous.get("imports_removed") or []), *imports_removed],
        "routes": list(artifacts.get("routes") or []),
        "tests": [*(previous.get("tests") or []), *tests],
        "tests_kept": tests_kept,
        "models_retained": list(artifacts.get("models") or []),
    }
    write_record(root, feature, record)
    return record


def write_record(root: Path, feature: str, record: dict) -> None:
    """Persist the archive record next to the fragments."""
    path = archive_path(root, feature)
    path.mkdir(parents=True, exist_ok=True)
    (path / RECORD_NAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_record(root: Path, feature: str) -> dict | None:
    """Read the archive record for *feature*, or ``None`` when there is none."""
    path = archive_path(root, feature) / RECORD_NAME
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def restore_artifacts(root: Path, feature: str, record: dict) -> dict:
    """Put archived fragments back into their files. Returns what was restored.

    Idempotent: a fragment whose class or function is already present is
    skipped, so re-running after a partial restore completes the rest.
    ``missing`` names fragments the record points at that are no longer on
    disk — the caller decides whether to rebuild those from the stored spec.
    """
    restored: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []

    # The imports first: the later removal pass undone first, and within a
    # pass in the order they were taken out, so each one's anchor (the import
    # statement before it) is back in its original form when it is replayed.
    # The fragments' own imports below then find them already in place.
    removed_imports = list(record.get("imports_removed") or [])
    removed_imports.sort(key=lambda entry: -int(entry.get("pass") or 0))
    for entry in removed_imports:
        host = root / _record_path(entry.get("file"))
        if not host.is_file():
            continue
        updated = restore_import_statement(host.read_text(encoding="utf-8"), entry)
        if updated is not None:
            write_source(host, updated)

    for fragment in record.get("fragments", []):
        source = archive_path(root, feature) / _record_path(fragment.get("fragment"))
        host = root / _record_path(fragment.get("file"))
        if not source.is_file():
            missing.append(fragment.get("name", "?"))
            continue
        if not host.is_file():
            missing.append(fragment.get("name", "?"))
            continue
        block = source.read_text(encoding="utf-8")
        content = host.read_text(encoding="utf-8")
        name = fragment.get("name", "")
        already = (
            extract_class_block(content, name)
            if fragment.get("is_class")
            else extract_function_block(content, name)
        )
        if already is not None:
            skipped.append(name)
            continue
        for import_line in fragment.get("imports", []):
            ensure_import(host, import_line)
        append_block(host, block)
        restored.append(name)
        source.unlink()

    tests: list[str] = []
    for entry in record.get("tests", []):
        source = archive_path(root, feature) / _record_path(entry.get("fragment"))
        target = root / _record_path(entry.get("path"))
        if not source.is_file():
            if not target.is_file():
                missing.append(entry.get("path", "?"))
            continue
        if target.exists():
            skipped.append(entry.get("path", "?"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        tests.append(entry.get("path", ""))

    return {
        "restored": restored,
        "tests_restored": tests,
        "skipped": skipped,
        "missing": missing,
        "routes": list(record.get("routes") or []),
    }


def purge_archive(root: Path, feature: str) -> bool:
    """Delete a feature's archive directory. Returns whether it existed."""
    path = archive_path(root, feature)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
