"""startproject command - Create new Zeeb project.

The templates themselves live in :mod:`zeeb_orm.scaffold.project`, which is the
single source of truth shared with the agent layer. They are re-exported here
because they are part of this module's established import surface.
"""

from pathlib import Path

from zeeb_orm.cli.output import fail, ok
from zeeb_orm.scaffold import accounts as accounts_templates
from zeeb_orm.scaffold.agent_guide import (
    CLAUDE_MD,
    render_agents_md,
    render_cursor_rules,
)
from zeeb_orm.scaffold.app import APP_INIT_PY
from zeeb_orm.scaffold.errors import ScaffoldError
from zeeb_orm.scaffold.harness import (
    PYTEST_INI,
    SMOKE_TEST_PY,
    render_conftest,
)
from zeeb_orm.scaffold.project import (
    APPS_INIT_PY,
    ASGI_PY,
    ENV_EXAMPLE,
    ENV_TEMPLATE,
    GITIGNORE,
    MANAGE_PY,
    PROJECT_INIT_PY,
    PYPROJECT_TOML,
    README_MD,
    REQUIREMENTS_TXT,
    SETTINGS_PY,
    URLS_PY,
    generate_secret_key,
    zeeb_version,
)

__all__ = [
    "APPS_INIT_PY",
    "ASGI_PY",
    "ENV_EXAMPLE",
    "ENV_TEMPLATE",
    "GITIGNORE",
    "MANAGE_PY",
    "PROJECT_INIT_PY",
    "PYPROJECT_TOML",
    "README_MD",
    "REQUIREMENTS_TXT",
    "SETTINGS_PY",
    "URLS_PY",
    "run_startproject",
]


