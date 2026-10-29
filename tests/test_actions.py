"""The actions against the fake Toolbag: scene, objects, materials, baker, lights, cameras,
sky, fog, render and timeline, including the rules learned on the real thing."""

from __future__ import annotations

import os

import pytest


def run(scene, action, **params):
    return scene.run(action, params)


# ---------------------------------------------------------------- scene and objects

def test_default_scene_info_tree_and_listing(scene):
    info = run(scene, "scene.info")
    assert info["objects"] == 4
    assert info["by_type"] == {"SceneObject": 1, "RenderObject": 1, "CameraObject": 1,
                               "SkyBoxObject": 1}
    tree = run(scene, "scene.tree", depth=2)
    assert tree["roots"][0]["name"] == "Scene"
    assert [c["name"] for c in tree["roots"][0]["children"]] == ["Render", "Main Camera", "Sky"]
    cams = run(scene, "scene.list_objects", object_type="CameraObject")
    assert cams["count"] == 1 and cams["objects"][0]["name"] == "Main Camera"
    found = run(scene, "object.find", name="cam")
    assert found["count"] == 1


def test_import_rename_duplicate_group_parent_delete(scene, tmp_path):
    fbx = tmp_path / "chair.fbx"
    fbx.write_bytes(b"x")
    imported = run(scene, "scene.import_model", path=str(fbx))
    assert imported["root"]["name"] == "chair" and imported["count"] == 3
    run(scene, "object.rename", ref="chair", name="Chair")
    copy = run(scene, "object.duplicate", ref="Chair", name="Chair 2")
    assert copy["name"] == "Chair 2" and copy["children"] == 1
    grouped = run(scene, "object.group", refs=["Chair", "Chair 2"], name="Furniture")
    assert grouped["group"]["name"] == "Furniture"
    assert run(scene, "object.get", ref="Chair")["parent"]["name"] == "Furniture"
    run(scene, "object.set_parent", ref="Chair 2")
    assert run(scene, "object.get", ref="Chair 2")["parent"] is None
    deleted = run(scene, "object.delete", refs=["Furniture"])
    assert deleted["deleted"][0]["name"] == "Furniture"
    with pytest.raises(scene.ActionError, match="No object"):
        run(scene, "object.get", ref="Chair")
    with pytest.raises(scene.ActionError, match="File not found"):
        run(scene, "scene.import_model", path=str(tmp_path / "missing.fbx"))


def test_selection_and_transform(scene):
    run(scene, "selection.set", refs=["Main Camera"])
    assert run(scene, "selection.get")["selection"][0]["name"] == "Main Camera"
    assert run(scene, "selection.clear")["selection"] == []
    moved = run(scene, "object.set_transform", ref="Main Camera", position=[1, 2, 3])
    assert moved["position"] == [1.0, 2.0, 3.0]
    with pytest.raises(scene.ActionError, match="three numbers"):
        run(scene, "object.set_transform", ref="Main Camera", scale=[1, 2])
    with pytest.raises(scene.ActionError, match="of type TransformObject"):
        run(scene, "object.set_transform", ref="Sky", position=[0, 0, 0])


def test_export_and_save(scene, tmp_path):
    out = run(scene, "scene.export", path=str(tmp_path / "out" / "scene.fbx"),
              options={"materials": False})
    assert out["exists"] and out["bytes"] == 128
    saved = run(scene, "scene.save", path=str(tmp_path / "scene.tbscene"))
    assert saved["path"].endswith("scene.tbscene")
    assert run(scene, "scene.info")["path"] == saved["path"]
    with pytest.raises(scene.ActionError, match="no file yet"):
        scene.fake._state["scene_path"] = ""
        run(scene, "scene.save")


# ---------------------------------------------------------------- materials

