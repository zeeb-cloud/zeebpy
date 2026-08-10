"""Feature identity — what a project is made of, and who owns each artifact.

``build_feature`` compiles a spec into models, serializers, endpoints, routes,
and tests, and then forgets.  That is enough to *create* capability but not to
*manage* it: a feature that nothing records cannot be listed, taken off the API,
put back, or removed, because nothing knows which classes and routes belonged to
it in the first place.

This module is that memory.  It keeps a small JSON manifest at
``<project>/.zeeb/features.json`` recording, per feature: the spec it was last
built from, its status, and — crucially — **per-artifact ownership**.  Ownership
is per artifact rather than per directory because features may share an app:
two features living in ``apps/blog/`` each own only their own models,
serializers, viewsets, routes, and tests, so archiving one must not disturb the
other.

The manifest is generated state, but it is *source* state: commit it.  Losing it
is recoverable — :func:`infer_features` reconstructs an approximate view from
what is on disk — but the reconstruction can only guess at feature boundaries
(one feature per app), so a project that has been managed through the manifest
should keep it.

Nothing here writes application code.  Archiving and restoring live in
:mod:`zeeb_agents.intent`; this module only records what happened.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

#: Bumped when the on-disk shape changes incompatibly.
MANIFEST_VERSION = 1

#: Project-relative home for Zeeb's own state. Committed, not ignored.
STATE_DIR = ".zeeb"
MANIFEST_NAME = "features.json"
ARCHIVE_DIRNAME = "archive"

#: Statuses a feature may hold.
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED)

#: The artifact buckets a feature may own. Order is display order.
ARTIFACT_KINDS = ("models", "serializers", "viewsets", "routes", "functions", "tests")


def state_dir(root: Path) -> Path:
    """Return ``<root>/.zeeb`` (not created)."""
    return root / STATE_DIR


def manifest_path(root: Path) -> Path:
    """Return the manifest file path (not created)."""
    return state_dir(root) / MANIFEST_NAME


def archive_path(root: Path, feature: str) -> Path:
    """Return the archive directory for *feature* (not created)."""
    return state_dir(root) / ARCHIVE_DIRNAME / feature


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_manifest() -> dict:
    """Return a well-formed, empty manifest document."""
    return {"version": MANIFEST_VERSION, "features": {}}


def load_manifest(root: Path) -> dict:
    """Read the manifest, returning an empty one when absent or unreadable.

    A corrupt manifest must not brick the lifecycle tools: the worst case is
    that features look un-recorded and :func:`infer_features` takes over, which
    is exactly the pre-manifest behaviour.
    """
    path = manifest_path(root)
    if not path.is_file():
        return empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty_manifest()
    if not isinstance(data, dict) or not isinstance(data.get("features"), dict):
        return empty_manifest()
    data.setdefault("version", MANIFEST_VERSION)
    return data


def save_manifest(root: Path, data: dict) -> None:
    """Write the manifest atomically, creating ``.zeeb/`` when needed."""
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def empty_artifacts() -> dict:
    """Return an artifact record with every bucket present and empty."""
    return {kind: [] for kind in ARTIFACT_KINDS}


def artifacts_from_plan(plan: dict) -> dict:
    """Derive the artifacts a plan's operations create, as ownership records.

    The plan is the precise statement of what this feature brings into
    existence, which is what makes shared apps workable: only the classes and
    routes named here belong to the feature, whatever else lives in the file.
    """
    owned = empty_artifacts()
    for op in plan.get("operations", []):
        kind = op.get("op")
        app = op.get("app")
        model = op.get("model")
        if kind in ("create_model", "create_user_model") and app:
            name = model or op.get("model_name")
            if name:
                owned["models"].append(f"{app}.{name}")
        elif kind == "create_serializer" and app and model:
            owned["serializers"].append(f"{app}.{model}Serializer")
        elif kind == "create_viewset" and app and model:
            owned["viewsets"].append(f"{app}.{model}ViewSet")
        elif kind == "register_route" and app and model:
            owned["routes"].append(
                {"app": app, "model": model, "prefix": op.get("url_prefix")}
            )
        elif kind == "add_viewset_action" and app and model:
            owned["functions"].append(
                {
                    "name": op.get("action_name"),
                    "kind": "action",
                    "app": app,
                    "entity": model,
                }
            )
        elif kind == "create_route" and app:
            owned["functions"].append(
                {
                    "name": op.get("function_name"),
                    "kind": "endpoint",
                    "app": app,
                    "path": op.get("path"),
                    "method": op.get("method"),
                }
            )
        elif kind == "create_signal_receiver" and app:
            owned["functions"].append(
                {
                    "name": op.get("function_name"),
                    "kind": "hook",
                    "app": app,
                    "entity": op.get("model_name") or model,
                    "signal": op.get("signal_name"),
                }
            )
        elif kind == "create_task" and app:
            owned["functions"].append(
                {"name": op.get("function_name"), "kind": "task", "app": app}
            )
        elif kind == "create_permission_class" and app:
            owned["functions"].append(
                {"name": op.get("class_name"), "kind": "rule", "app": app}
            )
        elif kind == "generate_tests" and app:
            owned["tests"].append(op.get("filename") or f"tests/test_{app}_generated.py")
    return _dedupe(owned)


def _dedupe(artifacts: dict) -> dict:
    """Drop duplicates while preserving order, for both str and dict entries."""
    out = empty_artifacts()
    for kind in ARTIFACT_KINDS:
        seen: list[str] = []
        for entry in artifacts.get(kind) or []:
            marker = json.dumps(entry, sort_keys=True)
            if marker in seen:
                continue
            seen.append(marker)
            out[kind].append(entry)
    return out


def merge_artifacts(existing: dict | None, addition: dict) -> dict:
    """Union two artifact records — ownership only ever grows on a rebuild."""
    merged = empty_artifacts()
    for kind in ARTIFACT_KINDS:
        merged[kind] = [*((existing or {}).get(kind) or []), *(addition.get(kind) or [])]
    return _dedupe(merged)


def record_feature(
    root: Path,
    name: str,
    app: str,
    spec: dict | None,
    plan: dict,
) -> dict:
    """Record (or update) a feature and return its manifest entry.

    Called after a successful build/change. Ownership is merged rather than
    replaced, so a second ``build_feature`` on an extended spec adds the new
    artifacts without disowning what the first build created.
    """
    data = load_manifest(root)
    entry = data["features"].get(name) or {}
    artifacts = merge_artifacts(entry.get("artifacts"), artifacts_from_plan(plan))
    entry.update(
        {
            "name": name,
            "app": app,
            "status": entry.get("status", STATUS_ACTIVE),
            "artifacts": artifacts,
            "created_at": entry.get("created_at", _now()),
            "updated_at": _now(),
        }
    )
    if spec is not None:
        entry["spec"] = spec
    data["features"][name] = entry
    save_manifest(root, data)
    return entry


def get_feature(root: Path, name: str) -> dict | None:
    """Return the manifest entry for *name*, or ``None``."""
    return load_manifest(root)["features"].get(name)


def feature_names(root: Path) -> list[str]:
    """Return every recorded feature name, sorted."""
    return sorted(load_manifest(root)["features"])


def set_feature(root: Path, name: str, entry: dict) -> dict:
    """Persist a full manifest *entry* for *name*, stamping ``updated_at``."""
    data = load_manifest(root)
    entry = {**entry, "updated_at": _now()}
    data["features"][name] = entry
    save_manifest(root, data)
    return entry


def set_status(root: Path, name: str, status: str, **extra: object) -> dict | None:
    """Set a feature's status (plus any extra keys) and return the entry."""
    data = load_manifest(root)
    entry = data["features"].get(name)
    if entry is None:
        return None
    entry.update({"status": status, "updated_at": _now(), **extra})
    save_manifest(root, data)
    return entry


