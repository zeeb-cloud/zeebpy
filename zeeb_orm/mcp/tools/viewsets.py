"""ViewSet management tools for MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import find_project_root, to_snake_case
from zeeb_orm.mcp.utils.code_gen import (
    generate_viewset_code,
    generate_url_registration,
    parse_model_file,
)


@register_tool(
    name="zeeb_create_viewset",
    description="Create a ViewSet for a model with CRUD operations",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name"},
            "viewset_name": {"type": "string", "description": "Custom viewset name (optional)"},
            "serializer_name": {"type": "string", "description": "Serializer to use (optional)"},
            "actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Actions to include: list, create, retrieve, update, delete, query"
            },
            "permission_classes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Permission classes: AllowAny, IsAuthenticated, IsAdminUser, IsAuthenticatedOrReadOnly"
            },
            "url_prefix": {"type": "string", "description": "URL prefix for this viewset"},
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name"]
    }
)
def zeeb_create_viewset(
    app_name: str,
    model_name: str,
    viewset_name: str | None = None,
    serializer_name: str | None = None,
    actions: list[str] | None = None,
    permission_classes: list[str] | None = None,
    url_prefix: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Create a ViewSet for a model."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    app_path = root / "apps" / app_name
    if not app_path.exists():
        return {"success": False, "error": f"App '{app_name}' not found"}
    
    # Generate viewset code
    viewset_code = generate_viewset_code(
        model_name=model_name,
        viewset_name=viewset_name,
        serializer_name=serializer_name,
        actions=actions,
        permission_classes=permission_classes,
    )
    
    # Write to views.py
    views_file = app_path / "views.py"
    current_content = views_file.read_text() if views_file.exists() else ""
    
    vs_name = viewset_name or f"{model_name}ViewSet"
    ser_name = serializer_name or f"{model_name}Serializer"
    
    # Build imports
    imports = [
        "from zeeb_api import viewsets, permissions",
        f"from .models import {model_name}",
        f"from .serializers import {ser_name}",
    ]
    
    # Add missing imports
    imports_to_add = [imp for imp in imports if imp not in current_content]
    
    if current_content.strip():
        if imports_to_add:
            lines = current_content.split("\n")
            last_import_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("from ") or line.startswith("import "):
                    last_import_idx = i
            
            for imp in imports_to_add:
                lines.insert(last_import_idx + 1, imp)
                last_import_idx += 1
            
            current_content = "\n".join(lines)
        
        new_content = current_content.rstrip() + "\n\n\n" + viewset_code + "\n"
    else:
        new_content = f'"""{app_name} views."""\n\n'
        new_content += "\n".join(imports) + "\n\n\n"
        new_content += viewset_code + "\n"
    
    views_file.write_text(new_content)
    
    # Update urls.py to register the viewset
    urls_file = app_path / "urls.py"
    url_registration = generate_url_registration(
        app_name=app_name,
        model_name=model_name,
        viewset_name=vs_name,
        url_prefix=url_prefix,
    )
    
    if urls_file.exists():
        urls_content = urls_file.read_text()
        
        # Add import if needed
        import_line = f"from .views import {vs_name}"
        if import_line not in urls_content:
            # Add after other imports
            lines = urls_content.split("\n")
            last_import_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("from ") or line.startswith("import "):
                    last_import_idx = i
            lines.insert(last_import_idx + 1, import_line)
            urls_content = "\n".join(lines)
        
        # Add registration if not present
        if url_registration not in urls_content:
            # Find router.register section or add before end
            if "router.register" in urls_content:
                # Add after last registration
                import re
                last_register = list(re.finditer(r'router\.register\([^)]+\)', urls_content))
                if last_register:
                    pos = last_register[-1].end()
                    urls_content = urls_content[:pos] + "\n" + url_registration + urls_content[pos:]
            else:
                # Add after router = line
                urls_content = urls_content.replace(
                    "router = DefaultRouter()",
                    f"router = DefaultRouter()\n{url_registration}"
                )
        
        urls_file.write_text(urls_content)
    
    prefix = url_prefix or to_snake_case(model_name) + "s"
    
    return {
        "success": True,
        "viewset_name": vs_name,
        "model_name": model_name,
        "views_file": str(views_file),
        "urls_file": str(urls_file),
        "viewset_code": viewset_code,
        "url_registration": url_registration,
        "endpoints": [
            f"POST /{prefix}/query/ - Query with Q filters",
            f"GET /{prefix}/ - List all",
            f"POST /{prefix}/ - Create",
            f"GET /{prefix}/{{id}}/ - Retrieve",
            f"PUT /{prefix}/{{id}}/ - Update",
            f"PATCH /{prefix}/{{id}}/ - Partial update",
            f"DELETE /{prefix}/{{id}}/ - Delete",
        ],
        "next_steps": [
            f"Include router in project urls.py",
            "Run 'zeeb-manage runserver' to test",
        ]
    }


@register_tool(
    name="zeeb_add_viewset_action",
    description="Add a custom action to an existing viewset",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "viewset_name": {"type": "string", "description": "ViewSet class name"},
            "action_name": {"type": "string", "description": "Action method name"},
            "methods": {
                "type": "array",
                "items": {"type": "string"},
                "description": "HTTP methods (GET, POST, etc.)"
            },
            "detail": {
                "type": "boolean",
                "description": "True for /items/{id}/action/, False for /items/action/"
            },
            "action_code": {
                "type": "string",
                "description": "Method body code"
            },
            "project_path": {"type": "string"}
        },
        "required": ["app_name", "viewset_name", "action_name", "action_code"]
    }
)
def zeeb_add_viewset_action(
    app_name: str,
    viewset_name: str,
    action_name: str,
    action_code: str,
    methods: list[str] | None = None,
    detail: bool = False,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Add a custom action to a viewset."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    views_file = root / "apps" / app_name / "views.py"
    if not views_file.exists():
        return {"success": False, "error": f"views.py not found in {app_name}"}
    
    content = views_file.read_text()
    
    # Find the viewset class
    import re
    pattern = rf'(class {viewset_name}\([^)]+\):.*?)(\n(?=class )|$)'
    match = re.search(pattern, content, re.DOTALL)
    
    if not match:
        return {"success": False, "error": f"ViewSet '{viewset_name}' not found"}
    
    # Build the action decorator and method
    methods_list = methods or ["GET"]
    methods_str = ", ".join(f'"{m.lower()}"' for m in methods_list)
    detail_str = "True" if detail else "False"
    
    action_method = f'''
    @viewsets.action(detail={detail_str}, methods=[{methods_str}])
    async def {action_name}(self, request, pk=None):
{_indent_code(action_code, 8)}
'''
    
    # Insert before the end of the class
    class_content = match.group(1)
    new_class_content = class_content.rstrip() + action_method
    
    new_content = content[:match.start()] + new_class_content + match.group(2) + content[match.end():]
    
    views_file.write_text(new_content)
    
    return {
        "success": True,
        "viewset_name": viewset_name,
        "action_name": action_name,
        "methods": methods_list,
        "detail": detail,
        "endpoint": f"/{action_name}/" if not detail else f"/{{id}}/{action_name}/",
    }


def _indent_code(code: str, spaces: int) -> str:
    """Indent code by a number of spaces."""
    indent = " " * spaces
    lines = code.strip().split("\n")
    return "\n".join(indent + line if line.strip() else line for line in lines)


@register_tool(
    name="zeeb_generate_crud",
    description="Generate complete CRUD setup: model, serializer, viewset, and URLs",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name"},
            "fields": {
                "type": "array",
                "description": "Field definitions for the model",
                "items": {"type": "object"}
            },
            "permission_classes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Permission classes for the viewset"
            },
            "include_tests": {
                "type": "boolean",
                "description": "Also generate basic tests"
            },
            "project_path": {"type": "string"}
        },
        "required": ["app_name", "model_name", "fields"]
    }
)
def zeeb_generate_crud(
    app_name: str,
    model_name: str,
    fields: list[dict[str, Any]],
    permission_classes: list[str] | None = None,
    include_tests: bool = False,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Generate complete CRUD setup for a model."""
    from zeeb_orm.mcp.tools.models import zeeb_create_model
    from zeeb_orm.mcp.tools.serializers import zeeb_create_serializer
    
    results = {
        "success": True,
        "steps": [],
    }
    
    # 1. Create model
    model_result = zeeb_create_model(
        app_name=app_name,
        model_name=model_name,
        fields=fields,
        project_path=project_path,
    )
    results["steps"].append({"action": "create_model", "result": model_result})
    
    if not model_result.get("success"):
        results["success"] = False
        return results
    
    # 2. Create serializer
    serializer_result = zeeb_create_serializer(
        app_name=app_name,
        model_name=model_name,
        project_path=project_path,
    )
    results["steps"].append({"action": "create_serializer", "result": serializer_result})
    
    if not serializer_result.get("success"):
        results["success"] = False
        return results
    
    # 3. Create viewset
    viewset_result = zeeb_create_viewset(
        app_name=app_name,
        model_name=model_name,
        permission_classes=permission_classes,
        project_path=project_path,
    )
    results["steps"].append({"action": "create_viewset", "result": viewset_result})
    
    if not viewset_result.get("success"):
        results["success"] = False
        return results
    
    # 4. Generate tests if requested
    if include_tests:
        test_result = _generate_basic_tests(app_name, model_name, project_path)
        results["steps"].append({"action": "create_tests", "result": test_result})
    
    results["summary"] = {
        "model": model_name,
        "serializer": f"{model_name}Serializer",
        "viewset": f"{model_name}ViewSet",
        "endpoints": viewset_result.get("endpoints", []),
    }
    
    results["next_steps"] = [
        "Run 'zeeb-manage makemigrations'",
        "Run 'zeeb-manage migrate'",
        "Include app router in project urls.py",
        "Run 'zeeb-manage runserver'",
    ]
    
    return results


