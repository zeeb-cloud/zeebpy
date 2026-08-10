"""Tests for JWT insecure-secret hardening."""

import logging

import pytest

import zeeb_api.auth.jwt as jwt_module
from zeeb_api.auth.jwt import (
    INSECURE_SECRETS,
    JWTConfig,
    TokenInvalidError,
    configure_jwt,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from zeeb_api.conf import settings
from zeeb_api.exceptions import ImproperlyConfigured, InsecureSecretError


@pytest.fixture(autouse=True)
def reset_jwt_state():
    """Save/restore global JWT config, warn-once flag and settings.DEBUG."""
    saved_config = jwt_module._jwt_config
    saved_warned = jwt_module._insecure_secret_warned
    saved_debug = getattr(settings, "DEBUG", False)
    saved_secret = settings.get_jwt_secret_key()

    jwt_module._jwt_config = None
    jwt_module._insecure_secret_warned = False

    yield

    jwt_module._jwt_config = saved_config
    jwt_module._insecure_secret_warned = saved_warned
    settings.DEBUG = saved_debug
    settings.SECRET_KEY = saved_secret


class TestJWTConfig:
    def test_default_secret_is_insecure(self):
        assert JWTConfig().is_insecure

    @pytest.mark.parametrize("secret", sorted(INSECURE_SECRETS))
    def test_known_insecure_secrets(self, secret):
        assert JWTConfig(secret_key=secret).is_insecure

    def test_real_secret_is_secure(self):
        assert not JWTConfig(secret_key="a-real-strong-secret-of-at-least-32-bytes").is_insecure


class TestInsecureSecretRefusal:
    def test_create_access_token_refused_without_debug(self):
        settings.DEBUG = False
        with pytest.raises(InsecureSecretError):
            create_access_token("user-1")

    def test_create_refresh_token_refused_without_debug(self):
        settings.DEBUG = False
        with pytest.raises(InsecureSecretError):
            create_refresh_token("user-1")

    def test_decode_token_refused_without_debug(self):
        settings.DEBUG = False
        with pytest.raises(InsecureSecretError):
            decode_token("not.a.token")

    def test_debug_true_allows_with_single_warning(self, caplog):
        settings.DEBUG = True
        with caplog.at_level(logging.WARNING, logger="zeeb_api.auth.jwt"):
            token = create_access_token("user-1")
            payload = decode_token(token, token_type="access")
        assert payload.sub == "user-1"
        warning_records = [
            r for r in caplog.records
            if r.name == "zeeb_api.auth.jwt" and r.levelno == logging.WARNING
        ]
        assert len(warning_records) == 1
        assert "insecure default" in warning_records[0].getMessage()

    def test_real_secret_works_without_debug(self):
        settings.DEBUG = False
        configure_jwt(secret_key="a-real-strong-secret-of-at-least-32-bytes")
        token = create_access_token("user-2")
        payload = decode_token(token, token_type="access")
        assert payload.sub == "user-2"


class TestIssuerValidation:
    """The `iss` claim is written but was never verified (M4)."""

    SECRET = "a-real-strong-secret-of-at-least-32-bytes"

    def test_matching_issuer_is_accepted(self):
        cfg = JWTConfig(secret_key=self.SECRET, issuer="https://issuer-a")
        token = create_access_token("u1", config=cfg)
        assert decode_token(token, token_type="access", config=cfg).sub == "u1"

    def test_wrong_issuer_is_rejected(self):
        cfg_a = JWTConfig(secret_key=self.SECRET, issuer="https://issuer-a")
        cfg_b = JWTConfig(secret_key=self.SECRET, issuer="https://issuer-b")
        token = create_access_token("u1", config=cfg_a)
        with pytest.raises(TokenInvalidError):
            decode_token(token, token_type="access", config=cfg_b)

    def test_missing_issuer_is_rejected_when_configured(self):
        cfg_none = JWTConfig(secret_key=self.SECRET)  # token carries no iss
        cfg_req = JWTConfig(secret_key=self.SECRET, issuer="https://issuer-a")
        token = create_access_token("u1", config=cfg_none)
        with pytest.raises(TokenInvalidError):
            decode_token(token, token_type="access", config=cfg_req)

    def test_no_issuer_config_still_accepts_plain_token(self):
        cfg = JWTConfig(secret_key=self.SECRET)
        token = create_access_token("u1", config=cfg)
        assert decode_token(token, token_type="access", config=cfg).sub == "u1"


class TestCreateAppFailFast:
    def test_create_app_refuses_insecure_secret_without_debug(self):
        from zeeb_api import create_app

        with pytest.raises(ImproperlyConfigured, match="insecure default"):
            create_app(DEBUG=False)

    def test_create_app_succeeds_with_debug(self):
        from zeeb_api import create_app

        app = create_app(DEBUG=True)
        assert app is not None

    def test_create_app_succeeds_with_real_secret(self):
        from zeeb_api import create_app

        app = create_app(DEBUG=False, SECRET_KEY="a-real-strong-secret-of-at-least-32-bytes")
        assert app is not None
