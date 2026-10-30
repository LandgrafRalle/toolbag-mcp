"""The MCP server: one typed tool per plugin action, served over stdio.

Tools call the plugin through ``ToolbagClient`` in a worker thread, so a long bake does not
block the protocol. Failures inside Toolbag come back as tool errors with the traceback.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

import anyio
import numpy as np
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, skew, tbscene
from .client import PluginError, PluginUnavailable, ToolbagClient

INSTRUCTIONS = """Controls a running Marmoset Toolbag 5 through the Toolbag MCP plugin.

Start with `status`; if it fails the plugin is not running (in Toolbag: Plugins -> Toolbag
MCP). Object references (`ref`, `refs`) are uids (int) or exact names (str); get them from
`scene_list_objects`, `scene_tree` or `object_find`. Materials and bake projects are
referenced by name; `baker` may be omitted when the scene has exactly one bake project.

Bake scenes: toggle visibility with `baker_set_visible` / `baker_isolate` (group, Low and
High nodes), never on single meshes or submeshes, that corrupts bakes. Textures set through
`material_set_texture` are re-read from disk. Imports, bakes and renders can take minutes.
Edge protect (skew masks by edge rule instead of hand painting): `baker_skew_preview` to look,
`baker_skew_apply` to write them into a new scene file.

Lights, cameras, sky, fog and the render object are objects too: `object_attributes` shows
every setting with its value, `object_set_attributes` changes them (nested settings as dicts
or dotted keys); the dedicated tools (`light_create`, `camera_set`, `sky_load`, `render_set`,
`render_image`) cover the common cases. `execute_python` only works while the user ticks
"Allow Python execution" in the plugin window inside Toolbag."""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                       openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

SHORT = 30.0    # reading and small changes
LONG = 600.0    # imports, exports, bakes

server = MCPServer("toolbag", title="Marmoset Toolbag", instructions=INSTRUCTIONS,
                   version=__version__)
client = ToolbagClient()

Ref = int | str
Refs = Ref | list[Ref]


def _p(**kwargs: Any) -> dict[str, Any]:
    """Parameters for the plugin: omitted (None) ones fall back to the plugin's defaults."""
    return {k: v for k, v in kwargs.items() if v is not None}


async def _call(action: str, params: dict[str, Any] | None = None, timeout: float = SHORT,
                with_log: bool = False) -> Any:
    try:
        payload = await anyio.to_thread.run_sync(lambda: client.call(action, params, timeout))
    except PluginUnavailable as exc:
        raise ToolError(str(exc)) from exc
    except PluginError as exc:
        raise ToolError(exc.describe()) from exc
    if with_log:
        return {"result": payload.get("result"), "log": payload.get("log", "")}
    return payload.get("result")


# ---------------------------------------------------------------- diagnostics

@server.tool(annotations=READ)
async def status() -> dict[str, Any]:
    """Toolbag version, graphics adapter, scene path, counts and plugin state (port, Python
    switch, call counter). Start here: it tells whether the plugin is reachable."""
    return await _call("status")


@server.tool(annotations=READ)
async def capabilities() -> dict[str, Any]:
    """Everything the running Toolbag exposes in `mset` plus the plugin's action list, for
    version differences between Toolbag builds."""
    return await _call("capabilities")


@server.tool(annotations=READ)
async def preferences_get(keys: list[str] | None = None) -> dict[str, Any]:
    """Toolbag application preferences, all or the given keys (e.g. autoTextureReload,
    defaultTangentMethod, rayTraceBackend)."""
    return await _call("preferences.get", _p(keys=keys))


@server.tool(annotations=WRITE)
async def preferences_set(key: str, value: Any) -> dict[str, Any]:
    """Set one Toolbag application preference (see preferences_get for keys)."""
    return await _call("preferences.set", {"key": key, "value": value})


# ---------------------------------------------------------------- scene

@server.tool(annotations=READ)
async def scene_info() -> dict[str, Any]:
    """Scene path, unit scale, bounds, object counts by type, bake projects, selection."""
    return await _call("scene.info")


@server.tool(annotations=DESTRUCTIVE)
async def scene_new() -> dict[str, Any]:
    """New empty scene. Unsaved changes are lost without a prompt."""
    return await _call("scene.new")


@server.tool(annotations=DESTRUCTIVE)
async def scene_load(path: str, download_missing_library_assets: bool = False) -> dict[str, Any]:
    """Load a .tbscene file. Unsaved changes in the current scene are lost without a prompt."""
    params = {"path": path,
              "download_missing_library_assets": download_missing_library_assets}
    return await _call("scene.load", params, LONG)


@server.tool(annotations=WRITE)
async def scene_save(path: str | None = None) -> dict[str, Any]:
    """Save the scene to its current file, or to `path` (save as). Overwrites without asking."""
    return await _call("scene.save", _p(path=path), LONG)


@server.tool(annotations=READ)
async def scene_list_objects(object_type: str | None = None, name_contains: str | None = None,
                             parent: Ref | None = None, include_hidden: bool = True,
                             limit: int = 500) -> dict[str, Any]:
    """Flat list of scene objects (uid, name, type, parent, visibility, transform).
    `object_type` filters by class: MeshObject, SubMeshObject, BakerObject, BakerTargetObject,
    LightObject, CameraObject, SkyBoxObject, FogObject, SceneObject (groups). `parent`
    restricts to the descendants of one object."""
    return await _call("scene.list_objects", _p(object_type=object_type,
                                                name_contains=name_contains, parent=parent,
                                                include_hidden=include_hidden, limit=limit))


