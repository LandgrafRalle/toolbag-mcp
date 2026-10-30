"""Edge protect: the skew mask compute core, the .tbscene writer, the plugin actions behind it
and the whole apply flow through the MCP layer."""

from __future__ import annotations

import json
import struct

import anyio
import numpy as np
import pytest
from mcp import Client

from toolbag_mcp import server as srv
from toolbag_mcp import skew, tbscene

# ---------------------------------------------------------------- test geometry

# A flat 2x1 strip of two quads in 3D. The middle edge (x = 1) is shared by both quads.
_POS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]


def _strip(split_uv: bool, hard: bool = False) -> skew.MeshArrays:
    """Left quad: uv 0..0.5; right quad: uv 0.5..1, or moved away (a UV seam) with
    split_uv. hard gives the right quad tilted normals, so the middle edge is hard."""
    left = [(0, (0.0, 0.0)), (1, (0.5, 0.0)), (2, (0.5, 1.0)), (3, (0.0, 1.0))]
    shift = 0.02 if split_uv else 0.0
    right = [(1, (0.5 + shift, 0.0)), (4, (1.0, 0.0)), (5, (1.0, 1.0)), (2, (0.5 + shift, 1.0))]
    verts, normals, uvs = [], [], []
    for quad, normal in ((left, (0, 0, 1)), (right, (0, 0.6, 0.8) if hard else (0, 0, 1))):
        for pos, uv in quad:
            verts.append(_POS[pos])
            normals.append(normal)
            uvs.append(uv)
    tris = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]
    return skew.MeshArrays(np.array(verts, float), np.array(normals, float),
                           np.array(uvs, float), np.array(tris))


