"""
JWT token creation and verification.

Provides functions to create and decode JWT tokens with
access/refresh token pattern.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pydantic import BaseModel, Field

from zeeb_api.exceptions import InsecureSecretError

logger = logging.getLogger(__name__)

# Known insecure default secret keys that must never be used in production.
INSECURE_SECRETS = frozenset({
    "change-me-in-production",
    "INSECURE-DEFAULT-KEY-CHANGE-IN-PRODUCTION",
    "your-secret-key-change-in-production",
})


class JWTConfig(BaseModel):
    """JWT configuration settings."""

    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for signing tokens"
    )
    algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )
    access_token_expire_minutes: int = Field(
        default=15,
        description="Access token expiration in minutes"
    )
    refresh_token_expire_days: int = Field(
        default=7,
        description="Refresh token expiration in days"
    )
    issuer: str | None = Field(
        default=None,
        description="Token issuer (iss claim)"
    )
    audience: str | None = Field(
        default=None,
        description="Token audience (aud claim)"
    )

    @property
    def is_insecure(self) -> bool:
        """True when the secret key is a known insecure default."""
        return self.secret_key in INSECURE_SECRETS


# Global config instance
_jwt_config: JWTConfig | None = None

# Module-level flag so the insecure-secret DEBUG warning is only logged once.
_insecure_secret_warned = False


def _ensure_secure_secret(config: JWTConfig) -> None:
    """
    Refuse to operate with a known insecure default secret key.

    - DEBUG falsy (or settings unavailable): raise InsecureSecretError.
    - DEBUG truthy: log a warning once per process and continue.
    """
    global _insecure_secret_warned

    if not config.is_insecure:
        return

    try:
        from zeeb_api.conf import settings
        debug = bool(getattr(settings, "DEBUG", False))
    except Exception:
        debug = False

    if not debug:
        raise InsecureSecretError()

    if not _insecure_secret_warned:
        logger.warning(
            "JWT secret key is an insecure default value. This is allowed "
            "because DEBUG is enabled, but tokens signed with this key are "
            "NOT safe for production. Set SECRET_KEY (or JWT_SECRET_KEY) to "
            "a strong, unique secret."
        )
        _insecure_secret_warned = True


def configure_jwt(
    secret_key: str | None = None,
    algorithm: str | None = None,
    access_token_expire_minutes: int | None = None,
    refresh_token_expire_days: int | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    **kwargs: Any,
) -> JWTConfig:
    """
    Configure JWT settings.
    
    Usage:
        configure_jwt(
            secret_key="your-secret-key",
            access_token_expire_minutes=30,
            refresh_token_expire_days=14
        )
    """
    global _jwt_config
    
    config_dict: dict[str, Any] = {}
    if secret_key is not None:
        config_dict["secret_key"] = secret_key
    if algorithm is not None:
        config_dict["algorithm"] = algorithm
    if access_token_expire_minutes is not None:
        config_dict["access_token_expire_minutes"] = access_token_expire_minutes
    if refresh_token_expire_days is not None:
        config_dict["refresh_token_expire_days"] = refresh_token_expire_days
    if issuer is not None:
        config_dict["issuer"] = issuer
    if audience is not None:
        config_dict["audience"] = audience
    
    _jwt_config = JWTConfig(**config_dict)
    return _jwt_config


def _config_from_settings() -> JWTConfig:
    """Build a JWTConfig from the project's settings.

    Mirrors the wiring the ``create_app`` factory does via ``configure_jwt``
    (``zeeb_api/app.py``), but lazily — so a scaffolded project whose ``asgi.py``
    never calls that factory still signs/verifies tokens with its own
    ``SECRET_KEY`` and honors the ``JWT_*`` lifetimes written by ``setup_auth``,
    instead of silently using the built-in insecure default. Falls back to the
    plain default when settings are unavailable.
    """
    try:
        from zeeb_api.conf import settings

        return JWTConfig(
            secret_key=settings.get_jwt_secret_key(),
            algorithm=getattr(settings, "JWT_ALGORITHM", None) or "HS256",
            access_token_expire_minutes=getattr(
                settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", None
            )
            or 15,
            refresh_token_expire_days=getattr(
                settings, "JWT_REFRESH_TOKEN_EXPIRE_DAYS", None
            )
            or 7,
            issuer=getattr(settings, "JWT_ISSUER", None),
            audience=getattr(settings, "JWT_AUDIENCE", None),
        )
    except Exception:
        return JWTConfig()


def get_jwt_config() -> JWTConfig:
    """Get the current JWT configuration.

    An explicit ``configure_jwt(...)`` call always wins. Otherwise the config is
    lazily derived from project settings on first use (see
    ``_config_from_settings``).
    """
    global _jwt_config
    if _jwt_config is None:
        _jwt_config = _config_from_settings()
    return _jwt_config


class TokenPayload(BaseModel):
    """JWT token payload structure."""
    
    sub: str = Field(description="Subject (user ID)")
    type: str = Field(description="Token type: 'access' or 'refresh'")
    exp: datetime = Field(description="Expiration time")
    iat: datetime = Field(description="Issued at time")
    jti: str = Field(description="JWT ID (unique token identifier)")
    
    # Optional claims
    iss: str | None = Field(default=None, description="Issuer")
    aud: str | None = Field(default=None, description="Audience")
    
    # Custom claims
    claims: dict[str, Any] = Field(default_factory=dict, description="Additional claims")


def create_access_token(
    user_id: str | uuid.UUID,
    claims: dict[str, Any] | None = None,
    config: JWTConfig | None = None,
) -> str:
    """
    Create an access token.
    
    Args:
        user_id: User identifier (will be stored in 'sub' claim)
        claims: Additional claims to include in the token
        config: Optional JWT config (uses global config if not provided)
    
    Returns:
        Encoded JWT access token
    """
    config = config or get_jwt_config()
    _ensure_secure_secret(config)
    now = datetime.now(timezone.utc)
    
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": now + timedelta(minutes=config.access_token_expire_minutes),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    
    if config.issuer:
        payload["iss"] = config.issuer
    if config.audience:
        payload["aud"] = config.audience
    if claims:
        payload["claims"] = claims
    
    return jwt.encode(payload, config.secret_key, algorithm=config.algorithm)


def create_refresh_token(
    user_id: str | uuid.UUID,
    config: JWTConfig | None = None,
) -> str:
    """
    Create a refresh token.
    
    Args:
        user_id: User identifier
        config: Optional JWT config
    
    Returns:
        Encoded JWT refresh token
    """
    config = config or get_jwt_config()
    _ensure_secure_secret(config)
    now = datetime.now(timezone.utc)
    
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": now + timedelta(days=config.refresh_token_expire_days),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    
    if config.issuer:
        payload["iss"] = config.issuer
    if config.audience:
        payload["aud"] = config.audience
    
    return jwt.encode(payload, config.secret_key, algorithm=config.algorithm)


def create_token_pair(
    user_id: str | uuid.UUID,
    claims: dict[str, Any] | None = None,
    config: JWTConfig | None = None,
) -> tuple[str, str]:
    """
    Create both access and refresh tokens.
    
    Args:
        user_id: User identifier
        claims: Additional claims for access token
        config: Optional JWT config
    
    Returns:
        Tuple of (access_token, refresh_token)
    """
    config = config or get_jwt_config()
    access_token = create_access_token(user_id, claims, config)
    refresh_token = create_refresh_token(user_id, config)
    return access_token, refresh_token


class TokenError(Exception):
    """Base exception for token errors."""
    
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class TokenExpiredError(TokenError):
    """Token has expired."""
    
    def __init__(self, message: str = "Token has expired"):
        super().__init__("AUTH_TOKEN_EXPIRED", message)


class TokenInvalidError(TokenError):
    """Token is invalid."""
    
    def __init__(self, message: str = "Invalid token"):
        super().__init__("AUTH_TOKEN_INVALID", message)


def decode_token(
    token: str,
    token_type: str | None = None,
    config: JWTConfig | None = None,
) -> TokenPayload:
    """
    Decode and verify a JWT token.
    
    Args:
        token: The JWT token string
        token_type: Expected token type ('access' or 'refresh'), None to skip check
        config: Optional JWT config
    
    Returns:
        TokenPayload with decoded claims
    
    Raises:
        TokenExpiredError: If token has expired
        TokenInvalidError: If token is invalid or wrong type
    """
    config = config or get_jwt_config()
    _ensure_secure_secret(config)

    try:
        # Decode with verification
        options = {}
        if config.audience:
            options["audience"] = config.audience
        
        payload = jwt.decode(
            token,
            config.secret_key,
            algorithms=[config.algorithm],
            options={"require": ["sub", "type", "exp", "iat", "jti"]},
            **options,
        )
        
        # Check token type
        if token_type and payload.get("type") != token_type:
            raise TokenInvalidError(f"Expected {token_type} token, got {payload.get('type')}")
        
        # Parse to TokenPayload
        return TokenPayload(
            sub=payload["sub"],
            type=payload["type"],
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            iat=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
            jti=payload["jti"],
            iss=payload.get("iss"),
            aud=payload.get("aud"),
            claims=payload.get("claims", {}),
        )
        
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except jwt.InvalidTokenError as e:
        raise TokenInvalidError(str(e))