def run_startproject(name: str, directory: str, json_output: bool = False) -> int:
    """Create a new Zeeb project."""
    if not name.isidentifier():
        return fail(
            f"'{name}' is not a valid Python identifier, so it cannot be a package name.",
            code="invalid_identifier",
            next_command="python -m zeeb_orm.cli.main startproject <valid_name>",
            json_output=json_output,
            name=name,
        )

    base_path = Path(directory).resolve()
    project_path = base_path / name

    if project_path.exists():
        return fail(
            f"Directory '{project_path}' already exists.",
            code="already_exists",
            next_command=f"cd {name} && python manage.py check",
            json_output=json_output,
            path=str(project_path),
        )

    if not json_output:
        print(f"Creating project '{name}' in {base_path}...")

    try:
        # Create directory structure
        project_path.mkdir(parents=True)
        (project_path / name).mkdir()
        (project_path / "apps").mkdir()
        (project_path / "apps" / "accounts").mkdir()
        (project_path / "migrations").mkdir()
        (project_path / "logs").mkdir()

        # Keep the empty directories in version control. Migration files live
        # flat in migrations/ — that is what makemigrations writes and what the
        # executor, the state module and the pre-flight checks read.
        (project_path / "logs" / ".gitkeep").write_text("")
        (project_path / "migrations" / ".gitkeep").write_text("")

        # A fresh signing key per project, so the generated app runs with
        # DEBUG=false without the operator having to think about it.
        accounts_context = {
            "app_name": "accounts",
            "app_class": "Accounts",
            "app_title": "Accounts",
        }

        # Create files
        files = {
            "manage.py": MANAGE_PY,
            f"{name}/__init__.py": PROJECT_INIT_PY.format(project_name=name),
            f"{name}/settings.py": SETTINGS_PY.format(project_name=name),
            f"{name}/urls.py": URLS_PY,
            f"{name}/asgi.py": ASGI_PY.format(project_name=name),
            "apps/__init__.py": APPS_INIT_PY,
            "apps/accounts/__init__.py": APP_INIT_PY.format(**accounts_context),
            "apps/accounts/models.py": accounts_templates.MODELS_PY,
            "apps/accounts/serializers.py": accounts_templates.SERIALIZERS_PY,
            "apps/accounts/views.py": accounts_templates.VIEWS_PY,
            "apps/accounts/urls.py": accounts_templates.URLS_PY,
            # The test harness ships with the project, not with the first
            # generated feature: `pytest` has to be a working verification loop
            # from minute one, and the db fixture builds the schema from the
            # model registry so it passes before `makemigrations` has ever run.
            "pytest.ini": PYTEST_INI,
            "tests/__init__.py": "",
            "tests/conftest.py": render_conftest(f"{name}.settings"),
            "tests/test_smoke.py": SMOKE_TEST_PY,
            "requirements.txt": REQUIREMENTS_TXT,
            "pyproject.toml": PYPROJECT_TOML.format(
                project_name=name, zeeb_version=zeeb_version()
            ),
            ".gitignore": GITIGNORE,
            ".env": ENV_TEMPLATE.format(
                project_name=name, secret_key=generate_secret_key()
            ),
            ".env.example": ENV_EXAMPLE.format(project_name=name),
            "README.md": README_MD.format(project_name=name),
            # What tells a coding agent what this project is. AGENTS.md is the
            # source of truth; the other two are vendor entry points rendered
            # from the same constant so they cannot drift from it.
            "AGENTS.md": render_agents_md(name),
            "CLAUDE.md": CLAUDE_MD,
            ".cursor/rules/zeebpy.mdc": render_cursor_rules(name),
        }

        for filepath, content in files.items():
            file_path = project_path / filepath
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            if not json_output:
                print(f"  Created {filepath}")

        # Make manage.py executable.
        manage_path = project_path / "manage.py"
        manage_path.chmod(manage_path.stat().st_mode | 0o111)
        # The signing key is in there: owner-only, like an ssh private key.
        (project_path / ".env").chmod(0o600)

    except Exception as exc:
        # Only a half-written tree is removed. Everything past this point runs
        # outside the try for exactly that reason — see below.
        import shutil

        if project_path.exists():
            shutil.rmtree(project_path)
        return fail(
            f"Could not create the project: {exc}",
            code="invalid_input",
            next_command=f"python -m zeeb_orm.cli.main startproject {name}",
            json_output=json_output,
        )

    # Wiring runs outside the block above. The files are on disk and valid, so
    # a failure here must not delete them — a project the user can repair in two
    # lines beats no project at all. (startapp makes the same choice.)
    from zeeb_orm.scaffold.wiring import ensure_app_urls_included

    try:
        # Same code path startapp uses, so the include statement and its
        # position are identical to what the wiring tools would produce.
        # INSTALLED_APPS already lists apps.accounts in the settings template.
        ensure_app_urls_included(project_path, "accounts")
    except ScaffoldError as exc:
        return fail(
            f"Project '{name}' was created, but its accounts router could not be "
            f"included: {exc}",
            code=exc.code,
            next_command=(
                f"Add 'from apps.accounts.urls import router as accounts_router' and "
                f"'router.include(accounts_router)' to {name}/{name}/urls.py"
            ),
            json_output=json_output,
            state_changed=True,
            path=str(project_path),
            **exc.data,
        )

    return ok(
        f"Project '{name}' created at {project_path}.",
        json_output=json_output,
        lines=(
            f"\nProject '{name}' created successfully!",
            "\nNext steps:",
            f"  cd {name}",
            "  python -m venv .venv",
            "  source .venv/bin/activate",
            "  pip install -r requirements.txt",
            "  pytest                           # The smoke suite passes already",
            "  python manage.py makemigrations  # Create initial migrations",
            "  python manage.py migrate         # Apply migrations",
            "  python manage.py createsuperuser # Create an admin account",
            "  python manage.py runserver",
            "\nAuthentication is already wired: POST /api/v1/auth/register,",
            "/api/v1/auth/login, /api/v1/auth/refresh and GET /api/v1/auth/me.",
            "Read AGENTS.md for the conventions; configure everything from .env.",
        ),
        name=name,
        path=str(project_path),
        files=sorted(files),
    )
