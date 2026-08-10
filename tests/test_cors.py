"""CORSMiddleware settings wiring and the wildcard+credentials guard (M1)."""

import logging

import pytest

from zeeb_api.conf import settings
from zeeb_api.exceptions import ImproperlyConfigured
from zeeb_api.middleware.cors import CORSMiddleware


@pytest.fixture(autouse=True)
def restore_debug():
    saved = getattr(settings, "DEBUG", False)
    yield
    settings.DEBUG = saved


def _app(_scope, _receive, _send):  # minimal ASGI app stand-in
    return None


def test_explicit_origins_with_credentials_is_allowed():
    mw = CORSMiddleware(
        _app,
        allow_origins=["https://app.example.com"],
        allow_credentials=True,
    )
    assert "https://app.example.com" in mw.allow_origins


def test_wildcard_without_credentials_is_allowed():
    mw = CORSMiddleware(_app, allow_origins=["*"], allow_credentials=False)
    assert mw is not None


def test_wildcard_with_credentials_refused_outside_debug():
    settings.DEBUG = False
    with pytest.raises(ImproperlyConfigured, match="wildcard"):
        CORSMiddleware(_app, allow_origins=["*"], allow_credentials=True)


def test_wildcard_with_credentials_warns_in_debug(caplog):
    settings.DEBUG = True
    with caplog.at_level(logging.WARNING, logger="zeeb_api.middleware.cors"):
        mw = CORSMiddleware(_app, allow_origins=["*"], allow_credentials=True)
    assert mw is not None
    assert any("Insecure CORS" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Origin regex: for frontends whose hostname changes on every deploy.
# ---------------------------------------------------------------------------

LOVABLE_PATTERN = r"https://.*\.(lovable\.app|lovableproject\.com)$"


def test_an_origin_regex_admits_a_preview_host():
    mw = CORSMiddleware(
        _app,
        allow_origins=[],
        allow_origin_regex=LOVABLE_PATTERN,
        allow_credentials=True,
    )
    assert mw.allow_origin_regex.match("https://my-app.lovableproject.com")
    assert mw.allow_origin_regex.match("https://preview-42.lovable.app")
    # Anchored: a lookalike host does not slip through.
    assert mw.allow_origin_regex.match("https://evil.lovable.app.attacker.com") is None
    assert mw.allow_origin_regex.match("https://lovable.app.evil.com") is None


def test_a_catch_all_regex_with_credentials_is_refused_outside_debug():
    """The same hole as allow_origins=['*'], wearing a different hat."""
    settings.DEBUG = False
    for pattern in (".*", "^.*$", ".+"):
        with pytest.raises(ImproperlyConfigured, match="catch-all"):
            CORSMiddleware(
                _app, allow_origins=[], allow_origin_regex=pattern, allow_credentials=True
            )


def test_a_catch_all_regex_without_credentials_is_allowed():
    settings.DEBUG = False
    assert CORSMiddleware(
        _app, allow_origins=[], allow_origin_regex=".*", allow_credentials=False
    ) is not None


def test_an_invalid_regex_is_refused_at_boot():
    """Otherwise it raises inside Starlette on the first preflight in production."""
    settings.DEBUG = True
    with pytest.raises(ImproperlyConfigured, match="not a valid regular expression"):
        CORSMiddleware(_app, allow_origins=[], allow_origin_regex="https://[")


def test_the_middleware_activates_on_a_regex_alone():
    """A preview frontend is configured through the regex only; skipping the
    middleware there would silently break every browser request."""
    from zeeb_api.middleware.loader import install_middleware

    installed = []

    class FakeApp:
        def add_middleware(self, cls, **kwargs):
            installed.append(cls)

    class FakeSettings:
        MIDDLEWARE = ["zeeb_api.middleware.CORSMiddleware"]
        CORS_ALLOW_ORIGINS = []
        CORS_ALLOW_ORIGIN_REGEX = LOVABLE_PATTERN

    install_middleware(FakeApp(), FakeSettings())
    assert installed == [CORSMiddleware]

    class NothingConfigured(FakeSettings):
        CORS_ALLOW_ORIGIN_REGEX = None

    installed.clear()
    install_middleware(FakeApp(), NothingConfigured())
    assert installed == []
