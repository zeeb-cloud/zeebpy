#!/usr/bin/env python3
"""
Zeeb CLI - Django-like management commands.

Usage (from anywhere after pip install):
    zeeb startproject <name>
    zeeb mcp stdio              # Start MCP server
    zeeb-manage <command>

Usage (from project directory):
    python manage.py <command>
    zeeb-manage <command>

Commands:
    startproject <name>     Create a new Zeeb project
    startapp <name>         Create a new app within a project
    makemigrations [app]    Create new migrations
    migrate [migration]     Apply migrations
    showmigrations          Show migration status
    createsuperuser         Create a superuser account
    check                   Check project configuration
    shell                   Start interactive Python shell
    runserver [host:port]   Start development server
    mcp <subcommand>        MCP server for LLM integration
"""

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    """Main CLI entry point."""
    # Determine program name based on how we were invoked
    prog_name = os.path.basename(sys.argv[0])
    if prog_name in ("zeeb-manage", "manage.py"):
        prog_name = "zeeb-manage"
    else:
        prog_name = "zeeb"
    
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="Zeeb ORM - Django-like management commands",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # startproject
    sp_project = subparsers.add_parser("startproject", help="Create a new Zeeb project")
    sp_project.add_argument("name", help="Project name")
    sp_project.add_argument(
        "--directory", "-d",
        help="Directory to create project in (default: current directory)",
        default="."
    )

    # startapp
    sp_app = subparsers.add_parser("startapp", help="Create a new app within a project")
    sp_app.add_argument("name", help="App name")

    # makemigrations
    sp_makemig = subparsers.add_parser("makemigrations", help="Create new migrations")
    sp_makemig.add_argument("app", nargs="?", help="App name (optional)")
    sp_makemig.add_argument("--name", "-n", help="Migration name")
    sp_makemig.add_argument("--empty", action="store_true", help="Create empty migration")

    # migrate
    sp_migrate = subparsers.add_parser("migrate", help="Apply migrations")
    sp_migrate.add_argument("app", nargs="?", help="App name (optional)")
    sp_migrate.add_argument("migration", nargs="?", help="Migration name (optional)")
    sp_migrate.add_argument("--rollback", "-r", type=int, metavar="N", help="Rollback N migrations")
    sp_migrate.add_argument("--fake", action="store_true", help="Mark as applied without running")

    # showmigrations
    sp_showmig = subparsers.add_parser("showmigrations", help="Show migration status")
    sp_showmig.add_argument("app", nargs="?", help="App name (optional)")

    # shell
    sp_shell = subparsers.add_parser("shell", help="Start interactive Python shell")
    sp_shell.add_argument("--ipython", "-i", action="store_true", help="Use IPython if available")

    # runserver
    sp_run = subparsers.add_parser("runserver", help="Start development server")
    sp_run.add_argument(
        "addrport",
        nargs="?",
        default="127.0.0.1:9000",
        help="Address and port (default: 127.0.0.1:9000)"
    )
    sp_run.add_argument("--reload", action="store_true", default=True, help="Enable auto-reload")
    sp_run.add_argument("--no-reload", action="store_false", dest="reload", help="Disable auto-reload")

    # createsuperuser
    sp_superuser = subparsers.add_parser("createsuperuser", help="Create a superuser account")
    sp_superuser.add_argument("--email", "-e", help="Email address")
    sp_superuser.add_argument("--password", "-p", help="Password (use with caution)")
    sp_superuser.add_argument("--username", "-u", help="Username (optional)")
    sp_superuser.add_argument("--noinput", action="store_true", help="Non-interactive mode")

    # check
    sp_check = subparsers.add_parser("check", help="Check project configuration and migrations")
    sp_check.add_argument("--deploy", action="store_true", help="Check deployment readiness")

    # init (for migrations in existing project)
    sp_init = subparsers.add_parser("init", help="Initialize migrations for a project")
    sp_init.add_argument("--directory", "-d", default="migrations", help="Migrations directory")

    # mcp - MCP server commands
    sp_mcp = subparsers.add_parser("mcp", help="MCP server for LLM integration")
    mcp_subparsers = sp_mcp.add_subparsers(dest="mcp_command", help="MCP commands")
    
    # mcp stdio
    mcp_stdio = mcp_subparsers.add_parser("stdio", help="Run MCP server in stdio mode (for Claude Desktop, Cursor, etc.)")
    
    # mcp serve
    mcp_serve = mcp_subparsers.add_parser("serve", help="Run MCP server in HTTP mode")
    mcp_serve.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    mcp_serve.add_argument("--port", type=int, default=3000, help="Port to bind to")
    
    # mcp tools
    mcp_tools = mcp_subparsers.add_parser("tools", help="List available MCP tools")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    # Import and run commands
    if args.command == "startproject":
        from zeeb_orm.cli.commands.startproject import run_startproject
        return run_startproject(args.name, args.directory)

    elif args.command == "startapp":
        from zeeb_orm.cli.commands.startapp import run_startapp
        return run_startapp(args.name)

    elif args.command == "makemigrations":
        from zeeb_orm.cli.commands.migrate import run_makemigrations
        return run_makemigrations(args.app, args.name, args.empty)

    elif args.command == "migrate":
        from zeeb_orm.cli.commands.migrate import run_migrate
        return run_migrate(args.app, args.migration, args.rollback, args.fake)

    elif args.command == "showmigrations":
        from zeeb_orm.cli.commands.migrate import run_showmigrations
        return run_showmigrations(args.app)

    elif args.command == "shell":
        from zeeb_orm.cli.commands.shell import run_shell
        return run_shell(args.ipython)

    elif args.command == "runserver":
        from zeeb_orm.cli.commands.runserver import run_server
        return run_server(args.addrport, args.reload)

    elif args.command == "createsuperuser":
        from zeeb_orm.cli.commands.createsuperuser import run_createsuperuser
        return run_createsuperuser(args.email, args.password, args.username, args.noinput)

    elif args.command == "check":
        from zeeb_orm.cli.commands.check import run_check
        return run_check(args.deploy)

    elif args.command == "init":
        from zeeb_orm.cli.commands.migrate import run_init
        return run_init(args.directory)

    elif args.command == "mcp":
        return run_mcp_command(args)

    else:
        parser.print_help()
        return 1


