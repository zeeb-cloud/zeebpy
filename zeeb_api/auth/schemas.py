"""
Pydantic schemas for authentication.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    """
    Token response returned after successful authentication.
    
    Example:
        {
            "access_token": "eyJ...",
            "refresh_token": "eyJ...",
            "token_type": "bearer",
            "expires_in": 900
        }
    """
    
    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(description="Access token expiration in seconds")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "expires_in": 900
                }
            ]
        }
    }


class RefreshRequest(BaseModel):
    """
    Request to refresh access token.
    
    Example:
        {"refresh_token": "eyJ..."}
    """
    
    refresh_token: str = Field(description="JWT refresh token")


class UserInfo(BaseModel):
    """
    Basic user information returned from /auth/me.
    
    This is a minimal schema - extend it based on your User model.
    """
    
    id: str = Field(description="User ID")
    is_authenticated: bool = Field(default=True, description="Always true for authenticated users")
    claims: dict[str, Any] = Field(default_factory=dict, description="Additional claims from token")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "is_authenticated": True,
                    "claims": {"email": "user@example.com", "roles": ["user"]}
                }
            ]
        }
    }


class LogoutResponse(BaseModel):
    """Response after successful logout."""
    
    success: bool = Field(default=True)
    message: str = Field(default="Successfully logged out")
