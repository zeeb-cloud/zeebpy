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
untouched.

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
    imports_referenced_by,
    remove_class_block,
    remove_route_function,
)
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
    return root / "apps" / app / filename


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
        host.write_text(updated, encoding="utf-8")

    return {
        "kind": kind,
        "name": name,
        "app": app,
        "file": f"apps/{app}/{filename}",
        "fragment": str(target.relative_to(archive_path(root, feature))),
        "imports": imports,
        "is_class": is_class,
    }


def archive_artifacts(root: Path, feature: str, artifacts: dict) -> dict:
    """Archive every API-layer artifact *feature* owns; return the archive record.

    ``models`` is read from the ownership record but deliberately never acted on
    — see the module docstring. Routes are unregistered by the caller (that is an
    agent-level operation), and are recorded here so the restore knows what to
    put back.
    """
    fragments: list[dict] = []

    for ref in artifacts.get("viewsets") or []:
        app, name = split_ref(ref)
        fragment = _cut(root, feature, app, "views.py", "viewset", name, is_class=True)
        if fragment:
            fragments.append(fragment)

    for ref in artifacts.get("serializers") or []:
        app, name = split_ref(ref)
        fragment = _cut(
            root, feature, app, "serializers.py", "serializer", name, is_class=True
        )
        if fragment:
            fragments.append(fragment)

    for func in artifacts.get("functions") or []:
        kind = func.get("kind")
        app = func.get("app")
        name = func.get("name")
        filename = _HOST_FILE.get(kind or "")
        if not (app and name and filename):
            continue
        if kind == "action":
            # An action is a method inside its viewset's body, so it left the
            # tree with the viewset — archiving it separately would be a
            # second, failing cut of code that is no longer there.
            continue
        fragment = _cut(
            root, feature, app, filename, kind, name, is_class=(kind == "rule")
        )
        if fragment:
            fragments.append(fragment)

    tests: list[dict] = []
    for rel in artifacts.get("tests") or []:
        source = root / rel
        if not source.is_file():
            continue
        target = archive_path(root, feature) / "tests" / Path(rel).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        tests.append({"path": rel, "fragment": f"tests/{Path(rel).name}"})

    record = {
        "feature": feature,
        "archived_at": _now(),
        "fragments": fragments,
        "routes": list(artifacts.get("routes") or []),
        "tests": tests,
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

    for fragment in record.get("fragments", []):
        source = archive_path(root, feature) / fragment.get("fragment", "")
        host = root / fragment.get("file", "")
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
        source = archive_path(root, feature) / entry.get("fragment", "")
        target = root / entry.get("path", "")
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
