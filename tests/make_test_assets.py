"""Neutral test assets for toolbag-mcp, generated with Blender in background mode:

    blender --background --factory-startup --python tests/make_test_assets.py -- <output folder>

(``--factory-startup`` keeps add-ons out; some keep the process alive after the script.)

* part_bake.fbx   : Part_low (cube, smart-projected UVs) and Part_high (bevelled,
                    subdivided cube with a cylinder boss) for the Quick Loader
* part_low_v2.fbx : the same low cube with a different UV layout (cube projection) for
                    baker.replace_low
* mask.png        : 256x256 grayscale alpha mask (white disc on black) for material.set_cutout

The acceptance run of milestone M1 used these files from ~/.toolbag-mcp/test/.
"""
import math
import os
import struct
import sys
import zlib

import bpy

out = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)


def select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def cube(name, size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    o = bpy.context.active_object
    o.name = name
    o.data.name = name
    return o


def unwrap(o, method):
    select_only([o])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if method == "smart":
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    else:
        bpy.ops.uv.cube_project(cube_size=2.0, scale_to_bounds=True)
    bpy.ops.object.mode_set(mode="OBJECT")


def export_fbx(path, objs):
    select_only(objs)
    kwargs = dict(filepath=path, use_selection=True, apply_scale_options="FBX_SCALE_ALL",
                  axis_forward="-Z", axis_up="Y", mesh_smooth_type="FACE",
                  use_mesh_modifiers=True, add_leaf_bones=False, bake_anim=False,
                  path_mode="COPY", embed_textures=False)
    if hasattr(bpy.ops.export_scene, "fbx"):
        bpy.ops.export_scene.fbx(**kwargs)
    else:  # Blender 5.x C++ exporter
        bpy.ops.wm.fbx_export(filepath=path, use_selection=True)


# low poly: plain cube
low = cube("Part_low")
unwrap(low, "smart")

# high poly: bevelled, subdivided cube plus a boss on top, joined into one object
high = cube("Part_high")
bevel = high.modifiers.new("Bevel", "BEVEL")
bevel.width = 0.08
bevel.segments = 4
subsurf = high.modifiers.new("Subsurf", "SUBSURF")
subsurf.levels = 2
select_only([high])
for mod in list(high.modifiers):
    bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=0.24, location=(0.0, 0.0, 0.55), vertices=48)
boss = bpy.context.active_object
select_only([high, boss])
bpy.ops.object.join()
high = bpy.context.active_object
high.name = "Part_high"
high.data.name = "Part_high"
select_only([high])
bpy.ops.object.shade_smooth()

export_fbx(os.path.join(out, "part_bake.fbx"), [low, high])

# second low with another UV layout
low2 = low.copy()
low2.data = low.data.copy()
low2.name = "Part_low"
low2.data.name = "Part_low_v2"
bpy.context.collection.objects.link(low2)
unwrap(low2, "cube")
export_fbx(os.path.join(out, "part_low_v2.fbx"), [low2])


# alpha mask: white disc on black, 8-bit grayscale PNG written with the standard library
def write_png(path, width, height, rows):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)


size = 256
rows = []
for y in range(size):
    row = []
    for x in range(size):
        inside = (x - size / 2) ** 2 + (y - size / 2) ** 2 <= (size * 0.35) ** 2
        row.append(255 if inside else 0)
    rows.append(row)
write_png(os.path.join(out, "mask.png"), size, size, rows)

print("ASSETS_OK", sorted(os.listdir(out)))