def forget_feature(root: Path, name: str) -> bool:
    """Drop *name* from the manifest. Returns whether it was there."""
    data = load_manifest(root)
    if name not in data["features"]:
        return False
    del data["features"][name]
    save_manifest(root, data)
    return True


def infer_features(snapshot: dict) -> list[dict]:
    """Reconstruct an approximate feature list from a ``describe_project`` snapshot.

    The backfill path for projects built before the manifest existed (and the
    fallback when it is deleted). Without recorded ownership the only defensible
    boundary is the app, so this returns one feature per app that has models,
    flagged ``inferred`` so callers can say so rather than implying the project
    was managed as features all along.
    """
    by_app: dict[str, dict] = {}
    for app in snapshot.get("apps", []):
        name = app.get("name")
        if not name or not app.get("model_count"):
            continue
        by_app[name] = {
            "name": name,
            "app": name,
            "status": STATUS_ACTIVE,
            "inferred": True,
            "artifacts": empty_artifacts(),
        }

    for model in snapshot.get("models", []):
        entry = by_app.get(model.get("app"))
        if entry is None:
            continue
        entry["artifacts"]["models"].append(f"{model['app']}.{model['model']}")

    for endpoint in snapshot.get("endpoints", []):
        entry = by_app.get(endpoint.get("app"))
        if entry is None:
            continue
        model = endpoint.get("model") or endpoint.get("viewset", "").removesuffix("ViewSet")
        if not model:
            continue
        entry["artifacts"]["serializers"].append(f"{endpoint['app']}.{model}Serializer")
        entry["artifacts"]["viewsets"].append(f"{endpoint['app']}.{model}ViewSet")
        entry["artifacts"]["routes"].append(
            {
                "app": endpoint["app"],
                "model": model,
                "prefix": endpoint.get("prefix"),
            }
        )

    return [
        {**entry, "artifacts": _dedupe(entry["artifacts"])}
        for entry in sorted(by_app.values(), key=lambda e: e["name"])
    ]


