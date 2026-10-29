"""A stand-in for Toolbag's ``mset`` module, enough to run the plugin's actions without
Toolbag: scene objects with hierarchy and uids, materials with subroutines and fields,
textures, bake projects with groups, maps and a bake that writes files, lights, cameras with
lens/limits/post-effect containers, sky, fog, render object, timeline and preferences.

It mirrors what was observed on Toolbag 5.02 (docs/pitfalls.md), traps included: texture
set getters raise unless tileMode matches, getSubroutine raises for unknown slots, unknown
fields raise NameError, cage offsets read as a sentinel while automatic, geometryMode,
interactive and getMapPath do not exist. ``build()`` returns a fresh module; ``newScene()``
fills it with Toolbag's default scene (Scene, Render, Main Camera, Sky, material Default).
"""

from __future__ import annotations

import os
import types

CAGE_AUTO = -1.0e30
DEFAULT_SHADERS = {
    "surface": ("Normals", ["Normal Map", "Scale & Bias", "Flip X", "Flip Y", "Flip Z",
                            "Generate Z", "Object Space"]),
    "microsurface": ("Roughness", ["Roughness Map", "Channel", "Roughness", "Invert;roughness"]),
    "albedo": ("Albedo", ["Albedo Map", "Color"]),
    "diffusion": ("Lambertian", []),
    "reflectivity": ("Metalness", ["Metalness Map", "Channel", "Metalness", "Invert"]),
    "reflection": ("GGX", []),
    "texture": ("UV", ["UV", "Texture Tiling", "Tile U", "Offset U", "Tile V", "Offset V",
                       "Rotation", "Wrap", "Aniso", "Filter"]),
}
SHADERS = {
    "Cutout": ["Use Albedo Alpha", "Alpha Map", "Channel", "Alpha", "Threshold"],
    "Dither": ["Use Albedo Alpha", "Alpha Map", "Channel", "Alpha"],
    "Specular": ["Specular Map", "Channel;specular", "Intensity", "Color", "Fresnel",
                 "Color;fresnel"],
    "Occlusion": ["Occlusion Map", "Channel", "Occlusion", "Cavity Map"],
    "Metalness": ["Metalness Map", "Channel", "Metalness", "Invert"],
    "Roughness": ["Roughness Map", "Channel", "Roughness", "Invert;roughness"],
    "Albedo": ["Albedo Map", "Color"],
    "Normals": DEFAULT_SHADERS["surface"][1],
    "Emissive": ["Emissive Map", "Color", "Intensity"],
}
SLOTS = ("subdivision", "displacement", "surface", "microsurface", "albedo", "diffusion",
         "transmission", "reflectivity", "reflection", "clearcoat reflection",
         "clearcoat microsurface", "clearcoat reflectivity", "occlusion", "emissive",
         "transparency", "texture")
DEFAULT_FIELD_VALUES = {"Color": [1.0, 1.0, 1.0], "Channel": 0, "Threshold": 0.5, "Alpha": 1.0,
                        "Use Albedo Alpha": True, "Roughness": 1.0, "Metalness": 0.0,
                        "Intensity": 1.0, "Fresnel": 1.0, "Scale & Bias": True,
                        "Flip X": False, "Flip Y": False, "Flip Z": False, "Generate Z": False,
                        "Object Space": False, "Invert": False, "Invert;roughness": False}


def _touch(path, size=16):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\0" * size)