def test_material_lifecycle(scene, tmp_path):
    mat = run(scene, "material.create", name="Metal")
    assert mat["slots"]["reflectivity"]["shader"] == "Metalness"
    assert mat["slots"]["transparency"] is None
    with pytest.raises(scene.ActionError, match="exists already"):
        run(scene, "material.create", name="Metal")
    listed = run(scene, "material.list")
    assert [m["name"] for m in listed["materials"]] == ["Default", "Metal"]

    sub = run(scene, "material.set_subroutine", name="Metal", slot="reflectivity",
              shader="Specular")
    assert "Channel;specular" in sub["fields"]
    with pytest.raises(scene.ActionError, match="failed"):
        run(scene, "material.set_subroutine", name="Metal", slot="albedo", shader="Glitter")
    field = run(scene, "material.set_field", name="Metal", slot="microsurface", field="Roughness",
                value=0.3)
    assert field["value"] == 0.3
    color = run(scene, "material.set_field", name="Metal", slot="albedo", field="Color",
                value=[1, 0.5, 0.25])
    assert color["value"] == [1.0, 0.5, 0.25]
    with pytest.raises(scene.ActionError, match="No field"):
        run(scene, "material.set_field", name="Metal", slot="albedo", field="Glow", value=1)
    with pytest.raises(scene.ActionError, match="no shader in slot"):
        run(scene, "material.set_field", name="Metal", slot="emissive", field="Color", value=1)


def test_textures_are_cleared_before_being_set(scene, tmp_path):
    image = tmp_path / "albedo.png"
    image.write_bytes(b"png")
    first = run(scene, "material.set_texture", name="Default", slot="albedo", field="Albedo Map",
                path=str(image), srgb=True, mipmaps=False)
    assert first["texture"]["sRGB"] is True and first["previous"] is None
    second = run(scene, "material.set_texture", name="Default", slot="albedo", field="Albedo Map",
                 path=str(image))
    assert second["previous"] == str(image)
    assert second["texture"]["sRGB"] is True  # inherited from the previous texture
    history = scene.fake.findMaterial("Default").getSubroutine("albedo").history
    assert [h[0] for h in history] == ["Albedo Map"] * 4
    assert history[2][1] is None  # cleared before the new texture goes in
    assert history[3][1].useMipmaps is False
    cleared = run(scene, "material.set_texture", name="Default", slot="albedo", field="Albedo Map",
                  path="")
    assert cleared["texture"] is None


def test_cutout_sets_up_the_transparency_slot(scene, tmp_path):
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"png")
    result = run(scene, "material.set_cutout", name="Default", alpha_map=str(mask), channel=1,
                 threshold=0.4)
    assert result["channel"] == 1 and result["threshold"] == 0.4
    assert result["texture"]["sRGB"] is False
    slot = run(scene, "material.get", name="Default")["slots"]["transparency"]
    assert slot["shader"] == "Cutout" and slot["fields"]["Use Albedo Alpha"] is False


def test_material_assign_export_import_delete(scene, tmp_path):
    fbx = tmp_path / "box.fbx"
    fbx.write_bytes(b"x")
    run(scene, "scene.import_model", path=str(fbx))
    run(scene, "material.create", name="Paint")
    assigned = run(scene, "material.assign", name="Paint", refs="box")
    assert assigned["assigned_to"][0]["name"] == "box"
    assert run(scene, "material.get", name="Paint")["assigned"][0]["type"] == "SubMeshObject"
    exported = run(scene, "material.export", name="Paint", path=str(tmp_path / "paint.tbmat"))
    assert exported["exists"]
    imported = run(scene, "material.import", path=exported["path"], name="Paint 2")
    assert imported["name"] == "Paint 2"
    copy = run(scene, "material.duplicate", name="Paint", new_name="Paint 3")
    assert copy["name"] == "Paint 3"
    assert run(scene, "material.delete", name="Paint 3") == {"deleted": "Paint 3"}
    assert run(scene, "material.set_group", name="Paint", group="Cars")["group"] == "Cars"


# ---------------------------------------------------------------- baker

