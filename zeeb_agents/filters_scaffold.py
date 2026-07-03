"""Agent functions for scaffolding zeeb_api FilterSet classes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
)
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier

# Lookups supported by zeeb_api.filters.FilterSet (its LOOKUP_MAP keys).
FILTERSET_LOOKUPS = frozenset(
    {
        "exact",
        "iexact",
        "contains",
        "icontains",
        "in",
        "gt",
        "gte",
        "lt",
        "lte",
        "startswith",
        "istartswith",
        "endswith",
        "iendswith",
        "isnull",
    }
)

_FILTERS_HEADER = '''"""FilterSet classes for the {app} app."""

from zeeb_api.filters import FilterSet

'''


@agent_function
async def create_filterset(
    app: str,
    model_name: str,
    filter_fields: dict[str, list[str]],
    project_root: Path | None = None,
) -> AgentResult:
    """Create a ``FilterSet`` class in ``apps/<app>/filters.py``.

    The generated ``<ModelName>Filter`` enables declarative query-parameter
    filtering — e.g. ``{"price": ["gte", "lte"]}`` accepts
    ``?price__gte=10&price__lte=50``.  Attach it to a viewset with
    :func:`~zeeb_agents.viewsets.create_viewset` /
    :func:`~zeeb_agents.viewsets.update_viewset` via
    ``filterset="<ModelName>Filter"``.

    Args:
        app: App directory name.
        model_name: The model to filter (must exist in the app's
            ``models.py``).
        filter_fields: Mapping of field name → list of lookups, e.g.
            ``{"status": ["exact", "in"], "price": ["gte", "lte"]}``.
            Valid lookups: exact, iexact, contains, icontains, in, gt, gte,
            lt, lte, startswith, istartswith, endswith, iendswith, isnull.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app name
        model (str): the model name
        filterset (str): the generated class name (``"<ModelName>Filter"``)
        fields (list[str]): the filterable field names

    Notes:
        - ``filters.py`` is created if missing.
        - An invalid lookup fails with ``error_code="invalid_input"``,
          naming the field and offering close-match ``suggestions``.
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``already_exists``, …).
    """
    ensure_identifier(model_name, "model name")
    if not isinstance(filter_fields, dict) or not filter_fields:
        return AgentResult(
            success=False,
            message=(
                "filter_fields must be a non-empty dict of field name → lookups, "
                'e.g. {"price": ["gte", "lte"]}'
            ),
            data={"error_code": "invalid_input"},
        )
    for field, lookups in filter_fields.items():
        ensure_identifier(field, "filter field name")
        if not isinstance(lookups, list) or not lookups:
            raise AgentError(
                f"filter_fields['{field}'] must be a non-empty list of lookups",
                code="invalid_input",
            )
        for lookup in lookups:
            if lookup not in FILTERSET_LOOKUPS:
                suggestions = close_matches(str(lookup), sorted(FILTERSET_LOOKUPS))
                hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                raise AgentError(
                    f"Invalid lookup '{lookup}' for field '{field}'.{hint} "
                    f"Valid lookups: {sorted(FILTERSET_LOOKUPS)}",
                    code="invalid_input",
                    suggestions=suggestions,
                )

    root = project_root
    app_path = ensure_app_exists(app, root)
    filters_path = app_path / "filters.py"
    class_name = f"{model_name}Filter"

    def _write() -> None:
        if not filters_path.exists():
            filters_path.write_text(
                _FILTERS_HEADER.format(app=app), encoding="utf-8"
            )
        content = filters_path.read_text(encoding="utf-8")
        if class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' already exists in {filters_path}",
                code="already_exists",
                filterset=class_name,
            )
        field_lines = []
        for field, lookups in filter_fields.items():
            lookups_repr = ", ".join(f'"{lk}"' for lk in lookups)
            field_lines.append(f'            "{field}": [{lookups_repr}],')
        class_code = "\n".join(
            [
                f"class {class_name}(FilterSet):",
                "    class Meta:",
                f"        model = {model_name}",
                "        fields = {",
                *field_lines,
                "        }",
            ]
        )
        ensure_import(filters_path, "from zeeb_api.filters import FilterSet")
        ensure_import(filters_path, f"from .models import {model_name}")
        append_block(filters_path, class_code)

    await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"'{class_name}' created in apps/{app}/filters.py",
        data={
            "app": app,
            "model": model_name,
            "filterset": class_name,
            "fields": list(filter_fields),
        },
    )
