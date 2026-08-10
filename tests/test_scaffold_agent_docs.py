"""The files that orient a coding agent inside a generated project.

An instruction file is trusted harder than data: an agent will follow a path or
a command it reads here without checking that it exists. So everything AGENTS.md
names is verified against a real scaffolded project.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from zeeb_orm.cli.commands.startproject import run_startproject
from zeeb_orm.cli.main import COMMANDS
from zeeb_orm.scaffold import agent_guide

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("agent_docs")
    assert run_startproject("guide_api", str(base)) == 0
    return base / "guide_api"


@pytest.fixture(scope="module")
def agents_md(project) -> str:
    return (project / "AGENTS.md").read_text()


# ---------------------------------------------------------------------------
# The three files, and the one source they come from
# ---------------------------------------------------------------------------


def test_the_project_ships_all_three_entry_points(project):
    assert (project / "AGENTS.md").is_file()
    assert (project / "CLAUDE.md").is_file()
    assert (project / ".cursor" / "rules" / "zeebpy.mdc").is_file()


def test_claude_md_imports_agents_md(project):
    """The `@` form is what makes Claude Code actually inline the file."""
    assert "@AGENTS.md" in (project / "CLAUDE.md").read_text()


def test_the_cursor_rule_cannot_drift_from_agents_md(project, agents_md):
    rule = (project / ".cursor" / "rules" / "zeebpy.mdc").read_text()
    frontmatter, _, body = rule.partition("---\n\n")
    assert frontmatter.startswith("---")
    assert "alwaysApply: true" in frontmatter
    assert body == agents_md


def test_no_placeholder_survives_rendering():
    rendered = agent_guide.render_agents_md("demo_api")
    assert not re.search(r"@[A-Z_]+@", rendered)
    assert "demo_api/settings.py" in rendered


# ---------------------------------------------------------------------------
# Everything it names has to be real
# ---------------------------------------------------------------------------


def describing_sections(agents_md: str) -> str:
    """The parts of AGENTS.md that describe *this* project.

    Two sections are illustrative by construction and are left out: the entity
    walkthrough names files of an app the reader has not created yet, and the
    framework-reading section points into the installed zeebpy package.
    """
    kept = []
    skip = {"Adding an entity end to end", "Reading the framework itself"}
    for section in agents_md.split("\n## "):
        if section.splitlines()[0].strip() not in skip:
            kept.append(section)
    return "\n## ".join(kept)


def test_every_path_it_mentions_exists(project, agents_md):
    """The single most valuable guard: an agent follows these paths blindly."""
    described = describing_sections(agents_md)
    # Paths inside backticks that look like a file or directory in this project.
    candidates = set(re.findall(r"`([\w./-]+\.(?:py|md|toml|ini|example))`", described))
    candidates |= set(re.findall(r"^([\w./-]+/)\s", described, re.MULTILINE))

    missing = sorted(
        path
        for path in candidates
        # `<app>` and `<name>` are placeholders.
        if "<" not in path and not (project / path).exists()
    )
    assert not missing, f"AGENTS.md points at paths that do not exist: {missing}"


def test_every_command_it_mentions_is_registered(agents_md):
    mentioned = set(re.findall(r"manage\.py ([a-z-]+)", agents_md))
    unknown = mentioned - set(COMMANDS)
    assert not unknown, f"AGENTS.md documents commands that do not exist: {sorted(unknown)}"


def test_every_flag_it_mentions_is_real(project, agents_md):
    """A flag that does not parse turns a copied command into an error."""
    from zeeb_orm.cli.main import build_parser

    choices = build_parser()._subparsers._group_actions[0].choices
    for command, flag in re.findall(r"manage\.py ([a-z-]+)[^\n`]*?(--[a-z-]+)", agents_md):
        if command not in choices:
            continue
        options = {
            option for action in choices[command]._actions for option in action.option_strings
        }
        assert flag in options, f"`manage.py {command} {flag}` is not a real option"


def test_every_class_it_mentions_is_importable(agents_md):
    """Permission and exception names an agent will copy into a viewset."""
    import zeeb_api.exceptions as exceptions
    import zeeb_api.permissions as permissions

    section = agents_md[agents_md.index("**Permission classes**"):]
    section = section[: section.index("- **CORS**")]
    named = re.findall(r"`([A-Z]\w+)`", section)
    assert len(named) >= 7, "the permission list shrank — is it still complete?"
    for name in named:
        assert hasattr(permissions, name), f"zeeb_api.permissions has no {name}"

    pattern = r"`(NotFound|ValidationError|PermissionDenied|\w+Exception)`"
    for name in re.findall(pattern, agents_md):
        assert hasattr(exceptions, name), f"zeeb_api.exceptions has no {name}"


def test_the_layout_block_matches_the_real_project(project, agents_md):
    """The tree is what an agent navigates by before it lists a directory."""
    block = agents_md.split("## Layout", 1)[1].split("```")[1]
    for line in block.splitlines():
        entry = line.split()[0] if line.split() else ""
        if not entry or "<" in entry:
            continue
        assert (project / entry.rstrip("/")).exists(), f"{entry} is in the tree but not on disk"


def test_it_names_the_batteries_so_they_are_not_rebuilt(agents_md):
    """The highest-value section: without it an agent bolts on a second auth
    stack next to the one that already works."""
    for expected in (
        "/auth/login",
        "/auth/register",
        "AUTH_USER_MODEL",
        "CORS",
        "X-API-Version",
        "/health",
        "/ready",
        "error envelope",
    ):
        assert expected in agents_md, f"AGENTS.md no longer mentions {expected}"


def test_the_entity_walkthrough_compiles(agents_md):
    """The snippets are meant to be copied, so they have to be valid Python."""
    section = agents_md.split("## Adding an entity end to end", 1)[1].split("## Conventions")[0]
    blocks = re.findall(r"```python\n(.*?)```", section, re.DOTALL)
    assert len(blocks) >= 3
    for index, block in enumerate(blocks):
        compile(block, f"agents_md_snippet_{index}.py", "exec")


# ---------------------------------------------------------------------------
# The promises it makes have to hold
# ---------------------------------------------------------------------------


def test_the_promised_verification_loop_actually_passes(project):
    """AGENTS.md tells an agent to verify with these four commands. If any of
    them fails on an untouched project, the advice is worse than none."""
    import os

    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")

    def run(*args):
        return subprocess.run(
            args, cwd=project, env=env, capture_output=True, text=True
        )

    assert run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider").returncode == 0
    # check is expected to fail before the first migration — and to say so.
    first = run(sys.executable, "manage.py", "check")
    assert "python manage.py makemigrations" in first.stdout

    assert run(sys.executable, "manage.py", "makemigrations").returncode == 0
    assert run(sys.executable, "manage.py", "migrate").returncode == 0
    assert run(sys.executable, "manage.py", "makemigrations", "--check").returncode == 0
    assert run(sys.executable, "manage.py", "check").returncode == 0
