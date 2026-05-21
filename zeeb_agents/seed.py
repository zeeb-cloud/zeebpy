"""Agent functions for generating database seed scripts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.code_gen import extract_field_names, extract_model_names
from zeeb_agents._utils.project import get_app_path, require_project_root

_SEED_HEADER = """\
\"\"\"Seed script for {app} app.

Run with:
    python seeds/{app}_seed.py
\"\"\"

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from {settings_module} import configure_from_settings  # noqa: E402  (adjust to your project)

"""

_SEED_FUNC_HEADER = """\
async def seed() -> None:
    \"\"\"Populate the database with {count} sample record(s) per model.\"\"\"
    from zeeb_orm import configure, setup_database
    configure(database={{"url": "sqlite+aiosqlite:///db.sqlite3"}})
    await setup_database()

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

_PLACEHOLDER_BY_TYPE: dict[str, str] = {
    "CharField": '"test_{name}_{i}"',
    "TextField": '"test_{name}_{i}"',
    "EmailField": '"test_{i}@example.com"',
    "URLField": '"https://example.com/{i}"',
    "SlugField": '"test-{name}-{i}"',
    "IntegerField": "{i}",
    "FloatField": "{i}.0",
    "DecimalField": '"{i}.00"',
    "BooleanField": "False",
    "DateTimeField": "None",
    "DateField": "None",
    "UUIDField": "None",
    "ForeignKey": "None",
    "OneToOneField": "None",
    "ManyToManyField": "None",
}

_DEFAULT_PLACEHOLDER = '"test_{i}"'


def _placeholder(field_name: str, field_type: str, index_var: str = "i") -> str:
    template = _PLACEHOLDER_BY_TYPE.get(field_type, _DEFAULT_PLACEHOLDER)
    return template.format(name=field_name, i=f"{{{index_var}}}")


def _build_seed_block(
    model_name: str,
    model_source: str,
    count: int,
) -> str:
    """Build the seed block for a single model."""
    import re

    # Extract field definitions: lines like `    fieldname = fields.SomeField(...)`
    field_pattern = re.compile(
        r"^\s{4}(\w+)\s*=\s*fields\.(\w+)\s*\(", re.MULTILINE
    )
    fields_list: list[str] = []
    for m in field_pattern.finditer(model_source):
        fname, ftype = m.group(1), m.group(2)
        if fname in ("id",):
            continue  # auto-generated
        placeholder = _placeholder(fname, ftype)
        fields_list.append(f"            {fname}=f{placeholder!r},")

    if not fields_list:
        fields_list = ["            # No auto-detectable fields — fill in manually"]

    fields_str = "\n".join(fields_list)
    return _SEED_MODEL_BLOCK.format(
        model=model_name,
        count=count,
        fields=fields_str,
    )


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
        project_root: Auto-detected if ``None``.

    Returns:
        ``AgentResult`` with ``path`` and ``models_seeded`` in ``data``.
    """
    try:
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
                raise ValueError(
                    f"No models found in {models_path}"
                    + (f" matching {models}" if models else "")
                )

            # Build import section
            imports = "\n".join(f"from apps.{app}.models import {m}" for m in selected)
            seed_func = _SEED_FUNC_HEADER.format(count=count)

            # Extract per-model source blocks for field introspection
            blocks = []
            for model_name in selected:
                # Extract the class block for this model
                pattern = re.compile(
                    rf"^(class {re.escape(model_name)}\b.*?)(?=\nclass |\Z)",
                    re.MULTILINE | re.DOTALL,
                )
                m = pattern.search(source)
                model_source = m.group(1) if m else ""
                blocks.append(_build_seed_block(model_name, model_source, count))

            seed_body = "".join(blocks)
            script = (
                f'"""Seed script for {app} app.\n\n'
                f"Run with:\n    python seeds/{app}_seed.py\n\"\"\"\n\n"
                "from __future__ import annotations\n\n"
                "import asyncio\n\n"
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
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
