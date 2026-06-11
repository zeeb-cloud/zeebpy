"""
Zeeb API configuration module.

Provides Django-style settings management.
"""

from zeeb_api.conf.orm import apply_orm_settings
from zeeb_api.conf.settings import configure_settings, get_settings, settings

__all__ = ["settings", "get_settings", "configure_settings", "apply_orm_settings"]
