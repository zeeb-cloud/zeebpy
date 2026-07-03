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
