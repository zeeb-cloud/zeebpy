"""Project discovery and settings helpers."""

from __future__ import annotations

import importlib.util
import keyword
import logging
import re
import sys
from pathlib import Path
from typing import Any

from zeeb_agents._utils.errors import AgentError

logger = logging.getLogger("zeeb_agents")


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``manage.py``."""
    current = (start or Path.cwd()).resolve()
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    return None


def require_project_root(project_root: Path | None) -> Path:
    """Return *project_root* if given, else auto-detect; raise RuntimeError on failure."""
    root = project_root or find_project_root()
    if root is None:
        raise RuntimeError(
            "Could not find project root (no manage.py found). "
            "Pass project_root explicitly or run from a Zeeb project directory."
        )
    return root


DEFAULT_FRAMEWORK = "zeebpy"

#: A framework key as written into ``[tool.zeeb]`` (``zeebpy``, ``@acme/flask``):
#: nothing that could end the TOML string it is written into.
_FRAMEWORK_KEY_RE = re.compile(r"^[A-Za-z0-9@._/\-]+$")


def ensure_framework_key(framework: object) -> str:
    """Validate a framework key before it is written into ``pyproject.toml``."""
    if not isinstance(framework, str) or not _FRAMEWORK_KEY_RE.match(framework):
        raise AgentError(
            f"Invalid framework key {framework!r}: letters, digits and '@._/-' only",
            code="invalid_input",
        )
    return framework


def write_framework_marker(project_root: Path, framework: str) -> None:
    """Record ``framework`` under ``[tool.zeeb]`` in the project's pyproject.toml.

    Creates ``pyproject.toml`` if absent; otherwise ensures a ``[tool.zeeb]``
    section with a ``framework`` key (a light, dependency-free text edit — no
    TOML writer needed for this one key).
    """
    ensure_framework_key(framework)
    path = project_root / "pyproject.toml"
    marker = f'[tool.zeeb]\nframework = "{framework}"\n'
    if not path.exists():
        path.write_text(marker, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if "[tool.zeeb]" not in text:
        sep = "" if text.endswith("\n") else "\n"
        path.write_text(f"{text}{sep}\n{marker}", encoding="utf-8")


def detect_framework(project_root: Path | None) -> str:
    """Return the project's framework id, defaulting to ``"zeebpy"``.

    Reads ``[tool.zeeb] framework`` from the project's ``pyproject.toml`` when
    present; otherwise falls back to :data:`DEFAULT_FRAMEWORK`. Never raises.
    """
    if project_root is None:
        return DEFAULT_FRAMEWORK
    path = project_root / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_FRAMEWORK
    m = re.search(
        r"\[tool\.zeeb\][^\[]*?\bframework\s*=\s*[\"']([^\"']+)[\"']",
        text,
        re.DOTALL,
    )
    return m.group(1) if m else DEFAULT_FRAMEWORK


class ProjectSettings(dict):
    """A project's top-level settings, plus how loading them went.

    A plain ``dict`` to every existing caller. ``load_error`` is ``None`` when
    ``settings.py`` executed cleanly (or there is none), else
    ``"<ExceptionType>: <message>"`` — in which case the dict holds only the
    defaults and must not be mistaken for the project's configuration.
    ``source`` is the settings file that was executed, if any.
    """

    load_error: str | None = None
    source: Path | None = None


def load_project_settings(project_root: Path) -> ProjectSettings:
    """Load the project's ``settings.py`` in-process and return its UPPERCASE names.

    The module is executed with the project root prepended to ``sys.path``;
    ``sys.path`` is restored exactly afterwards, whatever the module did to it.

    A settings module that raises used to be swallowed, silently leaving the
    defaults — a sqlite ``db.sqlite3`` among them — as if they were the
    project's configuration, so database tools ran against the wrong
    database. The failure is now recorded on the result (``load_error``) and
    logged; callers that act on the configuration use
    :func:`require_loaded_settings`, which turns it into an error.
    """
    settings = ProjectSettings(
        {
            "DATABASE": {"url": "sqlite+aiosqlite:///db.sqlite3"},
            "INSTALLED_APPS": [],
        }
    )
    for item in sorted(project_root.iterdir()):
        if item.is_dir() and (item / "settings.py").exists():
            settings.source = item / "settings.py"
            spec = importlib.util.spec_from_file_location("_zeeb_settings", item / "settings.py")
            if spec and spec.loader:
                saved_path = list(sys.path)
                sys.path.insert(0, str(project_root))
                try:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
                    # Capture every top-level setting by Django convention:
                    # UPPERCASE, non-dunder module attributes (DATABASE,
                    # INSTALLED_APPS, CORS_*, SECRET_KEY, DEBUG, …).
                    for attr in dir(module):
                        if attr.isupper() and not attr.startswith("_"):
                            settings[attr] = getattr(module, attr)
                except BaseException as exc:  # noqa: BLE001 — incl. SystemExit from a settings module
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    settings.load_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "Could not load %s: %s", settings.source, settings.load_error
                    )
                finally:
                    sys.path[:] = saved_path
            break
    return settings


def settings_error_message(settings: dict[str, Any]) -> str | None:
    """A caller-facing sentence when *settings* failed to load, else ``None``."""
    error = getattr(settings, "load_error", None)
    if not error:
        return None
    source = getattr(settings, "source", None)
    where = f"{source.parent.name}/settings.py" if source else "settings.py"
    return f"{where} could not be loaded ({error}); fix it before relying on its values."


def ensure_settings_loaded(settings: dict[str, Any]) -> dict[str, Any]:
    """Return *settings*, or fail with ``settings_error`` when they did not load.

    For callers that *act* on the configuration — above all on ``DATABASE`` —
    where the defaults would silently point at a different database.
    """
    message = settings_error_message(settings)
    if message:
        raise AgentError(
            message,
            code="settings_error",
            error=getattr(settings, "load_error", None),
        )
    return settings


def require_loaded_settings(project_root: Path) -> ProjectSettings:
    """:func:`load_project_settings`, failing with ``settings_error`` on a load error."""
    settings = load_project_settings(project_root)
    ensure_settings_loaded(settings)
    return settings


def project_database_url(project_root: Path) -> str:
    """The project's database URL (sqlite paths anchored), or ``settings_error``."""
    return resolve_db_url(require_loaded_settings(project_root), project_root)


