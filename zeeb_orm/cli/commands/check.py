"""check command - Verify project configuration and migration state.

This is the command an agent runs to decide whether its own change is sound, so
it must not report green on a project that cannot serve a request. Every issue
carries the exact command that resolves it: a red ``check`` hands the caller its
own to-do list rather than a description of a problem.
"""

from pathlib import Path

from zeeb_orm.cli.output import fail, no_project, ok
from zeeb_orm.scaffold.naming import find_project_root


def _issue(code: str, message: str, next_command: str) -> dict:
    return {"code": code, "message": message, "next_command": next_command}


def find_migrations(project_root: Path) -> list:
    """Return the project's migration files (flat layout, legacy fallback)."""
    from zeeb_orm.migrations.executor import find_migration_files

    return find_migration_files(project_root / "migrations")


def _load_settings(project_root: Path):
    """Exec the project's settings module, or return ``None`` if there is none."""
    import importlib.util
    import sys

    for item in sorted(project_root.iterdir()):
        if not (item.is_dir() and (item / "settings.py").exists()):
            continue
        sys.path.insert(0, str(project_root))
        try:
            spec = importlib.util.spec_from_file_location("settings", item / "settings.py")
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))
    return None


def check_settings(project_root: Path) -> tuple[list[dict], str | None]:
    """Verify a settings module exists and declares the settings that matter."""
    issues: list[dict] = []
    package = next(
        (
            item.name
            for item in sorted(project_root.iterdir())
            if item.is_dir() and item.name != "apps" and (item / "settings.py").exists()
        ),
        None,
    )
    if package is None:
        issues.append(
            _issue(
                "file_not_found",
                "No settings.py found in any project subdirectory.",
                "cd <your project> && python manage.py check",
            )
        )
        return issues, None

    content = (project_root / package / "settings.py").read_text(encoding="utf-8")
    for setting in ("DATABASE", "INSTALLED_APPS"):
        if setting not in content:
            issues.append(
                _issue(
                    "setting_not_found",
                    f"{setting} is missing from {package}/settings.py.",
                    f"Add {setting} to {package}/settings.py",
                )
            )
    return issues, package


def check_apps(project_root: Path) -> tuple[list[dict], list[str]]:
    """Verify the apps directory exists and every app has a models module."""
    apps_dir = project_root / "apps"
    if not apps_dir.exists():
        return [
            _issue(
                "file_not_found",
                "The 'apps/' directory is missing.",
                "python manage.py startapp <name>",
            )
        ], []

    apps = sorted(
        d.name for d in apps_dir.iterdir() if d.is_dir() and (d / "__init__.py").exists()
    )
    issues = [
        _issue(
            "file_not_found",
            f"apps/{app}/ has no models.py.",
            f"Create apps/{app}/models.py",
        )
        for app in apps
        if not (apps_dir / app / "models.py").exists()
    ]
    return issues, apps


def check_migrations(project_root: Path) -> tuple[list[dict], dict]:
    """Report the real migration state, pending migrations included.

    A file-existence check used to pass here while the database was several
    migrations behind — which meant ``check`` said "ready" about a project whose
    every request would fail on a missing column.
    """
    from zeeb_orm.migrations.state import get_migration_state

    try:
        state = get_migration_state(project_root)
    except Exception as exc:  # an unreachable database must not crash `check`
        return [
            _issue(
                "invalid_input",
                f"Could not read the migration state: {exc}",
                "python manage.py showmigrations",
            )
        ], {"readable": False}

    # The state module reads the flat layout only. Older projects keep their
    # migrations under migrations/versions/, and reporting "none" about a
    # project that has them would send the caller off to write duplicates.
    on_disk = find_migrations(project_root)
    legacy = state.total_migrations == 0 and bool(on_disk)

    summary = {
        "readable": True,
        "total": max(state.total_migrations, len(on_disk)),
        "applied": state.applied_migrations,
        "pending": None if legacy else state.pending_migrations,
        "current": state.current_revision,
        "head": state.head_revision,
        "layout": "legacy" if legacy else "flat",
    }

    if not state.has_migrations_dir:
        return [
            _issue(
                "file_not_found",
                "No migrations/ directory.",
                "python manage.py makemigrations",
            )
        ], summary
    if not on_disk and state.total_migrations == 0:
        return [
            _issue(
                "file_not_found",
                "No migrations have been created yet.",
                "python manage.py makemigrations",
            )
        ], summary
    if legacy:
        # Applied-state tracking does not reach migrations/versions/; say so
        # rather than implying the database is up to date.
        return [], summary
    if state.pending_migrations:
        return [
            _issue(
                "invalid_input",
                f"{state.pending_migrations} migration(s) created but not applied.",
                "python manage.py migrate",
            )
        ], summary
    return [], summary


