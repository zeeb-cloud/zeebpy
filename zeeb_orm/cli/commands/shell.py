"""shell command - Interactive Python shell with project context."""

import os
import sys
from pathlib import Path


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()

    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent

    return None


def run_shell(use_ipython: bool) -> int:
    """Start interactive Python shell with project context."""
    project_root = find_project_root()

    # Setup environment
    if project_root:
        sys.path.insert(0, str(project_root))
        os.chdir(project_root)
        print(f"Project: {project_root.name}")

    # Build banner and namespace
    banner = """
Zeeb ORM Interactive Shell
==========================
Available imports:
  - zeeb_orm: Model, fields, Q, F, configure, setup_database
  - asyncio: For running async code

Use 'await' syntax or asyncio.run() for async operations.
Example:
  >>> users = await User.objects.all()
  >>> user = await User.objects.create(name="Alice")
"""

    namespace: dict = {}

    # Pre-import common modules
    try:
        import asyncio
        namespace["asyncio"] = asyncio

        from zeeb_orm import (
            F,
            Model,
            Q,
            close_all_connections,
            configure,
            fields,
            setup_database,
        )
        namespace.update({
            "Model": Model,
            "fields": fields,
            "Q": Q,
            "F": F,
            "configure": configure,
            "setup_database": setup_database,
            "close_all_connections": close_all_connections,
        })

        # Try to load project models
        if project_root:
            try:
                # Find and import settings
                for item in project_root.iterdir():
                    if item.is_dir() and (item / "settings.py").exists():
                        settings_module = item.name
                        exec(f"from {settings_module} import settings", namespace)
                        break

                # Import models from installed apps
                apps_dir = project_root / "apps"
                if apps_dir.exists():
                    for app in apps_dir.iterdir():
                        if app.is_dir() and (app / "models.py").exists():
                            try:
                                exec(f"from apps.{app.name}.models import *", namespace)
                                print(f"  Loaded models from apps.{app.name}")
                            except Exception as e:
                                print(f"  Warning: Could not load apps.{app.name}.models: {e}")
            except Exception as e:
                print(f"  Warning: Could not load project modules: {e}")

    except ImportError as e:
        print(f"Warning: Could not import zeeb_orm: {e}")

    # Try IPython first
    if use_ipython:
        try:
            from IPython import embed
            print(banner)
            embed(user_ns=namespace, colors="neutral")
            return 0
        except ImportError:
            print("IPython not available, falling back to standard shell")

    # Fall back to standard asyncio-aware shell
    try:
        # Python 3.8+ has asyncio REPL support
        import asyncio

        # Create a custom REPL with async support
        print(banner)

        try:
            # Try to use ptpython if available
            import nest_asyncio
            from ptpython.repl import embed as pt_embed
            nest_asyncio.apply()
            pt_embed(globals=namespace, locals=namespace)
            return 0
        except ImportError:
            pass

        # Standard Python shell with exec for async
        import code
        import readline  # noqa: F401  (imported for its line-editing side effect)

        # Create an async-aware interactive console
        class AsyncConsole(code.InteractiveConsole):
            def runsource(self, source, filename="<input>", symbol="single"):
                # Check if it's an await expression
                if source.strip().startswith("await "):
                    # Wrap in async function and run
                    async_source = f"""
async def __async_exec__():
    return {source.strip()}
__result__ = asyncio.get_event_loop().run_until_complete(__async_exec__())
"""
                    try:
                        exec(compile(async_source, filename, "exec"), self.locals)
                        result = self.locals.get("__result__")
                        if result is not None:
                            print(repr(result))
                        return False
                    except Exception:
                        self.showtraceback()
                        return False

                return super().runsource(source, filename, symbol)

        console = AsyncConsole(locals=namespace)
        console.interact(banner="", exitmsg="Goodbye!")
        return 0

    except Exception as e:
        print(f"Error starting shell: {e}")
        return 1