@pytest.fixture
def baked(scene, tmp_path):
    fbx = tmp_path / "part_bake.fbx"
    fbx.write_bytes(b"x")
    run(scene, "baker.create", name="Bake")
    run(scene, "baker.quick_load", path=str(fbx))
    run(scene, "baker.set_settings", settings={"outputPath": str(tmp_path / "out" / "part.png"),
                                                "outputWidth": 512, "outputHeight": 512})
    return scene


def test_quick_load_groups_and_overview(baked):
    overview = run(baked, "baker.get")
    group = overview["groups"]["part"]
    assert group["low"]["meshes"][0]["name"] == "part_low"
    assert group["high"]["visible"] is False and group["low"]["cage_min"] is None
    assert overview["texture_sets"] is None and overview["udim_tiles"] is None
    assert "geometryMode" not in overview["settings"]
    assert [m["suffix"] for m in overview["maps"] if m["enabled"]] == ["normal"]
    assert run(baked, "baker.list")["bakers"][0]["groups"] == ["part"]


def test_baker_settings_maps_cage_visibility(baked):
    with pytest.raises(baked.ActionError, match="no setting 'geometryMode'"):
        run(baked, "baker.set_settings", settings={"geometryMode": 1})
    maps = run(baked, "baker.set_maps", enable=["normals", "AO"], options={"ao": {"rayCount": 64}})
    assert [m["suffix"] for m in maps["maps"]] == ["normal", "ao"]
    assert maps["maps"][1]["options"]["rayCount"] == 64
    with pytest.raises(baked.ActionError, match="has no option"):
        run(baked, "baker.set_maps", options={"normal": {"flipW": True}})
    cage = run(baked, "baker.set_cage", groups="part", estimate=True, max_offset=2.5)
    assert cage["groups"]["part"]["cage_max"] == 2.5 and cage["groups"]["part"]["cage_min"] is None
    with pytest.raises(baked.ActionError, match="Unknown bake groups"):
        run(baked, "baker.set_cage", groups=["nope"])
    vis = run(baked, "baker.set_visible", groups="part", low=False, high=True)
    assert vis["groups"]["part"]["low"]["visible"] is False
    assert vis["groups"]["part"]["high"]["visible"] is True
    run(baked, "baker.add_group", name="extra")
    isolated = run(baked, "baker.isolate", groups=["part"])
    assert isolated["hidden"] == ["extra"]
    with pytest.raises(baked.ActionError, match="Several bake projects"):
        run(baked, "baker.create", name="Second")
        run(baked, "baker.get")


def test_bake_writes_files_and_trial_bake_restores_settings(baked, tmp_path):
    run(baked, "baker.set_maps", enable=["ao"])
    result = run(baked, "baker.bake")
    assert [f["suffix"] for f in result["files"]] == ["normal", "ao"]
    assert all(f["exists"] for f in result["files"])
    trial = run(baked, "baker.bake_to", path=str(tmp_path / "trial" / "t.png"), maps="normals",
                width=256, samples=4, settings={"smoothCage": False})
    assert trial["files"][0]["path"].endswith("t_normal.png") and trial["files"][0]["exists"]
    after = run(baked, "baker.get", maps=True)
    assert after["settings"]["outputWidth"] == 512 and after["settings"]["smoothCage"] is True
    assert [m["suffix"] for m in after["maps"] if m["enabled"]] == ["normal", "ao"]
    with pytest.raises(baked.ActionError, match="Unknown settings"):
        run(baked, "baker.bake_to", path=str(tmp_path / "t2.png"), settings={"interactive": True})
    # a failing bake still restores everything
    with pytest.raises(RuntimeError, match="outputSamples"):
        run(baked, "baker.bake_to", path=str(tmp_path / "t3.png"), samples=0)
    assert run(baked, "baker.get")["settings"]["outputSamples"] == 16
    path = run(baked, "baker.map_path", map_name="Normals")
    assert path["source"] == "computed" and path["path"].endswith("part_normal.png")
    with pytest.raises(baked.ActionError, match="no getMapPath"):
        run(baked, "baker.map_path", map_name="Normals", tile_suffix="1001")


