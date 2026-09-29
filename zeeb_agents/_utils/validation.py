"""Shared input validators for agent functions.

All validators raise :class:`~zeeb_agents._utils.errors.AgentError`, which the
``@agent_function`` decorator converts into a failure ``AgentResult`` with an
``error_code`` and (where possible) ``suggestions``.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path

from zeeb_agents._utils.errors import AgentError, close_matches, did_you_mean
from zeeb_agents._utils.project import get_app_path, list_apps

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ensure_identifier(name: object, kind: str = "name") -> str:
    """Validate that *name* is a usable Python identifier (and not a keyword)."""
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        raise AgentError(
            f"Invalid {kind} {name!r}: must be a valid Python identifier",
            code="invalid_identifier",
            value=name if isinstance(name, str) else repr(name),
        )
    return name


def ensure_identifiers(names: object, kind: str = "name") -> list[str]:
    """Validate every entry of a list of identifiers; return it as a list."""
    if isinstance(names, str) or not isinstance(names, (list, tuple)):
        raise AgentError(
            f"{kind} must be a list of names, got {type(names).__name__}",
            code="invalid_input",
        )
    return [ensure_identifier(name, kind) for name in names]


# ---------------------------------------------------------------------------
# Values that end up in generated code as part of a string literal. Rendering
# goes through ``render_py_literal`` regardless; these shapes exist because the
# same value is *also* used somewhere a literal cannot protect it — inside an
# f-string of a generated test, as a URL the router parses, as a dotted import.
# ---------------------------------------------------------------------------

_SEGMENT_CHARS = r"A-Za-z0-9._~\-"
_PATH_PARAM = r"\{[A-Za-z_][A-Za-z0-9_]*\}"

#: A route path: ``/``-rooted, unreserved characters plus ``{identifier}``
#: parameters (never a brace around anything else).
ROUTE_PATH_RE = re.compile(rf"^/(?:[{_SEGMENT_CHARS}:@+,=]|{_PATH_PARAM}|/)*$")
#: A router registration prefix: plain segments, optional edge slashes.
URL_PREFIX_RE = re.compile(rf"^/?[{_SEGMENT_CHARS}]+(?:/[{_SEGMENT_CHARS}]+)*/?$")
#: An ``@action`` ``url_path``: segments of unreserved characters and parameters.
ACTION_URL_PATH_RE = re.compile(
    rf"^(?:[{_SEGMENT_CHARS}]|{_PATH_PARAM})+(?:/(?:[{_SEGMENT_CHARS}]|{_PATH_PARAM})+)*$"
)
#: Where a router is mounted: empty, ``/``, or ``/``-rooted plain segments.
MOUNT_PREFIX_RE = re.compile(rf"^(?:/[{_SEGMENT_CHARS}]+)*/?$")
#: A dotted Python import path (``zeeb_api.middleware.CORSMiddleware``).
DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
#: A field reference in ``search_fields``/``ordering_fields``: a name, optionally
#: behind one of the search/ordering prefixes (``^``, ``=``, ``@``, ``$``, ``-``).
FIELD_REF_RE = re.compile(r"^[=^@$\-]?[A-Za-z_][A-Za-z0-9_]*$")


def _ensure_shape(value: object, pattern: re.Pattern[str], kind: str, shape: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise AgentError(
            f"Invalid {kind} {value!r}: must be {shape}",
            code="invalid_input",
            value=value if isinstance(value, str) else repr(value),
        )
    return value


def ensure_route_path(value: object, kind: str = "path") -> str:
    """A ``/``-rooted route path; parameters only as ``{identifier}``."""
    return _ensure_shape(
        value,
        ROUTE_PATH_RE,
        kind,
        "a '/'-rooted path of letters, digits, '-', '_', '.', '~', ':', '@', '+', ',', "
        "'=' and {name} parameters (e.g. '/items/{item_id}')",
    )


def ensure_url_prefix(value: object, kind: str = "url prefix") -> str:
    """A router registration prefix — plain URL segments (``posts``, ``blog/posts``)."""
    return _ensure_shape(
        value,
        URL_PREFIX_RE,
        kind,
        "URL segments of letters, digits, '-', '_', '.' and '~' (e.g. 'posts')",
    )


def ensure_action_url_path(value: object, kind: str = "url_path") -> str:
    """An ``@action`` ``url_path`` — segments plus ``{identifier}`` parameters."""
    return _ensure_shape(
        value,
        ACTION_URL_PATH_RE,
        kind,
        "URL segments of letters, digits, '-', '_', '.', '~' and {name} parameters, "
        "without a leading '/'",
    )


def ensure_mount_prefix(value: object, kind: str = "url prefix") -> str:
    """Where a router is mounted — ``""``, ``/`` or ``/``-rooted plain segments."""
    return _ensure_shape(
        value,
        MOUNT_PREFIX_RE,
        kind,
        "empty or a '/'-rooted path of letters, digits, '-', '_', '.' and '~' (e.g. '/auth')",
    )


def ensure_dotted_name(value: object, kind: str = "dotted path") -> str:
    """A dotted Python import path."""
    return _ensure_shape(value, DOTTED_NAME_RE, kind, "a dotted Python path (e.g. 'pkg.mod.Name')")


def ensure_field_refs(values: object, kind: str = "field") -> list[str]:
    """Field references for ``search_fields`` / ``ordering_fields``."""
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise AgentError(
            f"{kind} must be a list of field names, got {type(values).__name__}",
            code="invalid_input",
        )
    return [
        _ensure_shape(v, FIELD_REF_RE, kind, "a field name, optionally prefixed by ^ = @ $ or -")
        for v in values
    ]


def ensure_app_exists(app: str, project_root: Path) -> Path:
    """Return the app directory, failing with suggestions if it doesn't exist."""
    path = get_app_path(app, project_root)
    if path.is_dir():
        return path
    apps = list_apps(project_root)
    hint = did_you_mean(app, apps)
    if not hint:
        hint = (
            f" Existing apps: {', '.join(apps)}." if apps else " No apps exist yet."
        ) + " Create one with create_app()."
    raise AgentError(
        f"App '{app}' not found under apps/.{hint}",
        code="app_not_found",
        suggestions=close_matches(app, apps),
        apps=apps,
    )


