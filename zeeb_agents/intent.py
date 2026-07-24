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
from zeeb_agents.feature_spec import (
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
_VALID_CHECKS = ("structure", "migrations", "openapi", "tests")


def _default_checks(root: Path) -> tuple[str, ...]:
    """Default verification chain; includes ``tests`` once a generated suite exists.

    The marker is the generated ``tests/conftest.py`` — cheap, stateless, and
    still true after the user adds their own tests to the directory.
    """
    if (root / "tests" / "conftest.py").exists():
        return (*_DEFAULT_CHECKS, "tests")
    return _DEFAULT_CHECKS

#: Generator-owned files each op kind touches — powers the ``affected`` report.
_OP_FILES: dict[str, tuple[str, ...]] = {
    "create_model": ("models.py",),
    "add_field": ("models.py",),
    "remove_field": ("models.py",),
    "add_relationship": ("models.py",),
    "create_user_model": ("models.py",),
    "create_serializer": ("serializers.py",),
    "create_viewset": ("views.py",),
    "add_viewset_action": ("views.py",),
    "register_route": ("urls.py",),
    "make_migrations": ("migrations",),
}


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


async def _run_verification(
    checks: tuple[str, ...] | list[str],
    project_root: Path,
    port: int = 8000,
) -> dict:
    """Run the requested read-only checks; never raises, never writes."""
    from zeeb_agents.migrations import get_migration_status
    from zeeb_agents.project import describe_project
    from zeeb_agents.schema import _fetch_openapi_spec
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
                results["openapi"] = {
                    "ok": True,
                    "paths_count": len(spec.get("paths", {})),
                }
            except AgentError as exc:
                results["openapi"] = {"ok": False, "paths_count": None, "error": str(exc)}
        elif check == "tests":
            res = await run_tests(project_id=project_root)
            data = res.data or {}
            results["tests"] = {
                "ok": bool(res.success)
                and not data.get("failed")
                and not data.get("errors"),
                "summary": res.message,
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
        actions.append(
            "The API was not reachable for the OpenAPI check — inspect the logs "
            "(read_logs) or the runtime reference (get_project_reference)."
        )
    tests = checks.get("tests")
    if tests and not tests["ok"]:
        actions.append("Fix the failing tests (run_tests shows the detail).")
    return actions


async def _apply_and_report(
    plan: dict,
    project_root: Path,
    migrate: bool,
    verify: bool,
    label: str,
    checks: tuple[str, ...] | None = None,
    extra_next_actions: list[str] | None = None,
) -> AgentResult:
    """Shared execute + verify + envelope tail for the mutating intent tools.

    ``checks=None`` selects the default chain, which auto-includes the
    ``tests`` check when the project carries a generated test suite — resolved
    *after* execution, so the build that generates the suite already gates on it.
    """
    outcome = await execute_plan(plan, project_root, migrate=migrate)
    summary = plan.get("summary", label)
    warnings = list(plan.get("warnings", []))
    warnings.extend(outcome.get("warnings", []))
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
                "affected": _affected(plan),
                "verification": None,
                "warnings": warnings,
                "next_actions": [
                    "Re-run the same call — completed steps are skipped "
                    "(idempotent) — or repair with the structural tools."
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

    message = f"{summary} — applied"
    if verification is not None:
        message += (
            " and verified" if verification["passed"] else "; verification found issues"
        )
    return AgentResult(
        success=True,
        message=message,
        data={
            "summary": summary,
            "changes": outcome["changes"],
            "steps": outcome["steps"],
            "affected": _affected(plan),
            "verification": verification,
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
        risk (dict): ``{"level", "destructive", "database_changes"}``.
        warnings (list[str]): non-fatal findings (existing models, auth
            assumptions, approximated operations, cycles).
        state_changed (bool): always ``False`` — nothing was written.

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
        warnings (list[str]): compiler warnings (existing models skipped,
            auth assumptions, …).
        next_actions (list[str]): concrete follow-ups (empty when everything
            passed).
        state_changed (bool): whether anything was actually written/applied.

    Notes:
        - Idempotent: re-running with the same spec completes missing steps
          and skips existing ones.
        - Partial failure returns ``success=False`` with
          ``data["steps_completed"]`` / ``data["errors"]`` — fix the cause and
          re-run the same call.
        - A failed verification does NOT fail the call: inspect
          ``verification.checks`` and ``next_actions``.
    """
    root = require_project_root(project_root)
    existing_models, existing_apps = await _project_inventory(root)
    plan = compile_feature_spec(spec, existing_models, existing_apps, tests=tests)
    label = f"Feature '{plan['feature']['name']}'"
    return await _apply_and_report(plan, root, migrate, verify, label)


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
        plan, root, migrate, verify, "Plan", extra_next_actions=extra_actions
    )


@agent_function
async def change_feature(
    changes: list[dict],
    app: str | None = None,
    migrate: bool = True,
    verify: bool = True,
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
            {"operation": "add_relation", "entity": "Post",
             "field": {"name": "category", "target": "Category",
                        "cardinality": "many-to-one"}}
            {"operation": "remove_field", "entity": "Post",
             "field_name": "subtitle"}
            {"operation": "add_entity",
             "entity": {"name": "Comment", "fields": [...]},
             "app": "blog"}
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
            model plus serializer/endpoint/route like build_feature.
            ``add_workflow`` adds the status field (when missing) plus one
            transition endpoint per transition (see the ``workflow`` shape in
            :func:`plan_feature`); ``add_transition`` adds a single
            transition endpoint to an existing status field — its from/to
            states cannot be validated without a states list, so check the
            warning it emits.
        app: Target app override — required only when an entity name exists
            in several apps (or for add_entity without its own ``app`` key).
        migrate: Create and apply migrations at the end (default true).
        verify: Run the verification chain after applying (default true).
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

    Notes:
        - Invalid changes fail up front with every problem listed under
          ``data["problems"]`` — nothing is written when compilation fails.
        - Removing a field that is already gone counts as a skip, so re-runs
          stay idempotent.
    """
    root = require_project_root(project_root)
    existing_models, existing_apps = await _project_inventory(root)
    operations, warnings = compile_changes(changes, existing_models, existing_apps, app)
    operations = [*operations, {"op": "make_migrations", "name": None}, {"op": "run_migrations"}]
    plan = {
        "plan_version": PLAN_VERSION,
        "summary": f"Apply {len(changes)} change(s)",
        "operations": operations,
        "risk": _plan_risk(operations),
        "preconditions": plan_preconditions(operations, existing_models, existing_apps),
        "warnings": warnings,
    }
    return await _apply_and_report(plan, root, migrate, verify, "Changes")


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
            pending), ``"openapi"`` (live contract reachable), ``"tests"``
            (project test suite, opt-in). Default: structure, migrations,
            openapi.
        port: Port the API listens on for the openapi check (default 8000).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        summary (str): one-line verdict.
        verification (dict): ``{"passed": bool, "checks": {<name>:
            {"ok": bool, ...detail}}}``.
        warnings (list[str]): always empty (details live in the checks).
        next_actions (list[str]): concrete fixes for each failed check.
        state_changed (bool): always ``False``.

    Notes:
        - Unknown check names fail with ``error_code="invalid_input"`` and
          suggestions.
        - ``tests`` runs the project's own test suite — include it once the
          project has tests worth gating on.
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
            if (prefix and needle.startswith(prefix)) or (path and path in needle):
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
