"""Stdlib-only ``.env`` loading and typed environment readers.

A generated project calls :func:`load_env` once at the top of its
``settings.py`` and then reads every value through the typed getters, so the
whole configuration is environment-driven without pulling in a third-party
dotenv dependency::

    from pathlib import Path
    from zeeb_api.conf.env import env_bool, env_list, env_str, load_env

    BASE_DIR = Path(__file__).resolve().parent.parent
    load_env(BASE_DIR / ".env")

    DEBUG = env_bool("DEBUG", True)
    SECRET_KEY = env_str("SECRET_KEY", "INSECURE-DEFAULT-KEY-CHANGE-IN-PRODUCTION")
    CORS_ALLOW_ORIGINS = env_list("CORS_ALLOW_ORIGINS", ["http://localhost:3000"])

Precedence is always **real process environment > .env file > default**, so a
container or CI runner can override anything a checked-out ``.env`` says.

Two properties make this safe for tooling that loads many projects in one
process (the CLI, the agent layer, the test suite):

- :func:`load_env` does **not** write to ``os.environ`` by default. The file
  values live in a private layer that the getters consult *after* the real
  environment. Loading project B therefore cannot make project A's
  ``DATABASE_URL`` look like a genuine environment variable to project B.
- :func:`load_env` **replaces** that layer rather than merging into it, so no
  key survives from a previously loaded project.

Nothing here raises. A malformed line is dropped, an unparsable value falls
back to its default with a warning. Callers such as
``zeeb_agents._utils.project.load_project_settings`` execute ``settings.py``
inside a bare ``except Exception: pass``, so an exception raised at import time
would silently degrade every answer the tooling gives instead of surfacing.
"""

from __future__ import annotations

import os
import re
import warnings
from pathlib import Path

__all__ = [
    "clear_env_cache",
    "env_bool",
    "env_int",
    "env_list",
    "env_oauth_providers",
    "env_str",
    "load_env",
    "loaded_env_path",
    "parse_env",
]

# Same grammar the agent layer validates ``.env`` keys against
# (``zeeb_agents._utils.validation.ENV_KEY_RE``).
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# How far up :func:`load_env` walks looking for a ``.env`` when given no path.
_MAX_SEARCH_DEPTH = 8

_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on", "t"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n", "off", "f", ""})

