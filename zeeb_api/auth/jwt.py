"""
JWT token creation and verification.

Provides functions to create and decode JWT tokens with
access/refresh token pattern.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pydantic import BaseModel, Field


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


# Global config instance
_jwt_config: JWTConfig | None = None


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


def get_jwt_config() -> JWTConfig:
    """Get the current JWT configuration."""
    global _jwt_config
    if _jwt_config is None:
        _jwt_config = JWTConfig()
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
