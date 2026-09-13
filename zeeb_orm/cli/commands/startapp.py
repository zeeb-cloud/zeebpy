"""startapp command - Create new app within a Zeeb project.

The templates and the naming helpers live in :mod:`zeeb_orm.scaffold.app` and
:mod:`zeeb_orm.scaffold.naming`, the single source of truth shared with the
agent layer. They are re-exported here because they are part of this module's
established import surface.
"""

from zeeb_orm.cli.output import fail, no_project, ok
from zeeb_orm.scaffold.app import (
    APP_INIT_PY,
    MODELS_PY,
    SERIALIZERS_PY,
    SLICE_MODELS_PY,
    SLICE_SERIALIZERS_PY,
    SLICE_TESTS_PY,
    SLICE_URLS_PY,
    SLICE_VIEWS_PY,
    TESTS_PY,
    URLS_PY,
    VIEWS_PY,
)
from zeeb_orm.scaffold.errors import ScaffoldError
from zeeb_orm.scaffold.harness import ensure_test_scaffold
from zeeb_orm.scaffold.naming import (
    find_project_root,
    pluralize,
    singularize,
    to_class_name,
    to_title,
)
from zeeb_orm.scaffold.wiring import ensure_app_urls_included, ensure_installed_app

__all__ = [
    "APP_INIT_PY",
    "MODELS_PY",
    "SERIALIZERS_PY",
    "TESTS_PY",
    "URLS_PY",
    "VIEWS_PY",
    "find_project_root",
    "run_startapp",
    "to_class_name",
    "to_title",
]