def run_mcp_command(args) -> int:
    """Handle MCP subcommands."""
    if args.mcp_command is None:
        print("Usage: zeeb mcp <command>")
        print("\nCommands:")
        print("  stdio    Run MCP server in stdio mode (for Claude Desktop, Cursor)")
        print("  serve    Run MCP server in HTTP mode")
        print("  tools    List available MCP tools")
        return 0
    
    if args.mcp_command == "stdio":
        import asyncio
        from zeeb_orm.mcp.server import run_stdio
        asyncio.run(run_stdio())
        return 0
    
    elif args.mcp_command == "serve":
        from zeeb_orm.mcp.server import run_http
        run_http(args.host, args.port)
        return 0
    
    elif args.mcp_command == "tools":
        # Import to register tools
        from zeeb_orm.mcp.server import _tool_handlers
        from zeeb_orm.mcp.tools import project, models, serializers, viewsets, migrations, data
        from zeeb_orm.mcp.tools import server as srv
        
        print("Available Zeeb MCP Tools")
        print("=" * 60)
        
        # Group by category
        categories = {
            "Project": ["zeeb_create_project", "zeeb_create_app", "zeeb_delete_app", "zeeb_project_info"],
            "Models": ["zeeb_create_model", "zeeb_update_model", "zeeb_delete_model", "zeeb_list_models", 
                      "zeeb_add_field", "zeeb_remove_field", "zeeb_add_relationship"],
            "Serializers": ["zeeb_create_serializer", "zeeb_update_serializer"],
            "ViewSets": ["zeeb_create_viewset", "zeeb_add_viewset_action", "zeeb_generate_crud", "zeeb_list_endpoints"],
            "Migrations": ["zeeb_run_migrations", "zeeb_migration_status", "zeeb_rollback_migration"],
            "Data": ["zeeb_seed_data", "zeeb_query_data"],
            "Server": ["zeeb_start_server", "zeeb_stop_server", "zeeb_server_status"],
        }
        
        for category, tools in categories.items():
            print(f"\n{category}:")
            for tool_name in tools:
                if tool_name in _tool_handlers:
                    desc = _tool_handlers[tool_name]["description"]
                    print(f"  {tool_name}")
                    print(f"    {desc}")
        
        print(f"\nTotal: {len(_tool_handlers)} tools")
        return 0
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
