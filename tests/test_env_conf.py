"""Tests for zeeb_api.conf.env — .env parsing, precedence and isolation."""

import os
import warnings
from pathlib import Path

import pytest

from zeeb_api.conf.env import (
    clear_env_cache,
    env_bool,
    env_int,
    env_list,
    env_oauth_providers,
    env_str,
    load_env,
    loaded_env_path,
    parse_env,
)


@pytest.fixture(autouse=True)
def _clean_layer():
    clear_env_cache()
    yield
    clear_env_cache()


def write_env(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_env_handles_comments_blanks_and_export():
    parsed = parse_env(
        "# leading comment\n"
        "\n"
        "  \n"
        "DEBUG=true\n"
        "export SECRET_KEY=abc123\n"
        "  SPACED  =  value  \n"
    )
    assert parsed == {"DEBUG": "true", "SECRET_KEY": "abc123", "SPACED": "value"}


def test_parse_env_strips_bom():
    assert parse_env("﻿DEBUG=true") == {"DEBUG": "true"}


def test_parse_env_handles_quotes_and_escapes():
    parsed = parse_env(
        'SINGLE=\'raw # not a comment\'\n'
        'DOUBLE="line\\nbreak"\n'
        'TABBED="a\\tb"\n'
        'ESCAPED_QUOTE="say \\"hi\\""\n'
        'BACKSLASH="a\\\\b"\n'
    )
    assert parsed["SINGLE"] == "raw # not a comment"
    assert parsed["DOUBLE"] == "line\nbreak"
    assert parsed["TABBED"] == "a\tb"
    assert parsed["ESCAPED_QUOTE"] == 'say "hi"'
    assert parsed["BACKSLASH"] == "a\\b"


def test_parse_env_inline_comment_needs_leading_whitespace():
    parsed = parse_env("A=value # trailing note\nB=has#hash\nC=frag#ment\n")
    assert parsed["A"] == "value"
    # No whitespace before '#', so it is part of the value — secrets and URL
    # fragments legitimately contain one.
    assert parsed["B"] == "has#hash"
    assert parsed["C"] == "frag#ment"


def test_parse_env_skips_malformed_lines():
    parsed = parse_env("NO_EQUALS_SIGN\n1BAD_KEY=x\nBAD-KEY=x\nGOOD=1\n")
    assert parsed == {"GOOD": "1"}


def test_parse_env_splits_on_first_equals_only():
    assert parse_env("DATABASE_URL=postgresql://u:p@h/db?a=b")["DATABASE_URL"] == (
        "postgresql://u:p@h/db?a=b"
    )


def test_parse_env_keeps_empty_value():
    assert parse_env("JWT_ISSUER=\n") == {"JWT_ISSUER": ""}


# ---------------------------------------------------------------------------
# load_env: search strategy
# ---------------------------------------------------------------------------


def test_load_env_accepts_a_file(tmp_path):
    path = write_env(tmp_path, "A=1\n")
    assert load_env(path) == path
    assert env_str("A") == "1"
    assert loaded_env_path() == path


def test_load_env_accepts_a_directory(tmp_path):
    path = write_env(tmp_path, "A=1\n")
    assert load_env(tmp_path) == path
    assert env_str("A") == "1"


def test_load_env_walks_up_from_cwd(tmp_path, monkeypatch):
    write_env(tmp_path, "A=1\n")
    nested = tmp_path / "pkg" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert load_env() == tmp_path / ".env"
    assert env_str("A") == "1"


def test_load_env_stops_at_the_project_root(tmp_path, monkeypatch):
    """A .env above manage.py belongs to another project and must not be read."""
    write_env(tmp_path, "OUTER=1\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "manage.py").write_text("")
    monkeypatch.chdir(project)

    assert load_env() is None
    assert env_str("OUTER") is None


def test_load_env_resets_the_layer_when_the_file_is_missing(tmp_path):
    load_env(write_env(tmp_path / "a", "A=1\n"))
    assert env_str("A") == "1"

    assert load_env(tmp_path / "nowhere" / ".env") is None
    assert env_str("A") is None
    assert loaded_env_path() is None


def test_load_env_never_raises_on_an_unreadable_file(tmp_path):
    # A directory named ".env" is not a regular file — must not blow up.
    (tmp_path / ".env").mkdir()
    assert load_env(tmp_path / ".env") is None


# ---------------------------------------------------------------------------
# Precedence and isolation
# ---------------------------------------------------------------------------


def test_real_environment_beats_the_file(tmp_path, monkeypatch):
    load_env(write_env(tmp_path, "SECRET_KEY=from-file\n"))
    monkeypatch.setenv("SECRET_KEY", "from-process")
    assert env_str("SECRET_KEY") == "from-process"


def test_an_empty_real_environment_value_still_beats_the_file(tmp_path, monkeypatch):
    load_env(write_env(tmp_path, "CORS_ALLOW_ORIGINS=http://a\n"))
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    assert env_list("CORS_ALLOW_ORIGINS", ["http://fallback"]) == []


def test_load_env_does_not_touch_os_environ(tmp_path):
    load_env(write_env(tmp_path, "ZEEB_TEST_ONLY_KEY=1\n"))
    assert "ZEEB_TEST_ONLY_KEY" not in os.environ
    assert env_str("ZEEB_TEST_ONLY_KEY") == "1"


def test_override_writes_os_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEEB_TEST_ONLY_KEY", raising=False)
    load_env(write_env(tmp_path, "ZEEB_TEST_ONLY_KEY=1\n"), override=True)
    try:
        assert os.environ["ZEEB_TEST_ONLY_KEY"] == "1"
    finally:
        os.environ.pop("ZEEB_TEST_ONLY_KEY", None)


def test_loading_a_second_project_does_not_inherit_the_first(tmp_path):
    """The regression this module's replace-not-merge design exists to prevent.

    The test suite scaffolds many projects in one process; project A's
    DATABASE_URL leaking into project B would make B silently share A's file.
    """
    load_env(write_env(tmp_path / "a", "SECRET_KEY=a-secret\nONLY_IN_A=1\n"))
    assert env_str("SECRET_KEY") == "a-secret"

    load_env(write_env(tmp_path / "b", "SECRET_KEY=b-secret\n"))
    assert env_str("SECRET_KEY") == "b-secret"
    assert env_str("ONLY_IN_A") is None


def test_clear_env_cache(tmp_path):
    load_env(write_env(tmp_path, "A=1\n"))
    clear_env_cache()
    assert env_str("A") is None
    assert loaded_env_path() is None


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------


def test_env_str_defaults_and_empty_values(tmp_path):
    load_env(write_env(tmp_path, "PRESENT=x\nEMPTY=\n"))
    assert env_str("PRESENT", "fallback") == "x"
    assert env_str("EMPTY", "fallback") == ""
    assert env_str("ABSENT", "fallback") == "fallback"
    assert env_str("ABSENT") is None


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Y", "on", "t", " true "])
def test_env_bool_truthy(tmp_path, raw):
    load_env(write_env(tmp_path, f"FLAG={raw}\n"))
    assert env_bool("FLAG") is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "n", "off", "f", ""])