def test_presets_texture_sets_and_replace_low(baked, tmp_path):
    run(baked, "baker.save_preset", name="fast")
    run(baked, "baker.set_settings", settings={"outputSamples": 4})
    assert run(baked, "baker.load_preset", name="fast")["settings"]["outputSamples"] == 16
    with pytest.raises(RuntimeError, match="Multiple"):
        run(baked, "baker.set_texture_set", index=0, width=256)
    run(baked, "baker.set_settings", settings={"tileMode": 1})
    sets = run(baked, "baker.set_texture_set", index="Default", width=256, enabled=False)
    assert sets["texture_sets"][0] == {"index": 0, "name": "Default", "enabled": False,
                                       "width": 256, "height": 1024}
    low2 = tmp_path / "part_low_v2.fbx"
    low2.write_bytes(b"x")
    before = run(baked, "scene.info")["objects"]
    swapped = run(baked, "baker.replace_low", group="part", path=str(low2))
    assert swapped["vertices"] == 24 and swapped["u_range"] == [0.2, 0.2]
    assert swapped["same_transform"] is True
    assert run(baked, "scene.info")["objects"] == before  # the import is gone again
    assert run(baked, "baker.get")["groups"]["part"]["low"]["meshes"][0]["name"] == "part_low"


# ---------------------------------------------------------------- lights, sky, fog, cameras

def test_lights_and_generic_attributes(scene):
    light = run(scene, "light.create", light_type="spot", name="Key", position=[0, 5, 0],
                attributes={"brightness": 3, "color": [1, 0.9, 0.8], "spotAngle": 30})
    assert light["applied"] == {"lightType": "spot", "brightness": 3, "color": [1.0, 0.9, 0.8],
                                "spotAngle": 30}
    with pytest.raises(scene.ActionError, match="light_type"):
        run(scene, "light.create", light_type="laser")
    lights = run(scene, "light.list")
    assert lights["count"] == 1 and lights["lights"][0]["brightness"] == 3
    attrs = run(scene, "object.attributes", ref="Key")["attributes"]
    assert attrs["lightType"] == "spot" and "position" not in attrs
    with pytest.raises(scene.ActionError, match="no attribute 'wattage'"):
        run(scene, "object.set_attributes", ref="Key", attributes={"wattage": 60})
    with pytest.raises(scene.ActionError, match="non-empty object"):
        run(scene, "object.set_attributes", ref="Key", attributes={})
    with pytest.raises(scene.ActionError, match="Cannot create"):
        run(scene, "object.create", object_type="HairObject")
    group = run(scene, "object.create", object_type="SceneObject", name="Lights")
    run(scene, "object.set_parent", ref="Key", parent=group["uid"])
    assert run(scene, "object.get", ref="Key")["parent"]["name"] == "Lights"


def test_cameras_with_nested_settings(scene):
    cam = run(scene, "camera.create", name="Shot", position=[0, 0, 10],
              attributes={"fov": 30, "lens": {"dofMode": 1, "dofStop": 2.8},
                          "postEffect.toneMappingMode": "hejl"}, activate=True)
    assert cam["active"] is True
    assert cam["applied"]["lens"] == {"dofMode": 1, "dofStop": 2.8}
    listed = run(scene, "camera.list")
    assert listed["active"] == cam["uid"] and listed["count"] == 2
    got = run(scene, "camera.get")
    assert got["name"] == "Shot" and got["attributes"]["lens"]["dofStop"] == 2.8
    assert got["attributes"]["postEffect"]["toneMappingMode"] == "hejl"
    with pytest.raises(scene.ActionError, match="no 'lense'"):
        run(scene, "camera.set", attributes={"lense.dofStop": 4})
    run(scene, "camera.set_active", ref="Main Camera")
    assert run(scene, "camera.get")["name"] == "Main Camera"


