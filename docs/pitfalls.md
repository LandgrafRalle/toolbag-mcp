<!-- Budget 15 KB. Verified traps of Toolbag's Python API and of running a plugin server; read before writing code against mset. Full? Split by topic, do not append. -->
# Pitfalls: Marmoset Toolbag's Python API and plugin servers

Everything here was observed on Toolbag 5.0.2.2 (`getToolbagVersion()` = 5022) on Windows,
either in this project (dates 29.10.2026) or in its private predecessor (dates in September
and October 2026). "Docs" means https://marmoset.co/python/reference5.html, which describes
build 5.03.

## Running plugin code

- **Toolbag runs Python only while its window has focus.** Not just `onPeriodicUpdate`: a
  plugin's background threads freeze too, so an HTTP request to the plugin does not time
  out, it simply never returns. The MCP server therefore brings Toolbag to the front for
  the duration of every call (`toolbag_mcp/focus.py`), finding the window by process name
  (`toolbag.exe`), because browser tabs can carry the same title.
- **Only one plugin may set `mset.callbacks.onPeriodicUpdate`.** Two plugins with their own
  callback stop each other; chaining did not help (24.09.2026). Never run Toolbag MCP next
  to another such plugin. `toolbag-mcp doctor` lists installed plugins that use the callback.
- **`newScene()` and `loadScene()` crash Toolbag when more Python runs in the same callback
  tick** (access violation in toolbag.exe, 29.10.2026). Marmoset's own example calls
  `loadScene` at script top level only. The plugin runs these two calls as the very last
  statement of a tick and reports the new scene in the next tick (`plugin/__main__.py`,
  `_drain`). `onFrameUpdate` is not used at all; `onPeriodicUpdate` ("a few times per
  second") is enough.
- **`http.server` sets `allow_reuse_address`, and on Windows that lets a second plugin
  instance bind the same port.** Requests then land in whichever instance accepted first,
  and the one that is not in front never answers. The plugin sets it to False, so a second
  instance fails loudly ("Cannot listen on port").
- **A plugin lives as long as it has a window.** `onShutdownPlugin` runs when the user closes
  it; the plugin removes its state file there. A plugin restarted inside the same Toolbag
  session keeps old modules in `sys.modules`, hence the `importlib.reload` at start.
- **Packaged apps see `%LOCALAPPDATA%` redirected** (MSIX, for example Claude Desktop from
  the Microsoft Store): what such a process writes there lands in the package's own
  container and Toolbag never sees it, and vice versa. The plugin therefore writes its
  state to `~/.toolbag-mcp/`, and `toolbag-mcp install` must run from a normal shell when the
  MCP client is such an app (the junction then exists only inside the container otherwise).
- **Plugin folder:** user plugins live in `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins`, not
  under Documents; the docs' `data/plugins` is `data\plugin` in the installation. A folder
  with `__main__.py` appears in the Plugins menu under the folder's name; a junction works.
  `toolbag.exe "<path>\__main__.py"` runs a plugin at start without the menu.

## Scene and objects

- **Never set `SubMeshObject.visible`.** It sets a flag after which Toolbag bakes the whole
  scene grainy and single parts empty; on `MeshObject` the same flag crashed Toolbag on
  load (24.09.2026). Toggle groups and a bake project's High/Low nodes; the meshes follow and
  report the effective state. The actions refuse submeshes and need `allow_mesh` for meshes.
- **Reading `MeshObject.mesh` and saving afterwards** writes low meshes without attribute 7
  (cage direction per vertex). Toolbag recomputes it on load; still, save before reading
  mesh data, not after. Only `baker.replace_low` reads mesh data.
- **Transforms are in scene units** (`getSceneUnitScale()` = 0.01 for a centimetre scene);
  bounds come as `[min_xyz, max_xyz]`, `None` for objects without geometry.
- `groupObjects()` returns nothing in 5.02; the new group is found by comparing uids
  before and after. `duplicate()` of a camera works; a duplicated object inside a bake
  group confuses the baker, so duplicate outside of it.
- **Hidden High nodes:** the Quick Loader creates the High node of a group hidden
  (`visible = False`); bakes still use it because `useHiddenMeshes` defaults to True.

## Materials

- **`Material.getSubroutine()` with an unknown slot name raises** (`IndexError` surfaces as
  `SystemError`); an empty slot returns `None`. Slot names for `setSubroutine` are
  `subdivision, displacement, surface, microsurface, albedo, diffusion, reflectivity,
  reflection, clearcoat reflection, clearcoat microsurface, clearcoat reflectivity,
  occlusion, emissive, transparency, texture` (note `emissive`, while the attribute is
  `Material.emission`).
- **Field names carry a qualifier after a semicolon when a label repeats** within a shader:
  `Invert;roughness`, `Channel;specular`, `Color;fresnel`. `getFieldNames()` returns them
  exactly so; use them verbatim in `setField`.
- **Textures have no reload.** To make Toolbag re-read a changed file, clear the field
  (`setField(name, None)`) and set a new `Texture(path)`. `Texture.path` is read-only.
  `setField` accepts lists for colours (`[1, 0.5, 0.25]`), numbers, booleans and strings.
- **Cutout masks:** set the transparency slot to `Cutout`, `Use Albedo Alpha` off, `Channel`
  0 (red), then the Alpha Map, with sRGB off. Otherwise Toolbag ignores the map or reads a
  missing alpha channel as opaque (02.10.2026).
- `importMaterial()` and `Material.exportFile()` round-trip a `.tbmat`; a material cannot be
  renamed through the import, pass `name` instead.

## Baker

- **Map suffixes differ from the display names:** `normal` (not `normals`), `normalobj`,
  `height`, `position`, `curve`, `convex`, `cavity`, `ao`, `ao2`, `thickness`,
  `bentnormal`, `bentnormalobj`, `uvisland`, `matid`, `objid`, `grpid`, `alphamask`,
  `complete`, `diffuse`, `specular`, `indirect`, and more. The actions accept suffix,
  class name and display name, with or without a plural s. `getMap()` with an unknown
  name writes an error into Toolbag's log, so it is tried last.
- **5.02 lacks what the 5.03 docs list:** `BakerObject.geometryMode`, `interactive` and
  `getMapPath()` do not exist; `multipleTextureSets` and `edgePadding = "Custom"` from the
  bundled examples are gone. Output paths are therefore computed as
  `<outputPath stem>_<suffix><ext>`, which matches what the bake writes.
- **Texture set and UDIM getters raise** (`tileMode must be Multiple mode`) unless
  `tileMode` is 1 or 2 respectively; the actions return `null` for them otherwise.
- **Cage offsets read as a large negative sentinel while automatic**; after
  `estimateOffset()` they still do. The actions report `null` for "automatic".
- **The Quick Loader pairs objects by name stem** (`<stem>_low` / `<stem>_high`). A mismatch
  yields a group without High and an empty bake (16 KB PNGs). Check the groups after loading.
- **`bake()` from Python is synchronous, never interactive, fast** (2048², 20 parts under a
  second) and deterministic: the same input gives byte-identical files, so trial bakes can
  be compared.
- **Replacing low geometry re-baked on its own once** (29.10.2026): after
  `baker.replace_low` (import, mesh assignment, delete import) the output files were
  rewritten without a bake call. Not yet understood; do not rely on output timestamps.
- `edgePadding` takes `'None'`, `'Moderate'`, `'Extreme'`; `outputBits` 8, 16 or 32;
  `outputPath` may be relative to the scene file, so pass absolute paths.