def merge_changes_into_spec(spec: dict | None, changes: list[dict]) -> dict | None:
    """Fold applied ``change_feature`` operations back into a stored FeatureSpec.

    Without this the manifest's spec would describe only the *first* build, and
    every later change would silently rot it — which matters twice over: it is
    what an agent reads to learn what a feature is, and it is the fallback
    :func:`~zeeb_agents.intent.activate_feature` rebuilds from when an archived
    fragment has gone missing. Restoring from a spec that predates three field
    additions would quietly resurrect an older feature.

    Operations that do not describe spec shape (workflow and function edits are
    recorded as artifacts instead) pass through untouched. Returns ``None`` when
    there was no spec to update.
    """
    if not spec:
        return None
    updated = json.loads(json.dumps(spec))  # deep copy — never mutate the caller's
    entities: list[dict] = updated.setdefault("entities", [])

    def _entity(name: str | None) -> dict | None:
        return next((e for e in entities if e.get("name") == name), None)

    for change in changes:
        op = change.get("operation")
        target = change.get("entity")
        if op == "add_entity" and isinstance(target, dict):
            existing = _entity(target.get("name"))
            if existing is None:
                entities.append(target)
            continue
        if op == "remove_entity":
            entities[:] = [e for e in entities if e.get("name") != target]
            continue

        entity = _entity(target if isinstance(target, str) else None)
        if entity is None:
            continue
        fields: list[dict] = entity.setdefault("fields", [])
        field = change.get("field")
        if op in ("add_field", "add_relation") and isinstance(field, dict):
            spec_field = dict(field)
            if op == "add_relation":
                spec_field.setdefault("type", "relation")
            if not any(f.get("name") == spec_field.get("name") for f in fields):
                fields.append(spec_field)
        elif op == "alter_field" and isinstance(field, dict):
            for i, existing_field in enumerate(fields):
                if existing_field.get("name") == field.get("name"):
                    fields[i] = dict(field)
                    break
        elif op == "remove_field":
            name = change.get("field_name")
            fields[:] = [f for f in fields if f.get("name") != name]
        elif op == "set_permissions":
            entity.setdefault("api", {})["permissions"] = change.get("permissions")
        elif op == "set_authentication":
            entity.setdefault("api", {})["authentication"] = change.get("authentication")
    return updated


def split_ref(ref: str) -> tuple[str, str]:
    """Split an ``app.ClassName`` ownership reference into its two halves."""
    app, _, name = ref.partition(".")
    return app, name
