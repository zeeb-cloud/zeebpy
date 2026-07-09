"""zeeb_agents — Async Python functions for Zeeb project scaffolding and management.

All functions return an :class:`AgentResult` and can be imported directly::

    from zeeb_agents import (
        create_project, create_app, get_project_info, list_apps, get_project_structure,
        create_model, add_field, remove_field, replace_model_fields, add_relationship,
        create_serializer, create_viewset, generate_crud, create_route,
        run_migrations, make_migrations, get_migration_status,
        generate_seed_script,
        read_logs, search_logs, clear_logs,
        get_settings, manage_settings, get_env, set_env, delete_env,
        read_file, write_file, list_files, search_code,
        list_tables, describe_table, run_query,
        run_tests,
        run_management_command,
        # BaaS tools
        create_user, list_users, get_user, update_user, delete_user, set_user_password,
        configure_cors, get_cors_config,
        create_task, list_tasks, delete_task,
        create_health_endpoint, check_system_health,
        get_model_json_schema, list_all_routes, export_openapi,
        generate_dockerfile, generate_requirements, check_production_readiness,
        create_permission_class, list_permission_classes,
        # MCP resources
        get_resource, get_capabilities_doc, get_project_lifecycle_doc,
        get_backend_generation_doc, get_frontend_generation_doc, get_deployment_doc,
    )

Designed to be used from an MCP server, CLI tool, or any other Python code
without any MCP dependency on this package itself.
"""

from zeeb_agents._utils import AgentResult

# Vendor-pluggable project_id → path resolution (configured once at startup).
from zeeb_agents._utils.resolver import (
    configure,
    set_project_resolver,
)

# API configuration (throttling, versioning)
from zeeb_agents.api_config import (
    configure_throttling,
    configure_versioning,
)

# Auth scaffolding (JWT router, OAuth, custom user model)
from zeeb_agents.auth_scaffold import (
    create_user_model,
    setup_auth,
    setup_oauth,
)

# Tool discovery
from zeeb_agents.capabilities import list_capabilities

# Configuration & environment
from zeeb_agents.config import (
    delete_env,
    get_env,
    get_settings,
    manage_settings,
    set_env,
)

# CORS configuration (BaaS)
from zeeb_agents.cors import (
    configure_cors,
    get_cors_config,
)

# Database introspection
from zeeb_agents.database import (
    describe_table,
    list_tables,
    run_query,
)

# Deployment scaffolding (BaaS)
from zeeb_agents.deploy import (
    check_production_readiness,
    generate_dockerfile,
    generate_requirements,
)

# File system
from zeeb_agents.files import (
    list_files,
    read_file,
    search_code,
    write_file,
)

# FilterSet scaffolding
from zeeb_agents.filters_scaffold import create_filterset

# Health checks (BaaS)
from zeeb_agents.health import (
    check_system_health,
    create_health_endpoint,
)

# Logs
from zeeb_agents.logs import (
    clear_logs,
    read_logs,
    search_logs,
)

# Migrations
from zeeb_agents.migrations import (
    get_migration_status,
    make_migrations,
    rollback_migration,
    run_migrations,
)

# Model management
from zeeb_agents.models import (
    add_field,
    add_relationship,
    create_model,
    delete_model,
    list_models,
    remove_field,
    replace_model_fields,
    update_model,
)

# Permission class scaffolding (BaaS)
from zeeb_agents.permissions_scaffold import (
    create_permission_class,
    list_permission_classes,
)

# Project & app management
from zeeb_agents.project import (
    create_app,
    create_project,
    delete_app,
    get_project_info,
    get_project_structure,
    list_apps,
    rename_app,
)

# MCP resource content
from zeeb_agents.resources import (
    RESOURCE_URIS,
    get_backend_generation_doc,
    get_capabilities_doc,
    get_deployment_doc,
    get_error_recovery_doc,
    get_frontend_generation_doc,
    get_principles_doc,
    get_project_lifecycle_doc,
    get_recipes_doc,
    get_resource,
)

