"""End to end: `zeeb startproject` produces a running, authenticated API.

Everything here goes through the real generated files — scaffold, makemigrations,
migrate, then boot the project's own settings module with a TestClient. Before
this, a fresh project could not even start: the migration gate globbed a
directory makemigrations never wrote to, and the default SECRET_KEY was on the
framework's own insecure-secret denylist.
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zeeb_orm.cli.commands.startproject import run_startproject

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def manage(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``python manage.py <args>`` in a clean subprocess.

    A subprocess, not an in-process call: the migration machinery registers
    models into process-global state, and the generated project's models share
    class names with the framework's own.
    """
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )



def boot(project: Path, monkeypatch, **env: str):
    """Build the generated app fresh, with *env* applied.

    The project's settings module is purged from ``sys.modules`` first:
    ``create_app`` imports it, and a cached module would not re-read the
    environment, so the override under test would silently do nothing. The
    throttle cache is process-global, so it is reset too.
    """
    monkeypatch.chdir(project)
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    for name in [n for n in sys.modules if n == "demo_api" or n.startswith("demo_api.")]:
        del sys.modules[name]

    from zeeb_api.throttling import InMemoryThrottleCache, set_throttle_cache

    set_throttle_cache(InMemoryThrottleCache())

    from zeeb_api import create_app

    return create_app(f"{project.name}.settings")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A scaffolded project with its migrations created and applied."""
    base = tmp_path_factory.mktemp("boilerplate")
    assert run_startproject("demo_api", str(base)) == 0
    root = base / "demo_api"

    made = manage(root, "makemigrations")
    assert made.returncode == 0, made.stdout + made.stderr
    applied = manage(root, "migrate")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return root


@pytest.fixture
def client(project, monkeypatch):
    # A generous login limit: these tests exercise the endpoint repeatedly and
    # the throttle has its own test below.
    app = boot(project, monkeypatch, AUTH_LOGIN_THROTTLE_RATE="1000/min")
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Scaffold contents
# ---------------------------------------------------------------------------


def test_the_scaffold_ships_env_files(project):
    env = (project / ".env").read_text()
    assert "SECRET_KEY=" in env
    assert (project / ".env.example").is_file()

    secret = next(
        line.split("=", 1)[1] for line in env.splitlines() if line.startswith("SECRET_KEY=")
    )
    from zeeb_api.auth.jwt import INSECURE_SECRETS

    assert secret not in INSECURE_SECRETS
    assert len(secret) >= 43

    gitignore = (project / ".gitignore").read_text()
    assert ".env" in gitignore and "!.env.example" in gitignore

    # It holds the signing key: owner-only, like an ssh private key.
    if os.name != "nt":
        assert (project / ".env").stat().st_mode & 0o077 == 0


def test_each_project_gets_its_own_secret(tmp_path):
    assert run_startproject("one", str(tmp_path)) == 0
    assert run_startproject("two", str(tmp_path)) == 0
    assert (tmp_path / "one" / ".env").read_text() != (tmp_path / "two" / ".env").read_text()


def test_a_fresh_project_passes_its_own_tests(tmp_path):
    """`pytest` works before `makemigrations` has ever run.

    This is what makes the test suite a usable verification loop from the first
    minute — and it is the regression guard for the scaffolded suite silently
    collecting nothing (which is what an unset ``asyncio_mode`` used to do).
    """
    assert run_startproject("fresh_api", str(tmp_path)) == 0
    root = tmp_path / "fresh_api"
    assert (root / "pytest.ini").is_file()
    assert (root / "tests" / "conftest.py").is_file()

    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # "0 passed" would satisfy the return code but prove nothing.
    assert re.search(r"\b([1-9]\d*) passed", result.stdout), result.stdout


def test_pyproject_records_the_project_identity(project):
    """Tooling recognises a Zeeb project without importing anything."""
    marker = (project / "pyproject.toml").read_text()
    assert 'framework = "zeebpy"' in marker
    assert 'settings_module = "demo_api.settings"' in marker
    # No dependency table — requirements.txt owns that, and two copies drift.
    assert not re.search(r"^\[project\]", marker, re.MULTILINE)


def test_a_generated_project_passes_its_own_lint(tmp_path):
    """`ruff check .` is advertised in AGENTS.md, so it has to actually pass.

    Both app shapes are covered: the docstring-only default and the --model
    slice. Generated code that lints dirty teaches an agent to write more of it.
    """
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")

    assert run_startproject("lint_api", str(tmp_path)) == 0
    root = tmp_path / "lint_api"
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    # Migrations run too: they are generated files, and the check has to say
    # whether they are in scope rather than never meeting them.
    for args in (
        ["startapp", "plain"],
        ["startapp", "shop", "--model", "Product"],
        ["makemigrations"],
        ["migrate"],
    ):
        made = subprocess.run(
            [sys.executable, "manage.py", *args], cwd=root, env=env,
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stdout + made.stderr

    linted = subprocess.run(
        [ruff, "check", "--output-format", "concise", "."],
        cwd=root, capture_output=True, text=True,
    )
    assert linted.returncode == 0, linted.stdout + linted.stderr


def test_a_wiring_failure_does_not_delete_the_project(tmp_path, capsys):
    """The files are on disk and valid; a two-line repair beats no project.

    The whole body used to sit inside one try/except that rmtree'd on any
    failure, so a wiring problem took the entire directory with it.
    """
    from unittest.mock import patch

    from zeeb_orm.scaffold.errors import ScaffoldError

    with patch(
        "zeeb_orm.scaffold.wiring.ensure_app_urls_included",
        side_effect=ScaffoldError("no router symbol", code="invalid_input"),
    ):
        assert run_startproject("kept_api", str(tmp_path)) == 1

    root = tmp_path / "kept_api"
    assert (root / "manage.py").is_file()
    assert (root / "kept_api" / "settings.py").is_file()
    # And the message says exactly what to add by hand.
    assert "router.include(accounts_router)" in capsys.readouterr().err


def test_the_readme_describes_the_project_that_was_generated(project):
    """It is the first file anyone opens, human or agent. It used to tell you to
    run `manage.py init` and showed a tree with no accounts app, no .env and no
    tests."""
    readme = (project / "README.md").read_text()

    tree = readme.split("## Structure", 1)[1].split("```")[1]
    for line in tree.splitlines():
        entry = line.strip("│├└─ ").split()[0] if line.strip("│├└─ ") else ""
        if not entry or entry.endswith("/") and entry == "demo_api/":
            continue
        # Nested entries are listed by name only; resolve them anywhere.
        assert list(project.rglob(entry.rstrip("/"))) or (project / entry.rstrip("/")).exists(), (
            f"README lists {entry}, which the scaffold does not create"
        )

    assert "manage.py init" not in readme
    assert "AGENTS.md" in readme
    assert "pytest" in readme


def test_the_accounts_app_is_wired(project):
    settings = (project / "demo_api" / "settings.py").read_text()
    assert '"apps.accounts",' in settings
    assert 'AUTH_USER_MODEL = "accounts.User"' in settings

    urls = (project / "demo_api" / "urls.py").read_text()
    assert "from apps.accounts.urls import router as accounts_router" in urls
    assert "router.include(accounts_router)" in urls
    assert "create_auth_router" in urls


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_initial_migration_creates_the_expected_tables(project):
    tables = {
        row[0]
        for row in sqlite3.connect(project / "db.sqlite3").execute(
            "select name from sqlite_master where type='table'"
        )
    }
    assert {"accounts_user", "auth_permissions", "auth_user_permissions"} <= tables
    # The OAuth identity table ships unconditionally, so enabling a provider
    # later needs no migration and the schema does not depend on the environment.
    assert "auth_external_identities" in tables
    # The framework's own user table is superseded by AUTH_USER_MODEL.
    assert "auth_users" not in tables


def test_migrations_are_written_flat(project):
    assert not (project / "migrations" / "versions").exists()
    assert list((project / "migrations").glob("0001_*.py"))


def test_the_check_command_passes(project):
    result = manage(project, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout
    assert "No migrations found" not in result.stdout


# ---------------------------------------------------------------------------
# The running API
# ---------------------------------------------------------------------------


def test_health_and_readiness_probes(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["status"] == "ready"


def test_register_login_and_me(client):
    registered = client.post(
        "/api/v1/auth/register", json={"email": "a@b.de", "password": "secret123"}
    )
    assert registered.status_code == 200, registered.text

    logged_in = client.post(
        "/api/v1/auth/login", json={"email": "a@b.de", "password": "secret123"}
    )
    assert logged_in.status_code == 200, logged_in.text
    tokens = logged_in.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["claims"]["email"] == "a@b.de"


def test_refresh_returns_a_new_token(client):
    client.post("/api/v1/auth/register", json={"email": "r@b.de", "password": "secret123"})
    tokens = client.post(
        "/api/v1/auth/login", json={"email": "r@b.de", "password": "secret123"}
    ).json()

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]


def test_the_users_endpoint_is_staff_only(client):
    assert client.get("/api/v1/users").status_code == 401

    client.post("/api/v1/auth/register", json={"email": "plain@b.de", "password": "secret123"})
    token = client.post(
        "/api/v1/auth/login", json={"email": "plain@b.de", "password": "secret123"}
    ).json()["access_token"]
    assert client.get(
        "/api/v1/users", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 403


def test_versioning_rejects_an_unknown_version(client):
    assert client.get("/health", headers={"X-API-Version": "1.0"}).status_code == 200

    rejected = client.get("/health", headers={"X-API-Version": "9.9"})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "API_VERSION_INVALID"


def test_cors_headers_are_present(client):
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_login_is_throttled(project, monkeypatch):
    app = boot(project, monkeypatch, AUTH_LOGIN_THROTTLE_RATE="5/min")
    with TestClient(app) as throttled:
        codes = [
            throttled.post(
                "/api/v1/auth/login", json={"email": "no@one.de", "password": "wrong"}
            ).status_code
            for _ in range(7)
        ]

    assert codes[:5] == [401] * 5
    assert codes[5] == 429


# ---------------------------------------------------------------------------
# Production posture
# ---------------------------------------------------------------------------


def test_production_mode_starts_with_the_generated_secret(project, monkeypatch):
    assert boot(project, monkeypatch, DEBUG="false") is not None


def test_production_mode_refuses_to_start_without_a_secret(tmp_path, monkeypatch):
    """Losing .env must fail loudly, not fall back to a guessable key."""
    assert run_startproject("nosecret", str(tmp_path)) == 0
    root = tmp_path / "nosecret"
    (root / ".env").unlink()

    from zeeb_api.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured, match="insecure default"):
        boot(root, monkeypatch, DEBUG="false")


def test_env_overrides_reach_the_settings(project, monkeypatch):
    app = boot(
        project,
        monkeypatch,
        API_TITLE="Overridden",
        CORS_ALLOW_ORIGINS="https://example.com",
    )
    assert app.title == "Overridden"

    from zeeb_api.conf import settings

    assert settings.CORS_ALLOW_ORIGINS == ["https://example.com"]


# ---------------------------------------------------------------------------
# The OAuth table ships in the initial migration
# ---------------------------------------------------------------------------


async def test_enabling_oauth_needs_no_migration(project):
    """The identity table is in 0001, so the schema never depends on the env.

    Gating the ExternalIdentity import on OAUTH_PROVIDERS would make
    makemigrations emit a DeleteModel for any developer without provider
    credentials in their .env — against a schema already committed to git.
    """
    import zeeb_agents as agents

    configured = await agents.setup_oauth("google", project_id=project)
    assert configured.success, configured.message

    settings = (project / "demo_api" / "settings.py").read_text()
    assert '"google"' in settings
    compile(settings, "settings.py", "exec")

    pending = manage(project, "makemigrations", "--check")
    assert pending.returncode == 0, pending.stdout + pending.stderr
    assert "No changes detected" in pending.stdout
