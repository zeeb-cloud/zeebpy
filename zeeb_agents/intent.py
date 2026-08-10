"""Intent workflow functions — declarative feature building with verification.

The high-level entry points for agent-driven backend work: instead of
sequencing a dozen structural calls, describe the outcome (a FeatureSpec, a
change list, an auth setup) and one call plans, executes, and verifies it.

Design:

- ``plan_feature`` → ``apply_plan`` and ``build_feature`` share ONE compiler
  and ONE executor (:mod:`zeeb_agents.feature_spec`), so a reviewed plan and
  a direct build can never diverge.
- Plans are **stateless**: ``plan_feature`` returns the plan object itself;
  pass that object back to ``apply_plan`` to execute it (no server-side plan
  storage, no plan ids).
- Execution is **idempotent**: structural steps run with ``if_exists="skip"``
  (the ``generate_crud`` pattern), so re-running the same call after a partial
  failure completes only what is missing.
- Mutating intent functions end with a deterministic verification chain
  (structure → migrations → OpenAPI) and report one envelope shape::

      {summary, changes, verification, warnings, next_actions, state_changed}

  ``changes`` groups what was created (apps, models, endpoints, fields,
  migrations); ``verification`` carries per-check ``ok`` flags plus an
  overall ``passed``; a failed check does NOT fail the call — it shows up in
  ``verification`` and ``next_actions`` (acceptance gate, not error).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.project import load_project_settings, require_project_root
from zeeb_agents.feature_archive import (
    archive_artifacts,
    purge_archive,
    read_record,
    restore_artifacts,
)
from zeeb_agents.feature_manifest import (
    MANIFEST_NAME,
    STATE_DIR,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    forget_feature,
    infer_features,
    load_manifest,
    merge_changes_into_spec,
    record_feature,
    set_status,
    split_ref,
)
from zeeb_agents.feature_spec import (
    _DB_CHANGING_OPS,
    PLAN_VERSION,
    _plan_risk,
    compile_changes,
    compile_feature_spec,
    execute_plan,
    plan_preconditions,
    staleness_warnings,
    validate_plan,
)

_DEFAULT_CHECKS = ("structure", "migrations", "openapi")
_VALID_CHECKS = (
    "structure",
    "migrations",
    "openapi",
    "tests",
    # Opt-in: these need the runtime up (runtime/endpoints) or judge deployment
    # posture rather than correctness (security), so they are not part of the
    # default build gate.
    "runtime",
    "endpoints",
    "security",
)


def _default_checks(root: Path) -> tuple[str, ...]:
    """Default verification chain; includes ``tests`` once the project has a suite.

    The marker is ``tests/conftest.py`` — cheap, stateless, and still true after
    the user adds their own tests to the directory. Every project scaffolded by
    this version ships one, so the probe only excludes projects created before
    it. That is why it stays a probe rather than a hardcoded default: an old
    project without ``tests/`` would otherwise fail the check with ``no_tests``
    on every build.
    """
    if (root / "tests" / "conftest.py").exists():
        return (*_DEFAULT_CHECKS, "tests")
    return _DEFAULT_CHECKS

def _contains_segments(path: str, prefix: str) -> bool:
    """True when ``prefix`` appears as a run of whole path segments inside ``path``.

    Endpoints are reported with the project's API prefix ("/api/v1/orders") while
    routes are registered bare ("orders"), so anchoring the comparison at the start
    of the path reported every endpoint as unregistered — a false "no route matches"
    that sends the caller chasing a routing problem that does not exist. Matching on
    whole segments works whatever the API prefix is, and still refuses a partial
    word ("orders" must not match "preorders").
    """
    haystack = [seg for seg in path.split("/") if seg]
    needle = [seg for seg in prefix.split("/") if seg]
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
    )


#: Generator-owned files each op kind touches — powers the ``affected`` report.
_OP_FILES: dict[str, tuple[str, ...]] = {
    "create_model": ("models.py",),
    "add_field": ("models.py",),
    "alter_field": ("models.py",),
    "remove_field": ("models.py",),
    "add_relationship": ("models.py",),
    "create_user_model": ("models.py",),
    "delete_model": ("models.py",),
    "create_serializer": ("serializers.py",),
    "sync_serializer": ("serializers.py",),
    "delete_serializer": ("serializers.py",),
    "create_viewset": ("views.py",),
    "update_viewset": ("views.py",),
    "delete_viewset": ("views.py",),
    "add_viewset_action": ("views.py",),
    "create_route": ("views.py", "urls.py"),
    "create_signal_receiver": ("signals.py",),
    "create_task": ("tasks.py",),
    "create_permission_class": ("permissions.py",),
    "register_route": ("urls.py",),
    "unregister_route": ("urls.py",),
    "make_migrations": ("migrations",),
}


def _plan_app(plan: dict) -> str | None:
    """The first app a plan's operations touch — the feature's home app."""
    for op in plan.get("operations", []):
        app = op.get("app")
        if isinstance(app, str):
            return app
    return None


def _affected(plan: dict) -> dict:
    """Apps, entities, and files a plan touches (or would touch, for plans).

    Computed statically from the operations, so it is attached to successful,
    partially-failed, and plan-only results alike — on failure it reads as
    "the scope this call was touching".
    """
    apps: set[str] = set()
    entities: set[str] = set()
    files: set[str] = set()
    for op in plan.get("operations", []):
        app = op.get("app")
        model = op.get("model")
        if isinstance(app, str):
            apps.add(app)
        if isinstance(app, str) and isinstance(model, str):
            entities.add(f"{app}.{model}")
        for name in _OP_FILES.get(op.get("op", ""), ()):
            files.add(f"apps/{app}/{name}" if isinstance(app, str) else name)
    return {"apps": sorted(apps), "entities": sorted(entities), "files": sorted(files)}


async def _project_inventory(root: Path) -> tuple[list[dict], list[str]]:
    """Return ``(existing models, existing app names)`` for compilation."""
    from zeeb_agents._utils.project import list_apps
    from zeeb_agents.models import list_models

    models_res = await list_models(project_id=root)
    models = (models_res.data or {}).get("models", []) if models_res.success else []
    return models, await asyncio.to_thread(list_apps, root)


async def _smoke_endpoints(project_root: Path, port: int) -> dict:
    """GET every registered collection endpoint against the running runtime.

    ``openapi`` proves a route is *documented*; this proves it *responds*. An
    endpoint that raises on every request still passes the rest of the chain,
    which is exactly the "verified but broken" report this closes.

    A 5xx is a failure. Anything else — including 401/403 on a gated endpoint —
    counts as reachable: the handler ran and the framework answered.
    """
    import httpx

    from zeeb_agents.schema import list_all_routes

    routes_res = await list_all_routes(project_id=project_root)
    if not routes_res.success:
        return {"ok": False, "error": routes_res.message, "checked": 0}
    prefixes = sorted(
        {
            prefix
            for route in (routes_res.data or {}).get("routes", [])
            if (prefix := str(route.get("prefix") or "").strip("/"))
        }
    )
    if not prefixes:
        return {"ok": True, "checked": 0, "detail": "no registered endpoints"}

    api_prefix = _api_prefix(project_root)
    base = f"http://127.0.0.1:{port}"
    failures: list[dict] = []
    checked = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for prefix in prefixes:
                # Canonical paths are slash-less; the trailing-slash variant is
                # served too, but generate against the canonical one.
                url = f"{base}{api_prefix}/{prefix}"
                try:
                    resp = await client.get(url)
                except Exception as exc:
                    failures.append({"endpoint": url, "error": str(exc)})
                    continue
                checked += 1
                if resp.status_code >= 500:
                    failures.append({"endpoint": url, "status": resp.status_code})
    except Exception as exc:  # client construction / transport-level failure
        return {"ok": False, "error": str(exc), "checked": checked}

    return {
        "ok": not failures,
        "checked": checked,
        **({"failures": failures} if failures else {}),
    }


def _api_prefix(project_root: Path) -> str:
    """The project's API_PREFIX setting, or '' when it cannot be read."""
    from zeeb_agents._utils.project import load_project_settings

    try:
        settings = load_project_settings(project_root)
    except Exception:
        return ""
    return str(settings.get("API_PREFIX") or "")


