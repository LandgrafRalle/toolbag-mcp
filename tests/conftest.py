"""Test fixtures: the plugin's action module imported against ``fake_mset`` (a stand-in for
Toolbag's ``mset``), so actions run without Toolbag."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import fake_mset
import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"


def load_actions(fake):
    spec = importlib.util.spec_from_file_location("tbmcp_actions_under_test",
                                                  PLUGIN_DIR / "tbmcp_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fake = fake
    return module


@pytest.fixture
def actions(monkeypatch):
    """The action module against an empty fake scene (no default objects)."""
    fake = fake_mset.build()
    monkeypatch.setitem(sys.modules, "mset", fake)
    return load_actions(fake)


@pytest.fixture
def scene(actions, tmp_path, monkeypatch):
    """The action module with Toolbag's default scene loaded and a render folder under
    tmp_path. Returns the module; ``scene.fake`` is the fake mset."""
    fake = actions.fake
    fake._state["render_folder"] = str(tmp_path / "renders")
    fake.newScene()
    actions.STATE.update(plugin_version="test", port=1, allow_python=False, calls=0)
    return actions
