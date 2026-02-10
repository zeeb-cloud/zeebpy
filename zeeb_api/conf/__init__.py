"""
Zeeb API configuration module.

Provides Django-style settings management.
"""

from zeeb_api.conf.settings import settings, get_settings, configure_settings

__all__ = ["settings", "get_settings", "configure_settings"]