# The file layer. Deliberately *not* os.environ — see the module docstring.
_file_env: dict[str, str] = {}
_loaded_path: Path | None = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ``# comment`` from an unquoted value.

    Only a ``#`` preceded by whitespace starts a comment, so values that
    legitimately contain one (``SECRET_KEY=ab#cd``, a URL fragment) survive.
    """
    for idx, ch in enumerate(value):
        if ch == "#" and idx > 0 and value[idx - 1] in " \t":
            return value[:idx]
    return value


def _unquote(value: str) -> str:
    """Remove matching surrounding quotes and decode escapes inside ``"``."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = (
                inner.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return inner
    return _strip_inline_comment(value).rstrip()


def parse_env(text: str) -> dict[str, str]:
    """Parse ``.env`` *text* into a dict. Malformed lines are skipped."""
    result: dict[str, str] = {}
    for raw_line in text.lstrip("﻿").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not _ENV_KEY_RE.match(key):
            continue
        result[key] = _unquote(value.strip())
    return result


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _discover(start: Path) -> Path | None:
    """Walk up from *start* looking for a ``.env``.

    Stops at a directory holding ``manage.py`` — that is the project root, and
    if it has no ``.env`` then neither does any ancestor worth reading.
    """
    current = start if start.is_dir() else start.parent
    for _ in range(_MAX_SEARCH_DEPTH):
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        if (current / "manage.py").is_file():
            return None
        if current.parent == current:
            return None
        current = current.parent
    return None


def load_env(
    path: str | Path | None = None,
    *,
    override: bool = False,
    encoding: str = "utf-8",
) -> Path | None:
    """Load a ``.env`` file into the private environment layer.

    Args:
        path: A file, or a directory whose ``.env`` should be read. When
            ``None``, walks up from the current working directory looking for
            one (stopping at the project root, i.e. the directory holding
            ``manage.py``). A generated ``settings.py`` always passes an
            explicit path: it is executed out-of-process with an arbitrary
            working directory by the migration tooling, ``zeeb check``,
            ``createsuperuser`` and the agent layer, so a cwd-relative lookup
            would read the wrong project.
        override: Also copy the parsed values into ``os.environ``, for third
            party code that reads ``os.environ`` directly. Off by default
            because it leaks one project's configuration into the next one
            loaded in the same process.
        encoding: Text encoding of the file.

    Returns:
        The path that was read, or ``None`` when no ``.env`` was found. In the
        latter case the layer is *reset*, so values from a previously loaded
        project never linger.

    Notes:
        - Real environment variables always win over file values.
        - Never raises: an unreadable file emits a warning and resets the layer.
    """
    global _file_env, _loaded_path

    if path is None:
        env_path = _discover(Path.cwd())
    else:
        candidate = Path(path)
        env_path = candidate / ".env" if candidate.is_dir() else candidate

    if env_path is None or not env_path.is_file():
        _file_env = {}
        _loaded_path = None
        return None

    try:
        text = env_path.read_text(encoding=encoding)
    except OSError as exc:
        warnings.warn(f"Could not read {env_path}: {exc}", stacklevel=2)
        _file_env = {}
        _loaded_path = None
        return None

    # Replace, never merge: a stale key from a previously loaded project would
    # otherwise masquerade as this project's configuration.
    _file_env = parse_env(text)
    _loaded_path = env_path

    if override:
        os.environ.update(_file_env)

    return env_path


def loaded_env_path() -> Path | None:
    """Return the path of the currently loaded ``.env``, if any."""
    return _loaded_path


def clear_env_cache() -> None:
    """Reset the file layer. Test and tooling hook."""
    global _file_env, _loaded_path
    _file_env = {}
    _loaded_path = None


# ---------------------------------------------------------------------------
# Typed getters
# ---------------------------------------------------------------------------


def _raw(key: str) -> str | None:
    """Return the raw value for *key*: real environment, then file, else None.

    A key *present* in ``os.environ`` wins even when its value is empty —
    ``CORS_ALLOW_ORIGINS=`` in a container is a deliberate "no origins", not a
    request to fall back to the ``.env`` file.
    """
    if key in os.environ:
        return os.environ[key]
    return _file_env.get(key)


def env_str(key: str, default: str | None = None) -> str | None:
    """Return *key* as a string, or *default* when it is not set at all.

    An empty value is a value: ``JWT_ISSUER=`` yields ``""``, not the default.
    """
    raw = _raw(key)
    return default if raw is None else raw


def env_bool(key: str, default: bool = False) -> bool:
    """Return *key* as a boolean.

    Truthy: ``1 true yes y on t``. Falsy: ``0 false no n off f`` and empty.
    Anything else warns and yields *default*.
    """
    raw = _raw(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    warnings.warn(
        f"{key}={raw!r} is not a boolean (use true/false) — using {default!r}.",
        stacklevel=2,
    )
    return default


def env_int(key: str, default: int = 0) -> int:
    """Return *key* as an integer. Unparsable values warn and yield *default*."""
    raw = _raw(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        warnings.warn(
            f"{key}={raw!r} is not an integer — using {default!r}.", stacklevel=2
        )
        return default


def env_list(
    key: str,
    default: list[str] | None = None,
    separator: str = ",",
) -> list[str]:
    """Return *key* split on *separator*, with blanks dropped.

    ``""`` yields ``[]`` (an explicit "none"), a missing key yields a **copy**
    of *default* so a caller mutating the result cannot poison it.
    """
    raw = _raw(key)
    if raw is None:
        return list(default or [])
    return [part.strip() for part in raw.split(separator) if part.strip()]


def env_oauth_providers(
    names: tuple[str, ...] = ("google", "github", "azure"),
) -> dict[str, dict]:
    """Build an ``OAUTH_PROVIDERS`` mapping from per-provider credentials.

    A provider activates as soon as ``<NAME>_CLIENT_ID`` is present, e.g.
    ``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``. ``azure`` additionally
    reads ``AZURE_TENANT_ID`` (default ``"common"``). Returns ``{}`` when
    nothing is configured, which is what keeps the OAuth routes unmounted.

    Building the mapping here rather than with inline ``if`` blocks in
    ``settings.py`` also keeps provider name literals out of that file, which
    ``zeeb_agents.setup_oauth`` scans to decide whether a provider is already
    configured.
    """
    providers: dict[str, dict] = {}
    for name in names:
        prefix = name.upper()
        client_id = env_str(f"{prefix}_CLIENT_ID")
        if not client_id:
            continue
        config: dict = {"client_id": client_id}
        secret = env_str(f"{prefix}_CLIENT_SECRET")
        if secret:
            config["client_secret"] = secret
        if name == "azure":
            config["tenant"] = env_str("AZURE_TENANT_ID", "common")
        providers[name] = config
    return providers