def resolve_db_url(settings: dict[str, Any], project_root: Path) -> str:
    """Return the settings DATABASE url with relative sqlite paths anchored
    at *project_root*.

    zeeb_agents operates on target projects by path and must not depend on
    the process CWD — but a relative sqlite URL (``sqlite:///db.sqlite3``)
    resolves against the CWD when passed to SQLAlchemy.  Absolute URLs and
    ``:memory:`` are returned unchanged.
    """
    url: str = settings.get("DATABASE", {}).get("url", "sqlite+aiosqlite:///db.sqlite3")
    m = re.match(r"^(sqlite(?:\+\w+)?)://(/?)(?!/)(.*)$", url)
    if m:
        driver, _slash, rel = m.groups()
        if rel and rel != ":memory:" and not rel.startswith("/"):
            return f"{driver}:///{(project_root / rel).resolve()}"
    return url


def list_apps(project_root: Path) -> list[str]:
    """Return app directory names found under ``apps/``."""
    apps_dir = project_root / "apps"
    if not apps_dir.exists():
        return []
    return [
        d.name
        for d in sorted(apps_dir.iterdir())
        if d.is_dir() and not d.name.startswith("_")
    ]


def get_app_path(app_name: str, project_root: Path) -> Path:
    """Return the path to an app directory (apps/<app_name>).

    The name is validated here, once, for every caller: an app is a Python
    package, so its name must be an identifier — which is also what keeps
    ``..``, ``/`` or an absolute path from turning ``apps/<name>`` into a path
    outside the project (``delete_app("..")`` used to remove the project).
    Raises :class:`AgentError` ``invalid_identifier`` otherwise.
    """
    if (
        not isinstance(app_name, str)
        or not app_name.isidentifier()
        or keyword.iskeyword(app_name)
    ):
        raise AgentError(
            f"Invalid app name {app_name!r}: must be a valid Python identifier",
            code="invalid_identifier",
            value=app_name if isinstance(app_name, str) else repr(app_name),
        )
    return project_root / "apps" / app_name


def to_class_name(name: str) -> str:
    """Convert ``snake_case`` → ``PascalCase``."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))


def to_table_name(app_name: str, model_name: str) -> str:
    """Return a sensible default table name: ``<app>_<model_lower>``."""
    return f"{app_name}_{model_name.lower()}"
