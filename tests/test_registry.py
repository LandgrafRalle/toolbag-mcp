"""The action registry: catalogue, parameter checks, references and the safety rules that
do not need a real Toolbag."""

import pytest


def test_every_action_is_documented(actions):
    catalogue = actions.describe()
    assert len(catalogue) >= 50
    for name, entry in catalogue.items():
        assert "." in name or name in ("status", "capabilities"), name
        assert entry["doc"], "%s has no docstring" % name
        for param, spec in entry["params"].items():
            assert set(spec) == {"required", "default"}, (name, param)


def test_unknown_action(actions):
    with pytest.raises(actions.ActionError, match="Unknown action"):
        actions.run("scene.explode", {})


def test_wrong_parameters_are_explained(actions):
    with pytest.raises(actions.ActionError, match=r"Parameters: status\(\)"):
        actions.run("status", {"nope": 1})
    with pytest.raises(actions.ActionError, match="missing a required argument"):
        actions.run("scene.load", {})


def test_status_against_fake(actions):
    actions.STATE.update(plugin_version="test", port=1, allow_python=False, calls=0)
    result = actions.run("status", {})
    assert result["toolbag_version"] == 5022
    assert result["toolbag_version_string"] == "5.02.2"
    assert result["plugin"]["plugin_version"] == "test"


def test_resolve_by_uid_name_and_ambiguity(actions):
    fake = actions.fake
    a = fake.MeshObject(uid=1, name="Body")
    b = fake.MeshObject(uid=2, name="Body")
    c = fake.SceneObject(uid=3, name="Group")
    fake._objects.extend([a, b, c])
    assert actions.resolve(3) is c
    assert actions.resolve("group") is c
    with pytest.raises(actions.ActionError, match="Ambiguous"):
        actions.resolve("Body")
    with pytest.raises(actions.ActionError, match="No object"):
        actions.resolve("Nothing")
    with pytest.raises(actions.ActionError, match="of type MeshObject"):
        actions.resolve(3, fake.MeshObject)


def test_visibility_guards(actions):
    fake = actions.fake
    group = fake.SceneObject(uid=1, name="Group")
    mesh = fake.MeshObject(uid=2, name="Mesh", parent=group)
    sub = fake.SubMeshObject(uid=3, name="Sub", parent=mesh)
    fake._objects.extend([group, mesh, sub])
    with pytest.raises(actions.ActionError, match="SubMeshObject"):
        actions.run("object.set_visible", {"refs": [3], "visible": False})
    with pytest.raises(actions.ActionError, match="allow_mesh"):
        actions.run("object.set_visible", {"refs": "Mesh", "visible": False})
    assert mesh.visible is True
    actions.run("object.set_visible", {"refs": "Mesh", "visible": False, "allow_mesh": True})
    assert mesh.visible is False
    result = actions.run("object.set_visible", {"refs": [1], "visible": False})
    assert group.visible is False
    assert result["objects"][0]["uid"] == 1


def test_python_exec_is_gated(actions):
    actions.STATE["allow_python"] = False
    with pytest.raises(actions.ActionError, match="disabled"):
        actions.run("python.exec", {"code": "result = 1"})
    actions.STATE["allow_python"] = True
    assert actions.run("python.exec", {"code": "result = 2 + 2"}) == {"result": 4}
    version = actions.run("python.exec", {"code": "result = mset.getToolbagVersion()"})
    assert version == {"result": 5022}


def test_export_rejects_unknown_extension_and_options(actions, tmp_path):
    with pytest.raises(actions.ActionError, match="Unsupported extension"):
        actions.run("scene.export", {"path": str(tmp_path / "scene.xyz")})
    with pytest.raises(actions.ActionError, match="Unknown export options"):
        actions.run("scene.export", {"path": str(tmp_path / "scene.obj"),
                                     "options": {"lights": True}})


def test_material_slot_names(actions):
    with pytest.raises(actions.ActionError, match="Unknown material slot"):
        actions._slot("glitter")
    assert actions._slot("Transparency") == "transparency"


def test_find_map_by_suffix_class_and_plural(actions):
    def make(class_name, suffix):
        cls = type(class_name, (), {})
        bmap = cls()
        bmap.suffix, bmap.enabled = suffix, False
        return bmap

    maps = [make("NormalBakerMap", "normal"), make("AOBakerMap", "ao"),
            make("CurvatureBakerMap", "curve")]
    baker = type("Baker", (), {"getAllMaps": lambda self: maps,
                               "getMap": lambda self, name: None})()
    assert actions._find_map(baker, "normals") is maps[0]
    assert actions._find_map(baker, "Normal") is maps[0]
    assert actions._find_map(baker, "AO") is maps[1]
    assert actions._find_map(baker, "curvature") is maps[2]
    with pytest.raises(actions.ActionError, match="Unknown map"):
        actions._find_map(baker, "glitter")


def test_scene_operations_are_prepared_two_phased(actions, tmp_path):
    fake = actions.fake
    with pytest.raises(actions.ActionError, match="Parameters"):
        actions.prepare_scene_op("scene.load", {})
    with pytest.raises(actions.ActionError, match="File not found"):
        actions.prepare_scene_op("scene.load", {"path": str(tmp_path / "missing.tbscene")})
    assert actions.prepare_scene_op("scene.new", {}) is fake.newScene
    scene = tmp_path / "scene.tbscene"
    scene.write_text("x")
    calls = []
    fake.loadScene = lambda path, download: calls.append((path, download))
    operation = actions.prepare_scene_op("scene.load", {"path": str(scene)})
    assert calls == []
    operation()
    assert calls == [(str(scene), False)]
