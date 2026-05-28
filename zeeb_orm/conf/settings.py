"""Configuration management for Zeeb ORM."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatabaseConfig:
    """Database connection configuration."""

    url: str
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    connect_args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str = "DATABASE") -> DatabaseConfig:
        """Create config from environment variables."""
        return cls(
            url=os.environ.get(f"{prefix}_URL", "sqlite+aiosqlite:///./db.sqlite3"),
            echo=os.environ.get(f"{prefix}_ECHO", "").lower() == "true",
            pool_size=int(os.environ.get(f"{prefix}_POOL_SIZE", "5")),
            max_overflow=int(os.environ.get(f"{prefix}_MAX_OVERFLOW", "10")),
            pool_timeout=int(os.environ.get(f"{prefix}_POOL_TIMEOUT", "30")),
            pool_recycle=int(os.environ.get(f"{prefix}_POOL_RECYCLE", "1800")),
            pool_pre_ping=os.environ.get(f"{prefix}_POOL_PRE_PING", "true").lower() != "false",
        )


@dataclass
class Settings:
    """Global ORM settings."""

    database: DatabaseConfig
    migrations_dir: str = "migrations"
    auto_create_tables: bool = False
    timezone: str = "UTC"

    _instance: Settings | None = None

    @classmethod
    def configure(cls, **kwargs: Any) -> Settings:
        """Configure global settings."""
        if "database" not in kwargs:
            kwargs["database"] = DatabaseConfig.from_env()
        elif isinstance(kwargs["database"], dict):
            kwargs["database"] = DatabaseConfig(**kwargs["database"])
        cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def get(cls) -> Settings:
        """Get current settings, creating default if needed."""
        if cls._instance is None:
            cls._instance = cls(database=DatabaseConfig.from_env())
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset settings to None (for testing)."""
        cls._instance = None


def configure(**kwargs: Any) -> Settings:
    """Shortcut to configure settings."""
    return Settings.configure(**kwargs)


def get_settings() -> Settings:
    """Shortcut to get settings."""
    return Settings.get()