async def _run_verification(
    checks: tuple[str, ...] | list[str],
    project_root: Path,
    port: int = 8000,
) -> dict:
    """Run the requested read-only checks; never raises, never writes."""
    from zeeb_agents.migrations import get_migration_status
    from zeeb_agents.project import describe_project
    from zeeb_agents.schema import _fetch_openapi_spec, list_all_routes
    from zeeb_agents.testing import run_tests

    results: dict[str, dict] = {}
    for check in checks:
        if check == "structure":
            res = await describe_project(project_id=project_root)
            data = (res.data or {}) if res.success else {}
            results["structure"] = {
                "ok": res.success and not data.get("warnings"),
                "served": bool(data.get("served")),
                "warnings": data.get("warnings", []) if res.success else [res.message],
            }
        elif check == "migrations":
            res = await get_migration_status(project_id=project_root)
            pending = (res.data or {}).get("pending_count") if res.success else None
            results["migrations"] = {
                "ok": bool(res.success) and pending == 0,
                "pending_count": pending,
            }
        elif check == "openapi":
            try:
                spec = await _fetch_openapi_spec(port)
                paths = list(spec.get("paths", {}))
                # Reachable is not the same as up to date. The runtime serves the last
                # deployed revision, so a spec fetched right after a write describes the
                # PREVIOUS one — the check passed on a contract that did not yet contain
                # the endpoints it was verifying. Require the registered routes to
                # actually be in the served document.
                routes_res = await list_all_routes(project_id=project_root)
                expected = (
                    [
                        prefix
                        for route in (routes_res.data or {}).get("routes", [])
                        if (prefix := str(route.get("prefix") or "").strip("/"))
                    ]
                    if routes_res.success
                    else []
                )
                missing = [
                    prefix
                    for prefix in expected
                    if not any(_contains_segments(path, prefix) for path in paths)
                ]
                results["openapi"] = {
                    "ok": not missing,
                    "paths_count": len(paths),
                    **({"missing_endpoints": sorted(missing)} if missing else {}),
                }
            except AgentError as exc:
                results["openapi"] = {"ok": False, "paths_count": None, "error": str(exc)}
        elif check == "runtime":
            from zeeb_agents.health import check_system_health

            res = await check_system_health(project_id=project_root)
            data = (res.data or {}).get("checks", {}) if res.success or res.data else {}
            results["runtime"] = {
                "ok": bool(res.success),
                "overall": data.get("overall"),
                "settings": data.get("settings"),
                "db": data.get("db"),
                **({"error": res.message} if not res.success else {}),
            }
        elif check == "security":
            from zeeb_agents.deploy import check_production_readiness

            res = await check_production_readiness(project_id=project_root)
            data = res.data or {}
            results["security"] = {
                "ok": bool(data.get("ready")),
                "issues": data.get("issues", []) if data else [res.message],
            }
        elif check == "endpoints":
            results["endpoints"] = await _smoke_endpoints(project_root, port)
        elif check == "tests":
            res = await run_tests(project_id=project_root)
            data = res.data or {}
            # An empty suite is not evidence of anything. run_tests is right to
            # call exit code 5 a successful RUN, but a verification chain that
            # counts "no tests collected" as a passing gate reports a project as
            # verified on the strength of zero assertions — including when an
            # import error silently emptied the collection.
            results["tests"] = {
                "ok": bool(res.success)
                and not data.get("failed")
                and not data.get("errors")
                and not data.get("no_tests"),
                "summary": res.message,
                "passed": data.get("passed"),
                "failed": data.get("failed"),
                "errors": data.get("errors"),
                "no_tests": bool(data.get("no_tests")),
                **(
                    {"failed_tests": data["failed_tests"]}
                    if data.get("failed_tests")
                    else {}
                ),
            }
    return {
        "passed": all(entry["ok"] for entry in results.values()),
        "checks": results,
    }


def _verification_next_actions(verification: dict) -> list[str]:
    """Turn failed checks into concrete follow-up actions."""
    actions: list[str] = []
    checks = verification.get("checks", {})
    migrations = checks.get("migrations")
    if migrations and not migrations["ok"]:
        actions.append("Apply the pending migrations (run_migrations).")
    structure = checks.get("structure")
    if structure and not structure["ok"]:
        actions.append(
            "Fix the wiring gaps listed under verification.checks.structure.warnings."
        )
    openapi = checks.get("openapi")
    if openapi and not openapi["ok"]:
        if openapi.get("missing_endpoints"):
            actions.append(
                "The served contract is missing "
                f"{', '.join(openapi['missing_endpoints'])} — it is still the previous "
                "revision. Redeploy the preview (redeploy_preview), then verify again."
            )
        else:
            actions.append(
                "The API was not reachable for the OpenAPI check — inspect the logs "
                "(read_logs) or the runtime reference (get_project_reference)."
            )
    tests = checks.get("tests")
    if tests and not tests["ok"]:
        if tests.get("no_tests"):
            actions.append(
                "No tests were collected — generate them with "
                "build_feature(tests=True), or check that the existing suite "
                "still imports (a broken import collects zero tests)."
            )
        elif tests.get("failed_tests"):
            named = ", ".join(tests["failed_tests"][:3])
            more = (
                f" (+{len(tests['failed_tests']) - 3} more)"
                if len(tests["failed_tests"]) > 3
                else ""
            )
            actions.append(f"Fix the failing tests: {named}{more}.")
        else:
            actions.append("Fix the failing tests (run_tests shows the detail).")
    runtime = checks.get("runtime")
    if runtime and not runtime["ok"]:
        actions.append(
            "The runtime is not healthy "
            f"(settings={runtime.get('settings')}, db={runtime.get('db')}) — "
            "check the database connection and settings, then read_logs."
        )
    endpoints = checks.get("endpoints")
    if endpoints and not endpoints["ok"]:
        failing = endpoints.get("failures") or []
        named = ", ".join(str(f.get("endpoint")) for f in failing[:3])
        actions.append(
            "Endpoints returned a server error or were unreachable: "
            f"{named or endpoints.get('error')} — read_logs shows the traceback."
        )
    security = checks.get("security")
    if security and not security["ok"]:
        issues = "; ".join(security.get("issues", [])[:3])
        actions.append(f"Resolve the production-readiness issues: {issues}.")
    return actions


async def _apply_and_report(
    plan: dict,
    project_root: Path,
    migrate: bool,
    verify: bool,
    label: str,
    checks: tuple[str, ...] | None = None,
    extra_next_actions: list[str] | None = None,
    feature: str | None = None,
    spec: dict | None = None,
) -> AgentResult:
    """Shared execute + verify + envelope tail for the mutating intent tools.

    ``checks=None`` selects the default chain, which auto-includes the
    ``tests`` check when the project carries a generated test suite — resolved
    *after* execution, so the build that generates the suite already gates on it.

    When *feature* is given the plan's artifacts are recorded against it in the
    manifest, including after a partial failure: ownership is what the lifecycle
    tools address a feature by, and a half-applied build still put some of those
    artifacts on disk.  Ownership is a union, so the re-run that completes the
    build converges rather than disowning anything.
    """
    outcome = await execute_plan(plan, project_root, migrate=migrate)
    if feature:
        await asyncio.to_thread(
            record_feature,
            project_root,
            feature,
            plan.get("feature", {}).get("app") or _plan_app(plan) or feature,
            spec,
            plan,
        )
    summary = plan.get("summary", label)
    warnings = list(plan.get("warnings", []))
    warnings.extend(outcome.get("warnings", []))
    drift = plan.get("drift")
    drift_action = (
        [
            "Destructive drift was detected but NOT applied — review "
            "data['drift'].entries and, if the removals are intended, send "
            "change_feature(changes=data['drift'].suggested_changes)."
        ]
        if drift
        else []
    )
    if outcome["errors"]:
        shown = "; ".join(outcome["errors"][:3])
        if len(outcome["errors"]) > 3:
            shown += "; …"
        return AgentResult(
            success=False,
            message=f"{label} partially applied: {shown}",
            data={
                "summary": summary,
                "changes": outcome["changes"],
                "steps_completed": outcome["steps"],
                "errors": outcome["errors"],
                # Which op failed, where in the plan, and what never ran — a
                # single state_changed boolean cannot tell a caller whether to
                # resume, repair, or start over.
                "failed_operations": outcome["failed_operations"],
                "completed_count": outcome["completed_count"],
                "total_count": outcome["total_count"],
                "remaining_operations": outcome["remaining_operations"],
                "error_code": "partial_failure",
                "recoverable": True,
                "affected": _affected(plan),
                "verification": None,
                "verified": False,
                **({"drift": drift} if drift else {}),
                "warnings": warnings,
                "next_actions": [
                    "Re-run the same call — completed steps are skipped "
                    "(idempotent) — or repair the first failed operation "
                    "(data['failed_operations'][0]) with the structural tools.",
                    *drift_action,
                ],
                "state_changed": outcome["state_changed"],
            },
        )

    verification = None
    next_actions: list[str] = []
    if verify:
        verification = await _run_verification(
            checks if checks is not None else _default_checks(project_root),
            project_root,
        )
        next_actions.extend(_verification_next_actions(verification))
    if not migrate:
        next_actions.append(
            "Migrations were skipped (migrate=False) — create and apply them "
            "to make the schema changes live."
        )
    next_actions.extend(extra_next_actions or [])
    next_actions.extend(drift_action)

    message = f"{summary} — applied"
    if verification is not None:
        if verification["passed"]:
            message += " and verified"
        else:
            # Loud, not soft: the writes landed but the acceptance gate did not
            # pass, and "verification found issues" reads as a footnote next to
            # a success. Failing the call instead would collide with
            # success=False meaning "incomplete — re-run to resume", which would
            # send an agent looping over an already-applied build.
            failed = sorted(
                name for name, entry in verification["checks"].items() if not entry["ok"]
            )
            message += f"; BUILT BUT VERIFICATION FAILED: {', '.join(failed)}"
    return AgentResult(
        success=True,
        message=message,
        data={
            "summary": summary,
            "changes": outcome["changes"],
            "steps": outcome["steps"],
            "affected": _affected(plan),
            "verification": verification,
            "verified": verification["passed"] if verification is not None else None,
            **({"drift": drift} if drift else {}),
            "warnings": warnings,
            "next_actions": next_actions,
            "state_changed": outcome["state_changed"],
        },
    )