def test_env_bool_falsy(tmp_path, raw):
    load_env(write_env(tmp_path, f"FLAG={raw}\n"))
    assert env_bool("FLAG", True) is False


def test_env_bool_unparsable_warns_and_uses_default(tmp_path):
    load_env(write_env(tmp_path, "FLAG=maybe\n"))
    with pytest.warns(UserWarning, match="not a boolean"):
        assert env_bool("FLAG", True) is True


def test_env_bool_missing_uses_default():
    assert env_bool("ABSENT_FLAG", True) is True
    assert env_bool("ABSENT_FLAG") is False


def test_env_int(tmp_path):
    load_env(write_env(tmp_path, "NUM=42\nPADDED= 7 \nBLANK=\n"))
    assert env_int("NUM", 1) == 42
    assert env_int("PADDED", 1) == 7
    assert env_int("BLANK", 5) == 5
    assert env_int("ABSENT", 9) == 9


def test_env_int_unparsable_warns_and_uses_default(tmp_path):
    load_env(write_env(tmp_path, "NUM=abc\n"))
    with pytest.warns(UserWarning, match="not an integer"):
        assert env_int("NUM", 5) == 5


def test_env_list(tmp_path):
    load_env(write_env(tmp_path, "ORIGINS=http://a, http://b ,\nEMPTY=\n"))
    assert env_list("ORIGINS") == ["http://a", "http://b"]
    assert env_list("EMPTY", ["http://fallback"]) == []
    assert env_list("ABSENT", ["http://fallback"]) == ["http://fallback"]
    assert env_list("ABSENT") == []


def test_env_list_returns_a_copy_of_the_default():
    default = ["http://a"]
    result = env_list("ABSENT", default)
    result.append("http://b")
    assert default == ["http://a"]


def test_env_list_custom_separator(tmp_path):
    load_env(write_env(tmp_path, "PATHS=a:b:c\n"))
    assert env_list("PATHS", separator=":") == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# env_oauth_providers
# ---------------------------------------------------------------------------


def test_env_oauth_providers_is_empty_without_credentials():
    assert env_oauth_providers() == {}


def test_env_oauth_providers_activates_on_client_id(tmp_path):
    load_env(
        write_env(
            tmp_path,
            "GOOGLE_CLIENT_ID=gid\nGOOGLE_CLIENT_SECRET=gsecret\nGITHUB_CLIENT_ID=hid\n",
        )
    )
    providers = env_oauth_providers()
    assert providers == {
        "google": {"client_id": "gid", "client_secret": "gsecret"},
        "github": {"client_id": "hid"},
    }


def test_env_oauth_providers_adds_the_azure_tenant(tmp_path):
    load_env(write_env(tmp_path, "AZURE_CLIENT_ID=aid\nAZURE_TENANT_ID=my-tenant\n"))
    assert env_oauth_providers()["azure"] == {
        "client_id": "aid",
        "tenant": "my-tenant",
    }


def test_env_oauth_providers_defaults_the_azure_tenant(tmp_path):
    load_env(write_env(tmp_path, "AZURE_CLIENT_ID=aid\n"))
    assert env_oauth_providers()["azure"]["tenant"] == "common"


def test_getters_do_not_warn_on_the_happy_path(tmp_path):
    load_env(write_env(tmp_path, "A=true\nB=3\nC=x,y\n"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert env_bool("A") is True
        assert env_int("B") == 3
        assert env_list("C") == ["x", "y"]
        assert env_str("A") == "true"
