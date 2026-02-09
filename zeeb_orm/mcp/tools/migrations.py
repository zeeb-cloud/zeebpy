"""Migration management tools for MCP."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import find_project_root


@register_tool(
    name="zeeb_run_migrations",
    description="Run makemigrations and migrate to apply database changes",
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Project path (optional)"},
            "message": {"type": "string", "description": "Migration message/name"},
            "migrate_only": {"type": "boolean", "description": "Only run migrate, not makemigrations"},
            "makemigrations_only": {"type": "boolean", "description": "Only run makemigrations"}
        }
    }
)
def zeeb_run_migrations(
    project_path: str | None = None,
    message: str | None = None,
    migrate_only: bool = False,
    makemigrations_only: bool = False,
) -> dict[str, Any]:
    """Run migrations."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    results = {"success": True, "steps": []}
    
    # Check if migrations are initialized
    migrations_dir = root / "migrations"
    if not migrations_dir.exists():
        # Initialize migrations first
        init_result = subprocess.run(
            ["python", "manage.py", "init"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        results["steps"].append({
            "action": "init",
            "success": init_result.returncode == 0,
            "output": init_result.stdout,
            "error": init_result.stderr if init_result.returncode != 0 else None,
        })
        
        if init_result.returncode != 0:
            results["success"] = False
            return results
    
    # Run makemigrations
    if not migrate_only:
        cmd = ["python", "manage.py", "makemigrations"]
        if message:
            cmd.extend(["--name", message])
        
        make_result = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
        )
        results["steps"].append({
            "action": "makemigrations",
            "success": make_result.returncode == 0,
            "output": make_result.stdout,
            "error": make_result.stderr if make_result.returncode != 0 else None,
        })
        
        if make_result.returncode != 0:
            results["success"] = False
            return results
    
    # Run migrate
    if not makemigrations_only:
        migrate_result = subprocess.run(
            ["python", "manage.py", "migrate"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        results["steps"].append({
            "action": "migrate",
            "success": migrate_result.returncode == 0,
            "output": migrate_result.stdout,
            "error": migrate_result.stderr if migrate_result.returncode != 0 else None,
        })
        
        if migrate_result.returncode != 0:
            results["success"] = False
    
    return results


@register_tool(
    name="zeeb_migration_status",
    description="Check the status of migrations",
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Project path (optional)"}
        }
    }
)
def zeeb_migration_status(project_path: str | None = None) -> dict[str, Any]:
    """Check migration status."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    # Check if migrations are initialized
    migrations_dir = root / "migrations"
    if not migrations_dir.exists():
        return {
            "success": True,
            "initialized": False,
            "message": "Migrations not initialized. Run zeeb_run_migrations() to initialize.",
        }
    
    # Run showmigrations
    result = subprocess.run(
        ["python", "manage.py", "showmigrations"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr,
        }
    
    # Parse output
    output = result.stdout
    migrations = []
    
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        
        if line.startswith("[X]"):
            migrations.append({"revision": line[4:].strip(), "applied": True})
        elif line.startswith("[ ]"):
            migrations.append({"revision": line[4:].strip(), "applied": False})
        elif not line.startswith("Revision") and not line.startswith("-"):
            migrations.append({"revision": line, "applied": None})
    
    pending = [m for m in migrations if m.get("applied") is False]
    
    return {
        "success": True,
        "initialized": True,
        "migrations": migrations,
        "pending_count": len(pending),
        "has_pending": len(pending) > 0,
        "raw_output": output,
    }


@register_tool(
    name="zeeb_rollback_migration",
    description="Rollback migrations",
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Project path (optional)"},
            "steps": {"type": "integer", "description": "Number of migrations to rollback (default: 1)"},
            "target": {"type": "string", "description": "Target revision to rollback to"}
        }
    }
)
def zeeb_rollback_migration(
    project_path: str | None = None,
    steps: int = 1,
    target: str | None = None,
) -> dict[str, Any]:
    """Rollback migrations."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    cmd = ["python", "manage.py", "migrate", "--rollback"]
    
    if target:
        cmd.append(target)
    else:
        cmd.append(str(steps))
    
    result = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
    )
    
    return {
        "success": result.returncode == 0,
        "output": result.stdout,
        "error": result.stderr if result.returncode != 0 else None,
    }