def build() -> types.ModuleType:
    m = types.ModuleType("mset")
    state = {"objects": [], "materials": [], "selection": [], "scene_path": "", "next_uid": 1,
             "quit": None, "camera": None, "calls": [], "timeline": None, "preferences": None}
    m._state = state

    def _uid(given):
        if given is None:
            given = state["next_uid"]
        state["next_uid"] = max(state["next_uid"], given + 1)
        return given

    # ------------------------------------------------------------ scene objects

    class SceneObject:
        def __init__(self, name=None, uid=None, parent=None, visible=True):
            self.uid = _uid(uid)
            self.name = name or type(self).__name__
            self._parent = None
            self._children = []
            self.visible, self.locked, self.collapsed = visible, False, False
            state["objects"].append(self)
            self.parent = parent

        @property
        def parent(self):
            return self._parent

        @parent.setter
        def parent(self, value):
            if self._parent is not None and self in self._parent._children:
                self._parent._children.remove(self)
            self._parent = value
            if value is not None:
                value._children.append(self)

        def getChildren(self):  # noqa: N802  (Toolbag naming)
            return list(self._children)

        def findInChildren(self, text):  # noqa: N802
            for child in self._children:
                if text in child.name:
                    return child
                hit = child.findInChildren(text)
                if hit is not None:
                    return hit
            return None

        def getBounds(self):  # noqa: N802
            return None

        def setKeyframe(self, *args):  # noqa: N802
            state["calls"].append(("setKeyframe", self.uid, args))

        def duplicate(self, name=""):
            copy = type(self)(name=name or self.name + " copy", parent=self.parent)
            for key, value in vars(self).items():
                if key not in ("uid", "name", "_parent", "_children") and not key.startswith("_"):
                    setattr(copy, key, list(value) if isinstance(value, list) else value)
            for child in self._children:
                child.duplicate(child.name).parent = copy
            return copy

        def destroy(self):
            for child in list(self._children):
                child.destroy()
            self.parent = None
            if self in state["objects"]:
                state["objects"].remove(self)
            if self in state["selection"]:
                state["selection"].remove(self)

    class TransformObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.position, self.rotation = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            self.scale, self.pivot = [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]

        def getBounds(self):  # noqa: N802
            return [[-50.0, -50.0, -50.0], [50.0, 50.0, 50.0]]

        def centerPivot(self):  # noqa: N802
            self.pivot = [0.0, 0.0, 0.0]

    class Mesh:
        def __init__(self, vertices=24, uvs=None):
            self.vertices = [0.0] * (vertices * 3)
            self.triangles = list(range(vertices))
            self.uvs = uvs if uvs is not None else [0.1, 0.9] * vertices
            self.normals = [0.0, 0.0, 1.0] * vertices
            self.tangents = list(self.normals)
            self.bitangents = list(self.normals)
            self.colors, self.polygons, self.secondaryUVs = [], [], []

    class MeshObject(TransformObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mesh = Mesh()
            self.castShadows, self.cullBackFaces = True, True
            self.tangentSpace, self.tangentInterpolation = "Mikk", "pixel"
            self.fixMirroredTangents, self.invisibleToCamera = False, False
            self.subdivisionEnabled, self.subdivisionLevel = False, 1
            self.subdivisionMode = "Catmull-Clark"

        def addSubmesh(self, name, material=None, startIndex=0, indexCount=-1):  # noqa: N803
            sub = SubMeshObject(name=name, parent=self)
            sub.material = material or _default_material()
            sub.startIndex, sub.indexCount = startIndex, indexCount
            return sub

    class SubMeshObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.material, self.startIndex, self.indexCount = None, 0, -1

    class CameraLimits:
        def __init__(self):
            self.useLimitsInViewport, self.panLimit = False, "Off"
            self.nearLimitEnabled, self.nearLimit = False, 1.0
            self.farLimitEnabled, self.farLimit = False, 100.0

    class CameraLens:
        def __init__(self):
            self.dofMode, self.dofEnabled, self.dofFocusDistance = 0, False, 10.0
            self.dofStop, self.dofAnamorphicRatio = 5.6, 1.0
            self.distortionBarrel, self.lensFlareStrength = 0.0, 0.0
            self.motionBlurEnable, self.motionBlurShutterSpeed = False, 60.0

    class CameraPostEffect:
        def __init__(self):
            self.toneMappingMode, self.exposure, self.contrast = "aces", 1.0, 1.0
            self.saturation, self.sharpen, self.bloomBrightness = 1.0, 0.0, 0.0
            self.bloomSize, self.vignetteStrength, self.filmGrainMode = 1.0, 0.0, "Off"

    class CameraObject(TransformObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mode, self.fov, self.focalLength = "perspective", 45.0, 35.0
            self.orthoScale, self.orbitRadius, self.nearPlaneScale = 1.0, 10.0, 1.0
            self.limits, self.lens = CameraLimits(), CameraLens()
            self.postEffect = CameraPostEffect()

        def loadPostEffect(self, path):  # noqa: N802
            state["calls"].append(("loadPostEffect", self.uid, path))
            self.postEffect.toneMappingMode = "hejl"

        def savePostEffect(self, path):  # noqa: N802
            _touch(path)

    class LightObject(TransformObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lightType, self.color, self.brightness = "omni", [1.0, 1.0, 1.0], 1.0
            self.castShadows, self.shape, self.width = True, "sphere", 0.1
            self.lengthX, self.lengthY, self.spotAngle = 1.0, 1.0, 45.0
            self.useTemperature, self.temperature, self.physicalUnits = False, 6500.0, False
            self.visibleShape, self.gelPath = False, ""

    class SkyBoxObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.brightness, self.rotation, self.mode = 1.0, 0.0, "sky"
            self.backgroundColor, self.blur, self.backgroundBrightness = [0.0, 0.0, 0.0], 0.0, 1.0
            self.time, self.day, self.latitude, self.longitude = 12.0, 180, 50.0, 10.0
            self.loaded, self.background = None, None

        def loadSky(self, path):  # noqa: N802
            self.loaded = ("tbsky", path)

        def importImage(self, path):  # noqa: N802
            self.loaded = ("image", path)

        def importBackgroundImage(self, path):  # noqa: N802
            self.background = path

        def clearBackgroundImage(self):  # noqa: N802
            self.background = None

        def saveSky(self, path):  # noqa: N802
            _touch(path)

        def computeProcedural(self):  # noqa: N802
            self.mode = "procedural"

        def clearLightChildren(self):  # noqa: N802
            for child in list(self._children):
                child.destroy()

    class FogObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.color, self.density, self.opacity = [0.5, 0.5, 0.5], 0.1, 1.0
            self.volumeType, self.heightMin, self.heightMax = "fit", 0.0, 10.0
            self.falloffMin, self.falloffMax = 0.0, 1.0

    class ShadowCatcherObject(TransformObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.opacity, self.skyShadow, self.edgeFade = 1.0, True, True

    class BackdropObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.path, self.alpha, self.useAlpha = "", 1.0, False

    class RenderOptions:
        def __init__(self):
            self.renderer, self.shadowQuality, self.rayTraceBounces = "raster", "High", 4
            self.drawWireframe, self.wireframeColor = False, [1.0, 1.0, 1.0, 1.0]
            self.occlusionMode, self.watermarkEnabled = "Screen", True

    class RenderOutputOptions:
        def __init__(self, folder):
            self.outputPath, self.format, self.width, self.height = folder, "PNG", 1920, 1080
            self.samples, self.transparency, self.overwrite = 64, False, True

    class RenderPassOptions:
        def __init__(self, name, enabled):
            self.renderPass, self.enabled = name, enabled

    class RenderCameraOptions:
        def __init__(self, camera, enabled=True):
            self.camera, self.enabled = camera, enabled

    class Image:
        def getPixelFormat(self):  # noqa: N802
            return "PIXEL_FORMAT_BGRA8"

        def writeOut(self, path):  # noqa: N802
            _touch(path)

    class RenderObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.options = RenderOptions()
            self.images = RenderOutputOptions(state.get("render_folder", "renders"))
            self.videos = RenderOutputOptions(state.get("render_folder", "renders"))
            self.cameras = []
            self.renderPasses = [RenderPassOptions("Full Quality", True),
                                 RenderPassOptions("Normals", False)]

        def renderImages(self):  # noqa: N802
            m.renderImages()

        def renderVideos(self):  # noqa: N802
            m.renderVideos()

    # ------------------------------------------------------------ baker

    class BakerMap:
        def __init__(self, suffix, display, enabled=False):
            self.suffix, self._display, self.enabled = suffix, display, enabled
            self.dither = False

        def resetSuffix(self):  # noqa: N802
            self.suffix = self._display.lower().replace(" ", "")

    class NormalBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.flipX = self.flipY = self.flipZ = False
            self.useTransforms = False

    class AOBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.rayCount, self.ignoreGroups, self.twoSided = 512, True, False
            self.floor, self.floorOcclusion = 0.0, 0.0
            self.cosineWeight, self.searchDistance = 0.0, 0.0

    class HeightBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.innerDistance, self.outerDistance = -1.0, 1.0

    class CurvatureBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.strength = 1.0

    class PositionBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.normalization = "Bounding Sphere"

    class ThicknessBakerMap(BakerMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.rayCount = 512

    class BakerTargetObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.minOffset = self.maxOffset = CAGE_AUTO
            self.cageAlpha, self.excludeWhenIgnoringGroups = 0.5, False
            self.estimated = False

        def estimateOffset(self):  # noqa: N802
            self.estimated = True

    def _group(baker, name):
        group = TransformObject(name=name, parent=baker)
        low = BakerTargetObject(name="Low", parent=group)
        high = SceneObject(name="High", parent=group, visible=False)
        return group, low, high

    class BakerObject(SceneObject):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.outputPath, self.outputWidth, self.outputHeight = "bake.psd", 2048, 2048
            self.outputBits, self.outputSamples, self.edgePadding = 8, 16, "Moderate"
            self.edgePaddingSize, self.outputSoften, self.smoothCage = 8.0, 0.0, True
            self.ignoreBackfaces, self.useHiddenMeshes, self.ignoreTransforms = True, True, False
            self.fixMirroredTangents, self.tileMode = False, 0
            self._maps = [NormalBakerMap("normal", "Normals", True),
                          NormalBakerMap("normalobj", "Normals (Object)"),
                          HeightBakerMap("height", "Height"),
                          PositionBakerMap("position", "Position"),
                          CurvatureBakerMap("curve", "Curvature"),
                          AOBakerMap("ao", "Ambient Occlusion"),
                          AOBakerMap("ao2", "Ambient Occlusion (2)"),
                          ThicknessBakerMap("thickness", "Thickness"),
                          BakerMap("albedo", "Albedo")]
            self.bakes, self.presets = [], {}
            self._sets = [["Default", True, 1024, 1024]]

        def getAllMaps(self):  # noqa: N802
            return list(self._maps)

        def getMap(self, name):  # noqa: N802
            for bmap in self._maps:
                if bmap._display == name:
                    return bmap
            m.err("baker: unknown map %r" % name)
            return None

        def _write_outputs(self):
            stem, ext = os.path.splitext(self.outputPath)
            written = []
            for bmap in self._maps:
                if bmap.enabled:
                    path = "%s_%s%s" % (stem, bmap.suffix, ext)
                    _touch(path, 32)
                    written.append(path)
            return written

        def bake(self):
            if self.outputSamples < 1:
                raise RuntimeError("bake failed: outputSamples must be >= 1")
            self.bakes.append(self._write_outputs())

        def bakePSD(self):  # noqa: N802
            _touch(self.outputPath, 32)

        def importModel(self, path):  # noqa: N802
            stem = os.path.splitext(os.path.basename(path))[0]
            for suffix in ("_bake", "_low", "_high"):
                stem = stem[:-len(suffix)] if stem.endswith(suffix) else stem
            group, low, high = _group(self, stem)
            MeshObject(name=stem + "_low", parent=low).addSubmesh("Default")
            MeshObject(name=stem + "_high", parent=high, visible=False).addSubmesh("Default")
            return group

        def addGroup(self, name):  # noqa: N802
            return _group(self, name)[0]

        def savePreset(self, name):  # noqa: N802
            self.presets[name] = dict(outputSamples=self.outputSamples)

        def loadPreset(self, name):  # noqa: N802
            if name not in self.presets:
                raise RuntimeError("no preset %r" % name)
            self.outputSamples = self.presets[name]["outputSamples"]

        def _need(self, mode, label):
            if self.tileMode != mode:
                raise RuntimeError("mset.BakerObject.tileMode must be %s mode before calling "
                                   "this function!" % label)

        def getTextureSetCount(self):  # noqa: N802
            self._need(1, "Multiple")
            return len(self._sets)

        def getTextureSetName(self, i):  # noqa: N802
            self._need(1, "Multiple")
            return self._sets[i][0]

        def getTextureSetEnabled(self, i):  # noqa: N802
            return self._sets[i][1]

        def setTextureSetEnabled(self, i, enabled):  # noqa: N802
            self._sets[i][1] = enabled

        def getTextureSetWidth(self, i):  # noqa: N802
            return self._sets[i][2]

        def setTextureSetWidth(self, i, width):  # noqa: N802
            self._sets[i][2] = width

        def getTextureSetHeight(self, i):  # noqa: N802
            return self._sets[i][3]

        def setTextureSetHeight(self, i, height):  # noqa: N802
            self._sets[i][3] = height

        def getUDIMTileCount(self):  # noqa: N802
            self._need(2, "UDIM")
            return 0

    # ------------------------------------------------------------ materials and textures

    class Texture:
        def __init__(self, path=""):
            self.path, self.sRGB, self.useMipmaps = path, True, True
            self.useFiltering, self.anisotropicFiltering, self.wrapping = True, True, "repeat"

    class MaterialSubroutine:
        def __init__(self, shader, field_names):
            self.name = shader
            self._fields = {}
            for field in field_names:
                default = None if "Map" in field else DEFAULT_FIELD_VALUES.get(field, 0.0)
                self._fields[field] = default
            self.history = []

        def getFieldNames(self):  # noqa: N802
            return list(self._fields)

        def getField(self, name):  # noqa: N802
            if name not in self._fields:
                raise NameError("no field %r" % name)
            return self._fields[name]

        def setField(self, name, value):  # noqa: N802
            if name not in self._fields:
                raise NameError("no field %r" % name)
            if isinstance(value, str) and "Map" in name:
                value = Texture(value)
            self._fields[name] = value
            self.history.append((name, value))

    class Material:
        def __init__(self, name=""):
            if any(mat.name == name for mat in state["materials"]):
                raise NameError("Material %r already exists" % name)
            self.name = name or "Material %d" % (len(state["materials"]) + 1)
            self._group = ""
            self._slots = {slot: None for slot in SLOTS}
            for slot, (shader, fields) in DEFAULT_SHADERS.items():
                self._slots[slot] = MaterialSubroutine(shader, fields)
            state["materials"].append(self)

        def getSubroutine(self, slot):  # noqa: N802
            if slot not in self._slots:
                raise SystemError("IndexError: unknown subroutine %r" % slot)
            return self._slots[slot]

        def setSubroutine(self, slot, shader):  # noqa: N802
            if slot not in self._slots:
                raise SystemError("IndexError: unknown subroutine %r" % slot)
            if shader not in SHADERS:
                raise ReferenceError("Material Subroutine %r doesn't exist" % shader)
            self._slots[slot] = MaterialSubroutine(shader, SHADERS[shader])

        def getGroup(self):  # noqa: N802
            return self._group

        def setGroup(self, name):  # noqa: N802
            self._group = name

        def duplicate(self, name=""):
            return Material(name or self.name + " copy")

        def destroy(self):
            for sub in _submeshes():
                if sub.material is self:
                    sub.material = _default_material()
            state["materials"].remove(self)

        def assign(self, obj, includeChildren=True):  # noqa: N803
            targets = [obj] + (_descendants(obj) if includeChildren else [])
            for target in targets:
                if isinstance(target, SubMeshObject):
                    target.material = self
                elif isinstance(target, MeshObject):
                    for child in target.getChildren():
                        if isinstance(child, SubMeshObject):
                            child.material = self

        def getAssignedObjects(self):  # noqa: N802
            return [sub for sub in _submeshes() if sub.material is self]

        def exportFile(self, path):  # noqa: N802
            _touch(path)

    def _descendants(obj):
        out = []
        for child in obj.getChildren():
            out.append(child)
            out.extend(_descendants(child))
        return out

    def _submeshes():
        return [o for o in state["objects"] if isinstance(o, SubMeshObject)]

    def _default_material():
        for mat in state["materials"]:
            if mat.name == "Default":
                return mat
        return Material("Default")

    # ------------------------------------------------------------ timeline, preferences

    class Timeline:
        def __init__(self):
            self.currentFrame, self.totalFrames, self.playing = 0, 24, False
            self.selectionStart, self.selectionEnd, self.playbackSpeed = 0, 24, 1.0
            self._rate = 24

        def getTime(self):  # noqa: N802
            return self.currentFrame / self._rate

        def setTime(self, time):  # noqa: N802
            self.currentFrame = int(round(time * self._rate))

        def getFrameRate(self):  # noqa: N802
            return self._rate

        def resample(self, rate):
            self._rate = rate

        def play(self):
            self.playing = True

        def pause(self):
            self.playing = False

    class Preferences:
        def __init__(self):
            self.autoTextureReload, self.defaultTangentMethod = True, "Mikk"
            self.rayTraceBackend, self.importFbxWithMaterials = "DXR", True

    # ------------------------------------------------------------ module functions

    def newScene():  # noqa: N802
        state["objects"].clear()
        state["materials"].clear()
        state["selection"].clear()
        state["scene_path"] = ""
        root = SceneObject(name="Scene")
        RenderObject(name="Render", parent=root)
        cam = CameraObject(name="Main Camera", parent=root)
        SkyBoxObject(name="Sky", parent=root)
        state["camera"] = cam
        _default_material()
        for obj in state["objects"]:
            if isinstance(obj, RenderObject):
                obj.cameras = [RenderCameraOptions(cam)]

    def loadScene(path, downloadMissingLibraryAssets=False):  # noqa: N802,N803
        newScene()
        state["scene_path"] = path

    def saveScene(path):  # noqa: N802
        _touch(path, 64)
        state["scene_path"] = path

    def importModel(path):  # noqa: N802
        stem = os.path.splitext(os.path.basename(path))[0]
        root = TransformObject(name=stem)
        mesh = MeshObject(name=stem + "_mesh", parent=root)
        mesh.mesh = Mesh(uvs=[0.2, 0.8] * 24)
        mesh.addSubmesh("Default")
        return root

    def importMaterial(path, name=""):  # noqa: N802
        return Material(name or os.path.splitext(os.path.basename(path))[0])

    def _export(path, **kwargs):
        _touch(path, 128)
        state["calls"].append(("export", path, kwargs))

    def renderCamera(path="", width=-1, height=-1, sampling=-1, transparency=False, camera="",  # noqa: N802
                     viewportPass=""):  # noqa: N803
        state["calls"].append(("renderCamera", path, width, height, sampling, camera))
        if path:
            _touch(path, 256)
        return Image()

    def _render_object():
        return next(o for o in state["objects"] if isinstance(o, RenderObject))

    def renderImages(width=-1, height=-1, sampling=-1, transparency=False):  # noqa: N802
        folder = _render_object().images.outputPath
        os.makedirs(folder, exist_ok=True)
        _touch(os.path.join(folder, "Main Camera.png"), 128)

    def renderVideos(width=-1, height=-1, sampling=-1, transparency=False):  # noqa: N802
        folder = _render_object().videos.outputPath
        os.makedirs(folder, exist_ok=True)
        _touch(os.path.join(folder, "Main Camera.avi"), 128)

    def exportViewer(path="", **kwargs):  # noqa: N802
        _export(path, **kwargs)

    def groupObjects(objs):  # noqa: N802
        group = TransformObject(name="Group", parent=objs[0].parent)
        for obj in objs:
            obj.parent = group

    for cls in (SceneObject, TransformObject, MeshObject, SubMeshObject, CameraObject, LightObject,
                SkyBoxObject, FogObject, ShadowCatcherObject, BackdropObject, RenderObject,
                BakerObject, BakerTargetObject, Material, Texture, Mesh, CameraLens, CameraLimits,
                CameraPostEffect, RenderOptions, RenderOutputOptions, RenderPassOptions,
                RenderCameraOptions, Image, Timeline, Preferences, BakerMap, NormalBakerMap,
                AOBakerMap, HeightBakerMap, CurvatureBakerMap, PositionBakerMap,
                ThicknessBakerMap, MaterialSubroutine):
        cls.__module__ = "mset"
        setattr(m, cls.__name__, cls)

    m.newScene, m.loadScene, m.saveScene = newScene, loadScene, saveScene
    m.importModel, m.importMaterial, m.groupObjects = importModel, importMaterial, groupObjects
    m.renderCamera, m.renderImages, m.renderVideos, m.exportViewer = (
        renderCamera, renderImages, renderVideos, exportViewer)
    for name in ("exportSceneFBX", "exportSceneOBJ", "exportSceneGLTF", "exportSceneUSD",
                 "exportSceneCOLLADA"):
        setattr(m, name, _export)
    m.getAllObjects = lambda: list(state["objects"])
    m.getAllObjectsOfType = lambda cls_name: [o for o in state["objects"]
                                              if type(o).__name__ == cls_name]
    m.findObject = lambda text: next((o for o in state["objects"] if o.name == text), None)
    m.getSelectedObjects = lambda: list(state["selection"])
    m.getSelectedObject = lambda: state["selection"][0] if state["selection"] else None

    def setSelectedObjects(objs):  # noqa: N802
        state["selection"][:] = list(objs)
        return True

    def findMaterial(name):  # noqa: N802
        return next((mat for mat in state["materials"] if mat.name == name), None)

    m.setSelectedObjects, m.findMaterial = setSelectedObjects, findMaterial
    m.getAllMaterials = lambda: list(state["materials"])
    m.getScenePath = lambda: state["scene_path"]
    m.getSceneUnitScale = lambda: 0.01
    m.getSceneBounds = lambda: [[-50.0, -50.0, -50.0], [50.0, 50.0, 50.0]]
    m.frameObject = lambda obj: state["calls"].append(("frameObject", obj.uid))
    m.frameScene = lambda: state["calls"].append(("frameScene",))
    m.getCamera = lambda: state["camera"]

    def setCamera(cam):  # noqa: N802
        state["camera"] = cam

    m.setCamera = setCamera
    state["timeline"], state["preferences"] = Timeline(), Preferences()
    m.getTimeline = lambda: state["timeline"]
    m.getPreferences = lambda: state["preferences"]
    m.getToolbagVersion = lambda: 5022
    m.getGraphicsAdapterName = lambda: "Fake GPU"
    plugin_main = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "__main__.py")
    m.getPluginPath = lambda: plugin_main
    m.log = lambda text: state["calls"].append(("log", text))
    m.err = lambda text: state["calls"].append(("err", text))
    m.shutdownPlugin = lambda: state["calls"].append(("shutdownPlugin",))
    m.refreshUI = lambda: None

    def quit(returnCode=0):  # noqa: N803
        state["quit"] = returnCode

    m.quit = quit
    m.callbacks = types.SimpleNamespace(onPeriodicUpdate=None, onFrameUpdate=None,
                                        onShutdownPlugin=None, onSceneLoaded=None,
                                        onSceneChanged=None, onRegainFocus=None)

    # ------------------------------------------------------------ UI (for plugin/__main__.py)

    class UIControl:
        def __init__(self, *args, **kwargs):
            self.text = args[0] if args else kwargs.get("name", "")
            self.value, self.label, self.onClick, self.onChange = None, "", None, None
            self.elements = []

        def addElement(self, child):  # noqa: N802
            self.elements.append(child)

        def addReturn(self):  # noqa: N802
            pass

        def close(self):
            pass

    for name in ("UIWindow", "UILabel", "UIButton", "UITextField", "UITextFieldInt",
                 "UITextFieldFloat", "UICheckBox", "UISliderInt", "UISliderFloat"):
        setattr(m, name, type(name, (UIControl,), {}))
    m.UIControl = UIControl
    return m