def _generate_basic_tests(app_name: str, model_name: str, project_path: str | None) -> dict[str, Any]:
    """Generate basic tests for a model."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    tests_file = root / "apps" / app_name / "tests.py"
    
    test_code = f'''
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_{to_snake_case(model_name)}(client: AsyncClient):
    """Test creating a {model_name}."""
    response = await client.post(
        "/api/v1/{to_snake_case(model_name)}s/",
        json={{"name": "Test"}}
    )
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_list_{to_snake_case(model_name)}s(client: AsyncClient):
    """Test listing {model_name}s."""
    response = await client.get("/api/v1/{to_snake_case(model_name)}s/")
    assert response.status_code == 200
'''
    
    current_content = tests_file.read_text() if tests_file.exists() else ""
    new_content = current_content.rstrip() + "\n" + test_code
    tests_file.write_text(new_content)
    
    return {"success": True, "file": str(tests_file)}


@register_tool(
    name="zeeb_list_endpoints",
    description="List all API endpoints in the project",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Filter by app (optional)"},
            "project_path": {"type": "string"}
        }
    }
)
def zeeb_list_endpoints(
    app_name: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """List all API endpoints in the project."""
    from zeeb_orm.mcp.utils.project_utils import list_apps
    
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    apps = [app_name] if app_name else list_apps(root)
    endpoints = {}
    
    for app in apps:
        urls_file = root / "apps" / app / "urls.py"
        if not urls_file.exists():
            continue
        
        content = urls_file.read_text()
        
        # Extract router.register calls
        import re
        registrations = re.findall(
            r'router\.register\s*\(\s*["\']([^"\']+)["\']',
            content
        )
        
        app_endpoints = []
        for prefix in registrations:
            app_endpoints.extend([
                {"method": "POST", "path": f"/{prefix}/query/", "description": "Query with filters"},
                {"method": "GET", "path": f"/{prefix}/", "description": "List all"},
                {"method": "POST", "path": f"/{prefix}/", "description": "Create"},
                {"method": "GET", "path": f"/{prefix}/{{id}}/", "description": "Retrieve"},
                {"method": "PUT", "path": f"/{prefix}/{{id}}/", "description": "Update"},
                {"method": "PATCH", "path": f"/{prefix}/{{id}}/", "description": "Partial update"},
                {"method": "DELETE", "path": f"/{prefix}/{{id}}/", "description": "Delete"},
            ])
        
        if app_endpoints:
            endpoints[app] = app_endpoints
    
    return {
        "success": True,
        "endpoints": endpoints,
        "total": sum(len(e) for e in endpoints.values()),
    }
