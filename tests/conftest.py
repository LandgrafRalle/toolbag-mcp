"""Test fixtures: a stand-in ``mset`` module so the plugin's action module imports and
runs outside Toolbag. Only what the registry tests need; a fuller fake is milestone M2."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"


def make_fake_mset() -> types.ModuleType:
    fake = types.ModuleType("mset")

    class SceneObject:
        def __init__(self, uid=0, name="", parent=None, visible=True):
            self.uid, self.name, self.parent, self.visible = uid, name, parent, visible
            self.locked = False
            self._children = []

        def getChildren(self):  # noqa: N802  (Toolbag naming)
            return list(self._children)

        def getBounds(self):  # noqa: N802
            return [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]

        def destroy(self):
            fake._objects.remove(self)

    class TransformObject(SceneObject):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.position, self.rotation, self.scale = [0.0] * 3, [0.0] * 3, [1.0] * 3
            self.pivot = [0.0] * 3

    class MeshObject(TransformObject):
        cullBackFaces = True

    class SubMeshObject(SceneObject):
        material = None
        startIndex, indexCount = 0, -1

    class BakerObject(SceneObject):
        pass

    class BakerTargetObject(SceneObject):
        pass

    class CameraObject(TransformObject):
        pass

    class LightObject(TransformObject):
        pass

    class Material:
        def __init__(self, name=""):
            self.name = name
            fake._materials.append(self)

    class Texture:
        def __init__(self, path=""):
            self.path, self.sRGB = path, True

    for cls in (SceneObject, TransformObject, MeshObject, SubMeshObject, BakerObject,
                BakerTargetObject, CameraObject, LightObject, Material, Texture):
        setattr(fake, cls.__name__, cls)

    fake._objects = []
    fake._materials = []
    fake._selection = []
    fake.callbacks = types.SimpleNamespace(onPeriodicUpdate=None, onFrameUpdate=None,
                                           onShutdownPlugin=None)
    fake.getAllObjects = lambda: list(fake._objects)
    fake.getAllMaterials = lambda: list(fake._materials)
    fake.getSelectedObjects = lambda: list(fake._selection)
    fake.setSelectedObjects = lambda objs: fake._selection.__init__(objs) or True
    fake.getToolbagVersion = lambda: 5022
    fake.getGraphicsAdapterName = lambda: "Fake GPU"
    fake.getScenePath = lambda: ""
    fake.getSceneUnitScale = lambda: 0.01
    fake.getSceneBounds = lambda: [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    fake.getPluginPath = lambda: str(PLUGIN_DIR / "__main__.py")
    fake.newScene = lambda: None
    fake.loadScene = lambda path, download=False: None
    return fake


@pytest.fixture
def actions(monkeypatch):
    """The plugin's action module imported against a fresh fake mset."""
    fake = make_fake_mset()
    monkeypatch.setitem(sys.modules, "mset", fake)
    spec = importlib.util.spec_from_file_location("tbmcp_actions_under_test",
                                                  PLUGIN_DIR / "tbmcp_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fake = fake
    return module
