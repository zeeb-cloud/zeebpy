"""MCP Tools for Zeeb API building."""

from zeeb_orm.mcp.tools.project import (
    zeeb_create_project,
    zeeb_create_app,
    zeeb_delete_app,
    zeeb_project_info,
)
from zeeb_orm.mcp.tools.models import (
    zeeb_create_model,
    zeeb_update_model,
    zeeb_delete_model,
    zeeb_list_models,
    zeeb_add_field,
    zeeb_remove_field,
    zeeb_add_relationship,
)
from zeeb_orm.mcp.tools.serializers import (
    zeeb_create_serializer,
    zeeb_update_serializer,
)
from zeeb_orm.mcp.tools.viewsets import (
    zeeb_create_viewset,
    zeeb_add_viewset_action,
    zeeb_generate_crud,
    zeeb_list_endpoints,
)
from zeeb_orm.mcp.tools.migrations import (
    zeeb_run_migrations,
    zeeb_migration_status,
    zeeb_rollback_migration,
)
from zeeb_orm.mcp.tools.data import (
    zeeb_seed_data,
    zeeb_query_data,
)
from zeeb_orm.mcp.tools.server import (
    zeeb_start_server,
    zeeb_stop_server,
    zeeb_server_status,
)

__all__ = [
    # Project
    "zeeb_create_project",
    "zeeb_create_app",
    "zeeb_delete_app",
    "zeeb_project_info",
    # Models
    "zeeb_create_model",
    "zeeb_update_model",
    "zeeb_delete_model",
    "zeeb_list_models",
    "zeeb_add_field",
    "zeeb_remove_field",
    "zeeb_add_relationship",
    # Serializers
    "zeeb_create_serializer",
    "zeeb_update_serializer",
    # ViewSets
    "zeeb_create_viewset",
    "zeeb_add_viewset_action",
    "zeeb_generate_crud",
    "zeeb_list_endpoints",
    # Migrations
    "zeeb_run_migrations",
    "zeeb_migration_status",
    "zeeb_rollback_migration",
    # Data
    "zeeb_seed_data",
    "zeeb_query_data",
    # Server
    "zeeb_start_server",
    "zeeb_stop_server",
    "zeeb_server_status",
]
