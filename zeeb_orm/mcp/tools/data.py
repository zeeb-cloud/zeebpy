"""Data management tools for MCP."""

from __future__ import annotations

import asyncio
import json
import random
import string
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import find_project_root
from zeeb_orm.mcp.utils.code_gen import parse_model_file


@register_tool(
    name="zeeb_seed_data",
    description="Seed the database with sample data",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model to seed"},
            "data": {
                "type": "array",
                "description": "Explicit data records to create",
                "items": {"type": "object"}
            },
            "count": {
                "type": "integer",
                "description": "Number of records to auto-generate (if data not provided)"
            },
            "project_path": {"type": "string"}
        },
        "required": ["app_name", "model_name"]
    }
)
def zeeb_seed_data(
    app_name: str,
    model_name: str,
    data: list[dict[str, Any]] | None = None,
    count: int = 10,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Seed the database with sample data."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    # Get model info
    models_file = root / "apps" / app_name / "models.py"
    if not models_file.exists():
        return {"success": False, "error": f"models.py not found in {app_name}"}
    
    models = parse_model_file(models_file)
    if model_name not in models:
        return {"success": False, "error": f"Model '{model_name}' not found"}
    
    model_info = models[model_name]
    fields = model_info["fields"]
    
    # Generate data if not provided
    if data is None:
        data = []
        for i in range(count):
            record = _generate_sample_record(fields, i)
            data.append(record)
    
    # Create a seed script
    seed_script = _create_seed_script(app_name, model_name, data)
    seed_file = root / "seed_data.py"
    seed_file.write_text(seed_script)
    
    # Run the seed script
    import subprocess
    result = subprocess.run(
        ["python", "seed_data.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    
    # Clean up
    seed_file.unlink()
    
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr,
            "output": result.stdout,
        }
    
    return {
        "success": True,
        "records_created": len(data),
        "sample_data": data[:3] if len(data) > 3 else data,
        "output": result.stdout,
    }


def _generate_sample_record(fields: dict[str, Any], index: int) -> dict[str, Any]:
    """Generate a sample record based on field types."""
    record = {}
    
    for field_name, field_info in fields.items():
        field_type = field_info.get("type", "CharField")
        
        if field_type in ("CharField", "TextField"):
            if "email" in field_name.lower():
                record[field_name] = f"user{index}@example.com"
            elif "name" in field_name.lower():
                record[field_name] = f"Sample {field_name.title()} {index}"
            elif "title" in field_name.lower():
                record[field_name] = f"Sample Title {index}"
            else:
                record[field_name] = f"Sample {field_name} {index}"
        
        elif field_type == "IntegerField":
            record[field_name] = random.randint(1, 100)
        
        elif field_type == "FloatField":
            record[field_name] = round(random.uniform(1.0, 100.0), 2)
        
        elif field_type == "DecimalField":
            record[field_name] = round(random.uniform(1.0, 1000.0), 2)
        
        elif field_type == "BooleanField":
            record[field_name] = random.choice([True, False])
        
        elif field_type == "DateTimeField":
            if "auto_now" in field_info.get("args", ""):
                continue  # Skip auto fields
            days_ago = random.randint(0, 365)
            record[field_name] = (datetime.now() - timedelta(days=days_ago)).isoformat()
        
        elif field_type == "EmailField":
            record[field_name] = f"user{index}@example.com"
        
        elif field_type == "URLField":
            record[field_name] = f"https://example.com/{index}"
        
        elif field_type == "UUIDField":
            record[field_name] = str(uuid4())
        
        elif field_type in ("ForeignKey", "OneToOneField"):
            # Skip relationships - would need special handling
            continue
    
    return record


def _create_seed_script(app_name: str, model_name: str, data: list[dict[str, Any]]) -> str:
    """Create a Python script to seed data."""
    data_json = json.dumps(data, indent=2, default=str)
    
    return f'''#!/usr/bin/env python3
"""Seed script for {model_name}."""

import asyncio
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

async def main():
    from zeeb_orm import setup_database, close_all_connections
    
    # Import settings
    try:
        # Try to import from project settings
        import importlib
        for item in Path(".").iterdir():
            if item.is_dir() and (item / "settings.py").exists():
                settings = importlib.import_module(f"{{item.name}}.settings")
                break
        else:
            print("Could not find settings.py")
            return
        
        await setup_database(settings.DATABASE["url"])
    except Exception as e:
        print(f"Database setup error: {{e}}")
        return
    
    # Import model
    from apps.{app_name}.models import {model_name}
    
    # Seed data
    data = {data_json}
    
    created = 0
    for record in data:
        try:
            await {model_name}.objects.create(**record)
            created += 1
        except Exception as e:
            print(f"Error creating record: {{e}}")
    
    print(f"Created {{created}} {model_name} records")
    
    await close_all_connections()

if __name__ == "__main__":
    asyncio.run(main())
'''


@register_tool(
    name="zeeb_query_data",
    description="Query data from the database",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model to query"},
            "filter": {
                "type": "string",
                "description": "Q filter expression (e.g., 'Q(name__contains=\"test\")' or 'active=True')"
            },
            "order_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields to order by"
            },
            "limit": {"type": "integer", "description": "Max records to return"},
            "project_path": {"type": "string"}
        },
        "required": ["app_name", "model_name"]
    }
)
def zeeb_query_data(
    app_name: str,
    model_name: str,
    filter: str | None = None,
    order_by: list[str] | None = None,
    limit: int = 20,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Query data from the database."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    # Create a query script
    query_script = _create_query_script(app_name, model_name, filter, order_by, limit)
    query_file = root / "query_data.py"
    query_file.write_text(query_script)
    
    # Run the query script
    import subprocess
    result = subprocess.run(
        ["python", "query_data.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    
    # Clean up
    query_file.unlink()
    
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr,
        }
    
    # Parse output
    try:
        output = json.loads(result.stdout)
        return {
            "success": True,
            **output,
        }
    except json.JSONDecodeError:
        return {
            "success": True,
            "raw_output": result.stdout,
        }


def _create_query_script(
    app_name: str,
    model_name: str,
    filter_expr: str | None,
    order_by: list[str] | None,
    limit: int,
) -> str:
    """Create a Python script to query data."""
    filter_code = ""
    if filter_expr:
        if filter_expr.startswith("Q("):
            filter_code = f"qs = qs.filter({filter_expr})"
        else:
            filter_code = f"qs = qs.filter({filter_expr})"
    
    order_code = ""
    if order_by:
        order_str = ", ".join(f'"{o}"' for o in order_by)
        order_code = f"qs = qs.order_by({order_str})"
    
    return f'''#!/usr/bin/env python3
"""Query script for {model_name}."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

async def main():
    from zeeb_orm import setup_database, close_all_connections, Q
    
    # Import settings
    try:
        import importlib
        for item in Path(".").iterdir():
            if item.is_dir() and (item / "settings.py").exists():
                settings = importlib.import_module(f"{{item.name}}.settings")
                break
        else:
            print(json.dumps({{"error": "Could not find settings.py"}}))
            return
        
        await setup_database(settings.DATABASE["url"])
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        return
    
    from apps.{app_name}.models import {model_name}
    
    try:
        qs = {model_name}.objects
        {filter_code}
        {order_code}
        
        records = await qs.all()
        records = records[:{limit}]
        
        # Convert to dicts
        data = []
        for r in records:
            d = {{}}
            for field in r._meta.fields:
                val = getattr(r, field.name, None)
                if hasattr(val, 'isoformat'):
                    val = val.isoformat()
                elif hasattr(val, 'hex'):
                    val = str(val)
                d[field.name] = val
            data.append(d)
        
        count = await qs.count()
        
        print(json.dumps({{
            "count": count,
            "returned": len(data),
            "records": data,
        }}, default=str))
        
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
    
    await close_all_connections()

if __name__ == "__main__":
    asyncio.run(main())
'''
