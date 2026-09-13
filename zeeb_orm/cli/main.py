#!/usr/bin/env python3
"""
Zeeb CLI — project management commands.

Usage (from anywhere after pip install):
    zeeb startproject <name>
    zeeb-manage <command>

Usage (from project directory):
    python manage.py <command>
    zeeb-manage <command>

Commands:
    startproject <name>     Create a new Zeeb project
    startapp <name>         Create a new app within a project
    makemigrations          Create new migrations
    migrate [migration]     Apply migrations
    showmigrations          Show migration status
    squashmigrations        Squash a range of migrations into one file
    showurls                List every served route
    inspect                 Report the project's live structure as JSON
    frontend-brief          Emit an integration brief for a frontend
    createsuperuser         Create a superuser account
    check                   Check project configuration
    shell                   Start interactive Python shell
    runserver [host:port]   Start development server
    init                    Initialize migrations in an existing project

Commands that report a result accept ``--json``: they then print exactly one
object with ``success``/``message``/``data`` on stdout — the same envelope the
agent tools return, and every failure names the next command to run.
"""

import argparse
import os
import sys

#: Every registered subcommand. Kept as data so the documentation drift test can
#: compare docs/cli/commands.md against what the parser actually offers.
COMMANDS: tuple[str, ...] = (
    "startproject",
    "startapp",
    "makemigrations",
    "migrate",
    "showmigrations",
    "squashmigrations",
    "showurls",
    "inspect",
    "frontend-brief",
    "shell",
    "runserver",
    "createsuperuser",
    "check",
    "init",
)

#: Subcommands that emit the machine-readable envelope. The interactive and
#: streaming ones (shell, runserver, createsuperuser) are deliberately absent:
#: they have no single result to report.
JSON_COMMANDS: frozenset[str] = frozenset(
    {
        "startproject",
        "startapp",
        "makemigrations",
        "migrate",
        "showmigrations",
        "showurls",
        "inspect",
        "frontend-brief",
        "check",
    }
)