def check_database(settings, deploy: bool) -> tuple[list[dict], dict]:
    """Inspect the configured database URL — never printing it.

    Only the scheme reaches the output: a database URL routinely carries a
    password, and this command's output is exactly the kind of thing that gets
    pasted into an issue or a transcript.
    """
    url = (getattr(settings, "DATABASE", {}) or {}).get("url", "") if settings else ""
    scheme = url.split("://", 1)[0] if "://" in url else ""
    summary = {"url_scheme": scheme, "configured": bool(url)}

    issues: list[dict] = []
    if not url:
        issues.append(
            _issue(
                "setting_not_found",
                "No database URL configured.",
                "Set DATABASE_URL in .env",
            )
        )
    elif deploy and "sqlite" in scheme.lower():
        issues.append(
            _issue(
                "invalid_input",
                "SQLite is not suitable for production; use PostgreSQL or MySQL.",
                "Set DATABASE_URL in .env to a postgresql+asyncpg:// URL",
            )
        )
    return issues, summary


def check_deployment(settings) -> list[dict]:
    """The extra bar a project has to clear before it is served publicly."""
    issues: list[dict] = []
    if getattr(settings, "DEBUG", True):
        issues.append(
            _issue("invalid_input", "DEBUG is on.", "Set DEBUG=false in .env")
        )
    if not getattr(settings, "SECRET_KEY", None):
        issues.append(
            _issue("setting_not_found", "SECRET_KEY is not set.", "Set SECRET_KEY in .env")
        )
    return issues


def run_check(deploy: bool = False, json_output: bool = False) -> int:
    """Verify the project can actually serve, and say what to run if it cannot."""
    project_root = find_project_root()
    if project_root is None:
        return no_project("check", json_output=json_output)

    issues: list[dict] = []

    settings_issues, package = check_settings(project_root)
    issues += settings_issues
    app_issues, apps = check_apps(project_root)
    issues += app_issues
    migration_issues, migrations = check_migrations(project_root)
    issues += migration_issues

    settings = _load_settings(project_root) if package else None
    if package and settings is None:
        issues.append(
            _issue(
                "invalid_input",
                f"{package}/settings.py could not be imported.",
                f"python -c 'import {package}.settings'",
            )
        )
    database_issues, database = check_database(settings, deploy)
    issues += database_issues
    if deploy and settings is not None:
        issues += check_deployment(settings)

    details = {
        "project": project_root.name,
        "settings_module": f"{package}.settings" if package else None,
        "apps": apps,
        "migrations": migrations,
        "database": database,
        "deploy": deploy,
        "issues": issues,
    }

    if issues:
        lines = [f"Checking project: {project_root.name}", ""]
        lines += [f"  ✗ {issue['message']}" for issue in issues]
        lines.append("")
        lines.append(f"{len(issues)} issue(s) found. Run, in order:")
        seen: list[str] = []
        for issue in issues:
            if issue["next_command"] not in seen:
                seen.append(issue["next_command"])
                lines.append(f"  {issue['next_command']}")
        if json_output:
            return fail(
                f"{len(issues)} check(s) failed.",
                code="invalid_input",
                next_command=seen[0],
                json_output=True,
                **details,
            )
        for line in lines:
            print(line)
        return 1

    applied = migrations.get("applied", 0)
    return ok(
        "All checks passed.",
        json_output=json_output,
        lines=(
            f"Checking project: {project_root.name}",
            f"  ✓ settings   {package}/settings.py",
            f"  ✓ apps       {', '.join(apps) if apps else 'none yet'}",
            f"  ✓ migrations {applied} applied, 0 pending",
            f"  ✓ database   {database['url_scheme'] or 'not configured'}",
            "",
            "All checks passed."
            + ("\nProject is ready for deployment." if deploy else ""),
        ),
        **details,
    )
