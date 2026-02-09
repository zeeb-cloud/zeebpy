"""startapp command - Create new app within a Zeeb project."""

import os
import sys
from pathlib import Path


# App template files

MODELS_PY = '''"""
{app_name} models.

Define your Zeeb ORM models here.
"""

from zeeb_orm import Model, fields


# Example model:
# class {model_name}(Model):
#     name = fields.CharField(max_length=100)
#     description = fields.TextField(null=True)
#     created_at = fields.DateTimeField(auto_now_add=True)
#     updated_at = fields.DateTimeField(auto_now=True)
#
#     class Meta:
#         table_name = "{app_name}_{table_name}"
#         ordering = ["-created_at"]
'''

SERIALIZERS_PY = '''"""
{app_name} serializers.

Define your API serializers here.
"""

from zeeb_api import serializers
# from .models import YourModel


# Example serializer:
# class {model_name}Serializer(serializers.ModelSerializer):
#     class Meta:
#         model = {model_name}
#         fields = ["id", "name", "description", "created_at", "updated_at"]
#         read_only_fields = ["created_at", "updated_at"]
'''

VIEWS_PY = '''"""
{app_name} views.

Define your API viewsets here.
"""

from zeeb_api import viewsets, permissions
# from .models import YourModel
# from .serializers import YourModelSerializer


# Example viewset:
# class {model_name}ViewSet(viewsets.ModelViewSet):
#     queryset = {model_name}.objects.all()
#     serializer_class = {model_name}Serializer
#     permission_classes = [permissions.IsAuthenticatedOrReadOnly]
#
#     @viewsets.action(detail=True, methods=["post"])
#     async def custom_action(self, request, pk=None):
#         obj = await self.get_object()
#         # Do something
#         return {{"status": "success"}}
'''

URLS_PY = '''"""
{app_name} URL configuration.

Register your viewsets with the router here.
"""

from zeeb_api.routers import DefaultRouter
# from .views import YourModelViewSet

router = DefaultRouter()

# Register viewsets:
# router.register("{app_name}", YourModelViewSet)
'''

APP_INIT_PY = '''"""
{app_name} app.
"""

default_app_config = "apps.{app_name}.apps.{app_class}Config"
'''

APPS_PY = '''"""
{app_name} app configuration.
"""


class {app_class}Config:
    """App configuration."""
    
    name = "apps.{app_name}"
    verbose_name = "{app_title}"
'''

ADMIN_PY = '''"""
{app_name} admin configuration.

Register your models for admin interface here.
"""

# from zeeb_admin import admin
# from .models import YourModel

# admin.site.register(YourModel)
'''

TESTS_PY = '''"""
{app_name} tests.
"""

import pytest
from httpx import AsyncClient

# from .models import YourModel


# @pytest.mark.asyncio
# async def test_create_{app_name}():
#     # Test your models/views here
#     pass
'''


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()
    
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    
    return None


def to_class_name(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def to_title(name: str) -> str:
    """Convert snake_case to Title Case."""
    return " ".join(word.capitalize() for word in name.split("_"))


def run_startapp(name: str) -> int:
    """Create a new app within a Zeeb project."""
    # Validate app name
    if not name.isidentifier():
        print(f"Error: '{name}' is not a valid Python identifier")
        return 1

    # Find project root
    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        print("Make sure you're in a Zeeb project directory")
        return 1

    apps_dir = project_root / "apps"
    if not apps_dir.exists():
        print("Error: 'apps' directory not found in project root")
        return 1

    app_path = apps_dir / name
    if app_path.exists():
        print(f"Error: App '{name}' already exists")
        return 1

    print(f"Creating app '{name}' in {apps_dir}...")

    try:
        # Create directory structure
        app_path.mkdir()

        # Template context
        app_class = to_class_name(name)
        app_title = to_title(name)
        model_name = app_class[:-1] if name.endswith("s") else app_class  # Remove plural
        table_name = name.rstrip("s")

        context = {
            "app_name": name,
            "app_class": app_class,
            "app_title": app_title,
            "model_name": model_name,
            "table_name": table_name,
        }

        # Create files
        files = {
            "__init__.py": APP_INIT_PY.format(**context),
            "apps.py": APPS_PY.format(**context),
            "models.py": MODELS_PY.format(**context),
            "serializers.py": SERIALIZERS_PY.format(**context),
            "views.py": VIEWS_PY.format(**context),
            "urls.py": URLS_PY.format(**context),
            "admin.py": ADMIN_PY.format(**context),
            "tests.py": TESTS_PY.format(**context),
        }

        for filename, content in files.items():
            file_path = app_path / filename
            file_path.write_text(content)
            print(f"  Created {name}/{filename}")

        print(f"\nApp '{name}' created successfully!")
        print(f"\nNext steps:")
        print(f"  1. Add 'apps.{name}' to INSTALLED_APPS in settings.py")
        print(f"  2. Define models in apps/{name}/models.py")
        print(f"  3. Create serializers in apps/{name}/serializers.py")
        print(f"  4. Create viewsets in apps/{name}/views.py")
        print(f"  5. Register routes in apps/{name}/urls.py")
        print(f"  6. Include router in project urls.py")
        print(f"  7. Run 'python manage.py makemigrations'")
        print(f"  8. Run 'python manage.py migrate'")

        return 0

    except Exception as e:
        print(f"Error creating app: {e}")
        # Cleanup on error
        import shutil
        if app_path.exists():
            shutil.rmtree(app_path)
        return 1
