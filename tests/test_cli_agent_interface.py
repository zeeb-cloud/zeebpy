"""The CLI as a machine interface.

An agent working on a generated project without the MCP layer drives it through
``python manage.py``. These tests pin the two properties that make it usable:
stdout in ``--json`` mode is exactly one parseable object, and every failure
names the command that resolves it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zeeb_orm.cli.commands.inspect import _redact_url
from zeeb_orm.cli.commands.startproject import run_startproject
from zeeb_orm.cli.main import COMMANDS, JSON_COMMANDS, build_parser
from zeeb_orm.cli.output import CLI_ERROR_CODES

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def manage(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``python manage.py <args>`` in a clean subprocess."""
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def envelope(result: subprocess.CompletedProcess) -> dict:
    """Parse stdout as the single result object it is supposed to be."""
    payload = json.loads(result.stdout)  # raises if a stray print got in the way
    assert set(payload) == {"success", "message", "data"}, payload
    return payload


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A scaffolded project with one --model app, migrated."""
    base = tmp_path_factory.mktemp("cli")
    assert run_startproject("cli_api", str(base)) == 0
    root = base / "cli_api"
    assert manage(root, "startapp", "blog", "--model", "Post").returncode == 0
    assert manage(root, "makemigrations").returncode == 0
    assert manage(root, "migrate").returncode == 0
    return root


# ---------------------------------------------------------------------------
# The command surface
# ---------------------------------------------------------------------------


def test_the_parser_matches_the_commands_constant():
    """COMMANDS is what the docs drift test compares against, so it has to be
    the real surface and not a stale copy of it."""
    registered = set(build_parser()._subparsers._group_actions[0].choices)
    assert registered == set(COMMANDS)


def test_every_json_command_accepts_the_flag():
    choices = build_parser()._subparsers._group_actions[0].choices
    for name in JSON_COMMANDS:
        options = {
            option for action in choices[name]._actions for option in action.option_strings
        }
        assert "--json" in options, f"{name} is listed as JSON-capable but has no --json"


def test_the_cli_error_codes_are_agent_error_codes():
    """One vocabulary across the CLI and the agent tools: an agent that has read
    error-recovery.md can recover from a CLI failure without learning a second
    set of names."""
    from zeeb_agents._utils.errors import ERROR_CODES

    assert CLI_ERROR_CODES <= ERROR_CODES


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("check",),
        ("showmigrations",),
        ("showurls",),
        ("inspect",),
        ("frontend-brief",),
        ("makemigrations", "--check"),
    ],
)
def test_json_mode_prints_exactly_one_object(project, args):
    result = manage(project, *args, "--json")
    payload = envelope(result)
    assert payload["success"] is True
    assert payload["message"]


def test_a_failure_names_the_next_command(tmp_path):
    """The property the fail() signature enforces, checked end to end."""
    empty = tmp_path / "not_a_project"
    empty.mkdir()
    (empty / "manage.py").write_text(
        "import sys\nfrom zeeb_orm.cli.main import main\nsys.exit(main())\n"
    )

    result = manage(empty, "check", "--json")
    assert result.returncode == 1
    payload = envelope(result)
    assert payload["success"] is False
    data = payload["data"]
    assert data["error_code"] in CLI_ERROR_CODES
    assert data["state_changed"] is False
    assert data["next_command"]


def test_startapp_reports_a_conflict_the_caller_can_act_on(project):
    result = manage(project, "startapp", "blog", "--json")
    assert result.returncode == 1
    data = envelope(result)["data"]
    assert data["error_code"] == "already_exists"
    assert data["next_command"].startswith("Edit apps/blog/models.py")


def test_makemigrations_check_fails_on_an_unmigrated_model(project):
    """The CI question. It must fail loudly, not report 'no changes'."""
    models = project / "apps" / "blog" / "models.py"
    original = models.read_text()
    models.write_text(
        original.replace(
            "class Post(Model):",
            "class Post(Model):\n\n    extra = fields.IntegerField(null=True)",
        )
    )
    try:
        result = manage(project, "makemigrations", "--check", "--json")
        assert result.returncode == 1
        data = envelope(result)["data"]
        assert data["next_command"] == "python manage.py makemigrations"
        assert data["pending_operations"]
    finally:
        models.write_text(original)


# ---------------------------------------------------------------------------
# check tells the truth
# ---------------------------------------------------------------------------


def test_check_fails_while_migrations_are_pending(tmp_path):
    """A file-existence check used to report green here — about a project whose
    every request would fail on a missing column."""
    assert run_startproject("pending_api", str(tmp_path)) == 0
    root = tmp_path / "pending_api"

    before = manage(root, "check", "--json")
    assert before.returncode == 1
    assert envelope(before)["data"]["next_command"] == "python manage.py makemigrations"

    assert manage(root, "makemigrations").returncode == 0
    made = manage(root, "check", "--json")
    assert made.returncode == 1
    assert envelope(made)["data"]["next_command"] == "python manage.py migrate"

    assert manage(root, "migrate").returncode == 0
    assert manage(root, "check").returncode == 0


def test_check_never_prints_the_database_url(project):
    """It routinely carries a password, and this output gets pasted around."""
    data = envelope(manage(project, "check", "--json"))["data"]
    assert data["database"]["url_scheme"] == "sqlite+aiosqlite"
    assert "url" not in data["database"]


# ---------------------------------------------------------------------------
# showurls / inspect / frontend-brief
# ---------------------------------------------------------------------------


def test_showurls_lists_the_real_routes_once_each(project):
    data = envelope(manage(project, "showurls", "--json"))["data"]
    by_path = {route["path"]: route for route in data["routes"]}

    assert "/api/v1/posts" in by_path
    assert "/api/v1/auth/login" in by_path
    # Not in the OpenAPI schema, but very much served — which is why the scan
    # reads app.routes rather than the generated schema.
    assert "/health" in by_path

    # The trailing-slash aliases answer the same endpoint; listing both would
    # double every line.
    assert "/api/v1/posts/" not in by_path
    assert set(by_path["/api/v1/posts"]["methods"]) == {"GET", "POST"}
    assert len(data["routes"]) == len(by_path)


def test_inspect_reports_apps_models_and_routes(project):
    data = envelope(manage(project, "inspect", "--json"))["data"]

    assert data["degraded"] is False
    assert data["project"]["settings_module"] == "cli_api.settings"
    assert data["settings"]["auth_user_model"] == "accounts.User"

    by_app = {app["name"]: app for app in data["apps"]}
    assert by_app["blog"]["installed"] is True
    post = next(m for m in by_app["blog"]["models"] if m["name"] == "Post")
    assert post["table"] == "blog_post"
    assert {"title", "slug", "owner"} <= set(post["fields"])
    assert data["migrations"]["pending"] == 0


def test_inspect_never_leaks_a_secret(project):
    secret = next(
        line.split("=", 1)[1].strip()
        for line in (project / ".env").read_text().splitlines()
        if line.startswith("SECRET_KEY=")
    )
    result = manage(project, "inspect", "--json")
    assert secret not in result.stdout
    assert envelope(result)["data"]["settings"]["secret_key_set"] is True


def test_redact_url_strips_credentials():
    assert (
        _redact_url("postgresql+asyncpg://user:hunter2@db.internal:5432/app")
        == "postgresql+asyncpg://***@db.internal:5432/app"
    )
    assert _redact_url("sqlite+aiosqlite:///db.sqlite3") == "sqlite+aiosqlite:///db.sqlite3"


def test_inspect_degrades_instead_of_failing(tmp_path):
    """A project that will not import is exactly when orientation is worth most."""
    assert run_startproject("broken_api", str(tmp_path)) == 0
    root = tmp_path / "broken_api"
    settings = root / "broken_api" / "settings.py"
    settings.write_text(settings.read_text() + "\nthis is ( not python\n")

    result = manage(root, "inspect", "--json")
    assert result.returncode == 0, result.stderr
    data = envelope(result)["data"]
    assert data["degraded"] is True
    assert data["errors"]
    # Still oriented: the app tree is on disk whatever the settings module says.
    assert [app["name"] for app in data["apps"]] == ["accounts"]


def test_showurls_reports_a_broken_project_as_a_failure(tmp_path):
    """Unlike inspect: an empty route list would read as 'this serves nothing'."""
    assert run_startproject("dead_api", str(tmp_path)) == 0
    root = tmp_path / "dead_api"
    settings = root / "dead_api" / "settings.py"
    settings.write_text(settings.read_text() + "\nthis is ( not python\n")

    result = manage(root, "showurls", "--json")
    assert result.returncode == 1
    data = envelope(result)["data"]
    assert data["error_code"] == "invalid_input"
    assert data["next_command"] == "python manage.py check"


def test_the_frontend_brief_is_pasteable(project):
    data = envelope(manage(project, "frontend-brief", "--json"))["data"]
    brief = data["brief"]

    # No unresolved placeholder reaches whoever pastes this.
    assert "@" not in brief.replace("@example.com", "")
    for expected in ("/api/v1/auth/login", "/api/v1/posts", "X-API-Version", "Bearer"):
        assert expected in brief, expected
    assert data["api_prefix"] == "/api/v1"