# Standalone routes
from zeeb_agents.routes import create_route

# Platform-managed runtime references (preview URLs, live OpenAPI)
from zeeb_agents.runtime import (
    get_openapi_url,
    get_project_reference,
)

# API schema & route introspection (BaaS)
from zeeb_agents.schema import (
    export_openapi,
    get_model_json_schema,
    list_all_routes,
)

# Seed data
from zeeb_agents.seed import generate_seed_script

# Serializer management
from zeeb_agents.serializers import (
    create_serializer,
    update_serializer,
)

# Shell / management commands
from zeeb_agents.shell import run_management_command

# Signals scaffolding
from zeeb_agents.signals import (
    create_signal_receiver,
    delete_signal_receiver,
    edit_signal_receiver,
    list_model_signals,
    list_signal_receivers,
    read_signal_receiver,
)

# Background tasks (BaaS)
from zeeb_agents.tasks import (
    create_task,
    delete_task,
    list_tasks,
)

# Testing
from zeeb_agents.testing import run_tests

# User management (BaaS)
from zeeb_agents.users import (
    create_user,
    delete_user,
    get_user,
    list_users,
    set_user_password,
    update_user,
)

# ViewSet & routing
from zeeb_agents.viewsets import (
    add_viewset_action,
    create_viewset,
    generate_crud,
    list_endpoints,
    register_route,
    update_viewset,
)

__version__ = "0.1.0"

__all__ = [
    # Result type
    "AgentResult",
    # Library configuration (vendor resolver hook)
    "configure",
    "set_project_resolver",
    # Project
    "create_project",
    "create_app",
    "delete_app",
    "get_project_info",
    "rename_app",
    "list_apps",
    "get_project_structure",
    # Models
    "create_model",
    "update_model",
    "replace_model_fields",
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
    "update_viewset",
    "add_viewset_action",
    "register_route",
    "generate_crud",
    "list_endpoints",
    # Standalone routes
    "create_route",
    # Migrations
    "make_migrations",
    "run_migrations",
    "get_migration_status",
    "rollback_migration",
    # Platform-managed runtime references
    "get_project_reference",
    "get_openapi_url",
    # Logs
    "read_logs",
    "search_logs",
    "clear_logs",
    # Config & environment
    "get_settings",
    "manage_settings",
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
    # Seed data
    "generate_seed_script",
    # Signals scaffolding
    "create_signal_receiver",
    "list_signal_receivers",
    "read_signal_receiver",
    "edit_signal_receiver",
    "delete_signal_receiver",
    "list_model_signals",
    # User management (BaaS)
    "create_user",
    "list_users",
    "get_user",
    "update_user",
    "delete_user",
    "set_user_password",
    # CORS configuration (BaaS)
    "configure_cors",
    "get_cors_config",
    # Background tasks (BaaS)
    "create_task",
    "list_tasks",
    "delete_task",
    # Health checks (BaaS)
    "create_health_endpoint",
    "check_system_health",
    # API schema & route introspection (BaaS)
    "get_model_json_schema",
    "list_all_routes",
    "export_openapi",
    # Deployment scaffolding (BaaS)
    "generate_dockerfile",
    "generate_requirements",
    "check_production_readiness",
    # Permission class scaffolding (BaaS)
    "create_permission_class",
    "list_permission_classes",
    # Auth scaffolding
    "setup_auth",
    "setup_oauth",
    "create_user_model",
    # FilterSet scaffolding
    "create_filterset",
    # API configuration
    "configure_throttling",
    "configure_versioning",
    # Tool discovery
    "list_capabilities",
    # MCP resource content
    "RESOURCE_URIS",
    "get_resource",
    "get_principles_doc",
    "get_capabilities_doc",
    "get_project_lifecycle_doc",
    "get_backend_generation_doc",
    "get_frontend_generation_doc",
    "get_deployment_doc",
    "get_recipes_doc",
    "get_error_recovery_doc",
]
