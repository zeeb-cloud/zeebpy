"""
Zeeb API configuration module.

Provides Django-style settings management.
"""

from zeeb_api.conf.env import (
    clear_env_cache,
    env_bool,
    env_int,
    env_list,
    env_oauth_providers,
    env_str,
    load_env,
)
from zeeb_api.conf.orm import apply_orm_settings
from zeeb_api.conf.settings import configure_settings, get_settings, settings

__all__ = [
    "settings",
    "get_settings",
    "configure_settings",
    "apply_orm_settings",
    "load_env",
    "clear_env_cache",
    "env_str",
    "env_bool",
    "env_int",
    "env_list",
    "env_oauth_providers",
]
