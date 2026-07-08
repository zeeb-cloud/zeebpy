#!/usr/bin/env python3
"""
Zeeb CLI - Django-like management commands.

Usage (from anywhere after pip install):
    zeeb startproject <name>
    zeeb-manage <command>

Usage (from project directory):
    python manage.py <command>
    zeeb-manage <command>

Commands:
    startproject <name>     Create a new Zeeb project
    startapp <name>         Create a new app within a project
    makemigrations              Create new migrations
    migrate [migration]     Apply migrations
    showmigrations          Show migration status
    squashmigrations        Squash a range of migrations into one file
    createsuperuser         Create a superuser account
    check                   Check project configuration
    shell                   Start interactive Python shell
    runserver [host:port]   Start development server
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
    sp_makemig.add_argument("--name", "-n", help="Migration name")
    sp_makemig.add_argument("--empty", action="store_true", help="Create empty migration")
    sp_makemig.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 if model changes are detected (no file written). Useful in CI.",
    )
    sp_makemig.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what migration would be created without writing any files.",
    )

    # migrate
    sp_migrate = subparsers.add_parser("migrate", help="Apply migrations")
    sp_migrate.add_argument("migration", nargs="?", help="Migration name or 'zero' (optional)")
    sp_migrate.add_argument("--rollback", "-r", type=int, metavar="N", help="Rollback N migrations")
    sp_migrate.add_argument("--fake", action="store_true", help="Mark as applied without running")
    sp_migrate.add_argument(
        "--fake-initial",
        action="store_true",
        dest="fake_initial",
        help="Skip initial migration if tables already exist.",
    )
    sp_migrate.add_argument(
        "--plan",
        action="store_true",
        help="Show which migrations would be applied without running them.",
    )
    sp_migrate.add_argument(
        "--noinput",
        "--no-input",
        action="store_true",
        dest="noinput",
        help="Django-compat no-op: migrate never prompts for input.",
    )

    # showmigrations
    sp_showmig = subparsers.add_parser("showmigrations", help="Show migration status")

    # squashmigrations
    sp_squash = subparsers.add_parser(
        "squashmigrations",
        help="Squash a range of migrations into a single file",
    )
    sp_squash.add_argument("start", help="First migration in the range (e.g. 0001_initial)")
    sp_squash.add_argument("end", help="Last migration in the range (e.g. 0005_add_views)")
    sp_squash.add_argument("--name", "-n", dest="squashed_name", help="Name for the squashed migration")
    sp_squash.add_argument(
        "--no-optimize",
        action="store_true",
        dest="no_optimize",
        help="Skip the optimizer — keep all operations as-is.",
    )

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
        return run_makemigrations(args.name, args.empty, args.check, args.dry_run)

    elif args.command == "migrate":
        from zeeb_orm.cli.commands.migrate import run_migrate
        return run_migrate(args.migration, args.rollback, args.fake, args.fake_initial, args.plan)

    elif args.command == "showmigrations":
        from zeeb_orm.cli.commands.migrate import run_showmigrations
        return run_showmigrations()

    elif args.command == "squashmigrations":
        from zeeb_orm.cli.commands.migrate import run_squashmigrations
        return run_squashmigrations(args.start, args.end, args.squashed_name, args.no_optimize)

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

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