def _middle_column(mask: np.ndarray) -> np.ndarray:
    size = mask.shape[0]
    return mask[size // 4: 3 * size // 4, size // 2 - 1]


# ---------------------------------------------------------------- compute core

def test_classify_seams_hard_and_open_edges():
    plain = skew.classify_edges(_strip(split_uv=False), 8.0)
    assert len(plain.seam) == 6 and len(plain.hard) == 6  # the six open border sides
    assert plain.bent == []
    seamed = skew.classify_edges(_strip(split_uv=True), 8.0)
    assert len(seamed.seam) == 8 and len(seamed.hard) == 6
    hard = skew.classify_edges(_strip(split_uv=False, hard=True), 8.0)
    assert len(hard.hard) == 8 and len(hard.seam) == 6


def test_band_follows_the_seam_only_where_there_is_one():
    no_border = {"hard_px": 0.0, "bend_px": 0.0}
    plain, covered = skew.edge_mask([_strip(split_uv=False)], 128, seam_px=0.0, **no_border)
    assert covered.mean() > 0.9
    assert plain[covered].max() == 0  # nothing protected: full correction everywhere
    with_borders, _ = skew.edge_mask([_strip(split_uv=False)], 128, seam_px=2.0, **no_border)
    assert _middle_column(with_borders).max() == 0  # no seam in the middle
    assert with_borders[64, 1] == 255  # but the open border is protected
    seamed, _ = skew.edge_mask([_strip(split_uv=True)], 128, seam_px=2.0, **no_border)
    assert _middle_column(seamed).min() == 255
    assert seamed[64, 32] == 0  # away from every edge: corrected


def test_soft_falloff_gives_grey_values():
    mask, covered = skew.edge_mask([_strip(split_uv=True)], 128, seam_px=1.0, hard_px=0.0,
                                   bend_px=0.0, soft_px=6.0)
    values = set(np.unique(mask[covered]).tolist())
    assert 0 in values and 255 in values and len(values) > 3


def test_png_writer(tmp_path):
    path = tmp_path / "m.png"
    skew.write_png_gray(str(path), np.arange(16, dtype=np.uint8).reshape(4, 4))
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (4, 4)


# ---------------------------------------------------------------- scene file

def _block(tag: str, fields: dict[str, bytes]) -> bytes:
    table, body = b"", b""
    for name, value in fields.items():
        table += name.encode().ljust(8, b"\0") + struct.pack("<II", len(body), len(value))
        body += value
    return tag.encode().ljust(8, b"\0") + struct.pack("<II", len(fields), len(body)) + table + body


def fake_scene(low_names=("Part_low",)) -> bytes:
    """A minimal .tbscene: a header block, then per target DBakeSO + SObject."""
    data = _block("Scene", {"version": b"\x05\0\0\0"})
    for name in low_names:
        data += _block("DBakeSO", {"maskcoun": struct.pack("<I", 1), "dirmsk0": b"\0",
                                   "dirmkE0": b"\0", "mkTx0": b"\0" * 4, "mkTy0": b"\0" * 4})
        data += _block("SObject", {"name": struct.pack("<I", len(name)) + name.encode()})
    return data


def test_write_and_read_skew_roundtrip():
    data = fake_scene(("A_low", "B_low"))
    mask = bytes(range(256)) * 4  # 32 x 32
    out = tbscene.write_skew(data, "B_low", mask)
    size, pixels = tbscene.read_skew(out, "B_low")
    assert (size, pixels) == (32, mask)
    assert tbscene.read_skew(out, "A_low") is None
    target = next(t for t in tbscene.targets(out) if t["name"] == "B_low")
    assert out[target["fields"]["dirmsk0"][0]] == 1 and out[target["fields"]["dirmkE0"][0]] == 1
    assert struct.unpack_from("<I", out, target["fields"]["mkTx0"][0])[0] == 32
    again = tbscene.write_skew(out, "B_low", bytes(1024))  # replaces, does not stack
    assert len(again) == len(out)
    assert tbscene.read_skew(again, "B_low")[1] == bytes(1024)
    with pytest.raises(KeyError, match="Nope"):
        tbscene.write_skew(data, "Nope", mask)
    with pytest.raises(ValueError, match="square"):
        tbscene.write_skew(data, "A_low", bytes(10))


def test_new_skew_path_never_overwrites(tmp_path):
    scene = tmp_path / "tank.tbscene"
    first = tbscene.new_skew_path(str(scene))
    assert first.endswith("/tank_skew.tbscene") and "\\" not in first
    open(first, "w").close()
    assert tbscene.new_skew_path(first).endswith("/tank_skew_2.tbscene")


# ---------------------------------------------------------------- plugin actions

@pytest.fixture
def baked(scene, tmp_path):
    fbx = tmp_path / "part_bake.fbx"
    fbx.write_bytes(b"x")
    scene.run("baker.create", {"name": "Bake"})
    scene.run("baker.quick_load", {"path": str(fbx)})
    return scene


def test_low_mesh_data_and_preview(baked, tmp_path):
    data = baked.run("baker.low_mesh_data", {})
    assert data["output_width"] == 2048
    meshes = data["groups"]["part"]
    assert meshes[0]["name"] == "part_low"
    assert len(meshes[0]["vertices"]) == 3 * len(meshes[0]["normals"]) // 3
    fake = baked.fake
    material = fake.Material("Paint")
    low = next(o for o in fake.getAllObjects() if o.name == "part_low")
    material.assign(low)
    albedo = material.getSubroutine("albedo")
    albedo.setField("Color", [0.2, 0.3, 0.4])
    image = tmp_path / "preview.png"
    image.write_bytes(b"x")
    shown = baked.run("baker.skew_preview", {"path": str(image)})
    assert shown["materials"] == ["Paint"]
    assert albedo.getField("Albedo Map").path == str(image)
    assert albedo.getField("Color") == [1.0, 1.0, 1.0]
    baked.run("baker.skew_preview", {"path": str(image)})  # twice keeps the first original
    assert baked.run("baker.skew_preview_clear", {}) == {"restored": ["Paint"]}
    assert albedo.getField("Albedo Map") is None
    assert albedo.getField("Color") == [0.2, 0.3, 0.4]


# ---------------------------------------------------------------- MCP flow

def _call_tool(name, arguments):
    async def go():
        async with Client(srv.server) as client:
            return await client.call_tool(name, arguments)
    return anyio.run(go)


def _payload(result):
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def _mesh_payload(mesh: skew.MeshArrays, name: str) -> dict:
    return {"name": name, "vertices": mesh.vertices.ravel().tolist(),
            "normals": mesh.normals.ravel().tolist(), "uvs": mesh.uvs.ravel().tolist(),
            "triangles": mesh.triangles.ravel().tolist()}


def _fake_plugin(tmp_path, calls):
    scene_path = (tmp_path / "tank.tbscene").as_posix()

    def fake_call(action, params=None, timeout=60.0):
        calls.append((action, params))
        if action == "baker.low_mesh_data":
            result = {"baker": "Bake", "output_width": 2048, "output_height": 2048,
                      "groups": {"Tracks": [_mesh_payload(_strip(True), "Track_low")]}}
        elif action == "scene.info":
            result = {"path": scene_path}
        elif action == "scene.save":
            with open(params["path"], "wb") as handle:
                handle.write(fake_scene(("Track_low",)))
            result = {"path": params["path"]}
        elif action == "scene.load":
            result = {"path": params["path"]}
        elif action == "baker.skew_preview":
            result = {"preview": params["path"], "materials": ["Dark"]}
        else:
            result = {"restored": []}
        return {"ok": True, "result": result, "log": "", "ms": 0}

    return fake_call


def test_apply_writes_masks_into_a_new_scene(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(srv.client, "call", _fake_plugin(tmp_path, calls))
    result = _call_tool("baker_skew_apply", {"mask_size": 512})
    assert result.is_error is False, result.content[0].text
    payload = _payload(result)
    target = (tmp_path / "tank_skew.tbscene").as_posix()
    assert payload["scene"] == target
    assert payload["groups"]["Tracks"]["low_mesh"] == "Track_low"
    assert 0 < payload["groups"]["Tracks"]["averaged_percent"] < 100
    assert [c[0] for c in calls] == ["baker.low_mesh_data", "scene.info",
                                     "baker.skew_preview_clear", "scene.save", "scene.load"]
    size, pixels = tbscene.read_skew(open(target, "rb").read(), "Track_low")
    assert size == 512 and 0 in pixels and 255 in pixels
    # A second run never overwrites the first file
    calls.clear()
    second = _payload(_call_tool("baker_skew_apply", {"mask_size": 512}))
    assert second["scene"].endswith("tank_skew_2.tbscene")


def test_apply_refuses_existing_path_and_bad_size(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.client, "call", _fake_plugin(tmp_path, []))
    existing = tmp_path / "taken.tbscene"
    existing.write_bytes(b"x")
    result = _call_tool("baker_skew_apply", {"path": str(existing)})
    assert result.is_error and "exists" in result.content[0].text
    result = _call_tool("baker_skew_apply", {"mask_size": 300})
    assert result.is_error and "mask_size" in result.content[0].text


def test_preview_writes_an_image_and_shows_it(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(srv.client, "call", _fake_plugin(tmp_path, calls))
    monkeypatch.setattr(srv, "SKEW_DIR", tmp_path / "skew")
    payload = _payload(_call_tool("baker_skew_preview", {"mask_size": 512, "seam_px": 16}))
    assert payload["materials"] == ["Dark"]
    assert payload["bake_pixels_per_mask_pixel"] == 4.0
    assert (tmp_path / "skew").glob("preview_*.png")
    assert calls[-1][0] == "baker.skew_preview"
    assert calls[-1][1]["path"] == payload["preview"]