@agent_function
async def plan_feature(
    spec: dict,
    tests: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Validate a FeatureSpec and return the execution plan — writes NOTHING.

    The read-only half of the plan → apply workflow: the spec is validated
    against the current project (every problem reported at once, with
    suggestions), compiled into an ordered operation list, and returned for
    review.  Pass the returned ``data`` object to :func:`apply_plan` to
    execute it, or call :func:`build_feature` with the same spec to compile
    and execute in one call — both run the identical compiler and executor.

    Args:
        spec: The FeatureSpec dict. Shape::

            {
              "name": "blog",                  # feature name (identifier)
              "app": "blog",                   # optional, defaults to name
              "entities": [
                {
                  "name": "Post",
                  "timestamps": true,          # created_at/updated_at (default)
                  "ordering": ["-created_at"], # optional model ordering
                  "fields": [
                    {"name": "title", "type": "string", "max_length": 200},
                    {"name": "status", "type": "enum",
                     "values": ["draft", "published"], "default": "draft"},
                    {"name": "author", "type": "relation", "target": "User",
                     "cardinality": "many-to-one", "required": false}
                  ],
                  "constraints": [
                    {"type": "unique", "fields": ["title", "author"]}
                  ],
                  "workflow": {                # optional state machine
                    "field": "status",         # default "status"
                    "states": ["draft", "submitted", "approved"],
                    "initial": "draft",        # default states[0]
                    "transitions": [
                      {"name": "submit", "from": "draft", "to": "submitted",
                       "actor": "authenticated"},
                      {"name": "approve", "from": ["submitted"],
                       "to": "approved", "permission": "IsAdminUser"}
                    ]
                  }
                }
              ],
              "api": {                         # defaults for all entities;
                "operations": ["list", "retrieve", "create",
                                "update", "delete"],
                "authentication": "read_only_public"  # or "required"/"public"
              }
            }

            Field types: any supported field alias (``string``, ``text``,
            ``int``, ``decimal``, ``bool``, ``date``, ``datetime``, ``json``,
            ``uuid``, ``email``, …) plus ``enum`` (choices) and ``relation``
            (cardinality ``many-to-one`` / ``one-to-one`` / ``many-to-many``;
            targets: another entity in the spec, an existing model name, a
            dotted ``app.Model``, or ``"self"``). ``required: false`` maps to
            ``null=True, blank=True``. Each entity may carry its own ``api``
            override; ``"expose": false`` skips endpoint generation.
        tests: Include a ``generate_tests`` operation in the plan (default
            true): per-entity smoke tests written into the project's
            ``tests/`` directory (existing files are never overwritten).

            An entity ``workflow`` declares a status state machine: the
            status enum field is synthesized when not declared, and every
            transition becomes a ``POST /<prefix>/{id}/<name>/`` endpoint
            that returns 409 on an illegal from-state. ``actor`` (``anyone``
            / ``authenticated`` / ``owner`` / ``admin``) or an explicit
            ``permission`` class gates each transition; omitting both
            inherits the endpoint's permission. ``actor: "owner"`` requires
            an ``owner`` (or ``user``) relation field on the entity.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        plan_version (int): plan format version (pass the whole object to
            apply_plan unchanged).
        summary (str): one-line description of what the plan does.
        feature (dict): ``{"name", "app"}``.
        operations (list[dict]): the ordered operations, each ``{"op", ...}``.
        risk (dict): ``{"level": "low"|"medium"|"high", "destructive",
            "database_changes"}`` — ``high`` when the plan drops a whole entity.
        drift (dict): present only when the spec diverges destructively from
            what is on disk — ``{"entries", "suggested_changes"}``; the plan
            itself never contains the destructive operations.
        warnings (list[str]): non-fatal findings (existing models, reconciled
            fields, auth assumptions, approximated operations, cycles).
        state_changed (bool): always ``False`` — nothing was written.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        preconditions (list[dict]): what must already be true for the plan
            to apply cleanly; carried through by ``apply_plan``.

    Notes:
        - Fails with the first problem's ``error_code`` and ALL problems under
          ``data["problems"]`` (each ``{"path", "code", "message",
          "suggestions"?}``) when the spec is invalid.
        - Entities are ordered so relation targets are created first; cycles
          fall back to declaration order with a warning (string references
          resolve lazily).
    """
    root = require_project_root(project_root)
    existing_models, existing_apps = await _project_inventory(root)
    plan = compile_feature_spec(spec, existing_models, existing_apps, tests=tests)
    return AgentResult(
        success=True,
        message=(
            f"{plan['summary']} — {len(plan['operations'])} operation(s); "
            "nothing written yet. Review, then run apply_plan with this plan "
            "(or build_feature with the same spec)."
        ),
        data={**plan, "affected": _affected(plan), "state_changed": False},
    )


@agent_function
async def build_feature(
    spec: dict,
    migrate: bool = True,
    verify: bool = True,
    tests: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Build a complete feature from a FeatureSpec — compile, apply, verify.

    One call scaffolds everything a bounded feature needs: the app (if
    missing), models (relation targets first), serializers, endpoints,
    routes, and migrations — then verifies the result.  This is the primary
    way to add backend capability; reach for the per-object tools only when a
    change is too specific for a spec.

    Args:
        spec: The FeatureSpec dict — see :func:`plan_feature` for the full
            shape and field dialect.
        migrate: Create and apply migrations at the end (default true). Pass
            false to keep the DB untouched (the schema steps still run).
        verify: Run the verification chain (structure → migrations → OpenAPI,
            plus the generated tests once they exist) after applying
            (default true).
        tests: Generate per-entity smoke tests as part of the build (default
            true; existing test files are never overwritten). The generated
            suite makes verification's ``tests`` check meaningful.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): what was built.
        changes (dict): ``{"apps_created", "models_created",
            "endpoints_created", "fields_added", "fields_removed",
            "actions_created", "tests_created", "migrations_created",
            "migrations_applied"}`` — each a list.
        steps (list[str]): human-readable step log (skips included).
        verification (dict | None): ``{"passed": bool, "checks": {...}}`` per
            requested check (``None`` when ``verify=false``).
        verified (bool | None): shorthand for ``verification.passed``
            (``None`` when ``verify=false``) — the acceptance gate.
        drift (dict): present only when the spec would DESTROY something —
            ``{"entries": [...], "suggested_changes": [...]}``. Never applied;
            ``suggested_changes`` is a ready-to-send ``change_feature`` payload.
        warnings (list[str]): compiler warnings (existing models skipped,
            fields reconciled, auth assumptions, …).
        next_actions (list[str]): concrete follow-ups (empty when everything
            passed).
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        errors (list[str]): step failures that did not stop the run.

    Notes:
        - Idempotent AND convergent: re-running with the same spec completes
          missing steps and skips existing ones; re-running with an EXTENDED
          spec also adds the fields an existing entity is missing (create steps
          are skip-idempotent, so without this the addition would silently do
          nothing).
        - Additive only. Fields on disk the spec no longer mentions, and fields
          whose type changed, are reported under ``data["drift"]`` and never
          applied — send ``drift.suggested_changes`` to ``change_feature`` when
          the removal is intended.
        - Partial failure returns ``success=False`` with
          ``error_code="partial_failure"`` plus ``data["steps_completed"]``,
          ``data["failed_operations"]`` (index + op + error code),
          ``completed_count`` / ``total_count`` and ``remaining_operations`` —
          fix the cause and re-run the same call.
        - A failed verification does NOT fail the call (``success=False`` means
          "incomplete, re-run to resume"): check ``verified`` — the message says
          ``BUILT BUT VERIFICATION FAILED`` — then ``verification.checks`` and
          ``next_actions``.
    """
    root = require_project_root(project_root)
    existing_models, existing_apps = await _project_inventory(root)
    plan = compile_feature_spec(spec, existing_models, existing_apps, tests=tests)
    name = plan["feature"]["name"]
    label = f"Feature '{name}'"
    return await _apply_and_report(
        plan, root, migrate, verify, label, feature=name, spec=spec
    )


@agent_function
async def apply_plan(
    plan: dict,
    migrate: bool = True,
    verify: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Execute a plan produced by :func:`plan_feature` — apply, then verify.

    The write half of the stateless plan → apply workflow: pass the plan
    object (the ``data`` dict plan_feature returned) back unchanged.  Uses
    the same executor as :func:`build_feature`, so applying a reviewed plan
    and building directly from the spec produce identical results.

    Args:
        plan: The plan object from plan_feature (``plan_version``,
            ``operations``, …). Do not hand-craft plans; re-run plan_feature
            after changing the spec.
        migrate: Execute the plan's migration operations (default true).
        verify: Run the verification chain after applying (default true).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): the plan's summary.
        changes (dict): what was created — same shape as
            :func:`build_feature`.
        steps (list[str]): human-readable step log.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        warnings (list[str]): the plan's compiler warnings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        verified (bool | None): shorthand for ``verification.passed``
            (``None`` when ``verify=false``) — the acceptance gate. A
            failed verification does NOT flip ``success``.
        errors (list[str]): step failures that did not stop the run.

    Notes:
        - An unknown/malformed plan (bad version, unknown op, invalid op
          payload) fails with ``error_code="invalid_input"`` and a message
          telling you to re-run plan_feature.
        - The plan's ``risk`` is recomputed from its operations at apply time
          (a tampered echo is corrected with a warning), and its compile-time
          ``preconditions`` fingerprint is diffed against the current project
          — a stale plan applies with warnings, never a hard failure, so the
          "re-run to resume" recovery loop keeps working.
        - Idempotent like build_feature — re-applying a plan skips whatever
          already exists.
    """
    root = require_project_root(project_root)
    problem = validate_plan(plan)
    if problem:
        return fail(problem, code="invalid_input")

    extra_warnings: list[str] = []
    extra_actions: list[str] = []
    if plan.get("plan_version", PLAN_VERSION) < PLAN_VERSION:
        extra_warnings.append(
            "Plan was produced by an older planner (no staleness fingerprint) "
            "— re-run plan_feature for full checking."
        )
    actual_risk = _plan_risk(plan["operations"])
    if plan.get("risk") != actual_risk:
        extra_warnings.append(
            "plan.risk did not match its operations — recomputed: "
            f"level={actual_risk['level']}, destructive={actual_risk['destructive']}, "
            f"database_changes={actual_risk['database_changes']}."
        )
    existing_models, existing_apps = await _project_inventory(root)
    stale = staleness_warnings(plan, existing_models, existing_apps)
    if stale:
        extra_warnings.extend(stale)
        extra_actions.append(
            "Project state changed since this plan was compiled — re-run "
            "plan_feature if the skipped or failed steps surprise you."
        )
    if extra_warnings:
        plan = {
            **plan,
            "risk": actual_risk,
            "warnings": [*plan.get("warnings", []), *extra_warnings],
        }
    return await _apply_and_report(
        plan,
        root,
        migrate,
        verify,
        "Plan",
        extra_next_actions=extra_actions,
        feature=(plan.get("feature") or {}).get("name"),
    )


@agent_function
async def change_feature(
    changes: list[dict],
    app: str | None = None,
    migrate: bool = True,
    verify: bool = True,
    tests: bool = True,
    feature: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Apply semantic changes to existing features — fields, relations, entities.

    Accepts *domain changes*, not file patches: each change names an entity
    and what should happen to it.  The entity's app is resolved automatically
    from the project's models (pass ``app=`` only to disambiguate).

    Args:
        changes: List of change dicts. Operations::

            {"operation": "add_field", "entity": "Post",
             "field": {"name": "subtitle", "type": "string",
                        "required": false}}
            {"operation": "alter_field", "entity": "Post",
             "field": {"name": "subtitle", "type": "text"}}
            {"operation": "add_relation", "entity": "Post",
             "field": {"name": "category", "target": "Category",
                        "cardinality": "many-to-one"}}
            {"operation": "remove_field", "entity": "Post",
             "field_name": "subtitle"}
            {"operation": "add_entity",
             "entity": {"name": "Comment", "fields": [...]},
             "app": "blog"}
            {"operation": "remove_entity", "entity": "Comment"}
            {"operation": "set_permissions", "entity": "Post",
             "permissions": ["IsAdminUser"]}
            {"operation": "set_authentication", "entity": "Post",
             "authentication": "required"}
            {"operation": "add_workflow", "entity": "Order",
             "workflow": {"states": ["draft", "submitted"],
                           "transitions": [{"name": "submit",
                                            "from": "draft",
                                            "to": "submitted"}]}}
            {"operation": "add_transition", "entity": "Order",
             "field": "status",
             "transition": {"name": "cancel",
                             "from": ["draft", "submitted"],
                             "to": "cancelled"}}

            Fields use the same dialect as :func:`plan_feature` (``enum``,
            ``relation``, ``required``, …). ``add_entity`` scaffolds the
            model plus serializer/endpoint/route like build_feature;
            ``remove_entity`` unregisters the route and deletes the viewset,
            serializer, and model (destructive — the table and its rows go
            with the next migration). ``alter_field`` redefines a field in
            place (restate every option; the old definition is replaced, not
            merged) instead of the remove+add that would drop the column.
            ``set_permissions`` / ``set_authentication`` re-gate an existing
            endpoint. ``add_workflow`` adds the status field (when missing)
            plus one transition endpoint per transition (see the ``workflow``
            shape in :func:`plan_feature`); ``add_transition`` adds a single
            transition endpoint to an existing status field — its from/to
            states cannot be validated without a states list, so check the
            warning it emits.
        app: Target app override — required only when an entity name exists
            in several apps (or for add_entity without its own ``app`` key).
        migrate: Create and apply migrations at the end (default true).
            Migration steps are added only when a change actually alters the
            schema — a permissions-only change runs none.
        verify: Run the verification chain after applying (default true).
        tests: Generate smoke tests for entities added by ``add_entity``
            (default true), into their own per-entity file so an app's
            existing generated suite is never overwritten. Field-level changes
            never regenerate tests — the result says so in ``next_actions``.
        feature: Name the feature these changes belong to (see
            :func:`list_features`). Recommended: it defaults ``app`` to the
            feature's app, records anything the changes create as owned by that
            feature, and folds the changes back into its stored spec so
            :func:`activate_feature` can never restore a stale shape. Omitting
            it still applies the changes — they just are not attributed.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): what was changed.
        changes (dict): ``{"fields_added", "fields_removed",
            "models_created", "endpoints_created", "migrations_created",
            "migrations_applied", ...}``.
        steps (list[str]): human-readable step log.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        warnings (list[str]): compiler warnings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        verified (bool | None): shorthand for ``verification.passed``
            (``None`` when ``verify=false``) — the acceptance gate. A
            failed verification does NOT flip ``success``.
        errors (list[str]): step failures that did not stop the run.

    Notes:
        - Invalid changes fail up front with every problem listed under
          ``data["problems"]`` — nothing is written when compilation fails.
        - Removing a field that is already gone counts as a skip, so re-runs
          stay idempotent.
        - An unknown ``feature`` fails with ``error_code="feature_not_found"``
          and the known names as suggestions; an archived one fails with
          ``feature_archived`` (activate it first — changing code that is not
          in the tree would silently do nothing).
    """
    root = require_project_root(project_root)
    entry: dict | None = None
    if feature is not None:
        entry, problem = await _resolve_feature(root, feature)
        if problem is not None:
            return problem
        assert entry is not None  # _resolve_feature returns one or the other
        if entry.get("status") == STATUS_ARCHIVED:
            return fail(
                f"Feature '{feature}' is archived — its code is not in the "
                "project tree, so these changes would apply to nothing.",
                code="feature_archived",
                suggestions=[f"activate_feature('{feature}')"],
            )
        app = app or entry.get("app")
    existing_models, existing_apps = await _project_inventory(root)
    operations, warnings = compile_changes(
        changes, existing_models, existing_apps, app, tests=tests
    )
    # Migrations only when something actually changed the schema: a
    # permissions-only change used to run makemigrations + migrate anyway,
    # which is slow, noisy, and can surface unrelated pending migrations as if
    # this call had caused them.
    if any(op["op"] in _DB_CHANGING_OPS for op in operations):
        operations = [
            *operations,
            {"op": "make_migrations", "name": None},
            {"op": "run_migrations"},
        ]
    else:
        warnings.append(
            "No schema-changing operations — migration steps were omitted."
        )
    extra_actions: list[str] = []
    if any(
        op["op"] in ("add_field", "alter_field", "remove_field", "delete_model")
        for op in operations
    ) and (root / "tests").is_dir():
        extra_actions.append(
            "Generated tests were not updated for these changes — edit the "
            "files under tests/, or delete the generated ones and re-run "
            "build_feature(tests=True) (generate_tests never overwrites)."
        )
    plan = {
        "plan_version": PLAN_VERSION,
        "summary": f"Apply {len(changes)} change(s)",
        "operations": operations,
        "risk": _plan_risk(operations),
        "preconditions": plan_preconditions(operations, existing_models, existing_apps),
        "warnings": warnings,
    }
    return await _apply_and_report(
        plan,
        root,
        migrate,
        verify,
        "Changes",
        extra_next_actions=extra_actions,
        feature=feature,
        spec=(
            merge_changes_into_spec(entry.get("spec"), changes)
            if entry is not None
            else None
        ),
    )


@agent_function
async def edit_feature(
    changes: list[dict],
    app: str | None = None,
    migrate: bool = True,
    verify: bool = True,
    tests: bool = True,
    feature: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Edit an existing feature — alias of :func:`change_feature`.

    Provided so the ``edit_*`` verb works alongside ``delete_feature`` /
    ``activate_feature`` / ``deactivate_feature``. Identical behavior, arguments,
    and return shape to ``change_feature`` (the canonical name).

    Returns data (on success):
        summary (str): what was changed.
        changes (dict): what was created, altered, and removed.
        steps (list[str]): human-readable step log.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        verified (bool | None): shorthand for ``verification.passed``.
        warnings (list[str]): compiler warnings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}``.
        errors (list[str]): step failures that did not stop the run.
    """
    return await change_feature(
        changes,
        app=app,
        migrate=migrate,
        verify=verify,
        tests=tests,
        feature=feature,
        project_id=project_root,
    )


async def _feature_index(root: Path) -> dict[str, dict]:
    """Every feature this project has — recorded ones first, inferred as fallback.

    A project that predates the manifest (or one whose manifest was deleted)
    still has features; they are just not written down. Merging the inferred
    view in means the lifecycle tools work on those projects too, at the cost of
    a coarser boundary (one feature per app), which each entry admits by
    carrying ``inferred: true``.
    """
    manifest = await asyncio.to_thread(load_manifest, root)
    features = {name: dict(entry) for name, entry in manifest["features"].items()}

    from zeeb_agents.project import describe_project

    snapshot_res = await describe_project(project_id=root)
    snapshot = snapshot_res.data or {} if snapshot_res.success else {}
    # An app a recorded feature already lives in must not also surface as an
    # inferred feature: the app would appear twice, once under the feature's
    # own name and once under the app's, and the second one would claim
    # ownership of artifacts the first one owns.
    recorded_apps = {entry.get("app") for entry in features.values()}
    for inferred in infer_features(snapshot):
        if inferred["app"] in recorded_apps:
            continue
        features.setdefault(inferred["name"], inferred)
    return features


async def _resolve_feature(root: Path, name: str) -> tuple[dict | None, AgentResult | None]:
    """Look up one feature by name, returning ``(entry, None)`` or ``(None, failure)``."""
    features = await _feature_index(root)
    entry = features.get(name)
    if entry is not None:
        return entry, None
    known = sorted(features)
    return None, fail(
        f"No feature named '{name}'.",
        code="feature_not_found",
        suggestions=close_matches(name, known) or known,
        known_features=known,
    )


def _feature_summary(entry: dict) -> dict:
    """The list-facing view of a feature: identity, status, and artifact counts."""
    artifacts = entry.get("artifacts") or {}
    return {
        "name": entry.get("name"),
        "app": entry.get("app"),
        "status": entry.get("status", STATUS_ACTIVE),
        "inferred": bool(entry.get("inferred")),
        "entities": [ref.split(".", 1)[-1] for ref in artifacts.get("models") or []],
        "endpoints": [
            route.get("prefix") or route.get("model") for route in artifacts.get("routes") or []
        ],
        "functions": [
            {"name": fn.get("name"), "kind": fn.get("kind")}
            for fn in artifacts.get("functions") or []
        ],
        "artifact_counts": {kind: len(value or []) for kind, value in artifacts.items()},
        "has_spec": bool(entry.get("spec")),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }


@agent_function
async def list_features(
    status: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """List the features this project is made of — the map for every other change.

    A feature is a bounded slice of capability built from one FeatureSpec: its
    entities, endpoints, functions, and tests.  This is what
    :func:`change_feature`, :func:`deactivate_feature`, :func:`activate_feature`
    and :func:`delete_feature` address by name, so call it first when you did
    not build the feature yourself in this session.

    Projects built before features were recorded still list: their features are
    reconstructed from what is on disk, one per app, and marked ``inferred``.
    The lifecycle tools work on those too — the boundary is just coarser, since
    nothing recorded which app-mate owned what.

    Args:
        status: Filter by lifecycle status — ``"active"`` (in the tree and
            served) or ``"archived"`` (code parked in ``.zeeb/archive/``,
            tables intact). Defaults to all.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        features (list[dict]): one per feature, each ``{"name", "app",
            "status", "inferred", "entities", "endpoints", "functions",
            "artifact_counts", "has_spec", "created_at", "updated_at"}``.
        count (int): number of features returned.
        active_count (int): how many are active, before the ``status`` filter.
        archived_count (int): how many are archived, before the filter.
        inferred_count (int): how many were reconstructed rather than recorded.
        manifest_path (str): project-relative path of the feature manifest.

    Notes:
        - Fails with ``invalid_input`` when ``status`` is not a known status.
        - ``inferred`` features carry no stored spec, so ``has_spec`` is false
          and :func:`activate_feature` cannot rebuild them from one — their
          archive fragments are the only copy.
    """
    root = require_project_root(project_root)
    if status is not None and status not in (STATUS_ACTIVE, STATUS_ARCHIVED):
        return fail(
            f"Unknown status '{status}'.",
            code="invalid_input",
            suggestions=[STATUS_ACTIVE, STATUS_ARCHIVED],
        )

    features = await _feature_index(root)
    summaries = [_feature_summary(entry) for entry in features.values()]
    summaries.sort(key=lambda f: f["name"] or "")
    active = sum(1 for f in summaries if f["status"] == STATUS_ACTIVE)
    archived = sum(1 for f in summaries if f["status"] == STATUS_ARCHIVED)
    inferred = sum(1 for f in summaries if f["inferred"])
    if status is not None:
        summaries = [f for f in summaries if f["status"] == status]

    return AgentResult(
        success=True,
        message=(
            f"{len(summaries)} feature(s): {active} active, {archived} archived"
            + (f" ({inferred} reconstructed from disk)" if inferred else "")
            + "."
        ),
        data={
            "features": summaries,
            "count": len(summaries),
            "active_count": active,
            "archived_count": archived,
            "inferred_count": inferred,
            "manifest_path": f"{STATE_DIR}/{MANIFEST_NAME}",
        },
    )


@agent_function
async def deactivate_feature(
    feature: str,
    verify: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Take a feature off the API without touching its data — reversible.

    The feature's API layer (viewsets, serializers, route handlers, hooks,
    tasks, permission classes, generated tests) is lifted out of the project
    and parked verbatim under ``.zeeb/archive/<feature>/``; its routes are
    unregistered.  Its endpoints stop existing.

    **Its models and its tables do not move.**  ``models.py`` is left exactly as
    it is, so the schema is unchanged, no migration is generated, and not one
    row is at risk.  :func:`activate_feature` puts everything back as it was,
    hand edits included.  Use this to retire capability you may want again, or
    to take a broken feature out of the way; use :func:`delete_feature` when the
    data should go too.

    Because features may share an app, only the artifacts recorded as belonging
    to *feature* are removed — an app-mate keeps its endpoints.

    Args:
        feature: The feature name, as reported by :func:`list_features`.
        verify: Re-run the verification chain afterwards (default true) so you
            see immediately whether the remaining API still holds together.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        feature (str): the feature name.
        status (str): ``"archived"``.
        archived (list[str]): the class/function names taken out of the tree.
        routes_unregistered (list[str]): route prefixes that no longer resolve.
        tests_archived (list[str]): generated test files moved aside.
        models_retained (list[str]): models left registered — the reason no
            migration is needed and no data is lost.
        archive_path (str): project-relative directory holding the fragments.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        verified (bool | None): shorthand for ``verification.passed``.
        warnings (list[str]): non-fatal findings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually moved.
        affected (dict): ``{"apps", "entities", "files"}``.

    Notes:
        - Idempotent: deactivating an already-archived feature fails with
          ``error_code="feature_archived"`` rather than double-archiving.
        - No migration runs and none is needed. If you also want the tables
          gone, that is :func:`delete_feature` — deliberately a separate,
          confirmed call.
        - An ``inferred`` feature (one reconstructed from disk rather than
          recorded) can be archived, but its ownership is app-wide, so
          everything in that app goes with it.
    """
    root = require_project_root(project_root)
    entry, problem = await _resolve_feature(root, feature)
    if problem is not None:
        return problem
    assert entry is not None
    if entry.get("status") == STATUS_ARCHIVED:
        return fail(
            f"Feature '{feature}' is already archived.",
            code="feature_archived",
            suggestions=[f"activate_feature('{feature}')"],
        )

    artifacts = entry.get("artifacts") or {}
    warnings: list[str] = []
    if entry.get("inferred"):
        warnings.append(
            f"'{feature}' was reconstructed from disk, not recorded, so its "
            f"ownership is the whole '{entry.get('app')}' app — everything in "
            "it is being archived."
        )

    from zeeb_agents.viewsets import unregister_route

    unregistered: list[str] = []
    for route in artifacts.get("routes") or []:
        app, model = route.get("app"), route.get("model")
        if not (app and model):
            continue
        result = await unregister_route(app, model, project_id=root)
        if result.success:
            unregistered.append(route.get("prefix") or model)
        else:
            warnings.append(f"Route for {app}.{model}: {result.message}")

    record = await asyncio.to_thread(archive_artifacts, root, feature, artifacts)
    await asyncio.to_thread(
        set_status, root, feature, STATUS_ARCHIVED, artifacts=artifacts, app=entry.get("app")
    )

    archived = [fragment["name"] for fragment in record["fragments"]]
    tests_archived = [entry_["path"] for entry_ in record["tests"]]
    retained = record["models_retained"]
    state_changed = bool(archived or tests_archived or unregistered)

    verification = None
    next_actions: list[str] = []
    if verify:
        verification = await _run_verification(_default_checks(root), root)
        next_actions.extend(_verification_next_actions(verification))
    next_actions.append(
        f"activate_feature('{feature}') restores it exactly as it was."
    )
    if retained:
        next_actions.append(
            f"{len(retained)} model(s) and their tables are still in place — "
            f"delete_feature('{feature}', confirm=True) is what removes them."
        )

    return AgentResult(
        success=True,
        message=(
            f"Feature '{feature}' archived: {len(archived)} artifact(s) and "
            f"{len(unregistered)} route(s) removed from the API; "
            f"{len(retained)} model(s) and all data left intact."
        ),
        data={
            "feature": feature,
            "status": STATUS_ARCHIVED,
            "archived": archived,
            "routes_unregistered": unregistered,
            "tests_archived": tests_archived,
            "models_retained": retained,
            "archive_path": f"{STATE_DIR}/archive/{feature}",
            "verification": verification,
            "verified": verification["passed"] if verification is not None else None,
            "warnings": warnings,
            "next_actions": next_actions,
            "state_changed": state_changed,
            "affected": _feature_scope(entry),
        },
    )


@agent_function
async def activate_feature(
    feature: str,
    migrate: bool = True,
    verify: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Put an archived feature back — code, routes, and tests, exactly as they were.

    The inverse of :func:`deactivate_feature`.  Archived fragments are copied
    back into their files with the imports they need, and every route the
    feature owned is registered again.  Because the fragments are the original
    source, edits made to the feature before it was archived come back with it.

    If a fragment has gone missing from the archive, the feature is rebuilt from
    its stored spec instead — the same compiler ``build_feature`` uses — and the
    result says which artifacts came from where.

    Args:
        feature: The feature name, as reported by :func:`list_features`.
        migrate: Create and apply migrations if the rebuild path adds anything
            to the schema (default true). The normal restore path changes no
            models, so it runs no migration whatever this is set to.
        verify: Run the verification chain afterwards (default true).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        feature (str): the feature name.
        status (str): ``"active"``.
        restored (list[str]): class/function names put back from the archive.
        routes_registered (list[str]): route prefixes that resolve again.
        tests_restored (list[str]): generated test files put back.
        rebuilt (bool): whether the stored spec had to be recompiled because
            fragments were missing.
        missing (list[str]): archived artifacts that were not on disk.
        summary (str): present only on the rebuild path — what was recompiled.
        changes (dict): present only on the rebuild path — what the rebuild
            created, same shape as :func:`build_feature`.
        steps (list[str]): present only on the rebuild path — its step log.
        errors (list[str]): present only on the rebuild path — step failures.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        verified (bool | None): shorthand for ``verification.passed``.
        warnings (list[str]): non-fatal findings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually restored.
        affected (dict): ``{"apps", "entities", "files"}``.

    Notes:
        - Fails with ``feature_active`` when the feature is not archived, and
          with ``archive_missing`` when there is neither an archive nor a
          stored spec to rebuild from.
        - Idempotent: an artifact already present in its file is left alone, so
          re-running after a partial restore finishes the job.
    """
    root = require_project_root(project_root)
    entry, problem = await _resolve_feature(root, feature)
    if problem is not None:
        return problem
    assert entry is not None
    if entry.get("status") != STATUS_ARCHIVED:
        return fail(
            f"Feature '{feature}' is already active.",
            code="feature_active",
            suggestions=["list_features()", f"deactivate_feature('{feature}')"],
        )

    record = await asyncio.to_thread(read_record, root, feature)
    spec = entry.get("spec")
    if record is None and not spec:
        return fail(
            f"Nothing to restore for '{feature}': no archive under "
            f"{STATE_DIR}/archive/{feature} and no stored spec.",
            code="archive_missing",
            suggestions=["build_feature(spec) to recreate it from scratch"],
        )

    warnings: list[str] = []
    outcome = {"restored": [], "tests_restored": [], "skipped": [], "missing": [], "routes": []}
    if record is not None:
        outcome = await asyncio.to_thread(restore_artifacts, root, feature, record)
        if outcome["skipped"]:
            warnings.append(
                f"Already present, left untouched: {', '.join(outcome['skipped'])}."
            )

    from zeeb_agents.viewsets import register_route

    registered: list[str] = []
    for route in outcome["routes"] or (entry.get("artifacts") or {}).get("routes") or []:
        app, model = route.get("app"), route.get("model")
        if not (app and model):
            continue
        result = await register_route(
            app, model, url_prefix=route.get("prefix"), if_exists="skip", project_id=root
        )
        if result.success:
            registered.append(route.get("prefix") or model)
        else:
            warnings.append(f"Route for {app}.{model}: {result.message}")

    # Fragments the archive could not supply are rebuilt from the spec that
    # produced them, so a damaged archive degrades to a regeneration rather
    # than leaving the feature half-restored.
    rebuilt = False
    if outcome["missing"] and spec:
        existing_models, existing_apps = await _project_inventory(root)
        plan = compile_feature_spec(spec, existing_models, existing_apps, tests=True)
        rebuild = await _apply_and_report(
            plan, root, migrate, False, f"Feature '{feature}'", feature=feature, spec=spec
        )
        rebuilt = True
        warnings.append(
            f"Missing from the archive, rebuilt from the stored spec: "
            f"{', '.join(outcome['missing'])}."
        )
        if not rebuild.success:
            return rebuild
    elif outcome["missing"]:
        warnings.append(
            f"Missing from the archive and no stored spec to rebuild them: "
            f"{', '.join(outcome['missing'])}."
        )

    await asyncio.to_thread(set_status, root, feature, STATUS_ACTIVE)
    await asyncio.to_thread(purge_archive, root, feature)

    verification = None
    next_actions: list[str] = []
    if verify:
        verification = await _run_verification(_default_checks(root), root)
        next_actions.extend(_verification_next_actions(verification))

    state_changed = bool(outcome["restored"] or outcome["tests_restored"] or registered)
    return AgentResult(
        success=True,
        message=(
            f"Feature '{feature}' restored: {len(outcome['restored'])} artifact(s) "
            f"and {len(registered)} route(s) back in the API."
            + (" Some artifacts were rebuilt from its spec." if rebuilt else "")
        ),
        data={
            "feature": feature,
            "status": STATUS_ACTIVE,
            "restored": outcome["restored"],
            "routes_registered": registered,
            "tests_restored": outcome["tests_restored"],
            "rebuilt": rebuilt,
            "missing": outcome["missing"],
            "verification": verification,
            "verified": verification["passed"] if verification is not None else None,
            "warnings": warnings,
            "next_actions": next_actions,
            "state_changed": state_changed,
            "affected": _feature_scope(entry),
        },
    )


@agent_function
async def delete_feature(
    feature: str,
    confirm: bool = False,
    migrate: bool = True,
    verify: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a feature and its data — the destructive one.

    Everything :func:`deactivate_feature` archives is removed, and then the
    feature's models go too: the classes are deleted and a migration drops their
    tables.  **The rows in those tables are gone.**  There is no archive left to
    restore from afterwards.

    If you want the endpoints gone but the data kept, that is
    :func:`deactivate_feature`, and it is reversible.

    Args:
        feature: The feature name, as reported by :func:`list_features`.
        confirm: Must be ``True``. Without it the call refuses and reports what
            *would* be destroyed, so the decision is always made against the
            actual scope rather than a guess.
        migrate: Create and apply the migration that drops the tables (default
            true). With ``false`` the models are removed from the code and the
            tables are left orphaned until the next migration.
        verify: Run the verification chain afterwards (default true).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        feature (str): the feature name.
        deleted (list[str]): artifacts removed from the tree.
        models_deleted (list[str]): models whose tables the migration drops.
        routes_unregistered (list[str]): route prefixes that no longer resolve.
        risk (dict): ``{"level": "high", "destructive": true,
            "database_changes": bool}``.
        migrations_created (list[str]): migration files written.
        verification (dict | None): ``{"passed", "checks"}`` or ``None``.
        verified (bool | None): shorthand for ``verification.passed``.
        warnings (list[str]): non-fatal findings.
        next_actions (list[str]): concrete follow-ups.
        state_changed (bool): whether anything was actually removed.
        affected (dict): ``{"apps", "entities", "files"}``.

    Returns data (when refused):
        confirm_required (bool): always ``True``.
        would_delete (dict): the exact scope — ``{"models", "endpoints",
            "functions", "tests"}`` — so the confirmation is informed.
        risk (dict): as above.

    Notes:
        - Refuses with ``error_code="invalid_input"`` unless ``confirm=True``.
        - The app directory itself is left in place: other features may live in
          it. An app with nothing left in it can be removed with ``delete_app``.
    """
    root = require_project_root(project_root)
    entry, problem = await _resolve_feature(root, feature)
    if problem is not None:
        return problem
    assert entry is not None

    artifacts = entry.get("artifacts") or {}
    models = list(artifacts.get("models") or [])
    risk = {"level": "high", "destructive": True, "database_changes": bool(models)}

    if not confirm:
        return fail(
            f"delete_feature('{feature}') destroys data and was not confirmed. "
            f"It would delete {len(models)} model(s) and drop their tables. "
            "Re-send with confirm=True, or use deactivate_feature to take it "
            "off the API and keep the data.",
            code="invalid_input",
            suggestions=[
                f"delete_feature('{feature}', confirm=True)",
                f"deactivate_feature('{feature}')",
            ],
            confirm_required=True,
            would_delete={
                "models": models,
                "endpoints": [
                    route.get("prefix") or route.get("model")
                    for route in artifacts.get("routes") or []
                ],
                "functions": [fn.get("name") for fn in artifacts.get("functions") or []],
                "tests": list(artifacts.get("tests") or []),
            },
            risk=risk,
        )

    warnings: list[str] = []
    from zeeb_agents.viewsets import unregister_route

    unregistered: list[str] = []
    for route in artifacts.get("routes") or []:
        app, model = route.get("app"), route.get("model")
        if not (app and model):
            continue
        result = await unregister_route(app, model, project_id=root)
        if result.success:
            unregistered.append(route.get("prefix") or model)
        else:
            warnings.append(f"Route for {app}.{model}: {result.message}")

    record = await asyncio.to_thread(archive_artifacts, root, feature, artifacts)
    deleted = [fragment["name"] for fragment in record["fragments"]]

    operations = [
        {"op": "delete_model", "app": app, "model": model}
        for app, model in (split_ref(ref) for ref in models)
        if app and model
    ]
    if operations and migrate:
        operations = [
            *operations,
            {"op": "make_migrations", "name": None},
            {"op": "run_migrations"},
        ]
    outcome = await execute_plan({"operations": operations}, root, migrate=migrate)
    warnings.extend(outcome.get("warnings", []))
    if outcome["errors"]:
        warnings.extend(outcome["errors"])

    # The archive was only a staging area for the removal: nothing may restore
    # a feature whose tables have been dropped.
    await asyncio.to_thread(purge_archive, root, feature)
    await asyncio.to_thread(forget_feature, root, feature)

    verification = None
    next_actions: list[str] = []
    if verify:
        verification = await _run_verification(_default_checks(root), root)
        next_actions.extend(_verification_next_actions(verification))
    if not migrate and models:
        next_actions.append(
            "Migrations were skipped (migrate=False) — the tables still exist "
            "until you run make_migrations() and run_migrations()."
        )

    return AgentResult(
        success=True,
        message=(
            f"Feature '{feature}' deleted: {len(deleted)} artifact(s), "
            f"{len(unregistered)} route(s), and {len(models)} model(s) removed."
        ),
        data={
            "feature": feature,
            "deleted": deleted,
            "models_deleted": models,
            "routes_unregistered": unregistered,
            "risk": risk,
            "migrations_created": outcome["changes"]["migrations_created"],
            "verification": verification,
            "verified": verification["passed"] if verification is not None else None,
            "warnings": warnings,
            "next_actions": next_actions,
            "state_changed": bool(deleted or unregistered or outcome["state_changed"]),
            "affected": _feature_scope(entry),
        },
    )


def _feature_scope(entry: dict) -> dict:
    """``affected``-shaped scope for a feature, from its ownership record."""
    artifacts = entry.get("artifacts") or {}
    apps = {entry["app"]} if entry.get("app") else set()
    entities = set()
    files = set()
    for ref in artifacts.get("models") or []:
        app, model = split_ref(ref)
        if app and model:
            apps.add(app)
            entities.add(f"{app}.{model}")
    for app in apps:
        files.update(
            {f"apps/{app}/views.py", f"apps/{app}/serializers.py", f"apps/{app}/urls.py"}
        )
    files.update(artifacts.get("tests") or [])
    return {"apps": sorted(apps), "entities": sorted(entities), "files": sorted(files)}


async def _user_model_ops(
    user_model: dict | None,
    root: Path,
    warnings: list[str],
) -> list[dict]:
    """Operations to create a custom user model — or warnings when it's too late.

    A custom user model must precede the first applied migration; when the
    project already migrated (or ``AUTH_USER_MODEL`` is already set), the
    creation is skipped with an explanatory warning instead of failing.
    """
    from zeeb_agents.migrations import get_migration_status

    um = user_model or {}
    um_app = um.get("app", "accounts")
    if not isinstance(um_app, str) or not um_app.isidentifier():
        raise AgentError(
            f"user_model.app must be an identifier, got {um_app!r}",
            code="invalid_input",
        )
    try:
        settings = await asyncio.to_thread(load_project_settings, root)
    except Exception:
        settings = {}
    if settings.get("AUTH_USER_MODEL"):
        warnings.append(
            f"AUTH_USER_MODEL is already set ({settings['AUTH_USER_MODEL']}) — "
            "keeping the existing user model."
        )
        return []
    migr = await get_migration_status(project_id=root)
    applied = (migr.data or {}).get("applied", []) if migr.success else []
    if applied:
        warnings.append(
            "Migrations were already applied — keeping the default user model "
            "(a custom user model must precede the first migration)."
        )
        return []
    return [
        {"op": "create_app", "app": um_app},
        {
            "op": "create_user_model",
            "app": um_app,
            "model_name": um.get("model_name", "User"),
            "extra_fields": um.get("extra_fields"),
        },
    ]


@agent_function
async def bootstrap_project(
    auth: bool = True,
    registration: bool = True,
    health_endpoint: bool = True,
    user_model: dict | None = None,
    migrate: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Bootstrap the bound project into a production-ready baseline.

    One call to run right after project creation: a custom user model, JWT
    authentication (login/refresh/logout/me and optional register), health
    endpoints, and the initial migration — verified at the end.  Idempotent:
    on an already-bootstrapped project every step reports a skip.

    Args:
        auth: Wire JWT authentication (default true) — includes creating a
            custom user model first (skipped with a warning when migrations
            were already applied, since a user model must precede them).
        registration: Expose the register endpoint (default true; only
            meaningful with ``auth=true``).
        health_endpoint: Scaffold ``/health`` and ``/ready`` probes
            (default true).
        user_model: Optional user-model config — ``{"app": "accounts",
            "model_name": "User", "extra_fields": [...]}`` (field dialect as
            in :func:`plan_feature`). Defaults to a plain ``accounts.User``.
        migrate: Create and apply the initial migration (default true).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): what was set up.
        changes (dict): apps/models/migrations created — same shape as
            :func:`build_feature`.
        steps (list[str]): human-readable step log.
        verification (dict | None): structure + migrations checks.
        warnings (list[str]): skipped steps and why.
        next_actions (list[str]): follow-ups (JWT secret, first feature).
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        verified (bool | None): shorthand for ``verification.passed``
            (``None`` when ``verify=false``) — the acceptance gate. A
            failed verification does NOT flip ``success``.
        errors (list[str]): step failures that did not stop the run.

    Notes:
        - Does NOT create a project — the connection is bound to one existing
          project; bootstrap prepares that project.
        - Re-runnable as a repair tool: it re-wires missing auth middleware
          and completes missing steps.
    """
    root = require_project_root(project_root)
    warnings: list[str] = []
    operations: list[dict] = []
    extra_actions: list[str] = []

    if auth:
        operations.extend(await _user_model_ops(user_model, root, warnings))
        operations.append({"op": "setup_auth", "enable_registration": registration})
        extra_actions.append(
            "Set a strong JWT_SECRET_KEY (set_env) before going to production."
        )
    if health_endpoint:
        operations.append({"op": "create_health_endpoint"})
    operations.append({"op": "make_migrations", "name": "bootstrap"})
    operations.append({"op": "run_migrations"})

    plan = {
        "plan_version": PLAN_VERSION,
        "summary": "Bootstrap project",
        "operations": operations,
        "risk": _plan_risk(operations),
        "warnings": warnings,
    }
    extra_actions.append(
        "Add your first resource with build_feature (see plan_feature for the "
        "spec shape)."
    )
    return await _apply_and_report(
        plan,
        root,
        migrate,
        verify=True,
        label="Bootstrap",
        checks=("structure", "migrations"),
        extra_next_actions=extra_actions,
    )


@agent_function
async def configure_auth(
    providers: list[dict] | None = None,
    registration: bool = True,
    user_model: dict | None = None,
    migrate: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Configure authentication as one coherent setup — password and/or OAuth.

    Wires everything JWT auth needs (user model, auth router, middleware) and
    registers the requested OAuth login providers, then runs migrations and
    verification.  Idempotent — safe to re-run to repair missing wiring.

    Args:
        providers: Auth providers, default ``[{"type": "password"}]``::

            [
              {"type": "password"},
              {"type": "oauth", "provider": "google"},
              {"type": "oauth", "provider": "github",
               "scopes": ["read:user"]}
            ]

            OAuth providers: ``azure``, ``github``, ``google``. Password auth
            is always wired (OAuth logins also need the JWT middleware).
        registration: Expose the register endpoint (default true).
        user_model: Optional custom user-model config (see
            :func:`bootstrap_project`); skipped with a warning when the
            project already migrated.
        migrate: Create and apply migrations at the end (default true).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): what was configured.
        changes (dict): same shape as :func:`build_feature`.
        steps (list[str]): human-readable step log.
        verification (dict | None): structure + migrations checks.
        warnings (list[str]): skipped steps and why.
        next_actions (list[str]): the credential env vars each OAuth provider
            still needs, plus the JWT secret reminder.
        state_changed (bool): whether anything was actually written/applied.
        affected (dict): ``{"apps", "entities", "files"}`` — the scope this
            call was touching, for the caller to invalidate or re-read.
        verified (bool | None): shorthand for ``verification.passed``
            (``None`` when ``verify=false``) — the acceptance gate. A
            failed verification does NOT flip ``success``.
        errors (list[str]): step failures that did not stop the run.

    Notes:
        - Unknown provider types/names fail up front with
          ``error_code="invalid_input"`` and suggestions — nothing is written.
    """
    from zeeb_agents.auth_scaffold import KNOWN_OAUTH_PROVIDERS

    root = require_project_root(project_root)
    providers = providers or [{"type": "password"}]

    oauth_providers: list[dict] = []
    for i, provider in enumerate(providers):
        if not isinstance(provider, dict):
            return fail(
                f"providers[{i}] must be a dict like "
                '{"type": "password"} or {"type": "oauth", "provider": "google"}',
                code="invalid_input",
            )
        ptype = provider.get("type")
        if ptype == "password":
            continue
        if ptype != "oauth":
            return fail(
                f"providers[{i}].type must be 'password' or 'oauth', got {ptype!r}",
                code="invalid_input",
                suggestions=close_matches(str(ptype), ["password", "oauth"]),
            )
        name = str(provider.get("provider", "")).lower()
        if name not in KNOWN_OAUTH_PROVIDERS:
            return fail(
                f"providers[{i}].provider {provider.get('provider')!r} is not a "
                f"known OAuth preset. Valid: {', '.join(KNOWN_OAUTH_PROVIDERS)}",
                code="invalid_input",
                suggestions=close_matches(name, list(KNOWN_OAUTH_PROVIDERS)),
            )
        oauth_providers.append({**provider, "provider": name})

    warnings: list[str] = []
    operations: list[dict] = await _user_model_ops(user_model, root, warnings)
    operations.append({"op": "setup_auth", "enable_registration": registration})
    extra_actions = [
        "Set a strong JWT_SECRET_KEY (set_env) before going to production."
    ]
    for provider in oauth_providers:
        operations.append(
            {
                "op": "setup_oauth",
                "provider": provider["provider"],
                "scopes": provider.get("scopes"),
            }
        )
        upper = provider["provider"].upper()
        extra_actions.append(
            f"Set {upper}_CLIENT_ID and {upper}_CLIENT_SECRET (set_env) so the "
            f"'{provider['provider']}' login works."
        )
    operations.append({"op": "make_migrations", "name": "auth"})
    operations.append({"op": "run_migrations"})

    plan = {
        "plan_version": PLAN_VERSION,
        "summary": "Configure authentication",
        "operations": operations,
        "risk": _plan_risk(operations),
        "warnings": warnings,
    }
    return await _apply_and_report(
        plan,
        root,
        migrate,
        verify=True,
        label="Auth setup",
        checks=("structure", "migrations"),
        extra_next_actions=extra_actions,
    )


@agent_function
async def verify_project(
    checks: list[str] | None = None,
    port: int = 8000,
    project_root: Path | None = None,
) -> AgentResult:
    """Run the deterministic acceptance gate — the call to make before "done".

    Read-only.  Runs the requested checks and reports one overall verdict.
    A failing check does NOT make the call fail — the result is the verdict
    (``verification.passed``) plus what to do about it (``next_actions``).

    Args:
        checks: Which checks to run — any of ``"structure"`` (wiring
            consistency via the project snapshot), ``"migrations"`` (nothing
            pending), ``"openapi"`` (live contract reachable and containing
            every registered route), ``"tests"`` (project test suite),
            ``"runtime"`` (settings load, database reachable, tables present),
            ``"endpoints"`` (GET every registered collection against the
            running API — a 5xx fails; 401/403 counts as reachable), or
            ``"security"`` (production readiness: DEBUG off, real SECRET_KEY,
            non-SQLite database, Dockerfile/requirements present).
            Default: structure, migrations, openapi.
        port: Port the API listens on for the openapi and endpoints checks
            (default 8000).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): one-line verdict.
        verification (dict): ``{"passed": bool, "checks": {<name>:
            {"ok": bool, ...detail}}}``. The ``tests`` check also reports
            ``passed``/``failed``/``errors`` counts, the failing node ids
            (``failed_tests``), and ``no_tests``.
        verified (bool): shorthand for ``verification.passed`` — the verdict.
        warnings (list[str]): always empty (details live in the checks).
        next_actions (list[str]): concrete fixes for each failed check.
        state_changed (bool): always ``False``.

    Notes:
        - Unknown check names fail with ``error_code="invalid_input"`` and
          suggestions.
        - ``tests`` runs the project's own test suite — include it once the
          project has tests worth gating on. An empty suite FAILS the check:
          "no tests collected" is zero evidence, and a broken import collects
          zero tests while looking like a pass.
    """
    root = require_project_root(project_root)
    requested = list(checks) if checks else list(_default_checks(root))
    unknown = [c for c in requested if c not in _VALID_CHECKS]
    if unknown:
        return fail(
            f"Unknown check(s) {unknown!r}. Valid: {', '.join(_VALID_CHECKS)}",
            code="invalid_input",
            suggestions=close_matches(str(unknown[0]), list(_VALID_CHECKS)),
        )
    verification = await _run_verification(requested, root, port=port)
    failed = [name for name, entry in verification["checks"].items() if not entry["ok"]]
    summary = (
        f"Verification passed ({', '.join(requested)})"
        if verification["passed"]
        else f"Verification failed: {', '.join(failed)}"
    )
    return AgentResult(
        success=True,
        message=summary,
        data={
            "summary": summary,
            "verification": verification,
            "verified": verification["passed"],
            "warnings": [],
            "next_actions": _verification_next_actions(verification),
            "state_changed": False,
        },
    )


# Log lines that point at a schema/migration mismatch.
_SCHEMA_ERROR_MARKERS = (
    "no such table",
    "no such column",
    "does not exist",
    "undefinedtable",
    "undefinedcolumn",
)


@agent_function
async def diagnose_problem(
    symptom: str = "",
    endpoint: str = "",
    lines: int = 200,
    project_root: Path | None = None,
) -> AgentResult:
    """Diagnose a misbehaving project — one read-only call instead of five.

    Composes the project snapshot, migration state, system health, routes,
    and recent error logs into ordered findings, the most likely root cause,
    and (when one exists) the exact call that fixes it.

    Args:
        symptom: Free-text description of what is wrong (e.g. ``"POST
            /api/reports returns 500"``) — included in the report for
            context.
        endpoint: The failing endpoint path, when the problem is
            endpoint-shaped — checked against the registered routes and
            searched for in the logs.
        lines: How many log lines to scan for errors (default 200).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        findings (list[dict]): each ``{"area", "detail", "evidence"?}`` —
            areas: ``health``, ``structure``, ``migrations``, ``routing``,
            ``logs``.
        root_cause (dict | None): ``{"type", "confidence", "evidence"}`` for
            the most likely cause, or ``None`` when nothing conclusive.
        recommended_fix (dict | None): ``{"tool", "arguments"}`` — the call
            that most likely fixes it (library function name), when one
            clearly applies.
        next_actions (list[str]): ordered follow-ups.
        state_changed (bool): always ``False``.
        symptom (str): the symptom string the diagnosis ran against,
            echoed back so a caller can correlate batched calls.

    Notes:
        - Root-cause types: ``migrations_pending``, ``schema_mismatch``,
          ``wiring_gap``, ``route_not_registered``, ``database_unreachable``,
          ``runtime_error``.
        - Heuristics are ordered and deterministic — the first matching cause
          wins; everything observed still appears under ``findings``.
    """
    from zeeb_agents.health import check_system_health
    from zeeb_agents.logs import read_logs, search_logs
    from zeeb_agents.project import describe_project
    from zeeb_agents.schema import list_all_routes

    root = require_project_root(project_root)
    findings: list[dict] = []
    next_actions: list[str] = []

    health_res = await check_system_health(project_id=root)
    health_checks = (health_res.data or {}).get("checks", {})
    db_error = None
    if str(health_checks.get("db", "")).startswith("error"):
        db_error = health_checks["db"]
        findings.append({"area": "health", "detail": f"Database check failed: {db_error}"})

    state_res = await describe_project(project_id=root)
    state = (state_res.data or {}) if state_res.success else {}
    structure_warnings = state.get("warnings", [])
    for warning in structure_warnings:
        findings.append({"area": "structure", "detail": warning})

    migrations = state.get("migrations", {})
    pending_count = migrations.get("pending_count", 0) if migrations.get("available") else 0
    if pending_count:
        findings.append(
            {
                "area": "migrations",
                "detail": f"{pending_count} pending migration(s)",
                "evidence": migrations.get("pending", []),
            }
        )

    endpoint_missing = False
    if endpoint:
        routes_res = await list_all_routes(project_id=root)
        routes = (routes_res.data or {}).get("routes", []) if routes_res.success else []
        needle = endpoint.strip("/").lower()
        matched = False
        for route in routes:
            prefix = str(route.get("prefix") or "").strip("/").lower()
            path = str(route.get("path") or "").strip("/").lower()
            if (prefix and _contains_segments(needle, prefix)) or (path and path in needle):
                matched = True
                break
        if not matched:
            endpoint_missing = True
            findings.append(
                {
                    "area": "routing",
                    "detail": f"No registered route matches '{endpoint}'",
                    "evidence": sorted(
                        {str(r.get("prefix") or r.get("path")) for r in routes}
                    ),
                }
            )

    error_lines: list[str] = []
    logs_res = await read_logs(lines=lines, level="ERROR", project_id=root)
    if logs_res.success:
        error_lines = (logs_res.data or {}).get("lines", [])
        if error_lines:
            findings.append(
                {
                    "area": "logs",
                    "detail": f"{len(error_lines)} ERROR line(s) in the last {lines} log lines",
                    "evidence": error_lines[-5:],
                }
            )
    if endpoint:
        search_res = await search_logs(re.escape(endpoint), project_id=root)
        matches = (search_res.data or {}).get("matches", []) if search_res.success else []
        if matches:
            findings.append(
                {
                    "area": "logs",
                    "detail": f"{len(matches)} log line(s) mention '{endpoint}'",
                    "evidence": [m["content"] for m in matches[-5:]],
                }
            )

    schema_errors = [
        line for line in error_lines
        if any(marker in line.lower() for marker in _SCHEMA_ERROR_MARKERS)
    ]

    root_cause: dict | None = None
    recommended_fix: dict | None = None
    if db_error:
        root_cause = {
            "type": "database_unreachable",
            "confidence": 0.9,
            "evidence": [db_error],
        }
        next_actions.append(
            "Check the DATABASE settings (get_settings) and the database service."
        )
    elif pending_count:
        root_cause = {
            "type": "migrations_pending",
            "confidence": 0.9 if schema_errors else 0.7,
            "evidence": [f"{pending_count} pending migration(s)"] + schema_errors[-3:],
        }
        recommended_fix = {"tool": "run_migrations", "arguments": {}}
        next_actions.append("Apply the pending migrations, then retry the request.")
    elif schema_errors:
        root_cause = {
            "type": "schema_mismatch",
            "confidence": 0.8,
            "evidence": schema_errors[-3:],
        }
        recommended_fix = {"tool": "make_migrations", "arguments": {}}
        next_actions.append(
            "The database schema is behind the models — create and apply a "
            "migration, then retry."
        )
    elif structure_warnings:
        root_cause = {
            "type": "wiring_gap",
            "confidence": 0.7,
            "evidence": structure_warnings,
        }
        next_actions.append(
            "Fix the wiring gaps listed in the findings (apps not installed / "
            "routers not included make endpoints 404)."
        )
    elif endpoint_missing:
        root_cause = {
            "type": "route_not_registered",
            "confidence": 0.7,
            "evidence": [f"'{endpoint}' matches no registered route"],
        }
        next_actions.append(
            "Register the resource (generate_crud scaffolds model + endpoint + "
            "route in one call)."
        )
    elif error_lines:
        root_cause = {
            "type": "runtime_error",
            "confidence": 0.5,
            "evidence": error_lines[-3:],
        }
        next_actions.append(
            "Inspect the ERROR log lines in the findings; read_logs shows more "
            "context."
        )

    if symptom and not root_cause:
        next_actions.append(
            "Nothing conclusive found for the reported symptom — check the "
            "project snapshot (describe_project) and recent logs manually."
        )

    message = (
        f"Root cause: {root_cause['type']} "
        f"(confidence {root_cause['confidence']:.2f})"
        if root_cause
        else f"No conclusive root cause; {len(findings)} finding(s)"
    )
    return AgentResult(
        success=True,
        message=message,
        data={
            "symptom": symptom or None,
            "findings": findings,
            "root_cause": root_cause,
            "recommended_fix": recommended_fix,
            "next_actions": next_actions,
            "state_changed": False,
        },
    )
