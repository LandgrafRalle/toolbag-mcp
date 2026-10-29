"""The MCP server: one typed tool per plugin action, served over stdio.

Tools call the plugin through ``ToolbagClient`` in a worker thread, so a long bake does not
block the protocol. Failures inside Toolbag come back as tool errors with the traceback.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .client import PluginError, PluginUnavailable, ToolbagClient

INSTRUCTIONS = """Controls a running Marmoset Toolbag 5 through the Toolbag MCP plugin.

Start with `status`; if it fails the plugin is not running (in Toolbag: Plugins -> Toolbag
MCP). Object references (`ref`, `refs`) are uids (int) or exact names (str); get them from
`scene_list_objects`, `scene_tree` or `object_find`. Materials and bake projects are
referenced by name; `baker` may be omitted when the scene has exactly one bake project.

Bake scenes: toggle visibility with `baker_set_visible` / `baker_isolate` (group, Low and
High nodes), never on single meshes or submeshes, that corrupts bakes. Textures set through
`material_set_texture` are re-read from disk. Imports and bakes can take minutes.
`execute_python` only works while the user ticks "Allow Python execution" in the plugin
window inside Toolbag."""

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


def run() -> None:
    """Serve over stdio (never print to stdout while serving)."""
    server.run("stdio")