@server.tool(annotations=READ)
async def scene_tree(root: Ref | None = None, depth: int = 4) -> dict[str, Any]:
    """Object hierarchy as nested nodes, `depth` levels deep (deeper levels show the child
    count only). Good first look at an unknown scene."""
    return await _call("scene.tree", _p(root=root, depth=depth))


@server.tool(annotations=READ)
async def object_get(ref: Ref) -> dict[str, Any]:
    """Everything about one object: transform, pivot, bounds, mesh/camera/light settings,
    submeshes with their materials."""
    return await _call("object.get", {"ref": ref})


@server.tool(annotations=READ)
async def object_find(name: str, exact: bool = False, limit: int = 100) -> dict[str, Any]:
    """Objects whose name contains `name` (case-insensitive), or equals it with `exact`."""
    return await _call("object.find", {"name": name, "exact": exact, "limit": limit})


@server.tool(annotations=READ)
async def selection_get() -> dict[str, Any]:
    """The currently selected objects."""
    return await _call("selection.get")


@server.tool(annotations=WRITE)
async def selection_set(refs: Refs) -> dict[str, Any]:
    """Select exactly these objects."""
    return await _call("selection.set", {"refs": refs})


@server.tool(annotations=WRITE)
async def selection_clear() -> dict[str, Any]:
    """Deselect everything."""
    return await _call("selection.clear")


@server.tool(annotations=WRITE)
async def object_set_visible(refs: Refs, visible: bool, allow_mesh: bool = False) -> dict[str, Any]:
    """Show or hide groups, lights, cameras or bake nodes. Refuses submeshes always and
    meshes unless `allow_mesh` is true: in bake scenes those flags corrupt bakes or crash
    Toolbag on load. For bake scenes use baker_set_visible or baker_isolate."""
    return await _call("object.set_visible", {"refs": refs, "visible": visible,
                                              "allow_mesh": allow_mesh})


@server.tool(annotations=WRITE)
async def object_set_transform(ref: Ref, position: list[float] | None = None,
                               rotation: list[float] | None = None,
                               scale: list[float] | None = None,
                               pivot: list[float] | None = None) -> dict[str, Any]:
    """Set position, rotation (degrees), scale or pivot (each three numbers) of a transform
    object; omitted ones stay."""
    return await _call("object.set_transform", _p(ref=ref, position=position, rotation=rotation,
                                                  scale=scale, pivot=pivot))


@server.tool(annotations=WRITE)
async def object_rename(ref: Ref, name: str) -> dict[str, Any]:
    """Rename an object."""
    return await _call("object.rename", {"ref": ref, "name": name})


@server.tool(annotations=WRITE)
async def object_duplicate(ref: Ref, name: str = "") -> dict[str, Any]:
    """Duplicate an object with its children; optional name for the copy."""
    return await _call("object.duplicate", {"ref": ref, "name": name})


@server.tool(annotations=DESTRUCTIVE)
async def object_delete(refs: Refs) -> dict[str, Any]:
    """Delete objects from the scene."""
    return await _call("object.delete", {"refs": refs})


@server.tool(annotations=WRITE)
async def object_group(refs: Refs, name: str | None = None) -> dict[str, Any]:
    """Put objects under a new group, optionally named."""
    return await _call("object.group", _p(refs=refs, name=name))


@server.tool(annotations=WRITE)
async def object_set_parent(ref: Ref, parent: Ref | None = None) -> dict[str, Any]:
    """Re-parent an object; without `parent` it moves to the scene root."""
    return await _call("object.set_parent", _p(ref=ref, parent=parent))


@server.tool(annotations=WRITE)
async def scene_import_model(path: str) -> dict[str, Any]:
    """Import a model file (FBX, OBJ, glTF, USD, ...) into the scene with the scene's import
    settings. Returns the imported root and the new objects."""
    return await _call("scene.import_model", {"path": path}, LONG)


