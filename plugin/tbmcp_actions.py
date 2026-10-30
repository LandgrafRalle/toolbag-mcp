"""Named actions of the Toolbag MCP plugin. Everything here runs inside Toolbag, on the
main thread, called by ``__main__.py`` for each request.

An action is a plain function registered with ``@action("family.name")``. Its parameters
are the request's ``params``; it returns JSON-serialisable data (dicts, lists, numbers,
strings). ``ActionError`` is for clean, expected failures (bad parameter, object not found);
anything else reaches the client with a traceback.

Rules the actions enforce, learned the hard way on Toolbag 5.0.2 (docs/bauplan.md):

* ``SubMeshObject.visible`` is never set: Toolbag then bakes the whole scene grainy and
  single parts empty. ``MeshObject.visible`` only with ``allow_mesh``: in bake scenes that
  flag has crashed Toolbag on load. Toggle groups and the bake project's High/Low nodes.
* ``MeshObject.mesh`` is read only where unavoidable (``baker.replace_low``,
  ``baker.low_mesh_data``): reading it and saving afterwards writes low meshes without their
  cage direction attribute, which Toolbag recomputes on load.
* Textures are replaced by clearing the field first, so Toolbag re-reads a changed file.
* ``Material.getSubroutine`` raises for unknown slot names and returns None for empty slots;
  both are wrapped into clear errors.
* ``bake()`` from Python is synchronous and never interactive.

Object references (``ref``) are a uid (int) or an exact object name (str). Ambiguous names
are rejected with the candidate uids.
"""

from __future__ import annotations

import inspect
import os
import sys
import time

import mset

ACTIONS = {}
STATE = {}  # filled by __main__: plugin_version, port, allow_python, calls, started


class ActionError(Exception):
    """A clean, user-facing failure: bad parameter, missing object, refused operation."""


def action(name):
    def register(fn):
        ACTIONS[name] = fn
        return fn
    return register


# ---------------------------------------------------------------- dispatch

def _param_list(fn):
    out = []
    for p in inspect.signature(fn).parameters.values():
        if p.default is inspect.Parameter.empty:
            out.append(p.name)
        else:
            out.append("%s=%r" % (p.name, p.default))
    return ", ".join(out)


def describe():
    """Catalogue for GET /actions: parameters (with defaults) and docstring per action."""
    out = {}
    for name, fn in sorted(ACTIONS.items()):
        params = {}
        for p in inspect.signature(fn).parameters.values():
            required = p.default is inspect.Parameter.empty
            params[p.name] = {"required": required,
                              "default": None if required else _jsonable(p.default)}
        out[name] = {"params": params, "doc": inspect.getdoc(fn) or ""}
    return out


def run(name, params):
    fn = ACTIONS.get(name)
    if fn is None:
        raise ActionError("Unknown action %r. Known actions: %s"
                          % (name, ", ".join(sorted(ACTIONS))))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ActionError("params must be an object")
    try:
        bound = inspect.signature(fn).bind(**params)
    except TypeError as exc:
        raise ActionError("%s: %s. Parameters: %s(%s)"
                          % (name, exc, name, _param_list(fn))) from None
    return _jsonable(fn(*bound.args, **bound.kwargs))


# ---------------------------------------------------------------- helpers

def _jsonable(value, depth=0):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 12:
        return repr(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth + 1) for v in value]
    if isinstance(value, mset.Texture):
        return {"texture": value.path, "sRGB": value.sRGB}
    if isinstance(value, mset.Material):
        return {"material": value.name}
    if isinstance(value, mset.SceneObject):
        return _ref(value)
    return repr(value)


def _ref(obj):
    return {"uid": obj.uid, "name": obj.name, "type": type(obj).__name__}


def _all():
    return mset.getAllObjects()


def _is_type(obj, type_name):
    cls = getattr(mset, type_name, None)
    return isinstance(cls, type) and isinstance(obj, cls)


def resolve(ref, expect=None):
    """One scene object by uid or exact name (case-insensitive as a fallback)."""
    if isinstance(ref, dict) and "uid" in ref:
        ref = ref["uid"]
    objs = _all()
    if isinstance(ref, bool) or not isinstance(ref, (int, str)):
        raise ActionError("An object reference is a uid (int) or a name (str), got %r" % (ref,))
    if isinstance(ref, int):
        hits = [o for o in objs if o.uid == ref]
    else:
        hits = [o for o in objs if o.name == ref]
        if not hits:
            hits = [o for o in objs if o.name.lower() == ref.lower()]
    if expect is not None:
        hits = [o for o in hits if isinstance(o, expect)]
    if not hits:
        what = " of type %s" % expect.__name__ if expect is not None else ""
        raise ActionError("No object %r%s in the scene (use scene.list_objects)" % (ref, what))
    if len(hits) > 1:
        raise ActionError("Ambiguous name %r, uids %s: pass the uid" % (ref, [o.uid for o in hits]))
    return hits[0]


def resolve_many(refs, expect=None):
    if isinstance(refs, (int, str, dict)):
        refs = [refs]
    if not isinstance(refs, (list, tuple)) or not refs:
        raise ActionError("Expected one reference or a non-empty list of references")
    return [resolve(r, expect) for r in refs]


def _abs(path):
    if not isinstance(path, str) or not path.strip():
        raise ActionError("A file path is required")
    return os.path.abspath(os.path.expanduser(path))


def _existing_file(path):
    path = _abs(path)
    if not os.path.isfile(path):
        raise ActionError("File not found: %s" % path)
    return path