def run_startapp(
    name: str,
    wire: bool = True,
    *,
    model: str | None = None,
    json_output: bool = False,
) -> int:
    """Create a new app within a Zeeb project.

    Writes ``__init__.py``, ``models.py``, ``serializers.py``, ``views.py`` and
    ``urls.py`` under ``apps/<name>/``, plus ``tests/test_<name>.py`` — the test
    lives in ``tests/`` because that is where the shared fixtures from
    ``tests/conftest.py`` apply and what ``pytest.ini`` collects.

    Args:
        name: App directory name (a valid Python identifier).
        wire: When true (the default) also register the app so it is actually
            served — append ``"apps.<name>"`` to ``INSTALLED_APPS`` (without it
            ``makemigrations`` never sees the app's models) and include its
            router in the project ``urls.py`` (without it every endpoint the
            app registers 404s). Both edits are idempotent. Pass ``False`` to
            scaffold the files only.
        model: Name of a model to generate a complete working resource for —
            model, serializer, viewset, route registration and a test that
            passes. Without it the files carry the canonical example in their
            module docstring and no live code beyond one import.
    """
    if not name.isidentifier():
        return fail(
            f"'{name}' is not a valid Python identifier, so it cannot be a package name.",
            code="invalid_identifier",
            next_command="python manage.py startapp <valid_name>",
            json_output=json_output,
            name=name,
        )

    if model is not None and not (model.isidentifier() and model[0].isupper()):
        return fail(
            f"'{model}' is not a usable model name.",
            code="invalid_identifier",
            next_command=f"python manage.py startapp {name} --model Post",
            json_output=json_output,
            suggestions=["Use a PascalCase Python identifier, e.g. Post"],
            model=model,
        )

    project_root = find_project_root()
    if project_root is None:
        return no_project(f"startapp {name}", json_output=json_output)

    apps_dir = project_root / "apps"
    if not apps_dir.exists():
        return fail(
            "The 'apps/' directory is missing from the project root.",
            code="file_not_found",
            next_command=f"mkdir -p apps && touch apps/__init__.py && "
            f"python manage.py startapp {name}",
            json_output=json_output,
            searched_in=str(apps_dir),
        )

    app_path = apps_dir / name
    if app_path.exists():
        return fail(
            f"App '{name}' already exists.",
            code="already_exists",
            next_command=f"Edit apps/{name}/models.py, then python manage.py makemigrations",
            json_output=json_output,
            path=str(app_path),
        )

    if not json_output:
        print(f"Creating app '{name}' in {apps_dir}...")

    try:
        # Create directory structure
        app_path.mkdir()

        # Template context. The example model's name is singularized through
        # the shared helper: a plain rstrip("s") turns "address" into "addre"
        # and "class" into "clas", and those names reach a table definition.
        app_class = to_class_name(name)
        app_title = to_title(name)
        model_name = model or to_class_name(singularize(name))
        model_slug = model_name.lower()

        context = {
            "app_name": name,
            "app_class": app_class,
            "app_title": app_title,
            "model_name": model_name,
            "model_slug": model_slug,
            "table_name": f"{name}_{model_slug}",
            "route_prefix": pluralize(model_slug),
        }

        models_py, serializers_py, views_py, urls_py, tests_py = (
            (SLICE_MODELS_PY, SLICE_SERIALIZERS_PY, SLICE_VIEWS_PY, SLICE_URLS_PY, SLICE_TESTS_PY)
            if model
            else (MODELS_PY, SERIALIZERS_PY, VIEWS_PY, URLS_PY, TESTS_PY)
        )

        # Create files
        files = {
            "__init__.py": APP_INIT_PY.format(**context),
            "models.py": models_py.format(**context),
            "serializers.py": serializers_py.format(**context),
            "views.py": views_py.format(**context),
            "urls.py": urls_py.format(**context),
        }

        written: list[str] = []
        for filename, content in files.items():
            (app_path / filename).write_text(content)
            written.append(f"apps/{name}/{filename}")

        # Retrofit the shared harness onto projects scaffolded before it
        # existed. Nothing is overwritten, and this runs even with --no-wire:
        # it is scaffolding, not wiring.
        written += ensure_test_scaffold(project_root)

        # The app's test goes in tests/, not in apps/<name>/: a conftest.py
        # under tests/ does not apply to a module outside it, and pytest.ini
        # collects tests/ only. A test that cannot see its fixtures never runs.
        test_file = project_root / "tests" / f"test_{name}.py"
        if not test_file.exists():
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(tests_py.format(**context))
            written.append(f"tests/test_{name}.py")

        if not json_output:
            for path in written:
                print(f"  Created {path}")

    except Exception as exc:
        import shutil

        if app_path.exists():
            shutil.rmtree(app_path)
        return fail(
            f"Could not create app '{name}': {exc}",
            code="invalid_input",
            next_command=f"python manage.py startapp {name}",
            json_output=json_output,
        )

    # Wiring happens outside the block above: the app files are on disk and
    # valid, so a failure here must not delete them — it leaves the project in
    # a state the user (or a re-run) can repair.
    installed = urls_wired = False
    wiring_error: ScaffoldError | None = None
    if wire:
        try:
            installed = ensure_installed_app(project_root, name)
            urls_wired = ensure_app_urls_included(project_root, name)
            if not json_output:
                if installed:
                    print(f"  Registered apps.{name} in INSTALLED_APPS")
                if urls_wired:
                    print(f"  Included the {name} router in the project urls.py")
        except ScaffoldError as exc:
            wiring_error = exc

    data = {
        "name": name,
        "path": str(app_path),
        "model": model,
        "files": written,
        "installed_apps_updated": installed,
        "urls_wired": urls_wired,
    }

    if wiring_error is not None:
        return fail(
            f"App '{name}' was created, but could not be wired: {wiring_error}",
            code=wiring_error.code,
            next_command=(
                f"Add 'apps.{name}' to INSTALLED_APPS and include its router in the "
                f"project urls.py"
            ),
            json_output=json_output,
            state_changed=True,
            **data,
            **wiring_error.data,
        )

    if model:
        steps = [
            "python manage.py makemigrations",
            "python manage.py migrate",
            f"pytest tests/test_{name}.py -q",
            f"python manage.py runserver   # then GET /api/v1/{context['route_prefix']}",
        ]
    else:
        steps = [
            f"Define models in apps/{name}/models.py (the docstring shows the shape)",
            f"Create serializers in apps/{name}/serializers.py",
            f"Create viewsets in apps/{name}/views.py",
            f"Register them in apps/{name}/urls.py",
            "python manage.py makemigrations",
            "python manage.py migrate",
            "pytest -q",
        ]

    return ok(
        f"App '{name}' created at apps/{name}.",
        json_output=json_output,
        lines=(
            f"\nApp '{name}' created successfully!",
            "\nNext steps:",
            *(f"  {number}. {step}" for number, step in enumerate(steps, start=1)),
        ),
        next_steps=steps,
        **data,
    )