@server.tool(annotations=WRITE)
async def scene_export(path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Export the scene; the format follows the extension: .fbx, .obj, .glb/.gltf,
    .usd/.usda/.usdc/.usdz, .dae. `options` are exporter flags such as
    {"materials": false, "triangulate": true, "embedassets": true}."""
    return await _call("scene.export", _p(path=path, options=options), LONG)


@server.tool(annotations=READ)
async def scene_bounds() -> dict[str, Any]:
    """Bounds of the whole scene as [min_xyz, max_xyz] plus the unit scale."""
    return await _call("scene.bounds")


@server.tool(annotations=WRITE)
async def camera_frame(ref: Ref | None = None) -> dict[str, Any]:
    """Frame one object, or the whole scene without `ref`, in the current camera."""
    return await _call("camera.frame", _p(ref=ref))


@server.tool(annotations=WRITE)
async def mesh_set_backfaces(show: bool = True, refs: Refs | None = None) -> dict[str, Any]:
    """Show back faces (cull back faces off) on all meshes or the given ones, as a two-sided
    shader would render them. Per object; lost on re-import."""
    return await _call("mesh.set_backfaces", _p(show=show, refs=refs))


# ---------------------------------------------------------------- materials

@server.tool(annotations=READ)
async def material_list(name_contains: str | None = None) -> dict[str, Any]:
    """All materials with group, number of assigned objects and the shader per slot."""
    return await _call("material.list", _p(name_contains=name_contains))


@server.tool(annotations=READ)
async def material_get(name: str, fields: bool = True) -> dict[str, Any]:
    """One material in full: each slot (albedo, surface, microsurface, reflectivity,
    transparency, ...) with shader and field values, textures as path and sRGB flag, and the
    objects it is assigned to."""
    return await _call("material.get", {"name": name, "fields": fields})


@server.tool(annotations=WRITE)
async def material_create(name: str) -> dict[str, Any]:
    """Create a material with Toolbag's default shaders."""
    return await _call("material.create", {"name": name})


@server.tool(annotations=WRITE)
async def material_duplicate(name: str, new_name: str = "") -> dict[str, Any]:
    """Duplicate a material; optional name for the copy."""
    return await _call("material.duplicate", {"name": name, "new_name": new_name})


@server.tool(annotations=DESTRUCTIVE)
async def material_delete(name: str) -> dict[str, Any]:
    """Delete a material; its objects fall back to the default material."""
    return await _call("material.delete", {"name": name})


@server.tool(annotations=WRITE)
async def material_assign(name: str, refs: Refs, include_children: bool = True) -> dict[str, Any]:
    """Assign a material to objects, by default including their children."""
    return await _call("material.assign", {"name": name, "refs": refs,
                                           "include_children": include_children})


@server.tool(annotations=WRITE)
async def material_set_group(name: str, group: str = "") -> dict[str, Any]:
    """Move a material into a material group ('' for none)."""
    return await _call("material.set_group", {"name": name, "group": group})


@server.tool(annotations=WRITE)
async def material_set_subroutine(name: str, slot: str, shader: str) -> dict[str, Any]:
    """Set the shader of a material slot, e.g. ('transparency', 'Cutout'), ('reflectivity',
    'Metalness'), ('surface', 'Normals'). Slots: subdivision, displacement, surface,
    microsurface, albedo, diffusion, transmission, reflectivity, reflection, clearcoat
    reflection, clearcoat microsurface, clearcoat reflectivity, occlusion, emissive,
    transparency, texture. Returns the slot's field names."""
    return await _call("material.set_subroutine", {"name": name, "slot": slot, "shader": shader})


@server.tool(annotations=WRITE)
async def material_set_field(name: str, slot: str, field: str, value: Any) -> dict[str, Any]:
    """Set a number, boolean, enum or color field of a material slot (field names from
    material_get). For texture fields use material_set_texture."""
    return await _call("material.set_field", {"name": name, "slot": slot, "field": field,
                                              "value": value})


@server.tool(annotations=WRITE)
async def material_set_texture(name: str, slot: str, field: str, path: str,
                               srgb: bool | None = None, mipmaps: bool | None = None,
                               wrapping: str | None = None,
                               filtering: bool | None = None) -> dict[str, Any]:
    """Put an image file into a texture field (e.g. slot 'albedo', field 'Albedo Map';
    slot 'surface', field 'Normal Map'). The field is cleared first so a changed file is
    re-read. `srgb` and `mipmaps` default to the previous texture's settings. An empty
    `path` clears the field."""
    return await _call("material.set_texture", _p(name=name, slot=slot, field=field, path=path,
                                                  srgb=srgb, mipmaps=mipmaps, wrapping=wrapping,
                                                  filtering=filtering))


@server.tool(annotations=WRITE)
async def material_set_cutout(name: str, alpha_map: str, channel: int = 0,
                              threshold: float | None = None, use_albedo_alpha: bool = False,
                              srgb: bool = False) -> dict[str, Any]:
    """Alpha-tested transparency from a mask image: transparency slot to Cutout, 'Use Albedo
    Alpha' off, channel (0 = R) and the mask as Alpha Map, re-read from disk."""
    return await _call("material.set_cutout", _p(name=name, alpha_map=alpha_map, channel=channel,
                                                 threshold=threshold,
                                                 use_albedo_alpha=use_albedo_alpha, srgb=srgb))


@server.tool(annotations=WRITE)
async def material_import(path: str, name: str = "") -> dict[str, Any]:
    """Import a material file (.tbmat, MTL, or named texture files)."""
    return await _call("material.import", {"path": path, "name": name}, LONG)


@server.tool(annotations=WRITE)
async def material_export(name: str, path: str) -> dict[str, Any]:
    """Export a material to a .tbmat file."""
    return await _call("material.export", {"name": name, "path": path}, LONG)


# ---------------------------------------------------------------- baker

@server.tool(annotations=READ)
async def baker_list() -> dict[str, Any]:
    """All bake projects with their groups and output path."""
    return await _call("baker.list")


@server.tool(annotations=READ)
async def baker_get(baker: Ref | None = None, maps: bool = True) -> dict[str, Any]:
    """One bake project in full: groups with Low (cage offsets, meshes) and High nodes,
    settings, maps with options, texture sets, UDIM tiles and the expected output files."""
    return await _call("baker.get", _p(baker=baker, maps=maps))


@server.tool(annotations=WRITE)
async def baker_create(name: str | None = None) -> dict[str, Any]:
    """Create a new bake project."""
    return await _call("baker.create", _p(name=name))


@server.tool(annotations=WRITE)
async def baker_add_group(name: str, baker: Ref | None = None) -> dict[str, Any]:
    """Add an empty bake group (with Low and High nodes) to a bake project."""
    return await _call("baker.add_group", _p(name=name, baker=baker))


@server.tool(annotations=WRITE)
async def baker_quick_load(path: str, baker: Ref | None = None) -> dict[str, Any]:
    """Load a model through the Quick Loader: objects named '<stem>_low' and '<stem>_high'
    pair into one group per stem. A mismatch leaves a group without High and an empty bake,
    so check the returned groups."""
    return await _call("baker.quick_load", _p(path=path, baker=baker), LONG)


@server.tool(annotations=WRITE)
async def baker_set_settings(settings: dict[str, Any], baker: Ref | None = None) -> dict[str, Any]:
    """Set bake project settings: outputPath, outputWidth, outputHeight, outputBits (8, 16,
    32), outputSamples, edgePadding ('None', 'Moderate', 'Extreme'), edgePaddingSize,
    outputSoften, smoothCage, ignoreBackfaces, useHiddenMeshes, ignoreTransforms,
    fixMirroredTangents, tileMode (0 single, 1 texture sets, 2 UDIM), geometryMode (0 high
    to low, 1 low to low, 2 UV match)."""
    return await _call("baker.set_settings", _p(settings=settings, baker=baker))


@server.tool(annotations=WRITE)
async def baker_set_maps(enable: list[str] | None = None, disable: list[str] | None = None,
                         options: dict[str, dict[str, Any]] | None = None,
                         baker: Ref | None = None) -> dict[str, Any]:
    """Switch maps on or off by suffix ('normals', 'ao', 'height', 'curvature', 'position',
    'thickness', 'bentnormals', ...) and set map options, e.g. {"normals": {"flipY": true},
    "ao": {"rayCount": 1024, "ignoreGroups": false}}. Returns the enabled maps."""
    return await _call("baker.set_maps", _p(enable=enable, disable=disable, options=options,
                                            baker=baker))


@server.tool(annotations=WRITE)
async def baker_set_cage(groups: str | list[str] | None = None, min_offset: float | None = None,
                         max_offset: float | None = None, estimate: bool = False,
                         cage_alpha: float | None = None,
                         exclude_when_ignoring_groups: bool | None = None,
                         baker: Ref | None = None) -> dict[str, Any]:
    """Cage offsets per bake group (all groups when omitted). `estimate` lets Toolbag
    estimate first, explicit min/max then override."""
    params = _p(groups=groups, min_offset=min_offset, max_offset=max_offset,
                estimate=estimate, cage_alpha=cage_alpha, baker=baker)
    if exclude_when_ignoring_groups is not None:
        params["exclude_when_ignoring_groups"] = exclude_when_ignoring_groups
    return await _call("baker.set_cage", params)


@server.tool(annotations=WRITE)
async def baker_set_visible(groups: str | list[str] | None = None, visible: bool | None = None,
                            low: bool | None = None, high: bool | None = None,
                            baker: Ref | None = None) -> dict[str, Any]:
    """Show or hide bake groups, or their Low and High nodes separately. The safe way to
    toggle meshes in a bake scene."""
    return await _call("baker.set_visible", _p(groups=groups, visible=visible, low=low, high=high,
                                               baker=baker))


@server.tool(annotations=WRITE)
async def baker_isolate(groups: str | list[str], baker: Ref | None = None) -> dict[str, Any]:
    """Show only these bake groups and hide all others."""
    return await _call("baker.isolate", _p(groups=groups, baker=baker))


@server.tool(annotations=WRITE)
async def baker_bake(baker: Ref | None = None) -> dict[str, Any]:
    """Bake with the project's current settings and write the enabled maps next to
    outputPath ('<stem>_<suffix>.<ext>'). Synchronous, can take minutes."""
    return await _call("baker.bake", _p(baker=baker), LONG)


@server.tool(annotations=WRITE)
async def baker_bake_psd(baker: Ref | None = None) -> dict[str, Any]:
    """Bake all enabled maps into one layered PSD at the output path."""
    return await _call("baker.bake_psd", _p(baker=baker), LONG)


@server.tool(annotations=WRITE)
async def baker_bake_to(path: str, maps: list[str] | None = None, width: int = 2048,
                        height: int | None = None, samples: int = 16,
                        settings: dict[str, Any] | None = None,
                        baker: Ref | None = None) -> dict[str, Any]:
    """Trial bake of selected maps (default ['normals']) into a separate file, e.g.
    'C:/tmp/test.png' gives 'C:/tmp/test_normals.png', at the given size and samples. The
    project's settings and map switches are restored afterwards; `settings` are temporary
    extras such as {"smoothCage": false}."""
    return await _call("baker.bake_to", _p(path=path, maps=maps, width=width, height=height,
                                           samples=samples, settings=settings, baker=baker), LONG)


@server.tool(annotations=READ)
async def baker_map_path(map_name: str, tile_suffix: str = "",
                         baker: Ref | None = None) -> dict[str, Any]:
    """Toolbag's own output path for a map by display name ('Normals', 'Ambient Occlusion',
    'Height'), optionally for a texture set name or UDIM tile ('1001')."""
    return await _call("baker.map_path",
                       _p(map_name=map_name, tile_suffix=tile_suffix, baker=baker))


@server.tool(annotations=WRITE)
async def baker_load_preset(name: str, baker: Ref | None = None) -> dict[str, Any]:
    """Load a saved baker preset by name."""
    return await _call("baker.load_preset", _p(name=name, baker=baker))


@server.tool(annotations=WRITE)
async def baker_save_preset(name: str, baker: Ref | None = None) -> dict[str, Any]:
    """Save the bake project's configuration as a named preset."""
    return await _call("baker.save_preset", _p(name=name, baker=baker))


@server.tool(annotations=WRITE)
async def baker_set_texture_set(index: int | str, enabled: bool | None = None,
                                width: int | None = None, height: int | None = None,
                                baker: Ref | None = None) -> dict[str, Any]:
    """Enable and size one texture set by index or name (tileMode 1)."""
    return await _call("baker.set_texture_set", _p(index=index, enabled=enabled, width=width,
                                                   height=height, baker=baker))


@server.tool(annotations=WRITE)
async def baker_set_udim_tile(index: int | str, enabled: bool | None = None,
                              width: int | None = None, height: int | None = None,
                              baker: Ref | None = None) -> dict[str, Any]:
    """Enable and size one UDIM tile by index or name such as '1001' (tileMode 2)."""
    return await _call("baker.set_udim_tile", _p(index=index, enabled=enabled, width=width,
                                                 height=height, baker=baker))


@server.tool(annotations=WRITE)
async def baker_replace_low(group: str, path: str, baker: Ref | None = None) -> dict[str, Any]:
    """Swap the geometry of a group's single low mesh for the mesh in a model file (new UVs,
    fixed topology) while keeping group, cage and material. Save the scene first if you rely
    on stored cage directions."""
    return await _call("baker.replace_low", _p(group=group, path=path, baker=baker), LONG)


# ---------------------------------------------------------------- skew masks (edge protect)

SKEW_DIR = Path.home() / ".toolbag-mcp" / "skew"  # Toolbag must read the previews: not %TEMP%
MASK_SIZES = (512, 1024, 2048)  # 4096 would add 32 MB per mask to the scene file


async def _skew_masks(groups: str | list[str] | None, baker: Ref | None, size: int,
                      widths: tuple[float, float, float], bend_deg: float,
                      soft_px: float) -> tuple[dict[str, Any], dict[str, tuple[str, Any, float]]]:
    """Low mesh data from the plugin, masks computed here. Widths come in bake pixels."""
    if size not in MASK_SIZES:
        raise ToolError("mask_size must be one of %s" % (MASK_SIZES,))
    data = await _call("baker.low_mesh_data", _p(groups=groups, baker=baker), LONG)
    if not data["groups"]:
        raise ToolError("No bake group with low meshes in %s" % data["baker"])
    k = size / float(data["output_width"] or size)
    seam_px, hard_px, bend_px = (w * k for w in widths)

    def compute() -> dict[str, tuple[str, Any, float]]:
        out = {}
        for name, meshes in data["groups"].items():
            arrays = [skew.MeshArrays.from_plugin(m) for m in meshes]
            mask, covered = skew.edge_mask(arrays, size, seam_px, hard_px, bend_px, bend_deg,
                                           soft_px * k)
            share = float((mask[covered] >= 128).mean() * 100) if covered.any() else 0.0
            out[name] = (meshes[0]["name"], mask, round(share, 1))
        return out

    return data, await anyio.to_thread.run_sync(compute)


def _skew_report(data: dict[str, Any], masks: dict[str, tuple[str, Any, float]],
                 size: int) -> dict[str, Any]:
    return {"baker": data["baker"], "mask_size": size,
            "bake_pixels_per_mask_pixel": round(data["output_width"] / size, 2),
            "groups": {name: {"low_mesh": low, "averaged_percent": share}
                       for name, (low, _mask, share) in masks.items()}}


@server.tool(annotations=WRITE)
async def baker_skew_preview(groups: str | list[str] | None = None, seam_px: float = 8.0,
                             hard_px: float = 8.0, bend_px: float = 8.0, bend_deg: float = 8.0,
                             soft_px: float = 0.0, mask_size: int = 1024,
                             baker: Ref | None = None) -> dict[str, Any]:
    """Edge protect: compute skew masks by edge rule and show them as base colour on the low
    materials (black = averaged cage, white = corrected). Full correction everywhere except
    bands along UV seams and open borders (`seam_px`), hard edges (`hard_px`) and soft edges
    bending more than `bend_deg` degrees (`bend_px`); widths in bake pixels, 0 switches a
    band off, `soft_px` adds a falloff. Nothing is written into the scene: check the look,
    adjust, then baker_skew_apply. baker_skew_clear_preview restores the materials."""
    data, masks = await _skew_masks(groups, baker, mask_size, (seam_px, hard_px, bend_px),
                                    bend_deg, soft_px)
    SKEW_DIR.mkdir(parents=True, exist_ok=True)
    for old in SKEW_DIR.glob("preview_*.png"):
        with contextlib.suppress(OSError):
            old.unlink()
    combined = None
    for _low, mask, _share in masks.values():
        combined = mask if combined is None else np.minimum(combined, mask)
    # A new name per preview: Toolbag caches textures by path.
    path = SKEW_DIR / ("preview_%d.png" % time.time_ns())
    skew.write_png_gray(str(path), 255 - combined)
    shown = await _call("baker.skew_preview", _p(path=path.as_posix(), groups=groups,
                                                  baker=baker))
    return {**_skew_report(data, masks, mask_size), "preview": shown["preview"],
            "materials": shown["materials"]}


@server.tool(annotations=WRITE)
async def baker_skew_clear_preview() -> dict[str, Any]:
    """End the skew mask preview: the low materials get their albedo map and colour back."""
    return await _call("baker.skew_preview_clear")


@server.tool(annotations=WRITE)
async def baker_skew_apply(path: str | None = None, groups: str | list[str] | None = None,
                           seam_px: float = 8.0, hard_px: float = 8.0, bend_px: float = 8.0,
                           bend_deg: float = 8.0, soft_px: float = 0.0, mask_size: int = 1024,
                           baker: Ref | None = None) -> dict[str, Any]:
    """Edge protect: write skew masks by edge rule (see baker_skew_preview, same parameters)
    into the bake groups. The Python API cannot set skew masks, so the scene is saved as a
    new file, the masks go into that file and Toolbag loads it. `path` is that new file
    (never overwritten); default `<scene>_skew.tbscene` next to the current scene, which then
    must have been saved once. The original scene file stays untouched."""
    data, masks = await _skew_masks(groups, baker, mask_size, (seam_px, hard_px, bend_px),
                                    bend_deg, soft_px)
    if path:
        target = os.path.abspath(os.path.expanduser(path)).replace("\\", "/")
        if not target.lower().endswith(".tbscene"):
            raise ToolError("path must end in .tbscene")
        if os.path.exists(target):
            raise ToolError("%s exists; skew masks are only written into a new file" % target)
    else:
        scene = (await _call("scene.info"))["path"]
        if not scene:
            raise ToolError("The scene has no file yet: save it once or pass 'path'")
        target = tbscene.new_skew_path(scene)
    await _call("baker.skew_preview_clear")  # the preview must not end up in the file
    await _call("scene.save", {"path": target}, LONG)

    def patch() -> None:
        with open(target, "rb") as handle:
            content = handle.read()
        for _group, (low, mask, _share) in masks.items():
            content = tbscene.write_skew(content, low, mask.tobytes())
        with open(target, "r+b") as handle:  # just written by Toolbag for this call
            handle.write(content)
            handle.truncate()

    try:
        await anyio.to_thread.run_sync(patch)
    except (KeyError, IndexError, ValueError) as exc:
        raise ToolError("Saved %s but could not set the masks: %s" % (target, exc)) from exc
    loaded = await _call("scene.load", {"path": target}, LONG)
    return {**_skew_report(data, masks, mask_size), "scene": loaded.get("path", target)}


# ---------------------------------------------------------------- any object's attributes

@server.tool(annotations=READ)
async def object_attributes(ref: Ref) -> dict[str, Any]:
    """Every plain attribute of an object with its current value, type-specific: light,
    camera (lens, limits, postEffect expanded), sky, fog, shadow catcher, backdrop, mesh.
    These are the names object_set_attributes takes."""
    return await _call("object.attributes", {"ref": ref})


@server.tool(annotations=WRITE)
async def object_set_attributes(ref: Ref, attributes: dict[str, Any]) -> dict[str, Any]:
    """Set attributes of any object by name: {"brightness": 3, "color": [1, 0.9, 0.8]} for a
    light, {"lens": {"dofStop": 2.8}} or {"lens.dofStop": 2.8} for a camera, {"density":
    0.2} for fog, {"opacity": 0.5} for a shadow catcher."""
    return await _call("object.set_attributes", {"ref": ref, "attributes": attributes})


@server.tool(annotations=WRITE)
async def object_create(object_type: str, name: str | None = None,
                        attributes: dict[str, Any] | None = None,
                        position: list[float] | None = None,
                        rotation: list[float] | None = None,
                        parent: Ref | None = None) -> dict[str, Any]:
    """Create a scene object by class: LightObject, CameraObject, FogObject, SkyBoxObject,
    ShadowCatcherObject, BackdropObject, SceneObject (a group), TransformObject,
    BakerObject; with optional name, transform, parent and attributes set right away."""
    return await _call("object.create", _p(object_type=object_type, name=name,
                                           attributes=attributes, position=position,
                                           rotation=rotation, parent=parent))


# ---------------------------------------------------------------- lights, sky, fog, cameras

@server.tool(annotations=READ)
async def light_list() -> dict[str, Any]:
    """All lights (sky child lights included) with type, brightness, colour, shadows, shape
    and transform."""
    return await _call("light.list")


@server.tool(annotations=WRITE)
async def light_create(light_type: str = "omni", name: str | None = None,
                       position: list[float] | None = None,
                       rotation: list[float] | None = None,
                       attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a light: 'omni', 'spot' or 'directional', with optional transform and
    attributes such as brightness, color [r, g, b], castShadows, width, lengthX, lengthY,
    spotAngle, useTemperature, temperature (object_attributes lists what this Toolbag build
    has; 5.02 has no shape or physical units)."""
    return await _call("light.create", _p(light_type=light_type, name=name, position=position,
                                          rotation=rotation, attributes=attributes))


@server.tool(annotations=READ)
async def sky_get(ref: Ref | None = None) -> dict[str, Any]:
    """The sky: mode, brightness, rotation, background, procedural sun settings and its
    child lights."""
    return await _call("sky.get", _p(ref=ref))


@server.tool(annotations=WRITE)
async def sky_set(attributes: dict[str, Any], ref: Ref | None = None) -> dict[str, Any]:
    """Set sky attributes: brightness, rotation (degrees), mode ('color', 'sky', 'blurred
    sky', 'ambient sky'), backgroundColor, blur, and for a procedural sky time, day,
    latitude, longitude, turbidity, sunBrightness."""
    return await _call("sky.set", _p(attributes=attributes, ref=ref))


@server.tool(annotations=WRITE)
async def sky_load(path: str, ref: Ref | None = None) -> dict[str, Any]:
    """Load a sky: a .tbsky file replaces the whole sky, an image (.hdr, .exr, .png, .jpg)
    becomes the panorama."""
    return await _call("sky.load", _p(path=path, ref=ref), LONG)


@server.tool(annotations=WRITE)
async def sky_save(path: str, ref: Ref | None = None) -> dict[str, Any]:
    """Save the sky as a .tbsky archive."""
    return await _call("sky.save", _p(path=path, ref=ref), LONG)


@server.tool(annotations=WRITE)
async def sky_set_background_image(path: str | None = None,
                                   ref: Ref | None = None) -> dict[str, Any]:
    """Use an image as the sky's background; an empty path clears it."""
    return await _call("sky.set_background_image", _p(path=path, ref=ref), LONG)


@server.tool(annotations=WRITE)
async def sky_compute_procedural(ref: Ref | None = None) -> dict[str, Any]:
    """Switch the sky to procedural mode (sun by time, day and location) and recompute it."""
    return await _call("sky.compute_procedural", _p(ref=ref))


@server.tool(annotations=READ)
async def fog_get(ref: Ref | None = None) -> dict[str, Any]:
    """The scene's fog with its attributes, or exists: false when there is none."""
    return await _call("fog.get", _p(ref=ref))


@server.tool(annotations=WRITE)
async def fog_set(attributes: dict[str, Any], ref: Ref | None = None,
                  create: bool = True) -> dict[str, Any]:
    """Set fog attributes (color, density, opacity, volumeType 'fit'/'vertical', heightMin,
    heightMax, falloffMin, falloffMax); creates the fog object when the scene has none."""
    return await _call("fog.set", _p(attributes=attributes, ref=ref, create=create))


@server.tool(annotations=READ)
async def camera_list() -> dict[str, Any]:
    """All cameras with mode, field of view, focal length and which one is active."""
    return await _call("camera.list")


@server.tool(annotations=READ)
async def camera_get(ref: Ref | None = None) -> dict[str, Any]:
    """One camera in full (the active one by default): transform, mode, fov, focal length,
    lens (depth of field, distortion, flares, motion blur), limits and postEffect (tone
    mapping, exposure, contrast, bloom, vignette, grain)."""
    return await _call("camera.get", _p(ref=ref))


@server.tool(annotations=WRITE)
async def camera_create(name: str | None = None, position: list[float] | None = None,
                        rotation: list[float] | None = None,
                        attributes: dict[str, Any] | None = None,
                        activate: bool = False) -> dict[str, Any]:
    """Create a camera with optional transform and attributes; activate makes it the
    viewport and render camera."""
    return await _call("camera.create", _p(name=name, position=position, rotation=rotation,
                                           attributes=attributes, activate=activate))


@server.tool(annotations=WRITE)
async def camera_set(attributes: dict[str, Any], ref: Ref | None = None) -> dict[str, Any]:
    """Set camera attributes (the active camera by default): fov, focalLength, mode,
    {"lens": {"dofMode": 1, "dofStop": 2.8, "dofFocusDistance": 50}}, {"postEffect":
    {"toneMappingMode": "aces", "exposure": 1.2, "bloomBrightness": 0.1}}, {"limits": {...}}."""
    return await _call("camera.set", _p(attributes=attributes, ref=ref))


@server.tool(annotations=WRITE)
async def camera_set_active(ref: Ref) -> dict[str, Any]:
    """Make a camera the active viewport and render camera."""
    return await _call("camera.set_active", {"ref": ref})


@server.tool(annotations=WRITE)
async def camera_post_effect_file(path: str, save: bool = False,
                                  ref: Ref | None = None) -> dict[str, Any]:
    """Load a camera post effect preset from a file, or save the current one (save=true)."""
    return await _call("camera.post_effect_file", _p(path=path, save=save, ref=ref))


# ---------------------------------------------------------------- render and timeline

@server.tool(annotations=READ)
async def render_get() -> dict[str, Any]:
    """The render object: options (renderer, ray tracing, shadows, occlusion, wireframe,
    watermark), image and video output settings (outputPath, format, width, height,
    samples, transparency), and the cameras and passes enabled for export."""
    return await _call("render.get")


@server.tool(annotations=WRITE)
async def render_set(options: dict[str, Any] | None = None, images: dict[str, Any] | None = None,
                     videos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Change render settings: options ({"renderer": "raytrace", "rayTraceBounces": 4,
    "shadowQuality": "High"}), images ({"outputPath": "C:/out", "format": "PNG", "width":
    1920, "height": 1080, "samples": 256, "transparency": true}), videos (same keys)."""
    return await _call("render.set", _p(options=options, images=images, videos=videos))


@server.tool(annotations=WRITE)
async def render_set_cameras(enable: list[Ref] | None = None,
                             disable: list[Ref] | None = None) -> dict[str, Any]:
    """Choose which cameras render on render_images and render_videos."""
    return await _call("render.set_cameras", _p(enable=enable, disable=disable))


@server.tool(annotations=WRITE)
async def render_set_passes(enable: list[str] | None = None,
                            disable: list[str] | None = None) -> dict[str, Any]:
    """Switch render passes on or off by name (render_get lists them)."""
    return await _call("render.set_passes", _p(enable=enable, disable=disable))


@server.tool(annotations=WRITE)
async def render_image(path: str | None = None, width: int = -1, height: int = -1,
                       sampling: int = -1, transparency: bool = False, camera: Ref | None = None,
                       viewport_pass: str = "") -> dict[str, Any]:
    """Render one image with the active camera (or the given one) using the render
    object's settings; width, height and sampling default to those settings. With path
    the file is written (format by extension). Can take minutes with ray tracing."""
    return await _call("render.image", _p(path=path, width=width, height=height,
                                          sampling=sampling, transparency=transparency,
                                          camera=camera, viewport_pass=viewport_pass), LONG)


@server.tool(annotations=WRITE)
async def render_images(width: int = -1, height: int = -1, sampling: int = -1,
                        transparency: bool = False) -> dict[str, Any]:
    """Render all enabled cameras and passes into the image output folder (render_get:
    images.outputPath). Returns the files written."""
    return await _call("render.images", _p(width=width, height=height, sampling=sampling,
                                           transparency=transparency), LONG)


@server.tool(annotations=WRITE)
async def render_videos(width: int = -1, height: int = -1, sampling: int = -1,
                        transparency: bool = False) -> dict[str, Any]:
    """Render the animation into the video output folder (render_get: videos.outputPath).
    Can take long."""
    return await _call("render.videos", _p(width=width, height=height, sampling=sampling,
                                           transparency=transparency), LONG)


@server.tool(annotations=WRITE)
async def render_export_viewer(path: str, title: str = "", author: str = "", author_url: str = "",
                               texture_quality: str = "high", width: int = -1, height: int = -1,
                               export_animations: bool = False) -> dict[str, Any]:
    """Export a Marmoset Viewer (.mview) archive of the scene."""
    return await _call("render.export_viewer", _p(path=path, title=title, author=author,
                                                  author_url=author_url,
                                                  texture_quality=texture_quality, width=width,
                                                  height=height,
                                                  export_animations=export_animations), LONG)


@server.tool(annotations=READ)
async def timeline_get() -> dict[str, Any]:
    """Animation timeline: time, frame, total frames, frame rate, selection, speed."""
    return await _call("timeline.get")


@server.tool(annotations=WRITE)
async def timeline_set(time: float | None = None, frame: int | None = None,
                       total_frames: int | None = None, frame_rate: int | None = None,
                       selection_start: int | None = None, selection_end: int | None = None,
                       playback_speed: float | None = None,
                       play: bool | None = None) -> dict[str, Any]:
    """Set the timeline: time in seconds or frame, total frames, frame rate (resamples
    keyframes), selection range, playback speed; play=true/false starts or stops playback."""
    return await _call("timeline.set", _p(time=time, frame=frame, total_frames=total_frames,
                                          frame_rate=frame_rate, selection_start=selection_start,
                                          selection_end=selection_end,
                                          playback_speed=playback_speed, play=play))


@server.tool(annotations=DESTRUCTIVE)
async def toolbag_quit() -> dict[str, Any]:
    """Quit Toolbag. Unsaved changes are lost without a prompt; the plugin answers and
    Toolbag closes right after."""
    return await _call("toolbag.quit")


# ---------------------------------------------------------------- free Python

@server.tool(annotations=DESTRUCTIVE)
async def execute_python(code: str, timeout: float = 120.0) -> dict[str, Any]:
    """Run Python inside Toolbag with `mset` imported; only while the user has ticked
    "Allow Python execution" in the Toolbag MCP window. The namespace persists between
    calls; assign `result` to return a value; print() output comes back as `log`."""
    return await _call("python.exec", {"code": code}, timeout, with_log=True)


# ---------------------------------------------------------------- resources

@server.resource("toolbag://scene", mime_type="application/json")
async def scene_resource() -> dict[str, Any]:
    """The current scene: path, counts, bake projects, selection."""
    return await _call("scene.info")


@server.resource("toolbag://materials", mime_type="application/json")
async def materials_resource() -> dict[str, Any]:
    """All materials with their shaders."""
    return await _call("material.list")


@server.resource("toolbag://baker", mime_type="application/json")
async def baker_resource() -> dict[str, Any]:
    """The bake projects of the scene; the single one in full, otherwise the list."""
    bakers = await _call("baker.list")
    if len(bakers.get("bakers", [])) == 1:
        return await _call("baker.get")
    return bakers


@server.resource("toolbag://cameras", mime_type="application/json")
async def cameras_resource() -> dict[str, Any]:
    """All cameras and which one is active."""
    return await _call("camera.list")


@server.resource("toolbag://render", mime_type="application/json")
async def render_resource() -> dict[str, Any]:
    """Render options, output settings, cameras and passes."""
    return await _call("render.get")


def run() -> None:
    """Serve over stdio (never print to stdout while serving)."""
    server.run("stdio")