def _ensure_dir(path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _get(obj, key):
    try:
        return _jsonable(getattr(obj, key))
    except Exception as exc:  # noqa: BLE001
        return "<error: %s>" % exc


def _descendants(obj):
    out = []
    for child in obj.getChildren():
        out.append(child)
        out.extend(_descendants(child))
    return out


def _meshes_under(obj):
    return [o for o in [obj] + _descendants(obj) if isinstance(o, mset.MeshObject)]


_TRANSFORM_KEYS = ("position", "rotation", "scale")
_MESH_KEYS = ("castShadows", "cullBackFaces", "tangentSpace", "tangentInterpolation",
              "fixMirroredTangents", "subdivisionEnabled", "subdivisionLevel", "subdivisionMode",
              "invisibleToCamera")
_CAMERA_KEYS = ("mode", "fov", "focalLength", "orthoScale", "orbitRadius", "nearPlaneScale")
_LIGHT_KEYS = ("lightType", "color", "brightness", "shape", "castShadows", "spotAngle", "width",
               "lengthX", "lengthY", "useTemperature", "temperature", "physicalUnits",
               "visibleShape")


def info(obj, detail=False):
    """Compact description of a scene object; ``detail`` adds type-specific attributes and
    bounds. Never reads ``MeshObject.mesh`` (see module docstring)."""
    d = _ref(obj)
    d["visible"] = obj.visible
    d["locked"] = obj.locked
    parent = obj.parent
    d["parent"] = _ref(parent) if parent is not None else None
    d["children"] = len(obj.getChildren())
    if isinstance(obj, mset.TransformObject):
        for key in _TRANSFORM_KEYS:
            d[key] = _get(obj, key)
    if isinstance(obj, mset.SubMeshObject):
        material = obj.material
        d["material"] = material.name if material is not None else None
    if detail:
        if isinstance(obj, mset.TransformObject):
            d["pivot"] = _get(obj, "pivot")
        if isinstance(obj, mset.MeshObject):
            for key in _MESH_KEYS:
                d[key] = _get(obj, key)
            d["submeshes"] = [{"uid": s.uid, "name": s.name,
                               "material": s.material.name if s.material is not None else None,
                               "startIndex": s.startIndex, "indexCount": s.indexCount}
                              for s in obj.getChildren() if isinstance(s, mset.SubMeshObject)]
        if isinstance(obj, mset.SubMeshObject):
            d["startIndex"], d["indexCount"] = obj.startIndex, obj.indexCount
        if isinstance(obj, mset.CameraObject):
            for key in _CAMERA_KEYS:
                d[key] = _get(obj, key)
        if isinstance(obj, mset.LightObject):
            for key in _LIGHT_KEYS:
                d[key] = _get(obj, key)
        try:
            d["bounds"] = obj.getBounds()
        except Exception:  # noqa: BLE001
            d["bounds"] = None
    return d


# ---------------------------------------------------------------- family 1: diagnostics

def _version_string(version):
    digits = str(version)
    if len(digits) >= 4:
        return "%s.%s.%s" % (digits[0], digits[1:3], digits[3:])
    return digits


@action("status")
def status():
    """Toolbag version, graphics adapter, scene path and plugin state (port, Python switch,
    call counter). Start here to check that the plugin is reachable."""
    version = mset.getToolbagVersion()
    objs = _all()
    return {
        "toolbag_version": version,
        "toolbag_version_string": _version_string(version),
        "graphics_adapter": mset.getGraphicsAdapterName(),
        "python": sys.version.split()[0],
        "scene_path": mset.getScenePath(),
        "scene_unit_scale_m": mset.getSceneUnitScale(),
        "objects": len(objs),
        "materials": len(mset.getAllMaterials()),
        "bakers": [b.name for b in objs if isinstance(b, mset.BakerObject)],
        "plugin": dict(STATE),
    }


@action("capabilities")
def capabilities():
    """Everything the running Toolbag exposes in ``mset`` plus the action catalogue, for
    version differences (Toolbag 4 vs 5)."""
    return {
        "mset": sorted(n for n in dir(mset) if not n.startswith("_")),
        "callbacks": sorted(n for n in dir(mset.callbacks) if not n.startswith("_")),
        "actions": sorted(ACTIONS),
        "plugin_version": STATE.get("plugin_version"),
    }


@action("preferences.get")
def preferences_get(keys=None):
    """Application preferences, all of them or the listed keys."""
    prefs = mset.getPreferences()
    names = keys or [n for n in dir(prefs) if not n.startswith("_")]
    out = {}
    for name in names:
        try:
            value = getattr(prefs, name)
        except Exception as exc:  # noqa: BLE001
            out[name] = "<error: %s>" % exc
            continue
        if callable(value):
            continue
        out[name] = _jsonable(value)
    return out


@action("preferences.set")
def preferences_set(key, value):
    """Set one application preference (see preferences.get for keys and current values)."""
    prefs = mset.getPreferences()
    if key.startswith("_") or not hasattr(prefs, key) or callable(getattr(prefs, key)):
        raise ActionError("Unknown preference %r" % key)
    setattr(prefs, key, value)
    return {key: _jsonable(getattr(prefs, key))}


# ---------------------------------------------------------------- family 2: scene and objects

@action("scene.info")
def scene_info():
    """Scene path, unit scale, bounds, object counts by type, bake projects and selection."""
    objs = _all()
    by_type = {}
    for o in objs:
        by_type[type(o).__name__] = by_type.get(type(o).__name__, 0) + 1
    return {
        "path": mset.getScenePath(),
        "unit_scale_m": mset.getSceneUnitScale(),
        "bounds": mset.getSceneBounds(),
        "objects": len(objs),
        "by_type": by_type,
        "materials": len(mset.getAllMaterials()),
        "bakers": [b.name for b in objs if isinstance(b, mset.BakerObject)],
        "selection": [_ref(o) for o in mset.getSelectedObjects()],
    }


@action("scene.new")
def scene_new():
    """New empty scene. Unsaved changes are lost without a prompt."""
    # Never reached through run(): __main__ performs scene operations two-phased (see
    # prepare_scene_op), because Toolbag 5.02 crashes when Python keeps running in the
    # callback tick that tore the scene down.
    mset.newScene()
    return scene_info()


@action("scene.load")
def scene_load(path, download_missing_library_assets=False):
    """Load a .tbscene file. Unsaved changes are lost without a prompt."""
    mset.loadScene(_existing_file(path), bool(download_missing_library_assets))
    return scene_info()


SCENE_ACTIONS = ("scene.new", "scene.load")
QUIT_ACTION = "toolbag.quit"


@action(QUIT_ACTION)
def toolbag_quit():
    """Quit Toolbag; unsaved changes are lost without a prompt. The plugin answers first
    and quits in its next callback tick (handled by __main__)."""
    mset.quit()
    return {"quitting": True}


def prepare_scene_op(name, params):
    """Validate the parameters of a scene operation and return a zero-argument callable
    that does only the Toolbag call. The caller runs it as the last thing in a callback tick
    and reports scene.info in a later tick."""
    fn = ACTIONS[name]
    try:
        bound = inspect.signature(fn).bind(**(params or {}))
    except TypeError as exc:
        raise ActionError("%s: %s. Parameters: %s(%s)"
                          % (name, exc, name, _param_list(fn))) from None
    _SKEW_PREVIEW.clear()  # the materials it remembers belong to the scene being replaced
    if name == "scene.new":
        return mset.newScene
    path = _existing_file(bound.arguments["path"])
    download = bool(bound.arguments.get("download_missing_library_assets", False))

    def _load():
        mset.loadScene(path, download)
    return _load


@action("scene.save")
def scene_save(path=None):
    """Save the scene to its current file, or to ``path`` (save as)."""
    if path:
        path = _abs(path)
        _ensure_dir(path)
    else:
        path = mset.getScenePath()
        if not path:
            raise ActionError("The scene has no file yet: pass 'path'")
    # Backslashes break Toolbag's autosave: it then appends the whole path to its folder.
    mset.saveScene(path.replace("\\", "/"))
    return {"path": mset.getScenePath()}


@action("scene.list_objects")
def scene_list_objects(object_type=None, name_contains=None, parent=None, include_hidden=True,
                       limit=500):
    """Flat list of scene objects with uid, type, parent, visibility and transform.
    ``object_type`` filters by class name (MeshObject, SubMeshObject, BakerObject,
    LightObject, CameraObject, SkyBoxObject, ...), ``parent`` restricts to the descendants of
    one object."""
    objs = _descendants(resolve(parent)) if parent is not None else _all()
    out = []
    for o in objs:
        if object_type and type(o).__name__ != object_type and not _is_type(o, object_type):
            continue
        if name_contains and name_contains.lower() not in o.name.lower():
            continue
        if not include_hidden and not o.visible:
            continue
        out.append(info(o))
        if len(out) >= int(limit):
            break
    return {"count": len(out), "total": len(objs), "objects": out}


@action("scene.tree")
def scene_tree(root=None, depth=4):
    """Object hierarchy as nested nodes (uid, name, type, visible), ``depth`` levels deep;
    deeper levels show only the child count."""
    def node(obj, level):
        entry = {"uid": obj.uid, "name": obj.name, "type": type(obj).__name__,
                 "visible": obj.visible}
        kids = obj.getChildren()
        if kids:
            entry["children"] = [node(c, level + 1) for c in kids] if level < depth else len(kids)
        return entry

    roots = [resolve(root)] if root is not None else [o for o in _all() if o.parent is None]
    return {"roots": [node(o, 1) for o in roots]}


@action("object.get")
def object_get(ref):
    """Everything about one object: transform, pivot, bounds, mesh/camera/light attributes,
    submeshes with materials."""
    return info(resolve(ref), detail=True)


@action("object.find")
def object_find(name, exact=False, limit=100):
    """Objects whose name contains ``name`` (or equals it with ``exact``)."""
    if exact:
        hits = [o for o in _all() if o.name == name]
    else:
        hits = [o for o in _all() if name.lower() in o.name.lower()]
    return {"count": len(hits), "objects": [info(o) for o in hits[:int(limit)]]}


@action("selection.get")
def selection_get():
    """The selected objects."""
    return {"selection": [info(o) for o in mset.getSelectedObjects()]}


@action("selection.set")
def selection_set(refs):
    """Select exactly these objects (uids or names)."""
    objs = resolve_many(refs)
    ok = mset.setSelectedObjects(objs)
    return {"ok": bool(ok), "selection": [_ref(o) for o in mset.getSelectedObjects()]}


@action("selection.clear")
def selection_clear():
    """Deselect everything."""
    mset.setSelectedObjects([])
    return {"selection": [_ref(o) for o in mset.getSelectedObjects()]}


@action("object.set_visible")
def object_set_visible(refs, visible, allow_mesh=False):
    """Show or hide objects. Refuses SubMeshObjects always and MeshObjects unless
    ``allow_mesh`` is true: in bake scenes those flags corrupt bakes or crash on load.
    Toggle groups or the bake project's High/Low nodes instead."""
    objs = resolve_many(refs)
    for o in objs:
        if isinstance(o, mset.SubMeshObject):
            raise ActionError(
                "Refusing to change visibility of SubMeshObject %r (uid %d): Toolbag then bakes "
                "the whole scene grainy and parts empty. Toggle its parent mesh's group or the "
                "bake group's High/Low node." % (o.name, o.uid))
        if isinstance(o, mset.MeshObject) and not allow_mesh:
            raise ActionError(
                "Refusing to change visibility of MeshObject %r (uid %d) by default: in a bake "
                "scene this flag has crashed Toolbag on load. Toggle the parent group or the bake "
                "group's High/Low node, or pass allow_mesh=true to accept the risk."
                % (o.name, o.uid))
    for o in objs:
        o.visible = bool(visible)
    return {"objects": [info(o) for o in objs]}


@action("object.set_transform")
def object_set_transform(ref, position=None, rotation=None, scale=None, pivot=None):
    """Set position, rotation (degrees), scale or pivot of a transform object; each a list
    of three numbers, omitted ones stay."""
    obj = resolve(ref, mset.TransformObject)
    for key, value in (("position", position), ("rotation", rotation), ("scale", scale),
                       ("pivot", pivot)):
        if value is None:
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ActionError("%s must be a list of three numbers" % key)
        setattr(obj, key, [float(v) for v in value])
    return info(obj, detail=True)


@action("object.rename")
def object_rename(ref, name):
    """Rename an object."""
    obj = resolve(ref)
    obj.name = str(name)
    return info(obj)


@action("object.duplicate")
def object_duplicate(ref, name=""):
    """Duplicate an object (with its children); optional name for the copy."""
    copy = resolve(ref).duplicate(name or "")
    return info(copy)


@action("object.delete")
def object_delete(refs):
    """Delete objects from the scene (no undo from here)."""
    objs = resolve_many(refs)
    deleted = [_ref(o) for o in objs]
    for o in objs:
        o.destroy()
    return {"deleted": deleted}


@action("object.group")
def object_group(refs, name=None):
    """Put objects under a new group, optionally named."""
    objs = resolve_many(refs)
    before = {o.uid for o in _all()}
    mset.groupObjects(objs)
    new = [o for o in _all() if o.uid not in before]
    group = new[0] if new else objs[0].parent
    if name and group is not None:
        group.name = str(name)
    return {"group": info(group) if group is not None else None,
            "members": [_ref(o) for o in objs]}


@action("object.set_parent")
def object_set_parent(ref, parent=None):
    """Re-parent an object; ``parent`` omitted moves it to the scene root."""
    obj = resolve(ref)
    obj.parent = resolve(parent) if parent is not None else None
    return info(obj)


@action("scene.import_model")
def scene_import_model(path):
    """Import a model file (FBX, OBJ, glTF, USD, ...) with the scene's import settings.
    Returns the imported root and all new objects."""
    path = _existing_file(path)
    before = {o.uid for o in _all()}
    root = mset.importModel(path)
    new = [o for o in _all() if o.uid not in before]
    return {"root": info(root) if root is not None else None, "count": len(new),
            "new_objects": [info(o) for o in new]}


_EXPORTERS = {
    ".fbx": ("exportSceneFBX", ("models", "lights", "cameras", "materials", "embedassets", "uvmaps",
                                "triangulate", "subdivision", "displacement")),
    ".obj": ("exportSceneOBJ", ("models", "materials", "uvmaps", "subdivision", "displacement")),
    ".glb": ("exportSceneGLTF", ("models", "lights", "cameras", "materials", "normals", "tangents",
                                 "uvmaps", "vertexcolors", "subdivision", "displacement",
                                 "texturequality", "metalnessthreshold")),
    ".usd": ("exportSceneUSD", ("models", "lights", "skies", "cameras", "materials", "normals",
                                "uvmaps", "vertexcolors", "triangulate", "subdivision",
                                "displacement", "copyfilereferences", "skiptextures",
                                "uniquetextures")),
    ".dae": ("exportSceneCOLLADA", ("models", "lights", "materials", "embedassets", "uvmaps",
                                    "subdivision", "displacement")),
}
_EXPORTERS[".gltf"] = _EXPORTERS[".glb"]
for _ext in (".usda", ".usdc", ".usdz"):
    _EXPORTERS[_ext] = _EXPORTERS[".usd"]


@action("scene.export")
def scene_export(path, options=None):
    """Export the scene; the format follows the extension (.fbx, .obj, .glb/.gltf,
    .usd/.usda/.usdc/.usdz, .dae). ``options`` are the exporter's flags, e.g.
    {"materials": false, "triangulate": true}."""
    path = _abs(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in _EXPORTERS:
        raise ActionError("Unsupported extension %r, use one of %s" % (ext, sorted(_EXPORTERS)))
    fn_name, allowed = _EXPORTERS[ext]
    options = dict(options or {})
    unknown = [k for k in options if k not in allowed]
    if unknown:
        raise ActionError("Unknown export options %s for %s, allowed: %s"
                          % (unknown, ext, list(allowed)))
    _ensure_dir(path)
    getattr(mset, fn_name)(path, **options)
    exists = os.path.isfile(path)
    return {"path": path, "exists": exists, "bytes": os.path.getsize(path) if exists else None}


@action("scene.bounds")
def scene_bounds():
    """Bounds of the whole scene as [min_xyz, max_xyz]."""
    return {"bounds": mset.getSceneBounds(), "unit_scale_m": mset.getSceneUnitScale()}


@action("camera.frame")
def camera_frame(ref=None):
    """Frame one object, or the whole scene when ``ref`` is omitted, in the current camera."""
    if ref is None:
        mset.frameScene()
        return {"framed": "scene"}
    obj = resolve(ref)
    mset.frameObject(obj)
    return {"framed": _ref(obj)}


@action("mesh.set_backfaces")
def mesh_set_backfaces(show=True, refs=None):
    """Show back faces (cullBackFaces off) on all meshes or the given ones, like a
    two-sided shader would. Per object, lost on re-import."""
    if refs is not None:
        meshes = resolve_many(refs, mset.MeshObject)
    else:
        meshes = [o for o in _all() if isinstance(o, mset.MeshObject)]
    for m in meshes:
        m.cullBackFaces = not bool(show)
    return {"show_backfaces": bool(show), "meshes": [_ref(m) for m in meshes]}


# ---------------------------------------------------------------- family 3: materials

_SLOTS = ("subdivision", "displacement", "surface", "microsurface", "albedo", "diffusion",
          "transmission", "reflectivity", "reflection", "clearcoat reflection",
          "clearcoat microsurface", "clearcoat reflectivity", "occlusion", "emissive",
          "transparency", "texture")


def _material(name):
    if isinstance(name, dict) and "material" in name:
        name = name["material"]
    if not isinstance(name, str):
        raise ActionError("A material is referenced by its name (str), got %r" % (name,))
    materials = mset.getAllMaterials()
    hits = [m for m in materials if m.name == name]
    if not hits:
        hits = [m for m in materials if m.name.lower() == name.lower()]
    if not hits:
        raise ActionError("No material %r. Materials: %s"
                          % (name, sorted(m.name for m in materials)))
    return hits[0]


def _slot(slot):
    if not isinstance(slot, str) or slot.lower() not in _SLOTS:
        raise ActionError("Unknown material slot %r, slots: %s" % (slot, list(_SLOTS)))
    return slot.lower()


def _subroutine(material, slot, required=True):
    slot = _slot(slot)
    try:
        sub = material.getSubroutine(slot)
    except Exception:  # noqa: BLE001  (IndexError surfaces as SystemError)
        sub = None
    if sub is None and required:
        raise ActionError("Material %r has no shader in slot %r; set one with "
                          "material.set_subroutine" % (material.name, slot))
    return sub


def _field_value(sub, field):
    try:
        return _jsonable(sub.getField(field))
    except Exception as exc:  # noqa: BLE001
        return "<error: %s>" % exc


def _check_field(sub, slot, field):
    names = sub.getFieldNames()
    if field not in names:
        raise ActionError("No field %r in slot %r (%s). Fields: %s"
                          % (field, slot, sub.name, names))


def material_info(material, fields=True):
    d = {"name": material.name, "group": material.getGroup(),
         "assigned": [_ref(o) for o in material.getAssignedObjects()], "slots": {}}
    for slot in _SLOTS:
        sub = _subroutine(material, slot, required=False)
        if sub is None:
            d["slots"][slot] = None
            continue
        entry = {"shader": sub.name}
        if fields:
            entry["fields"] = {f: _field_value(sub, f) for f in sub.getFieldNames()}
        d["slots"][slot] = entry
    return d


@action("material.list")
def material_list(name_contains=None):
    """All materials with group, number of assigned objects and the shader per slot."""
    out = []
    for m in mset.getAllMaterials():
        if name_contains and name_contains.lower() not in m.name.lower():
            continue
        shaders = {}
        for slot in _SLOTS:
            sub = _subroutine(m, slot, required=False)
            if sub is not None:
                shaders[slot] = sub.name
        out.append({"name": m.name, "group": m.getGroup(),
                    "assigned": len(m.getAssignedObjects()), "shaders": shaders})
    return {"count": len(out), "materials": out}


@action("material.get")
def material_get(name, fields=True):
    """One material in full: every slot with shader and field values (textures as path and
    sRGB flag) and the objects it is assigned to."""
    return material_info(_material(name), bool(fields))


@action("material.create")
def material_create(name):
    """Create a material with Toolbag's default shaders."""
    if any(m.name == name for m in mset.getAllMaterials()):
        raise ActionError("Material %r exists already" % name)
    material = mset.Material(name)
    return material_info(material)


@action("material.duplicate")
def material_duplicate(name, new_name=""):
    """Duplicate a material; optional name for the copy."""
    copy = _material(name).duplicate(new_name or "")
    return material_info(copy, fields=False)


@action("material.delete")
def material_delete(name):
    """Delete a material (its objects fall back to the default material)."""
    material = _material(name)
    label = material.name
    material.destroy()
    return {"deleted": label}


@action("material.assign")
def material_assign(name, refs, include_children=True):
    """Assign a material to objects (uids or names), by default including their children."""
    material = _material(name)
    objs = resolve_many(refs)
    for o in objs:
        material.assign(o, bool(include_children))
    return {"material": material.name, "assigned_to": [_ref(o) for o in objs]}


@action("material.set_group")
def material_set_group(name, group=""):
    """Move a material into a material group ('' for none)."""
    material = _material(name)
    material.setGroup(group or "")
    return {"material": material.name, "group": material.getGroup()}


@action("material.set_subroutine")
def material_set_subroutine(name, slot, shader):
    """Set the shader of a slot, e.g. ('transparency', 'Cutout'), ('reflectivity',
    'Metalness'). Returns the slot's field names afterwards."""
    material = _material(name)
    slot = _slot(slot)
    try:
        material.setSubroutine(slot, shader)
    except Exception as exc:  # noqa: BLE001
        raise ActionError("setSubroutine(%r, %r) failed: %s" % (slot, shader, exc)) from exc
    sub = _subroutine(material, slot, required=False)
    return {"material": material.name, "slot": slot, "shader": sub.name if sub else None,
            "fields": sub.getFieldNames() if sub else []}


@action("material.set_field")
def material_set_field(name, slot, field, value):
    """Set a scalar, boolean, enum or color field of a slot (see material.get for names).
    Textures: use material.set_texture."""
    material = _material(name)
    slot = _slot(slot)
    sub = _subroutine(material, slot)
    _check_field(sub, slot, field)
    if isinstance(value, (list, tuple)):
        value = [float(v) for v in value]
    try:
        sub.setField(field, value)
    except Exception as exc:  # noqa: BLE001
        raise ActionError("setField(%r, %r) failed: %s" % (field, value, exc)) from exc
    return {"material": material.name, "slot": slot, "field": field,
            "value": _field_value(sub, field)}


@action("material.set_texture")
def material_set_texture(name, slot, field, path, srgb=None, mipmaps=None, wrapping=None,
                         filtering=None):
    """Put an image file into a texture field (e.g. slot 'albedo', field 'Albedo Map').
    The field is cleared first so Toolbag re-reads a changed file. ``srgb`` and
    ``mipmaps`` default to the previous texture's settings; an empty ``path`` clears the
    field."""
    material = _material(name)
    slot = _slot(slot)
    sub = _subroutine(material, slot)
    _check_field(sub, slot, field)
    old = sub.getField(field)
    old_tex = old if isinstance(old, mset.Texture) else None
    if not path:
        sub.setField(field, None)
        return {"material": material.name, "slot": slot, "field": field, "texture": None,
                "previous": old_tex.path if old_tex else None}
    path = _existing_file(path)
    sub.setField(field, None)
    tex = mset.Texture(path)
    if srgb is not None:
        tex.sRGB = bool(srgb)
    elif old_tex is not None:
        tex.sRGB = old_tex.sRGB
    if mipmaps is not None:
        tex.useMipmaps = bool(mipmaps)
    elif old_tex is not None:
        tex.useMipmaps = old_tex.useMipmaps
    if wrapping is not None:
        tex.wrapping = str(wrapping)
    elif old_tex is not None:
        tex.wrapping = old_tex.wrapping
    if filtering is not None:
        tex.useFiltering = bool(filtering)
    sub.setField(field, tex)
    return {"material": material.name, "slot": slot, "field": field,
            "texture": _field_value(sub, field), "previous": old_tex.path if old_tex else None}


@action("material.set_cutout")
def material_set_cutout(name, alpha_map, channel=0, threshold=None, use_albedo_alpha=False,
                        srgb=False):
    """Alpha-tested transparency from a mask image: transparency slot to Cutout, 'Use
    Albedo Alpha' off, channel (0 = R) and the mask as Alpha Map, re-read from disk."""
    material = _material(name)
    sub = _subroutine(material, "transparency", required=False)
    if sub is None or sub.name != "Cutout":
        material.setSubroutine("transparency", "Cutout")
        sub = _subroutine(material, "transparency")
    sub.setField("Use Albedo Alpha", bool(use_albedo_alpha))
    sub.setField("Channel", int(channel))
    if threshold is not None:
        sub.setField("Threshold", float(threshold))
    result = material_set_texture(material.name, "transparency", "Alpha Map", alpha_map, srgb=srgb)
    result["threshold"] = _field_value(sub, "Threshold")
    result["channel"] = _field_value(sub, "Channel")
    return result


@action("material.import")
def material_import(path, name=""):
    """Import a material file (.tbmat, MTL, named texture files)."""
    material = mset.importMaterial(_existing_file(path), name or "")
    return material_info(material)


@action("material.export")
def material_export(name, path):
    """Export a material to a .tbmat file."""
    material = _material(name)
    path = _abs(path)
    _ensure_dir(path)
    material.exportFile(path)
    return {"material": material.name, "path": path, "exists": os.path.isfile(path)}


# ---------------------------------------------------------------- family 4: baker

_BAKER_SETTINGS = ("outputPath", "outputWidth", "outputHeight", "outputBits", "outputSamples",
                   "edgePadding", "edgePaddingSize", "outputSoften", "smoothCage",
                   "ignoreBackfaces", "useHiddenMeshes", "ignoreTransforms",
                   "fixMirroredTangents", "tileMode", "geometryMode", "interactive")
_RESTORE_SETTINGS = tuple(k for k in _BAKER_SETTINGS if k != "interactive")


def _bakers():
    return [o for o in _all() if isinstance(o, mset.BakerObject)]


def _baker(ref=None):
    bakers = _bakers()
    if ref is not None:
        if isinstance(ref, dict) and "uid" in ref:
            ref = ref["uid"]
        if isinstance(ref, int) and not isinstance(ref, bool):
            hits = [b for b in bakers if b.uid == ref]
        else:
            hits = [b for b in bakers if b.name == ref]
        if not hits:
            raise ActionError("No bake project %r. Bake projects: %s"
                              % (ref, [b.name for b in bakers]))
        return hits[0]
    if not bakers:
        raise ActionError("No bake project in the scene (baker.create makes one)")
    if len(bakers) > 1:
        raise ActionError("Several bake projects, pass 'baker': %s" % [b.name for b in bakers])
    return bakers[0]


def _groups(baker):
    """Group name -> {"group", "low": BakerTargetObject|None, "high": SceneObject|None}."""
    out = {}
    for group in baker.getChildren():
        entry = {"group": group, "low": None, "high": None}
        for child in group.getChildren():
            if isinstance(child, mset.BakerTargetObject):
                entry["low"] = child
            elif child.name == "High" or entry["high"] is None:
                entry["high"] = child
        out[group.name] = entry
    return out


def _select_groups(baker, names):
    groups = _groups(baker)
    if names is None:
        return groups
    if isinstance(names, str):
        names = [names]
    missing = [n for n in names if n not in groups]
    if missing:
        raise ActionError("Unknown bake groups %s. Groups: %s" % (missing, sorted(groups)))
    return {n: groups[n] for n in names}


def _cage(value):
    return None if value is None or value < -1e5 else round(value, 4)


def _map_info(bmap):
    options = {}
    for key in dir(bmap):
        if key.startswith("_") or key in ("suffix", "enabled"):
            continue
        try:
            value = getattr(bmap, key)
        except Exception:  # noqa: BLE001
            continue
        if callable(value):
            continue
        options[key] = _jsonable(value)
    return {"suffix": bmap.suffix, "enabled": bmap.enabled, "type": type(bmap).__name__,
            "options": options}


def _norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _find_map(baker, name):
    """A baker map by suffix ('normal', 'ao'), by class ('AOBakerMap', 'normals') or by
    Toolbag's display name ('Ambient Occlusion'), case-insensitive and tolerant of a plural
    s; the suffix is tried first because getMap() with an unknown name writes an error
    into Toolbag's log."""
    if not isinstance(name, str):
        raise ActionError("A map is referenced by its suffix or name (str), got %r" % (name,))
    maps = baker.getAllMaps()
    wanted = _norm(name)
    variants = {wanted, wanted.rstrip("s")}
    for m in maps:
        suffix = _norm(m.suffix)
        if suffix in variants or suffix.rstrip("s") in variants:
            return m
    for m in maps:
        cls = _norm(type(m).__name__).replace("bakermap", "")
        if cls in variants or cls.rstrip("s") in variants:
            return m
    try:
        m = baker.getMap(name)
        if m is not None:
            return m
    except Exception:  # noqa: BLE001
        pass
    raise ActionError("Unknown map %r. Suffixes: %s" % (name, [m.suffix for m in maps]))


def _output_files(baker, only_enabled=True):
    stem, ext = os.path.splitext(baker.outputPath or "")
    out = []
    for m in baker.getAllMaps():
        if only_enabled and not m.enabled:
            continue
        path = "%s_%s%s" % (stem, m.suffix, ext) if stem else None
        out.append({"suffix": m.suffix, "enabled": m.enabled, "path": path,
                    "exists": bool(path and os.path.isfile(path))})
    return out


def _texture_sets(baker):
    if getattr(baker, "tileMode", 0) != 1:
        return None  # Toolbag raises unless tileMode is 'Multiple'
    sets = []
    try:
        for i in range(baker.getTextureSetCount()):
            sets.append({"index": i, "name": baker.getTextureSetName(i),
                         "enabled": baker.getTextureSetEnabled(i),
                         "width": baker.getTextureSetWidth(i),
                         "height": baker.getTextureSetHeight(i)})
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return sets


def _udim_tiles(baker):
    if getattr(baker, "tileMode", 0) != 2:
        return None  # Toolbag raises unless tileMode is 'UDIM'
    tiles = []
    try:
        for i in range(baker.getUDIMTileCount()):
            tiles.append({"index": i, "name": baker.getUDIMTileName(i),
                          "enabled": baker.getUDIMTileEnabled(i),
                          "width": baker.getUDIMTileWidth(i),
                          "height": baker.getUDIMTileHeight(i)})
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return tiles


def baker_info(baker, maps=True):
    groups = {}
    for name, part in _groups(baker).items():
        low, high = part["low"], part["high"]
        groups[name] = {
            "uid": part["group"].uid,
            "visible": part["group"].visible,
            "low": None if low is None else {
                "uid": low.uid, "visible": low.visible,
                "cage_min": _cage(low.minOffset), "cage_max": _cage(low.maxOffset),
                "cage_alpha": low.cageAlpha,
                "exclude_when_ignoring_groups": low.excludeWhenIgnoringGroups,
                "meshes": [_ref(m) for m in _meshes_under(low)]},
            "high": None if high is None else {
                "uid": high.uid, "name": high.name, "visible": high.visible,
                "meshes": [_ref(m) for m in _meshes_under(high)]},
        }
    d = {"name": baker.name, "uid": baker.uid, "visible": baker.visible, "groups": groups,
         "settings": {k: _get(baker, k) for k in _BAKER_SETTINGS if hasattr(baker, k)},
         "texture_sets": _texture_sets(baker), "udim_tiles": _udim_tiles(baker),
         "output_files": _output_files(baker)}
    if maps:
        d["maps"] = [_map_info(m) for m in baker.getAllMaps()]
    return d


@action("baker.list")
def baker_list():
    """All bake projects with their groups and output path."""
    return {"bakers": [{"name": b.name, "uid": b.uid, "groups": list(_groups(b)),
                        "outputPath": b.outputPath} for b in _bakers()]}


@action("baker.get")
def baker_get(baker=None, maps=True):
    """One bake project in full: groups with Low (cage) and High nodes and their meshes,
    settings, maps with options, texture sets, UDIM tiles and expected output files.
    ``baker`` may be omitted when the scene has exactly one."""
    return baker_info(_baker(baker), bool(maps))


@action("baker.create")
def baker_create(name=None):
    """Create a new bake project."""
    try:
        baker = mset.BakerObject()
    except Exception as exc:  # noqa: BLE001
        raise ActionError("Toolbag could not create a BakerObject: %s" % exc) from exc
    if name:
        baker.name = str(name)
    return baker_info(baker, maps=False)


@action("baker.add_group")
def baker_add_group(name, baker=None):
    """Add an empty bake group (gets Low and High nodes) to a bake project."""
    baker = _baker(baker)
    group = baker.addGroup(str(name))
    return {"baker": baker.name, "group": info(group) if group is not None else str(name),
            "groups": list(_groups(baker))}


@action("baker.quick_load")
def baker_quick_load(path, baker=None):
    """Load a model through the bake project's Quick Loader. Objects pair into groups by
    name stem: '<stem>_low' with '<stem>_high'. A mismatch gives a group without High and an
    empty bake, so check the returned groups."""
    baker = _baker(baker)
    path = _existing_file(path)
    before = {o.uid for o in _all()}
    baker.importModel(path)
    new = [o for o in _all() if o.uid not in before]
    return {"baker": baker.name, "count": len(new), "new_objects": [info(o) for o in new],
            "groups": {n: {"low": p["low"] is not None and bool(_meshes_under(p["low"])),
                           "high": p["high"] is not None and bool(_meshes_under(p["high"]))}
                       for n, p in _groups(baker).items()}}


@action("baker.set_settings")
def baker_set_settings(settings, baker=None):
    """Set bake project settings: outputPath, outputWidth, outputHeight, outputBits (8, 16,
    32), outputSamples, edgePadding ('None', 'Moderate', 'Extreme'), edgePaddingSize,
    outputSoften, smoothCage, ignoreBackfaces, useHiddenMeshes, ignoreTransforms,
    fixMirroredTangents, tileMode (0 single, 1 texture sets, 2 UDIM), geometryMode."""
    baker = _baker(baker)
    if not isinstance(settings, dict) or not settings:
        raise ActionError("settings must be a non-empty object, keys: %s" % list(_BAKER_SETTINGS))
    applied = {}
    for key, value in settings.items():
        if key.startswith("_") or not hasattr(baker, key):
            raise ActionError("This Toolbag's BakerObject has no setting %r, known: %s"
                              % (key, [k for k in _BAKER_SETTINGS if hasattr(baker, k)]))
        if key == "outputPath":
            value = _abs(value)
            _ensure_dir(value)
        try:
            setattr(baker, key, value)
        except Exception as exc:  # noqa: BLE001
            raise ActionError("Could not set %s=%r: %s" % (key, value, exc)) from exc
        applied[key] = _get(baker, key)
    return {"baker": baker.name, "settings": applied}


@action("baker.set_maps")
def baker_set_maps(enable=None, disable=None, options=None, baker=None):
    """Switch maps on or off by suffix or name ('normals', 'ao', 'height', 'curvature',
    'position', 'thickness', ...) and set map options, e.g. {"normals": {"flipY": true},
    "ao": {"rayCount": 1024}}. Returns the enabled maps with their options."""
    baker = _baker(baker)
    for name in enable or []:
        _find_map(baker, name).enabled = True
    for name in disable or []:
        _find_map(baker, name).enabled = False
    for name, attrs in (options or {}).items():
        bmap = _find_map(baker, name)
        if not isinstance(attrs, dict):
            raise ActionError("options for %r must be an object" % name)
        for key, value in attrs.items():
            if key.startswith("_") or not hasattr(bmap, key) or callable(getattr(bmap, key)):
                raise ActionError("%s (%s) has no option %r"
                                  % (type(bmap).__name__, bmap.suffix, key))
            setattr(bmap, key, value)
    return {"baker": baker.name, "maps": [_map_info(m) for m in baker.getAllMaps() if m.enabled]}


@action("baker.set_cage")
def baker_set_cage(groups=None, min_offset=None, max_offset=None, estimate=False,
                   cage_alpha=None, exclude_when_ignoring_groups=None, baker=None):
    """Cage offsets per bake group (all groups when omitted). ``estimate`` lets Toolbag
    estimate first; explicit min/max values then override."""
    baker = _baker(baker)
    out = {}
    for name, part in _select_groups(baker, groups).items():
        low = part["low"]
        if low is None:
            out[name] = None
            continue
        if estimate:
            low.estimateOffset()
        if min_offset is not None:
            low.minOffset = float(min_offset)
        if max_offset is not None:
            low.maxOffset = float(max_offset)
        if cage_alpha is not None:
            low.cageAlpha = float(cage_alpha)
        if exclude_when_ignoring_groups is not None:
            low.excludeWhenIgnoringGroups = bool(exclude_when_ignoring_groups)
        out[name] = {"cage_min": _cage(low.minOffset), "cage_max": _cage(low.maxOffset),
                     "cage_alpha": low.cageAlpha}
    return {"baker": baker.name, "groups": out}


@action("baker.set_visible")
def baker_set_visible(groups=None, visible=None, low=None, high=None, baker=None):
    """Show or hide bake groups, or their Low and High nodes separately. This is the safe
    way to toggle meshes in a bake scene."""
    baker = _baker(baker)
    for part in _select_groups(baker, groups).values():
        if visible is not None:
            part["group"].visible = bool(visible)
        if low is not None and part["low"] is not None:
            part["low"].visible = bool(low)
        if high is not None and part["high"] is not None:
            part["high"].visible = bool(high)
    return {"baker": baker.name, "groups": baker_info(baker, maps=False)["groups"]}


@action("baker.isolate")
def baker_isolate(groups, baker=None):
    """Show only these bake groups, hide all others."""
    baker = _baker(baker)
    keep = set([groups] if isinstance(groups, str) else groups)
    all_groups = _groups(baker)
    missing = [n for n in keep if n not in all_groups]
    if missing:
        raise ActionError("Unknown bake groups %s. Groups: %s" % (missing, sorted(all_groups)))
    for name, part in all_groups.items():
        part["group"].visible = name in keep
    return {"baker": baker.name, "visible": sorted(keep),
            "hidden": sorted(n for n in all_groups if n not in keep)}


@action("baker.bake")
def baker_bake(baker=None):
    """Bake with the project's current settings (synchronous, never interactive).
    Returns the output files that exist afterwards."""
    baker = _baker(baker)
    started = time.time()
    baker.bake()
    return {"baker": baker.name, "seconds": round(time.time() - started, 2),
            "outputPath": baker.outputPath, "files": _output_files(baker)}


@action("baker.bake_psd")
def baker_bake_psd(baker=None):
    """Bake all enabled maps into one layered PSD at the output path."""
    baker = _baker(baker)
    started = time.time()
    baker.bakePSD()
    return {"baker": baker.name, "seconds": round(time.time() - started, 2),
            "outputPath": baker.outputPath}


@action("baker.bake_to")
def baker_bake_to(path, maps=("normals",), width=2048, height=None, samples=16, settings=None,
                  baker=None):
    """Trial bake of selected maps into a separate file (e.g. 'C:/tmp/test.png' gives
    'test_normals.png') at the given size and sample count; the project's settings and
    map switches are restored afterwards. ``settings`` are extra temporary settings such
    as {"smoothCage": false}."""
    baker = _baker(baker)
    extra = dict(settings or {})
    restorable = [k for k in _RESTORE_SETTINGS if hasattr(baker, k)]
    unknown = [k for k in extra if k not in restorable]
    if unknown:
        raise ActionError("Unknown settings %s, known: %s" % (unknown, restorable))
    if isinstance(maps, str):
        maps = [maps]
    saved = {k: getattr(baker, k) for k in restorable}
    switches = [(m, m.enabled) for m in baker.getAllMaps()]
    path = _abs(path)
    _ensure_dir(path)
    started = time.time()
    try:
        for bmap, _ in switches:
            bmap.enabled = False
        for name in maps:
            _find_map(baker, name).enabled = True
        baker.outputPath = path
        baker.outputWidth = int(width)
        baker.outputHeight = int(height or width)
        baker.outputSamples = int(samples)
        for key, value in extra.items():
            setattr(baker, key, value)
        baker.bake()
        files = _output_files(baker)
    finally:
        for key, value in saved.items():
            try:
                setattr(baker, key, value)
            except Exception:  # noqa: BLE001
                pass
        for bmap, state in switches:
            bmap.enabled = state
    return {"baker": baker.name, "seconds": round(time.time() - started, 2), "files": files}


@action("baker.map_path")
def baker_map_path(map_name, tile_suffix="", baker=None):
    """Output path of a map: Toolbag's own answer (getMapPath, display names such as
    'Normals', optionally a texture set or UDIM tile '1001') where the build has it,
    otherwise computed from outputPath and the map's suffix (Toolbag 5.02 has no
    getMapPath; tile paths cannot be computed there)."""
    baker = _baker(baker)
    if hasattr(baker, "getMapPath"):
        return {"path": baker.getMapPath(str(map_name), str(tile_suffix or "")),
                "source": "toolbag"}
    if tile_suffix:
        raise ActionError("This Toolbag build has no getMapPath; paths of texture set or "
                          "UDIM tiles cannot be computed reliably")
    bmap = _find_map(baker, map_name)
    stem, ext = os.path.splitext(baker.outputPath or "")
    path = "%s_%s%s" % (stem, bmap.suffix, ext) if stem else None
    return {"path": path, "source": "computed", "exists": bool(path and os.path.isfile(path))}


@action("baker.load_preset")
def baker_load_preset(name, baker=None):
    """Load a saved baker preset by name."""
    baker = _baker(baker)
    baker.loadPreset(str(name))
    return baker_info(baker)


@action("baker.save_preset")
def baker_save_preset(name, baker=None):
    """Save the current bake project configuration as a preset."""
    baker = _baker(baker)
    baker.savePreset(str(name))
    return {"baker": baker.name, "preset": str(name)}


def _tile_index(count_fn, name_fn, index):
    if isinstance(index, str):
        for i in range(count_fn()):
            if name_fn(i) == index:
                return i
        raise ActionError("No tile or set named %r" % index)
    index = int(index)
    if not 0 <= index < count_fn():
        raise ActionError("Index %d out of range (0..%d)" % (index, count_fn() - 1))
    return index


@action("baker.set_texture_set")
def baker_set_texture_set(index, enabled=None, width=None, height=None, baker=None):
    """Enable and size one texture set (by index or name); tileMode must be 1."""
    baker = _baker(baker)
    i = _tile_index(baker.getTextureSetCount, baker.getTextureSetName, index)
    if enabled is not None:
        baker.setTextureSetEnabled(i, bool(enabled))
    if width is not None:
        baker.setTextureSetWidth(i, int(width))
    if height is not None:
        baker.setTextureSetHeight(i, int(height))
    return {"baker": baker.name, "texture_sets": _texture_sets(baker)}


@action("baker.set_udim_tile")
def baker_set_udim_tile(index, enabled=None, width=None, height=None, baker=None):
    """Enable and size one UDIM tile (by index or name such as '1001'); tileMode must be 2."""
    baker = _baker(baker)
    i = _tile_index(baker.getUDIMTileCount, baker.getUDIMTileName, index)
    if enabled is not None:
        baker.setUDIMTileEnabled(i, bool(enabled))
    if width is not None:
        baker.setUDIMTileWidth(i, int(width))
    if height is not None:
        baker.setUDIMTileHeight(i, int(height))
    return {"baker": baker.name, "udim_tiles": _udim_tiles(baker)}


@action("baker.replace_low")
def baker_replace_low(group, path, baker=None):
    """Swap the geometry of a group's single low mesh for the mesh in a model file (new
    UVs, fixed topology) without touching group, cage or material: the file is imported,
    its mesh data copied over, the import deleted. Save the scene before calling this if
    you rely on cage directions: reading mesh data marks them for re-computation."""
    baker = _baker(baker)
    part = _select_groups(baker, group)[group]
    targets = _meshes_under(part["low"]) if part["low"] is not None else []
    if len(targets) != 1:
        raise ActionError("Group %r has %d low meshes, expected exactly one"
                          % (group, len(targets)))
    target = targets[0]
    path = _existing_file(path)
    before = {o.uid for o in _all()}
    mset.importModel(path)
    new = [o for o in _all() if o.uid not in before]
    meshes = [o for o in new if isinstance(o, mset.MeshObject)]
    try:
        if len(meshes) != 1:
            raise ActionError("Expected exactly one mesh in %s, found %s"
                              % (path, [o.name for o in new]))
        source = meshes[0]
        same_place = (list(source.position) == list(target.position)
                      and list(source.rotation) == list(target.rotation)
                      and list(source.scale) == list(target.scale))
        data = source.mesh
        vertices = len(data.vertices) // 3
        us = list(data.uvs)[0::2]
        target.mesh = data
    finally:
        for o in new:
            try:
                if o.parent is None or o.parent.uid in before:
                    o.destroy()
            except Exception:  # noqa: BLE001
                pass
    return {"group": group, "mesh": _ref(target), "vertices": vertices,
            "u_range": [min(us), max(us)] if us else None, "same_transform": same_place}


# ---------------------------------------------------------------- skew masks (edge protect)
# The masks themselves are computed by the MCP server and written into the scene file: the
# Python API has no access to them. Here only the low mesh data and the preview.

_SKEW_PREVIEW = {}  # material name -> (albedo map, color) as they were before the preview


def _low_meshes(baker, groups):
    out = {}
    for name, part in _select_groups(baker, groups).items():
        meshes = _meshes_under(part["low"]) if part["low"] is not None else []
        if meshes:
            out[name] = meshes
    return out


@action("baker.low_mesh_data")
def baker_low_mesh_data(groups=None, baker=None):
    """Geometry of the low meshes per bake group, for the skew mask computation: vertices,
    normals, uvs and triangles as flat lists, plus the bake size. Reading mesh data marks
    the cage directions for re-computation, which Toolbag does on the next scene load."""
    baker = _baker(baker)
    out = {}
    for name, meshes in _low_meshes(baker, groups).items():
        out[name] = []
        for mesh_object in meshes:
            mesh = mesh_object.mesh
            out[name].append({"name": mesh_object.name, "vertices": list(mesh.vertices),
                              "normals": list(mesh.normals), "uvs": list(mesh.uvs),
                              "triangles": list(mesh.triangles)})
    return {"baker": baker.name, "output_width": baker.outputWidth,
            "output_height": baker.outputHeight, "groups": out}


@action("baker.skew_preview")
def baker_skew_preview(path, groups=None, baker=None):
    """Show an image as base colour on the materials of the low meshes (all groups when
    omitted). The previous albedo map and colour are kept; baker.skew_preview_clear puts
    them back."""
    path = _existing_file(path)
    baker = _baker(baker)
    materials = {}
    for meshes in _low_meshes(baker, groups).values():
        for mesh in meshes:
            for sub in mesh.getChildren():
                if isinstance(sub, mset.SubMeshObject) and sub.material is not None:
                    materials[sub.material.name] = sub.material
    for name, material in materials.items():
        albedo = _subroutine(material, "albedo")
        if name not in _SKEW_PREVIEW:
            _SKEW_PREVIEW[name] = (albedo.getField("Albedo Map"), albedo.getField("Color"))
        albedo.setField("Albedo Map", None)
        albedo.setField("Albedo Map", mset.Texture(path))
        albedo.setField("Color", [1.0, 1.0, 1.0])
    return {"preview": path, "materials": sorted(materials)}


def skew_preview_restore():
    """Put back what baker.skew_preview replaced; also called when the plugin shuts down."""
    restored = []
    for name, (texture, color) in list(_SKEW_PREVIEW.items()):
        hits = [m for m in mset.getAllMaterials() if m.name == name]
        if hits:
            albedo = _subroutine(hits[0], "albedo")
            albedo.setField("Albedo Map", texture)
            albedo.setField("Color", color)
            restored.append(name)
    _SKEW_PREVIEW.clear()
    return restored


@action("baker.skew_preview_clear")
def baker_skew_preview_clear():
    """End the skew mask preview: the low materials get their albedo map and colour back."""
    return {"restored": skew_preview_restore()}


# ---------------------------------------------------------------- generic attributes

_BASE_ATTRS = frozenset(("name", "parent", "visible", "locked", "collapsed", "uid", "mesh"))
_TRANSFORM_ATTRS = frozenset(("position", "rotation", "scale", "pivot"))
_OPTION_CLASSES = frozenset(("CameraLimits", "CameraLens", "CameraPostEffect", "RenderOptions",
                             "RenderOutputOptions", "RenderPassOptions", "RenderCameraOptions"))
# Documented attribute names (reference for 5.03); only those the running build has are read.
_KNOWN_ATTRS = {
    "LightObject": ("lightType", "color", "useTemperature", "temperature", "brightness", "shape",
                    "lengthX", "lengthY", "width", "spotAngle", "spotSharpness", "spotVignette",
                    "physicalUnits", "physicalConserveEnergy", "castShadows", "lightScaling",
                    "normalize", "visibleShape", "gelPath", "gelTile", "castLensFlare",
                    "lensFlareStrength", "profile", "profilePath", "profileWidth"),
    "SkyBoxObject": ("brightness", "brightnessUnit", "rotation", "backgroundBrightness",
                     "backgroundRotation", "blur", "mode", "backgroundColor",
                     "childLightBrightness", "time", "day", "timeZone", "latitude", "longitude",
                     "turbidity", "sunBrightness", "sunScale", "groundColorNear",
                     "groundColorFar", "saturation", "whiteBalance", "tint",
                     "realisticBrightness"),
    "FogObject": ("color", "density", "opacity", "volumeType", "heightMin", "heightMax",
                  "falloffMin", "falloffMax"),
    "CameraObject": ("mode", "fov", "orbitRadius", "orthoScale", "focalLength", "nearPlaneScale",
                     "limits", "lens", "postEffect"),
    "MeshObject": _MESH_KEYS,
    "ShadowCatcherObject": ("skyShadow", "indirectShadow", "edgeFade", "fadeRadius",
                            "fadeFalloff", "fadeTexturePath", "textureChannel", "opacity",
                            "roughness", "roughnessTexturePath", "roughnessTextureChannel",
                            "invertRoughness", "specularIntensity", "specularFresnel",
                            "specularTexturePath", "specularTextureChannel"),
    "BackdropObject": ("path", "alpha", "useAlpha"),
}


def _is_option_object(value):
    """A Toolbag settings container (camera lens, render options, ...) as opposed to a
    scene object, material, texture or plain value."""
    if value is None or isinstance(value, (bool, int, float, str, list, tuple, dict)):
        return False
    if isinstance(value, (mset.SceneObject, mset.Material, mset.Texture)):
        return False
    cls = type(value)
    return cls.__name__ in _OPTION_CLASSES or getattr(cls, "__module__", "") == "mset"


def _read_attrs(obj, keys=None, skip=_BASE_ATTRS, depth=0):
    """Plain attributes of a Toolbag object as a dict; settings containers one level down
    are expanded, methods and the base object/transform fields are left out."""
    out = {}
    if keys is None:
        names = set(n for n in dir(obj) if not n.startswith("_"))
        names |= set(_KNOWN_ATTRS.get(type(obj).__name__, ()))
        keys = sorted(n for n in names if hasattr(obj, n))
    hidden = set(skip)
    if isinstance(obj, mset.TransformObject) and skip is _BASE_ATTRS:
        hidden |= _TRANSFORM_ATTRS
    for key in keys:
        if key in hidden:
            continue
        try:
            value = getattr(obj, key)
        except Exception as exc:  # noqa: BLE001
            out[key] = "<error: %s>" % exc
            continue
        if callable(value):
            continue
        if depth < 2 and _is_option_object(value):
            out[key] = _read_attrs(value, skip=(), depth=depth + 1)
        elif (depth < 2 and isinstance(value, (list, tuple)) and value
              and _is_option_object(value[0])):
            out[key] = [_read_attrs(v, skip=(), depth=depth + 1) for v in value]
        else:
            out[key] = _jsonable(value)
    return out


def _apply_attrs(obj, attributes, prefix=""):
    """Set attributes from a dict. Nested dicts go into settings containers ('lens':
    {'dofStop': 2.8}), dotted keys work too ('lens.dofStop'); lists become float lists."""
    if not isinstance(attributes, dict) or not attributes:
        raise ActionError("attributes must be a non-empty object, e.g. {\"brightness\": 2}")
    applied = {}
    for key, value in attributes.items():
        target, attr = obj, str(key)
        if "." in attr:
            parts = attr.split(".")
            for part in parts[:-1]:
                if part.startswith("_") or not hasattr(target, part):
                    raise ActionError("%s%s: no %r" % (prefix, key, part))
                target = getattr(target, part)
            attr = parts[-1]
        if attr.startswith("_") or not hasattr(target, attr):
            raise ActionError("%s has no attribute %r. Known: %s"
                              % (type(obj).__name__, prefix + str(key), sorted(_read_attrs(obj))))
        current = getattr(target, attr)
        if callable(current):
            raise ActionError("%r is a method, not a setting" % (prefix + str(key)))
        if isinstance(value, dict) and _is_option_object(current):
            applied[key] = _apply_attrs(current, value, prefix + str(key) + ".")
            continue
        if isinstance(value, (list, tuple)):
            value = [float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                     for v in value]
        try:
            setattr(target, attr, value)
        except Exception as exc:  # noqa: BLE001
            raise ActionError("Could not set %s=%r: %s" % (prefix + str(key), value, exc)) from exc
        applied[key] = _jsonable(getattr(target, attr))
    return applied


_CREATABLE = ("LightObject", "CameraObject", "FogObject", "SkyBoxObject", "ShadowCatcherObject",
              "BackdropObject", "SceneObject", "TransformObject", "BakerObject")


@action("object.attributes")
def object_attributes(ref):
    """Every plain attribute of an object with its value, type-specific (light, camera with
    lens/limits/postEffect, sky, fog, shadow catcher, backdrop, mesh). These are the names
    for object.set_attributes."""
    obj = resolve(ref)
    return {"object": _ref(obj), "attributes": _read_attrs(obj)}


@action("object.set_attributes")
def object_set_attributes(ref, attributes):
    """Set attributes of any object by name, e.g. {"brightness": 3, "color": [1, 0.9, 0.8]}
    for a light or {"lens": {"dofStop": 2.8}} / {"lens.dofStop": 2.8} for a camera."""
    obj = resolve(ref)
    return {"object": _ref(obj), "applied": _apply_attrs(obj, attributes)}


@action("object.create")
def object_create(object_type, name=None, attributes=None, position=None, rotation=None,
                  parent=None):
    """Create a scene object by class name: LightObject, CameraObject, FogObject,
    SkyBoxObject, ShadowCatcherObject, BackdropObject, SceneObject (a group),
    TransformObject, BakerObject; optional name, transform, parent and attributes."""
    cls = getattr(mset, str(object_type), None)
    if object_type not in _CREATABLE or not isinstance(cls, type):
        raise ActionError("Cannot create %r, choose one of %s" % (object_type, list(_CREATABLE)))
    try:
        obj = cls()
    except Exception as exc:  # noqa: BLE001
        raise ActionError("Toolbag could not create a %s: %s" % (object_type, exc)) from exc
    if name:
        obj.name = str(name)
    if parent is not None:
        obj.parent = resolve(parent)
    for key, value in (("position", position), ("rotation", rotation)):
        if value is None:
            continue
        if not isinstance(obj, mset.TransformObject):
            raise ActionError("%s has no transform" % object_type)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ActionError("%s must be a list of three numbers" % key)
        setattr(obj, key, [float(v) for v in value])
    applied = _apply_attrs(obj, attributes) if attributes else {}
    d = info(obj, detail=True)
    d["applied"] = applied
    return d


def _single(class_name, ref=None):
    cls = getattr(mset, class_name)
    if ref is not None:
        return resolve(ref, cls)
    found = [o for o in _all() if isinstance(o, cls)]
    if not found:
        raise ActionError("No %s in the scene" % class_name)
    return found[0]


# ---------------------------------------------------------------- family 5: light, sky, fog, camera

_LIGHT_TYPES = ("directional", "spot", "omni")
_LIGHT_SUMMARY = ("lightType", "brightness", "color", "castShadows", "shape", "spotAngle",
                  "useTemperature", "temperature", "physicalUnits")


@action("light.list")
def light_list():
    """All lights (sky child lights included) with type, brightness, colour, shadows,
    shape and transform."""
    out = []
    for light in _all():
        if not isinstance(light, mset.LightObject):
            continue
        d = info(light)
        for key in _LIGHT_SUMMARY:
            d[key] = _get(light, key)
        out.append(d)
    return {"count": len(out), "lights": out}


@action("light.create")
def light_create(light_type="omni", name=None, position=None, rotation=None, attributes=None):
    """Create a light: 'omni', 'spot' or 'directional', with optional transform and
    attributes (brightness, color [r,g,b], castShadows, width, lengthX, lengthY, spotAngle,
    useTemperature, temperature, ...; object.attributes lists what this build has)."""
    if light_type not in _LIGHT_TYPES:
        raise ActionError("light_type must be one of %s" % list(_LIGHT_TYPES))
    attrs = {"lightType": light_type}
    attrs.update(attributes or {})
    return object_create("LightObject", name=name, attributes=attrs, position=position,
                         rotation=rotation)


@action("sky.get")
def sky_get(ref=None):
    """The sky (the scene's first SkyBoxObject, or the given one): mode, brightness,
    rotation, background, procedural sun settings, child lights."""
    sky = _single("SkyBoxObject", ref)
    d = info(sky)
    d["attributes"] = _read_attrs(sky)
    d["child_lights"] = [_ref(c) for c in sky.getChildren() if isinstance(c, mset.LightObject)]
    return d


@action("sky.set")
def sky_set(attributes, ref=None):
    """Set sky attributes: brightness, rotation (degrees), mode ('color', 'sky', 'blurred
    sky', 'ambient sky'), backgroundColor, blur, time/day/latitude/longitude for a
    procedural sky, ..."""
    sky = _single("SkyBoxObject", ref)
    return {"sky": _ref(sky), "applied": _apply_attrs(sky, attributes)}


@action("sky.load")
def sky_load(path, ref=None):
    """Load a sky: a .tbsky file replaces the whole sky, an image (.hdr, .exr, .png, .jpg)
    becomes the panorama."""
    path = _existing_file(path)
    sky = _single("SkyBoxObject", ref)
    if path.lower().endswith(".tbsky"):
        sky.loadSky(path)
        how = "tbsky"
    else:
        sky.importImage(path)
        how = "image"
    return {"sky": _ref(sky), "loaded": path, "as": how, "mode": _get(sky, "mode")}


@action("sky.save")
def sky_save(path, ref=None):
    """Save the sky as a .tbsky archive."""
    sky = _single("SkyBoxObject", ref)
    path = _abs(path)
    _ensure_dir(path)
    sky.saveSky(path)
    return {"sky": _ref(sky), "path": path, "exists": os.path.isfile(path)}


@action("sky.set_background_image")
def sky_set_background_image(path=None, ref=None):
    """Use an image as the sky's background, or clear it when path is empty."""
    sky = _single("SkyBoxObject", ref)
    if not path:
        sky.clearBackgroundImage()
        return {"sky": _ref(sky), "background": None}
    path = _existing_file(path)
    sky.importBackgroundImage(path)
    return {"sky": _ref(sky), "background": path}


@action("sky.compute_procedural")
def sky_compute_procedural(ref=None):
    """Switch the sky to procedural mode (sun by time, day and location) and recompute it."""
    sky = _single("SkyBoxObject", ref)
    sky.computeProcedural()
    return sky_get(sky.uid)


@action("fog.get")
def fog_get(ref=None):
    """The scene's fog object with its attributes, or exists: false."""
    fogs = [o for o in _all() if isinstance(o, mset.FogObject)]
    fog = resolve(ref, mset.FogObject) if ref is not None else (fogs[0] if fogs else None)
    if fog is None:
        return {"exists": False, "fog": None}
    d = info(fog)
    d["attributes"] = _read_attrs(fog)
    return {"exists": True, "fog": d}


@action("fog.set")
def fog_set(attributes, ref=None, create=True):
    """Set fog attributes (color, density, opacity, volumeType 'fit'/'vertical', heightMin,
    heightMax, falloffMin, falloffMax); creates the fog object when the scene has none."""
    fogs = [o for o in _all() if isinstance(o, mset.FogObject)]
    if ref is not None:
        fog = resolve(ref, mset.FogObject)
    elif fogs:
        fog = fogs[0]
    elif create:
        fog = mset.FogObject()
    else:
        raise ActionError("No fog object in the scene")
    return {"fog": _ref(fog), "applied": _apply_attrs(fog, attributes)}


def _active_camera():
    try:
        return mset.getCamera()
    except Exception:  # noqa: BLE001
        return None


_CAMERA_SUMMARY = ("mode", "fov", "focalLength", "orthoScale")


@action("camera.list")
def camera_list():
    """All cameras with mode, field of view, focal length and which one is active."""
    active = _active_camera()
    active_uid = active.uid if active is not None else None
    cams = []
    for cam in _all():
        if not isinstance(cam, mset.CameraObject):
            continue
        d = info(cam)
        d["active"] = cam.uid == active_uid
        for key in _CAMERA_SUMMARY:
            d[key] = _get(cam, key)
        cams.append(d)
    return {"count": len(cams), "active": active_uid, "cameras": cams}


@action("camera.get")
def camera_get(ref=None):
    """One camera in full (the active one when ref is omitted): transform, mode, fov,
    focal length, and the lens (depth of field, distortion, flares, motion blur), limits
    and postEffect (tone mapping, exposure, contrast, bloom, vignette, grain) settings."""
    cam = resolve(ref, mset.CameraObject) if ref is not None else _active_camera()
    if cam is None:
        raise ActionError("No active camera")
    d = info(cam, detail=True)
    d["attributes"] = _read_attrs(cam)
    return d


@action("camera.create")
def camera_create(name=None, position=None, rotation=None, attributes=None, activate=False):
    """Create a camera with optional transform and attributes; activate makes it the
    viewport and render camera."""
    d = object_create("CameraObject", name=name, attributes=attributes, position=position,
                      rotation=rotation)
    if activate:
        mset.setCamera(resolve(d["uid"], mset.CameraObject))
        d["active"] = True
    return d


@action("camera.set")
def camera_set(attributes, ref=None):
    """Set camera attributes (the active camera when ref is omitted): fov, focalLength,
    mode, {"lens": {"dofMode": 1, "dofStop": 2.8, "dofFocusDistance": 50}},
    {"postEffect": {"toneMappingMode": "aces", "exposure": 1.2}}, {"limits": {...}}."""
    cam = resolve(ref, mset.CameraObject) if ref is not None else _active_camera()
    if cam is None:
        raise ActionError("No active camera")
    return {"camera": _ref(cam), "applied": _apply_attrs(cam, attributes)}


@action("camera.set_active")
def camera_set_active(ref):
    """Make a camera the active viewport and render camera."""
    cam = resolve(ref, mset.CameraObject)
    mset.setCamera(cam)
    return {"active": _ref(cam)}


@action("camera.post_effect_file")
def camera_post_effect_file(path, save=False, ref=None):
    """Load a camera post effect preset from a file, or save the current one with
    save=true."""
    cam = resolve(ref, mset.CameraObject) if ref is not None else _active_camera()
    if cam is None:
        raise ActionError("No active camera")
    if save:
        path = _abs(path)
        _ensure_dir(path)
        cam.savePostEffect(path)
        return {"camera": _ref(cam), "saved": path, "exists": os.path.isfile(path)}
    path = _existing_file(path)
    cam.loadPostEffect(path)
    return {"camera": _ref(cam), "loaded": path,
            "postEffect": _read_attrs(cam.postEffect, skip=())}


# ---------------------------------------------------------------- family 6: render, timeline

def _render_object():
    found = [o for o in _all() if isinstance(o, mset.RenderObject)]
    if not found:
        raise ActionError("No RenderObject in the scene")
    return found[0]


def _render_cameras(render):
    out = []
    for entry in render.cameras:
        cam = getattr(entry, "camera", None)
        out.append({"enabled": bool(getattr(entry, "enabled", False)),
                    "camera": _ref(cam) if cam is not None else None})
    return out


def _render_passes(render):
    out = []
    for entry in render.renderPasses:
        out.append({"enabled": bool(getattr(entry, "enabled", False)),
                    "renderPass": getattr(entry, "renderPass", "")})
    return out


@action("render.get")
def render_get():
    """The render object: options (renderer, ray tracing, shadows, occlusion, wireframe,
    watermark, ...), image and video output settings (outputPath, format, width, height,
    samples, transparency), the cameras and render passes enabled for export."""
    render = _render_object()
    return {"object": _ref(render),
            "options": _read_attrs(render.options, skip=()),
            "images": _read_attrs(render.images, skip=()),
            "videos": _read_attrs(render.videos, skip=()),
            "cameras": _render_cameras(render),
            "passes": _render_passes(render)}


@action("render.set")
def render_set(options=None, images=None, videos=None):
    """Change render settings: options ({"renderer": "raytrace", "rayTraceBounces": 4,
    "shadowQuality": "High"}), images ({"outputPath": "C:/out", "format": "PNG", "width":
    1920, "height": 1080, "samples": 256, "transparency": true}), videos (same keys)."""
    render = _render_object()
    applied = {}
    for name, attrs in (("options", options), ("images", images), ("videos", videos)):
        if not attrs:
            continue
        if isinstance(attrs, dict) and isinstance(attrs.get("outputPath"), str):
            attrs = dict(attrs)
            attrs["outputPath"] = _abs(attrs["outputPath"])
            os.makedirs(attrs["outputPath"], exist_ok=True)
        applied[name] = _apply_attrs(getattr(render, name), attrs, name + ".")
    if not applied:
        raise ActionError("Pass options, images or videos")
    return applied


def _match_ref(obj, refs):
    if obj is None:
        return False
    for ref in refs:
        if ref == obj.uid or ref == obj.name:
            return True
    return False


@action("render.set_cameras")
def render_set_cameras(enable=None, disable=None):
    """Choose which cameras render on render.images / render.videos (uids or names)."""
    render = _render_object()
    enable = resolve_many(enable) if enable else []
    disable = resolve_many(disable) if disable else []
    for entry in render.cameras:
        cam = getattr(entry, "camera", None)
        if cam is None:
            continue
        if any(c.uid == cam.uid for c in enable):
            entry.enabled = True
        if any(c.uid == cam.uid for c in disable):
            entry.enabled = False
    return {"cameras": _render_cameras(render)}


@action("render.set_passes")
def render_set_passes(enable=None, disable=None):
    """Switch render passes on or off by name (render.get lists them)."""
    render = _render_object()
    known = [getattr(p, "renderPass", "") for p in render.renderPasses]
    for name in list(enable or []) + list(disable or []):
        if name not in known:
            raise ActionError("Unknown render pass %r. Passes: %s" % (name, known))
    for entry in render.renderPasses:
        if entry.renderPass in (enable or []):
            entry.enabled = True
        if entry.renderPass in (disable or []):
            entry.enabled = False
    return {"passes": _render_passes(render)}


@action("render.image")
def render_image(path=None, width=-1, height=-1, sampling=-1, transparency=False, camera="",
                 viewport_pass=""):
    """Render one image with the active camera (or the named one) using the render
    object's settings; width, height and sampling default to those settings. With path the
    file is written (format by extension), otherwise the image is only rendered."""
    if path:
        path = _abs(path)
        _ensure_dir(path)
    if camera:
        camera = resolve(camera, mset.CameraObject).name
    started = time.time()
    image = mset.renderCamera(path or "", int(width), int(height), int(sampling),
                              bool(transparency), str(camera or ""), str(viewport_pass or ""))
    out = {"seconds": round(time.time() - started, 2), "path": path,
           "exists": bool(path and os.path.isfile(path)),
           "bytes": os.path.getsize(path) if path and os.path.isfile(path) else None}
    try:
        out["pixel_format"] = image.getPixelFormat()
    except Exception:  # noqa: BLE001
        pass
    return out


def _new_files(folder, since):
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        try:
            if os.path.isfile(full) and os.path.getmtime(full) >= since - 1:
                out.append({"path": full, "bytes": os.path.getsize(full)})
        except OSError:
            pass
    return out


@action("render.images")
def render_images(width=-1, height=-1, sampling=-1, transparency=False):
    """Render all enabled cameras and passes to the image output folder (render.get:
    images.outputPath). Returns the files written."""
    render = _render_object()
    folder = getattr(render.images, "outputPath", "")
    started = time.time()
    mset.renderImages(int(width), int(height), int(sampling), bool(transparency))
    return {"outputPath": folder, "seconds": round(time.time() - started, 2),
            "files": _new_files(folder, started)}


@action("render.videos")
def render_videos(width=-1, height=-1, sampling=-1, transparency=False):
    """Render the animation to the video output folder (render.get: videos.outputPath).
    Can take long."""
    render = _render_object()
    folder = getattr(render.videos, "outputPath", "")
    started = time.time()
    mset.renderVideos(int(width), int(height), int(sampling), bool(transparency))
    return {"outputPath": folder, "seconds": round(time.time() - started, 2),
            "files": _new_files(folder, started)}


@action("render.export_viewer")
def render_export_viewer(path, title="", author="", author_url="", texture_quality="high",
                         width=-1, height=-1, export_animations=False):
    """Export a Marmoset Viewer (.mview) archive of the scene."""
    path = _abs(path)
    _ensure_dir(path)
    mset.exportViewer(path=path, title=str(title), author=str(author), authorURL=str(author_url),
                      textureQuality=str(texture_quality), width=int(width), height=int(height),
                      exportAnimations=bool(export_animations))
    return {"path": path, "exists": os.path.isfile(path),
            "bytes": os.path.getsize(path) if os.path.isfile(path) else None}


@action("timeline.get")
def timeline_get():
    """Animation timeline: time, frame, total frames, frame rate, selection, speed."""
    t = mset.getTimeline()
    return {"time": t.getTime(), "frame": t.currentFrame, "total_frames": t.totalFrames,
            "frame_rate": t.getFrameRate(), "selection": [t.selectionStart, t.selectionEnd],
            "playback_speed": t.playbackSpeed}


@action("timeline.set")
def timeline_set(time=None, frame=None, total_frames=None, frame_rate=None,
                 selection_start=None, selection_end=None, playback_speed=None, play=None):
    """Set the timeline: time in seconds or frame, total frames, frame rate (resamples
    keyframes), selection range, playback speed; play=true/false starts or stops playback."""
    t = mset.getTimeline()
    if total_frames is not None:
        t.totalFrames = int(total_frames)
    if frame_rate is not None:
        t.resample(int(frame_rate))
    if time is not None:
        t.setTime(float(time))
    if frame is not None:
        t.currentFrame = int(frame)
    if selection_start is not None:
        t.selectionStart = int(selection_start)
    if selection_end is not None:
        t.selectionEnd = int(selection_end)
    if playback_speed is not None:
        t.playbackSpeed = float(playback_speed)
    if play is True:
        t.play()
    elif play is False:
        t.pause()
    return timeline_get()


# ---------------------------------------------------------------- family 7: free Python (gated)

_NAMESPACE = {"mset": mset}


@action("python.exec")
def python_exec(code):
    """Run Python inside Toolbag with ``mset`` imported. Only while 'Allow Python
    execution' is ticked in the plugin window. The namespace persists between calls; set
    a variable ``result`` to return a value; print() output comes back as log."""
    if not STATE.get("allow_python"):
        raise ActionError("Python execution is disabled. Tick 'Allow Python execution' in the "
                          "Toolbag MCP window inside Toolbag (it resets at every start).")
    if not isinstance(code, str) or not code.strip():
        raise ActionError("code must be a non-empty string")
    exec(compile(code, "<toolbag-mcp>", "exec"), _NAMESPACE)  # noqa: S102  (explicitly allowed)
    return {"result": _jsonable(_NAMESPACE.pop("result", None))}
