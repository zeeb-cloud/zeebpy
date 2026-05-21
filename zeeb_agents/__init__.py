"""zeeb_agents — Async Python functions for Zeeb project scaffolding and management.

All functions return an :class:`AgentResult` and can be imported directly::

    from zeeb_agents import (
        create_project, create_app, get_project_info,
        create_model, add_field, remove_field, add_relationship,
        create_serializer, create_viewset, generate_crud,
        run_migrations, make_migrations, get_migration_status,
        read_logs, search_logs, clear_logs,
        get_settings, get_env, set_env, delete_env,
        read_file, write_file, list_files, search_code,
        list_tables, describe_table, run_query,
        run_tests,
        run_management_command,
    )

Designed to be used from an MCP server, CLI tool, or any other Python code
without any MCP dependency on this package itself.
"""

from zeeb_agents._utils import AgentResult

# Project & app management
from zeeb_agents.project import (
    create_app,
    create_project,
    delete_app,
    get_project_info,
    rename_app,
)

# Model management
from zeeb_agents.models import (
    add_field,
    add_relationship,
    create_model,
    delete_model,
    list_models,
    remove_field,
    update_model,
)

# Serializer management
from zeeb_agents.serializers import (
    create_serializer,
    update_serializer,
)

# ViewSet & routing
from zeeb_agents.viewsets import (
    add_viewset_action,
    create_viewset,
    generate_crud,
    list_endpoints,
    register_route,
)

# Migrations
from zeeb_agents.migrations import (
    get_migration_status,
    make_migrations,
    rollback_migration,
    run_migrations,
)

# Dev server
from zeeb_agents.server import (
    get_server_status,
    start_server,
    stop_server,
)

# Logs
from zeeb_agents.logs import (
    clear_logs,
    read_logs,
    search_logs,
)

# Configuration & environment
from zeeb_agents.config import (
    delete_env,
    get_env,
    get_settings,
    set_env,
)

# File system
from zeeb_agents.files import (
    list_files,
    read_file,
    search_code,
    write_file,
)

# Database introspection
from zeeb_agents.database import (
    describe_table,
    list_tables,
    run_query,
)

# Testing
from zeeb_agents.testing import run_tests

# Shell / management commands
from zeeb_agents.shell import run_management_command

__version__ = "0.1.0"

__all__ = [
    # Result type
    "AgentResult",
    # Project
    "create_project",
    "create_app",
    "delete_app",
    "get_project_info",
    "rename_app",
    # Models
    "create_model",
    "update_model",
    "delete_model",
    "list_models",
    "add_field",
    "remove_field",
    "add_relationship",
    # Serializers
    "create_serializer",
    "update_serializer",
    # ViewSets
    "create_viewset",
    "add_viewset_action",
    "register_route",
    "generate_crud",
    "list_endpoints",
    # Migrations
    "make_migrations",
    "run_migrations",
    "get_migration_status",
    "rollback_migration",
    # Server
    "start_server",
    "stop_server",
    "get_server_status",
    # Logs
    "read_logs",
    "search_logs",
    "clear_logs",
    # Config & environment
    "get_settings",
    "get_env",
    "set_env",
    "delete_env",
    # Files
    "read_file",
    "write_file",
    "list_files",
    "search_code",
    # Database
    "list_tables",
    "describe_table",
    "run_query",
    # Testing
    "run_tests",
    # Shell
    "run_management_command",
]
