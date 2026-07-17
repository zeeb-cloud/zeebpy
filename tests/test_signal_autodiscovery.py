"""Signal-module autodiscovery in create_app (G11).

``create_app()`` imports ``<app>.signals`` for every ``INSTALLED_APPS`` entry
so receivers scaffolded by ``create_signal_receiver`` connect at startup. Only
the signals module itself being absent is skippable; a broken import *inside*
an existing signals.py must fail startup loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zeeb_api.app import _autodiscover_signal_modules


class _Settings:
    def __init__(self, installed_apps):
        self.INSTALLED_APPS = installed_apps


@pytest.fixture
def apps_tree(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in list(sys.modules):
        if name == "apps" or name.startswith("apps."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    yield tmp_path
    for name in list(sys.modules):
        if name == "apps" or name.startswith("apps."):
            del sys.modules[name]


def _make_app(root: Path, name: str, signals_body: str | None = None) -> None:
    app_dir = root / "apps" / name
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    if signals_body is not None:
        (app_dir / "signals.py").write_text(signals_body)


def test_autodiscovery_imports_signal_modules(apps_tree: Path):
    _make_app(apps_tree, "blog", "LOADED = True\n")
    _make_app(apps_tree, "shop")  # no signals.py — must be skipped silently
    _autodiscover_signal_modules(_Settings(["apps.blog", "apps.shop"]))
    assert sys.modules["apps.blog.signals"].LOADED is True


def test_autodiscovery_tolerates_missing_settings_key(apps_tree: Path):
    _autodiscover_signal_modules(_Settings([]))
    _autodiscover_signal_modules(_Settings([None, 42]))  # non-str entries skipped


def test_autodiscovery_propagates_broken_imports(apps_tree: Path):
    _make_app(apps_tree, "blog", "import definitely_not_a_real_module_xyz\n")
    with pytest.raises(ModuleNotFoundError, match="definitely_not_a_real_module_xyz"):
        _autodiscover_signal_modules(_Settings(["apps.blog"]))