def test_sky_and_fog(scene, tmp_path):
    sky = run(scene, "sky.get")
    assert sky["attributes"]["mode"] == "sky" and sky["child_lights"] == []
    assert sky["attributes"]["rotation"] == 0.0  # a sky attribute, not a transform
    run(scene, "sky.set", attributes={"brightness": 2.0, "rotation": 90})
    assert run(scene, "sky.get")["attributes"]["rotation"] == 90.0
    assert "position" not in run(scene, "object.attributes", ref="Main Camera")["attributes"]
    hdr = tmp_path / "studio.hdr"
    hdr.write_bytes(b"hdr")
    assert run(scene, "sky.load", path=str(hdr))["as"] == "image"
    tbsky = tmp_path / "day.tbsky"
    tbsky.write_bytes(b"sky")
    assert run(scene, "sky.load", path=str(tbsky))["as"] == "tbsky"
    assert run(scene, "sky.set_background_image", path=str(hdr))["background"] == str(hdr)
    assert run(scene, "sky.set_background_image")["background"] is None
    assert run(scene, "sky.compute_procedural")["attributes"]["mode"] == "procedural"
    saved = run(scene, "sky.save", path=str(tmp_path / "out" / "sky.tbsky"))
    assert saved["exists"]
    assert run(scene, "fog.get") == {"exists": False, "fog": None}
    fog = run(scene, "fog.set", attributes={"density": 0.25})
    assert fog["applied"] == {"density": 0.25}
    assert run(scene, "fog.get")["fog"]["attributes"]["density"] == 0.25


# ---------------------------------------------------------------- render and timeline

def test_render_settings_and_outputs(scene, tmp_path):
    settings = run(scene, "render.get")
    assert settings["options"]["renderer"] == "raster"
    assert settings["cameras"][0]["camera"]["name"] == "Main Camera"
    assert [p["renderPass"] for p in settings["passes"]] == ["Full Quality", "Normals"]
    folder = tmp_path / "frames"
    applied = run(scene, "render.set", options={"renderer": "raytrace"},
                  images={"outputPath": str(folder), "width": 640, "height": 480})
    assert applied["options"] == {"renderer": "raytrace"}
    assert folder.is_dir()
    with pytest.raises(scene.ActionError, match="Pass options"):
        run(scene, "render.set")
    passes = run(scene, "render.set_passes", enable=["Normals"], disable=["Full Quality"])
    assert [p["enabled"] for p in passes["passes"]] == [False, True]
    with pytest.raises(scene.ActionError, match="Unknown render pass"):
        run(scene, "render.set_passes", enable=["Depth"])
    cams = run(scene, "render.set_cameras", disable=["Main Camera"])
    assert cams["cameras"][0]["enabled"] is False
    shot = run(scene, "render.image", path=str(tmp_path / "shot.png"), width=320, height=240)
    assert shot["exists"] and shot["bytes"] == 256 and shot["pixel_format"] == "PIXEL_FORMAT_BGRA8"
    frames = run(scene, "render.images")
    assert [os.path.basename(f["path"]) for f in frames["files"]] == ["Main Camera.png"]
    viewer = run(scene, "render.export_viewer", path=str(tmp_path / "scene.mview"), title="Demo")
    assert viewer["exists"]


def test_timeline(scene):
    assert run(scene, "timeline.get")["total_frames"] == 24
    moved = run(scene, "timeline.set", time=0.5, total_frames=48, play=True)
    assert moved["frame"] == 12 and moved["total_frames"] == 48
    assert scene.fake.getTimeline().playing is True
    assert run(scene, "timeline.set", frame=3, play=False)["time"] == 3 / 24


# ---------------------------------------------------------------- quitting

def test_quit_action_is_two_phased_in_the_registry(scene):
    assert scene.QUIT_ACTION in scene.ACTIONS
    assert scene.run("toolbag.quit", {}) == {"quitting": True}
    assert scene.fake._state["quit"] == 0