def ensure_model_exists(content: str, model_name: str, where: str) -> None:
    """Fail with suggestions if *model_name* is not a Model subclass in *content*."""
    from zeeb_agents._utils.code_gen import class_exists, extract_model_names

    if class_exists(content, model_name):
        return
    names = extract_model_names(content)
    hint = did_you_mean(model_name, names)
    if not hint:
        hint = f" Models present: {', '.join(names) or '(none)'}."
    raise AgentError(
        f"Model '{model_name}' not found in {where}.{hint}",
        code="model_not_found",
        suggestions=close_matches(model_name, names),
        models=names,
    )


def validate_field_specs(fields: object) -> None:
    """Validate a list of field-spec dicts up front, reporting every problem at once.

    Delegates per-spec checks to
    :func:`~zeeb_agents._utils.field_types.validate_field_spec` so validation
    and rendering can never disagree.
    """
    from zeeb_agents._utils.field_types import validate_field_spec

    if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
        raise AgentError(
            "fields must be a list of dicts, e.g. "
            '[{"name": "title", "type": "CharField", "max_length": 200}]',
            code="invalid_field_spec",
        )
    problems: list[str] = []
    for i, spec in enumerate(fields):
        try:
            validate_field_spec(spec)
        except AgentError as exc:
            label = spec.get("name") if isinstance(spec.get("name"), str) else f"#{i}"
            problems.append(f"{label}: {exc}")
    if problems:
        raise AgentError(
            "Invalid field spec(s): " + "; ".join(problems),
            code="invalid_field_spec",
            problems=problems,
        )
