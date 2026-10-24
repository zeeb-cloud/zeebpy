"""The read-only verification chain behind ``verify_project`` and the intent tools.

Each check (``structure``, ``code``, ``migrations``, ``openapi``, ``runtime``,
``security``, ``endpoints``, ``tests``) runs a read-only agent tool and folds its
answer into ``{"ok", ...}``; :func:`_verification_next_actions` turns failures
into follow-ups that name the tool to call. Split out of :mod:`zeeb_agents.intent`,
which imports these names back (callers and tests address them there).
"""

from __future__ import annotations

from pathlib import Path

from zeeb_agents._utils.errors import AgentError

_DEFAULT_CHECKS = ("structure", "code", "migrations", "openapi")
_VALID_CHECKS = (
    "structure",
    "code",
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

    # Whatever listens on the port is not necessarily this project: another
    # project's server, or an older revision of this one. Its GETs would answer
    # 404 (< 500, "reachable") and pass. Its OpenAPI document must list this
    # project's endpoints before they are smoked.
    from zeeb_agents.schema import _fetch_openapi_spec

    try:
        spec = await _fetch_openapi_spec(port)
    except AgentError:
        spec = None  # unreachable: the requests below report it endpoint by endpoint
    if spec is not None:
        served = list(spec.get("paths", {}))
        not_served = [
            prefix
            for prefix in prefixes
            if not any(_contains_segments(path, prefix) for path in served)
        ]
        if not_served:
            return {
                "ok": False,
                "checked": 0,
                "not_served": not_served,
                "error": (
                    f"the server on port {port} does not serve this project's "
                    f"endpoints {', '.join(not_served)} — it is another project, "
                    "or an older revision of this one"
                ),
            }

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
        elif check == "code":
            from zeeb_agents.checks import check_code

            res = await check_code(project_id=project_root)
            data = res.data or {}
            errors = data.get("errors", []) if res.success else []
            results["code"] = {
                "ok": bool(res.success) and not errors,
                "errors": errors[:20],
                "count": len(errors),
                **({"error": res.message} if not res.success else {}),
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
    """Turn failed checks into concrete follow-up actions, each naming its tool."""
    actions: list[str] = []
    checks = verification.get("checks", {})
    code = checks.get("code")
    if code and not code["ok"]:
        first = (code.get("errors") or [{}])[0]
        where = (
            f"{first.get('file')}:{first.get('line')}"
            if first.get("file") and first.get("line")
            else first.get("file") or first.get("module") or "a project module"
        )
        actions.append(
            f"Fix the {first.get('kind') or 'code'} error at {where} ({first.get('message')}) — "
            "edit_function / set_class_method for code inside a generated class, "
            "edit_file otherwise, add_dependency for a package that is not installed; "
            "check_code() re-checks."
        )
    migrations = checks.get("migrations")
    if migrations and not migrations["ok"]:
        actions.append("Apply the pending migrations (run_migrations).")
    structure = checks.get("structure")
    if structure and not structure["ok"]:
        actions.append(
            "Fix the wiring gaps listed under verification.checks.structure.warnings — "
            "create_app(name=...) re-applies an app's registration and url include, "
            "setup_auth() re-wires auth, configure_cors() re-wires CORS."
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
                "build_feature(tests=True), or run check_code(): a broken import "
                "collects zero tests."
            )
        elif tests.get("failed_tests"):
            named = ", ".join(tests["failed_tests"][:3])
            more = (
                f" (+{len(tests['failed_tests']) - 3} more)"
                if len(tests["failed_tests"]) > 3
                else ""
            )
            actions.append(
                f"Fix the failing tests: {named}{more} — run_tests(path=<node id>) shows "
                "the traceback; fix the code with edit_function / set_class_method; if "
                "the generated suite is stale after a spec change, "
                "regenerate_tests(feature=...)."
            )
        else:
            actions.append(
                "Fix the failing tests (run_tests shows the detail) — edit_function / "
                "set_class_method fix the code in place."
            )
    runtime = checks.get("runtime")
    if runtime and not runtime["ok"]:
        actions.append(
            "The runtime is not healthy "
            f"(settings={runtime.get('settings')}, db={runtime.get('db')}) — "
            "get_env() shows DATABASE_URL, set_env(key=..., value=...) fixes it, "
            "check_system_health() confirms; read_logs for the detail."
        )
    endpoints = checks.get("endpoints")
    if endpoints and endpoints.get("not_served"):
        actions.append(
            f"The server that answered is not running this project's current code "
            f"({endpoints['error']}) — restart this project's server "
            "(zeeb-manage runserver) on the port you verify against."
        )
    elif endpoints and not endpoints["ok"]:
        failing = endpoints.get("failures") or []
        named = ", ".join(str(f.get("endpoint")) for f in failing[:3])
        actions.append(
            "Endpoints returned a server error or were unreachable: "
            f"{named or endpoints.get('error')} — read_logs shows the traceback, "
            "diagnose_problem(endpoint=...) names the fix, check_code() catches a "
            "module that no longer imports."
        )
    security = checks.get("security")
    if security and not security["ok"]:
        issues = "; ".join(security.get("issues", [])[:3])
        actions.append(
            f"Resolve the production-readiness issues: {issues} — set_env(key=..., "
            "value=...) for DEBUG/SECRET_KEY/DATABASE_URL, generate_requirements() and "
            "generate_dockerfile() for the missing files."
        )
    return actions
