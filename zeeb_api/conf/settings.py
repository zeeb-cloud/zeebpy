"""
Settings loader for Zeeb API.

Loads settings from the project's settings module and merges with defaults.
"""

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from zeeb_api.conf import default_settings


class Settings:
    """
    Lazy settings object that loads from project settings on first access.
    
    Similar to Django's LazySettings.
    """
    
    _wrapped: Optional[ModuleType] = None
    _explicit_settings: set
    
    def __init__(self):
        self._explicit_settings = set()
        self._wrapped = None
        self._configured = False
    
    def _setup(self, settings_module: Optional[str] = None):
        """
        Load settings from the specified module or auto-detect.
        """
        if settings_module is None:
            settings_module = os.environ.get("ZEEB_SETTINGS_MODULE")
        
        if settings_module is None:
            settings_module = self._auto_detect_settings()
        
        if settings_module:
            self._wrapped = importlib.import_module(settings_module)
            
            # Track which settings were explicitly set
            for setting in dir(self._wrapped):
                if setting.isupper():
                    self._explicit_settings.add(setting)
        
        self._configured = True
    
    def _auto_detect_settings(self) -> Optional[str]:
        """
        Find the settings module of the project the process runs in.

        Uses the ORM's single project resolver (``zeeb_orm.conf.project``):
        the nearest directory at or above the working directory that holds
        ``manage.py``, and in it the ``[tool.zeeb] settings_module`` that
        ``startproject`` writes to ``pyproject.toml`` (else the first
        top-level package, skipping ``apps/`` and tooling directories, with a
        ``settings.py``). The project root is put on ``sys.path``, as
        ``manage.py`` does. Outside a project nothing is detected.

        This used to import the first ``settings.py`` in any subdirectory of
        the working directory whose text merely mentioned ``DEBUG``.
        """
        from zeeb_orm.conf.project import find_project_root, find_settings_module

        root = find_project_root(Path.cwd())
        if root is None:
            return None
        module = find_settings_module(root)
        if module and str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return module

    def has_settings_module(self) -> bool:
        """Whether a settings module was loaded (not just runtime overrides)."""
        if not self._configured:
            self._setup()
        wrapped = self._wrapped
        return wrapped is not None and wrapped.__name__ != "runtime_settings"
    
    def __getattr__(self, name: str) -> Any:
        """
        Get a setting value.
        
        First checks the project settings, then falls back to defaults.
        """
        if name.startswith('_'):
            raise AttributeError(f"Settings has no attribute '{name}'")
        
        if not self._configured:
            self._setup()
        
        # Check project settings first
        if self._wrapped is not None and hasattr(self._wrapped, name):
            return getattr(self._wrapped, name)
        
        # Fall back to defaults
        if hasattr(default_settings, name):
            return getattr(default_settings, name)
        
        raise AttributeError(f"Settings has no attribute '{name}'")
    
    def __setattr__(self, name: str, value: Any):
        """
        Set a setting value at runtime.
        """
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            if not self._configured:
                self._setup()
            self._explicit_settings.add(name)
            if self._wrapped is None:
                # Create a dummy module to hold runtime settings
                self._wrapped = ModuleType("runtime_settings")
            setattr(self._wrapped, name, value)
    
    def is_configured(self) -> bool:
        """Check if settings have been configured."""
        return self._configured
    
    def configure(self, **options):
        """
        Configure settings programmatically (mainly for testing).
        """
        if self._configured:
            raise RuntimeError("Settings already configured.")
        
        # Create a module to hold the settings
        holder = ModuleType("programmatic_settings")
        for name, value in options.items():
            setattr(holder, name, value)
            self._explicit_settings.add(name)
        
        self._wrapped = holder
        self._configured = True
    
    def is_overridden(self, setting: str) -> bool:
        """
        Check if a setting was explicitly set (vs using default).
        """
        return setting in self._explicit_settings
    
    def get_jwt_secret_key(self) -> str:
        """
        Get the JWT secret key, falling back to SECRET_KEY if not set.
        """
        jwt_key = getattr(self, 'JWT_SECRET_KEY', None)
        if jwt_key:
            return jwt_key
        return self.SECRET_KEY
    
    def as_dict(self) -> dict:
        """
        Return all settings as a dictionary.
        """
        result = {}
        
        # Start with defaults
        for name in dir(default_settings):
            if name.isupper():
                result[name] = getattr(default_settings, name)
        
        # Override with project settings
        if self._wrapped:
            for name in dir(self._wrapped):
                if name.isupper():
                    result[name] = getattr(self._wrapped, name)
        
        return result


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """
    Get the global settings instance.
    """
    return settings


def configure_settings(settings_module: Optional[str] = None, **options):
    """
    Configure settings from a module or with explicit options.
    
    Args:
        settings_module: Dotted path to settings module (e.g., "myproject.settings")
        **options: Explicit settings to override
    """
    if settings_module:
        os.environ["ZEEB_SETTINGS_MODULE"] = settings_module
        settings._setup(settings_module)
    
    if options:
        for name, value in options.items():
            setattr(settings, name, value)