def build_parser(prog_name: str = "zeeb-manage") -> argparse.ArgumentParser:
    """Build the full argument parser.

    Split out of :func:`main` so tests can introspect the real command surface
    instead of asserting against a hand-maintained copy of it.
    """
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="Zeeb — project management commands",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Attached to each reporting subcommand rather than to the top-level parser:
    # a global flag would have to be written *before* the subcommand, which is
    # the ordering callers get wrong and argparse reports worst.
    json_flag = argparse.ArgumentParser(add_help=False)
    json_flag.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print one JSON object on stdout instead of prose.",
    )

    # startproject
    sp_project = subparsers.add_parser(
        "startproject", parents=[json_flag], help="Create a new Zeeb project"
    )
    sp_project.add_argument("name", help="Project name")
    sp_project.add_argument(
        "--directory", "-d",
        help="Directory to create project in (default: current directory)",
        default="."
    )

    # startapp
    sp_app = subparsers.add_parser(
        "startapp", parents=[json_flag], help="Create a new app within a project"
    )
    sp_app.add_argument("name", help="App name")
    sp_app.add_argument(
        "--no-wire",
        dest="wire",
        action="store_false",
        help=(
            "Only scaffold the files. By default the app is also registered in "
            "INSTALLED_APPS and its router is included in the project urls.py."
        ),
    )
    sp_app.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "Also generate a complete working resource for this model — model, "
            "serializer, viewset, route registration and a passing test "
            "(e.g. --model Post)."
        ),
    )
    # The feature-spec layer calls the same thing an entity; accept both so a
    # caller never has to know which vocabulary this command speaks.
    sp_app.add_argument("--entity", dest="model", help=argparse.SUPPRESS)

    # makemigrations
    sp_makemig = subparsers.add_parser(
        "makemigrations", parents=[json_flag], help="Create new migrations"
    )
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
    sp_migrate = subparsers.add_parser(
        "migrate", parents=[json_flag], help="Apply migrations"
    )
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
    subparsers.add_parser(
        "showmigrations", parents=[json_flag], help="Show migration status"
    )

    # squashmigrations
    sp_squash = subparsers.add_parser(
        "squashmigrations",
        help="Squash a range of migrations into a single file",
    )
    sp_squash.add_argument("start", help="First migration in the range (e.g. 0001_initial)")
    sp_squash.add_argument("end", help="Last migration in the range (e.g. 0005_add_views)")
    sp_squash.add_argument(
        "--name", "-n", dest="squashed_name", help="Name for the squashed migration"
    )
    sp_squash.add_argument(
        "--no-optimize",
        action="store_true",
        dest="no_optimize",
        help="Skip the optimizer — keep all operations as-is.",
    )

    # showurls
    subparsers.add_parser(
        "showurls",
        parents=[json_flag],
        help="List every route the project serves, with its methods",
    )

    # inspect
    subparsers.add_parser(
        "inspect",
        parents=[json_flag],
        help="Report the project's live apps, models, routes and settings",
    )

    # frontend-brief
    subparsers.add_parser(
        "frontend-brief",
        parents=[json_flag],
        help="Emit an integration brief to hand to a frontend or an AI app builder",
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
    sp_run.add_argument(
        "--no-reload", action="store_false", dest="reload", help="Disable auto-reload"
    )

    # createsuperuser
    sp_superuser = subparsers.add_parser("createsuperuser", help="Create a superuser account")
    sp_superuser.add_argument("--email", "-e", help="Email address")
    sp_superuser.add_argument("--password", "-p", help="Password (use with caution)")
    sp_superuser.add_argument("--username", "-u", help="Username (optional)")
    sp_superuser.add_argument("--noinput", action="store_true", help="Non-interactive mode")

    # check
    sp_check = subparsers.add_parser(
        "check", parents=[json_flag], help="Check project configuration and migrations"
    )
    sp_check.add_argument("--deploy", action="store_true", help="Check deployment readiness")

    # init (for migrations in existing project)
    sp_init = subparsers.add_parser("init", help="Initialize migrations for a project")
    sp_init.add_argument("--directory", "-d", default="migrations", help="Migrations directory")

    return parser


def main() -> int:
    """Main CLI entry point."""
    # Determine program name based on how we were invoked
    prog_name = os.path.basename(sys.argv[0])
    prog_name = "zeeb-manage" if prog_name in ("zeeb-manage", "manage.py") else "zeeb"

    parser = build_parser(prog_name)
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    as_json = getattr(args, "json_output", False)

    # Import and run commands
    if args.command == "startproject":
        from zeeb_orm.cli.commands.startproject import run_startproject
        return run_startproject(args.name, args.directory, json_output=as_json)

    elif args.command == "startapp":
        from zeeb_orm.cli.commands.startapp import run_startapp
        return run_startapp(args.name, wire=args.wire, model=args.model, json_output=as_json)

    elif args.command == "makemigrations":
        from zeeb_orm.cli.commands.migrate import run_makemigrations
        return run_makemigrations(
            args.name, args.empty, args.check, args.dry_run, json_output=as_json
        )

    elif args.command == "migrate":
        from zeeb_orm.cli.commands.migrate import run_migrate
        return run_migrate(
            args.migration, args.rollback, args.fake, args.fake_initial, args.plan,
            json_output=as_json,
        )

    elif args.command == "showmigrations":
        from zeeb_orm.cli.commands.migrate import run_showmigrations
        return run_showmigrations(json_output=as_json)

    elif args.command == "squashmigrations":
        from zeeb_orm.cli.commands.migrate import run_squashmigrations
        return run_squashmigrations(args.start, args.end, args.squashed_name, args.no_optimize)

    elif args.command == "showurls":
        from zeeb_orm.cli.commands.inspect import run_showurls
        return run_showurls(json_output=as_json)

    elif args.command == "inspect":
        from zeeb_orm.cli.commands.inspect import run_inspect
        return run_inspect(json_output=as_json)

    elif args.command == "frontend-brief":
        from zeeb_orm.cli.commands.inspect import run_frontend_brief
        return run_frontend_brief(json_output=as_json)

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
        return run_check(args.deploy, json_output=as_json)

    elif args.command == "init":
        from zeeb_orm.cli.commands.migrate import run_init
        return run_init(args.directory)

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
