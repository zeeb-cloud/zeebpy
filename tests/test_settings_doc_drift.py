"""The settings reference must list every setting the framework reads.

``docs/configuration/settings.md`` is where a user looks up what they can
configure. Nothing tied it to ``zeeb_api/conf/default_settings.py``, so the
table had drifted: throttling, versioning, OAuth and logging were all missing.
"""

import re
from pathlib import Path

import pytest

from zeeb_api.conf import default_settings

DOC = Path(__file__).resolve().parent.parent / "docs" / "configuration" / "settings.md"


def framework_settings() -> set[str]:
    return {name for name in dir(default_settings) if name.isupper()}


def documented_names(text: str) -> set[str]:
    """Every ``NAME`` appearing as inline code or a heading in the doc."""
    names = set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", text))
    names |= set(re.findall(r"^#{2,4}\s+([A-Z][A-Z0-9_]{2,})\s*$", text, re.MULTILINE))
    return names


def test_every_setting_is_documented():
    missing = framework_settings() - documented_names(DOC.read_text())
    assert not missing, (
        "settings missing from docs/configuration/settings.md: " + ", ".join(sorted(missing))
    )


def reference_table() -> set[str]:
    """The names listed in the "Default Settings Reference" table.

    Anchored on the heading, not on the header row: the environment-variable
    table earlier in the page also has a "Setting" column.
    """
    text = DOC.read_text()
    table = text[text.index("## Default Settings Reference"):]
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`", table, re.MULTILINE))


def test_the_reference_table_covers_every_setting():
    tabled = reference_table()

    missing = framework_settings() - tabled
    assert not missing, "missing from the reference table: " + ", ".join(sorted(missing))


def test_the_reference_table_invents_nothing():
    unknown = reference_table() - framework_settings()
    assert not unknown, "documented but not a real setting: " + ", ".join(sorted(unknown))


@pytest.mark.parametrize(
    "variable",
    [
        "DEBUG",
        "SECRET_KEY",
        "DATABASE_URL",
        "CORS_ALLOW_ORIGINS",
        "AUTH_LOGIN_THROTTLE_RATE",
        "THROTTLE_ANON_RATE",
        "GOOGLE_CLIENT_ID",
        "LOG_LEVEL",
    ],
)
def test_environment_variables_are_documented(variable):
    """The scaffold is env-driven; the doc has to name the variables."""
    assert variable in DOC.read_text(), variable


def test_every_documented_env_var_exists_in_the_scaffold():
    """Guards against documenting a variable settings.py never reads."""
    from zeeb_orm.scaffold.project import ENV_EXAMPLE, SETTINGS_PY

    settings_source = SETTINGS_PY.format(project_name="demo")
    example = ENV_EXAMPLE.format(project_name="demo")

    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.MULTILINE))
    read_by_settings = set(re.findall(r'env_\w+\(\s*"([A-Z][A-Z0-9_]*)"', settings_source))
    # env_oauth_providers() derives these from the provider names.
    derived = {
        f"{provider}_{suffix}"
        for provider in ("GOOGLE", "GITHUB", "AZURE")
        for suffix in ("CLIENT_ID", "CLIENT_SECRET", "TENANT_ID")
    }

    stale = documented - read_by_settings - derived
    assert not stale, ".env.example documents unread variables: " + ", ".join(sorted(stale))
