"""Configuration hygiene: DEFAULT_LIMIT/MAX_LIMIT wiring (C1), logging from the
LOGGING setting (C2), and the reconciled JWT expiry default (C4)."""

import pytest

from zeeb_api.conf import settings


@pytest.fixture
def restore_settings():
    keys = ["DEFAULT_LIMIT", "MAX_LIMIT", "LOGGING"]
    saved = {k: getattr(settings, k, None) for k in keys}
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


# --------------------------------------------------------------------------- #
# C1: DEFAULT_LIMIT / MAX_LIMIT are honored (were previously dead settings)
# --------------------------------------------------------------------------- #


def test_default_settings_declare_pagination_limits():
    from zeeb_api.conf import default_settings

    assert default_settings.DEFAULT_LIMIT == 20
    assert default_settings.MAX_LIMIT == 100


def test_limitoffset_honors_settings(restore_settings):
    from starlette.requests import Request

    from zeeb_api.pagination import LimitOffsetPagination

    settings.DEFAULT_LIMIT = 7
    settings.MAX_LIMIT = 30

    p = LimitOffsetPagination()

    def _req(qs: bytes) -> Request:
        return Request({"type": "http", "query_string": qs, "headers": []})

    # No explicit limit -> uses DEFAULT_LIMIT.
    assert p._get_limit(_req(b"")) == 7
    # Above MAX_LIMIT -> clamped to MAX_LIMIT.
    assert p._get_limit(_req(b"limit=999")) == 30


def test_limitoffset_subclass_override_wins_over_settings(restore_settings):
    from starlette.requests import Request

    from zeeb_api.pagination import LimitOffsetPagination

    settings.DEFAULT_LIMIT = 7

    class Fixed(LimitOffsetPagination):
        default_limit = 5

    p = Fixed()
    req = Request({"type": "http", "query_string": b"", "headers": []})
    assert p._get_limit(req) == 5


# --------------------------------------------------------------------------- #
# C4: JWT access-token expiry default is a single reconciled value (60)
# --------------------------------------------------------------------------- #


def test_jwt_default_expiry_is_consistent():
    from zeeb_api.auth.jwt import JWTConfig
    from zeeb_api.conf import default_settings

    assert JWTConfig().access_token_expire_minutes == 60
    assert default_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES == 60


# --------------------------------------------------------------------------- #
# C2: create_app configures logging from the LOGGING setting
# --------------------------------------------------------------------------- #


def test_create_app_configures_logging_from_setting(restore_settings, monkeypatch):
    import zeeb_api.logging as logging_module

    called = {}

    def _fake_configure_logging(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(logging_module, "configure_logging", _fake_configure_logging)

    from zeeb_api import create_app

    create_app(
        DEBUG=True,
        LOGGING={"level": "WARNING", "json_logs": True, "log_file": "x.log"},
    )

    assert called["level"] == "WARNING"
    assert called["json_logs"] is True
    assert called["log_file"] == "x.log"
    # Rotation and retention are forwarded too, so a project never has to call
    # configure_logging() itself — a second call resets the root handlers.
    assert called["log_rotation"] is True
    assert called["log_retention_days"] == 30


def test_create_app_forwards_the_full_logging_setting(restore_settings, monkeypatch):
    import zeeb_api.logging as logging_module

    called = {}
    monkeypatch.setattr(
        logging_module, "configure_logging", lambda **kw: called.update(kw)
    )

    from zeeb_api import create_app

    create_app(
        DEBUG=True,
        LOGGING={
            "level": "ERROR",
            "json_logs": False,
            "log_file": "app.log",
            "log_rotation": False,
            "log_retention_days": 7,
            "include_uvicorn": False,
            "include_sqlalchemy": True,
        },
    )

    assert called == {
        "level": "ERROR",
        "json_logs": False,
        "log_file": "app.log",
        "log_rotation": False,
        "log_retention_days": 7,
        "include_uvicorn": False,
        "include_sqlalchemy": True,
    }


def test_create_app_without_logging_does_not_configure(restore_settings, monkeypatch):
    import zeeb_api.logging as logging_module

    called = {"n": 0}

    def _fake_configure_logging(**kwargs):
        called["n"] += 1

    monkeypatch.setattr(logging_module, "configure_logging", _fake_configure_logging)
    settings.LOGGING = None

    from zeeb_api import create_app

    create_app(DEBUG=True)
    # No LOGGING setting -> we must not clobber an externally configured root.
    assert called["n"] == 0
