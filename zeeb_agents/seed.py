"""Agent functions for generating database seed scripts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import extract_model_names, find_settings_file
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.project import get_app_path, require_project_root

_SEED_FUNC_HEADER = """\
async def seed() -> None:
    \"\"\"Populate the database with {count} sample record(s) per model.\"\"\"
    from zeeb_orm import setup_database
    await setup_database(DATABASE["url"])

"""

_SEED_MODEL_BLOCK = """\
    # --- {model} ---
    for i in range({count}):
        await {model}.objects.create(
{fields}
        )
    print(f"Seeded {count} {model} record(s)")

"""

_SEED_FOOTER = """\

if __name__ == "__main__":
    asyncio.run(seed())
"""

# Placeholder *code* (final Python source) per field type; ``{name}`` and
# ``{i}`` are substituted (``{i}`` stays a live loop variable in f-strings).
_PLACEHOLDER_BY_TYPE: dict[str, str] = {
    "CharField": 'f"test_{name}_{{i}}"',
    "TextField": 'f"test_{name}_{{i}}"',
    "EmailField": 'f"test_{{i}}@example.com"',
    "URLField": 'f"https://example.com/{{i}}"',
    "SlugField": 'f"test-{name}-{{i}}"',
    "IntegerField": "i",
    "SmallIntegerField": "i",
    "BigIntegerField": "i",
    "PositiveIntegerField": "i + 1",
    "PositiveSmallIntegerField": "i + 1",
    "PositiveBigIntegerField": "i + 1",
    "FloatField": "float(i)",
    "DecimalField": 'f"{{i}}.00"',
    "BooleanField": "bool(i % 2)",
    "JSONField": '{{"seed": i}}',
    "BinaryField": 'b"seed"',
    "DurationField": "timedelta(minutes=i)",
    "GenericIPAddressField": 'f"10.0.0.{{i}}"',
}

# Field types the script must not pass to ``create`` (auto-generated values).
_OMITTED_TYPES = frozenset(
    {"AutoField", "BigAutoField", "UUIDAutoField", "UUIDField", "ManyToManyField"}
)

_DEFAULT_PLACEHOLDER = 'f"test_{{i}}"'


def _extract_choices_value(params: str) -> str | None:
    """Return the first choice value from a ``choices=[...]`` kwarg, as code."""
    import ast

    idx = params.find("choices=")
    if idx == -1:
        return None
    start = params.find("[", idx)
    if start == -1:
        return None
    depth = 0
    for end in range(start, len(params)):
        depth += params[end] in "([{"
        depth -= params[end] in ")]}"
        if depth == 0:
            break
    else:
        return None
    try:
        choices = ast.literal_eval(params[start : end + 1])
        first = choices[0]
        value = first[0] if isinstance(first, (list, tuple)) else first
        return repr(value)
    except (ValueError, SyntaxError, IndexError, TypeError):
        return None


def _placeholder(field_name: str, field_type: str, params: str) -> str | None:
    """Return final placeholder code for a field, or ``None`` to omit it."""
    if field_type in _OMITTED_TYPES:
        return None
    if field_type in ("DateField", "TimeField", "DateTimeField"):
        if "auto_now" in params:  # auto_now / auto_now_add
            return None
        return "None"
    if field_type in ("ForeignKey", "OneToOneField"):
        if "null=True" in params:
            return None
        return "None,  # TODO: set a real FK — this field is not nullable"
    choice_value = _extract_choices_value(params)
    if choice_value is not None:
        return choice_value
    template = _PLACEHOLDER_BY_TYPE.get(field_type, _DEFAULT_PLACEHOLDER)
    return template.format(name=field_name)


def _build_seed_block(
    model_name: str,
    model_source: str,
    count: int,
) -> tuple[str, bool]:
    """Build the seed block for a single model.

    Returns ``(block, uses_timedelta)`` so the caller can add the
    ``timedelta`` import when needed.
    """
    import re

    # Field lines are single-line in generated code; capture params to EOL.
    field_pattern = re.compile(
        r"^\s{4}(\w+)\s*=\s*fields\.(\w+)\s*\((.*)$", re.MULTILINE
    )
    fields_list: list[str] = []
    uses_timedelta = False
    for m in field_pattern.finditer(model_source):
        fname, ftype, params = m.group(1), m.group(2), m.group(3)
        if fname in ("id",):
            continue  # auto-generated
        placeholder = _placeholder(fname, ftype, params)
        if placeholder is None:
            continue
        if "timedelta" in placeholder:
            uses_timedelta = True
        suffix = "" if "# TODO" in placeholder else ","  # TODO lines embed the comma
        fields_list.append(f"            {fname}={placeholder}{suffix}")

    if not fields_list:
        fields_list = ["            # No auto-detectable fields — fill in manually"]

    fields_str = "\n".join(fields_list)
    block = _SEED_MODEL_BLOCK.format(
        model=model_name,
        count=count,
        fields=fields_str,
    )
    return block, uses_timedelta


@agent_function
async def generate_seed_script(
    app: str,
    models: list[str] | None = None,
    count: int = 5,
    output_path: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Generate a Python seed script that populates the database with sample data.

    Reads model definitions from ``apps/{app}/models.py`` and writes an
    ``asyncio.run(seed())`` script to ``seeds/{app}_seed.py`` (or *output_path*).

    Args:
        app: App directory name.
        models: List of model names to seed.  Defaults to all models in the app.
        count: Number of records to create per model.
        output_path: Override the output file path.  Relative paths are resolved
            from the project root.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): script path relative to the project root
            (default ``seeds/<app>_seed.py``).
        app (str): the app name.
        models_seeded (list[str]): model class names the script covers.
        count (int): records the script creates per model.

    Notes:
        - This only *writes* the script — it does not execute it. Run it with
          ``python seeds/<app>_seed.py`` (from the project root) after
          migrations have been applied.
        - Fails (``success=False``) when ``models.py`` is missing or none of
          the requested *models* exist in it; the failure ``data`` carries
          ``error_code`` and close-match ``suggestions``.
        - Placeholders are typed (ints stay ints, booleans stay booleans,
          JSON fields get dict literals); fields with ``choices`` use the
          first choice value; auto-generated fields (PKs, ``auto_now``
          timestamps, M2M) and nullable relations are omitted, and non-null
          relations are emitted as ``None`` with a ``TODO`` comment.
    """
    root = require_project_root(project_root)
    models_path = get_app_path(app, root) / "models.py"
    if not models_path.exists():
        return AgentResult(
            success=False,
            message=f"models.py not found at {models_path}",
        )

    def _generate() -> tuple[str, list[str]]:
        import re

        source = models_path.read_text(encoding="utf-8")
        all_models = extract_model_names(source)
        selected = [m for m in all_models if not models or m in models]

        if not selected:
            missing = [m for m in (models or []) if m not in all_models]
            suggestions = [
                s for m in missing for s in close_matches(m, all_models)
            ]
            raise AgentError(
                f"No models found in {models_path}"
                + (f" matching {models}" if models else "")
                + (f". Models present: {', '.join(all_models)}" if all_models else ""),
                code="model_not_found",
                suggestions=suggestions or None,
                models=all_models,
            )

        # Build import section — DATABASE comes from the project settings
        # package (detected by directory) so the script hits the real DB.
        settings_path = find_settings_file(root)
        if settings_path is None:
            raise AgentError(
                "settings.py not found in project", code="file_not_found"
            )
        settings_import = f"from {settings_path.parent.name}.settings import DATABASE"
        imports = "\n".join(
            [settings_import]
            + [f"from apps.{app}.models import {m}" for m in selected]
        )
        seed_func = _SEED_FUNC_HEADER.format(count=count)

        # Extract per-model source blocks for field introspection
        blocks = []
        uses_timedelta = False
        for model_name in selected:
            # Extract the class block for this model
            pattern = re.compile(
                rf"^(class {re.escape(model_name)}\b.*?)(?=\nclass |\Z)",
                re.MULTILINE | re.DOTALL,
            )
            m = pattern.search(source)
            model_source = m.group(1) if m else ""
            block, block_timedelta = _build_seed_block(model_name, model_source, count)
            blocks.append(block)
            uses_timedelta = uses_timedelta or block_timedelta

        seed_body = "".join(blocks)
        extra_imports = "from datetime import timedelta\n\n" if uses_timedelta else ""
        script = (
            f'"""Seed script for {app} app.\n\n'
            f"Run with:\n    python seeds/{app}_seed.py\n\"\"\"\n\n"
            "from __future__ import annotations\n\n"
            "import asyncio\n"
            "import sys\n"
            "from pathlib import Path\n\n"
            "# Make the project root importable when run as a script\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n\n"
            f"{extra_imports}"
            f"{imports}\n\n\n"
            f"{seed_func}"
            f"{seed_body}"
            f"{_SEED_FOOTER}"
        )
        return script, selected

    script, seeded_models = await asyncio.to_thread(_generate)

    # Determine output path
    if output_path:
        out = root / output_path if not Path(output_path).is_absolute() else Path(output_path)
    else:
        seeds_dir = root / "seeds"
        seeds_dir.mkdir(exist_ok=True)
        out = seeds_dir / f"{app}_seed.py"

    out.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(out.write_text, script, "utf-8")

    rel = str(out.relative_to(root))
    return AgentResult(
        success=True,
        message=f"Seed script written to {rel} ({len(seeded_models)} model(s))",
        data={"path": rel, "app": app, "models_seeded": seeded_models, "count": count},
    )
