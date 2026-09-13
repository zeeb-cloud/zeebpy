"""``docs/cli/commands.md`` must describe the CLI that exists.

Nothing tied the page to the parser, and half of it had become fiction:
`rollback`, `changepassword`, `dbshell`, `showurls`, `collectstatic`,
`clearsessions`, a `zeeb_orm.cli.base.BaseCommand` API and four global options —
none of which were ever implemented — while `squashmigrations` went undocumented.
An agent that reads a page like that writes code that cannot run.
"""

import re
from pathlib import Path

import pytest

from zeeb_orm.cli.main import COMMANDS, JSON_COMMANDS, build_parser

DOC = Path(__file__).resolve().parent.parent / "docs" / "cli" / "commands.md"

#: Headings under a command section that name something other than a subcommand.
_NOT_COMMANDS: frozenset[str] = frozenset()


def documented_commands(text: str) -> set[str]:
    """Every ``### <name>`` heading that sits under a command section."""
    found: set[str] = set()
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        elif line.startswith("### ") and current.endswith("Commands"):
            found.add(line[4:].strip())
    return found - _NOT_COMMANDS


def subparsers() -> dict:
    return build_parser()._subparsers._group_actions[0].choices


def test_every_documented_command_exists():
    unknown = documented_commands(DOC.read_text()) - set(COMMANDS)
    assert not unknown, (
        "docs/cli/commands.md documents commands that do not exist: "
        + ", ".join(sorted(unknown))
    )


def test_every_command_is_documented():
    missing = set(COMMANDS) - documented_commands(DOC.read_text())
    assert not missing, (
        "commands missing from docs/cli/commands.md: " + ", ".join(sorted(missing))
    )


def test_every_documented_option_is_real():
    """A flag that does not parse turns a copied command into an error."""
    text = DOC.read_text()
    choices = subparsers()

    problems: list[str] = []
    for command in documented_commands(text):
        # The section body: from this heading to the next one at any level.
        body = text.split(f"### {command}\n", 1)[1]
        body = re.split(r"\n#{2,3} ", body, maxsplit=1)[0]

        options = {
            option
            for action in choices[command]._actions
            for option in action.option_strings
        }
        for flag in set(re.findall(r"`(--[a-z][a-z-]*)", body)):
            if flag not in options:
                problems.append(f"{command} {flag}")
    assert not problems, "documented options that do not exist: " + ", ".join(
        sorted(problems)
    )


def test_the_json_flag_is_documented_where_it_is_offered():
    text = DOC.read_text()
    section = text.split("## JSON Output", 1)[1]
    listed = set(re.findall(r"`([a-z-]+)`", section.split("accept `--json`")[0]))
    missing = JSON_COMMANDS - listed
    assert not missing, (
        "commands that accept --json but are not listed in the JSON Output "
        "section: " + ", ".join(sorted(missing))
    )


@pytest.mark.parametrize(
    "fiction",
    [
        "zeeb_orm.cli.base",
        "BaseCommand",
        "collectstatic",
        "clearsessions",
        "dbshell",
        "changepassword",
        "--pythonpath",
        "--verbosity",
        "--no-color",
    ],
)
def test_the_page_no_longer_promises_things_that_do_not_exist(fiction):
    assert fiction not in DOC.read_text(), f"docs/cli/commands.md still mentions {fiction}"
