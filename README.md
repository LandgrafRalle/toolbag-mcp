<!-- Budget 10 KB. The public face of the project: install, use, safety. Full? Move a chapter to docs/ and leave a line with a link. -->
# toolbag-mcp

An [MCP](https://modelcontextprotocol.io) server for **Marmoset Toolbag 5**. It lets an MCP
client such as Claude Code, Claude Desktop or Cursor drive a running Toolbag through typed
tools: inspect and change the scene, materials and textures, set up and run bake projects,
place lights and cameras, render images. Free Python runs only when you allow it in Toolbag.

> Early stage: built and tested against Toolbag 5.0.2 on Windows. Not affiliated with
> Marmoset LLC; "Marmoset" and "Toolbag" are their trademarks.

## How it works

Two parts, one package:

- **A Toolbag plugin** (`plugin/`) that runs inside Toolbag. It listens on `127.0.0.1`
  (port 18770 by default) and executes named actions on Toolbag's main thread.
- **The MCP server** (`toolbag_mcp`) that runs outside Toolbag, talks to the plugin over
  HTTP and exposes the actions as MCP tools over stdio.

Toolbag runs plugin code at full speed only while its window has focus. So the server
brings Toolbag forward while a call waits for it (Windows), but stays out of your way: it
switches only after 1.5 s without keyboard or mouse input, hands the focus back as soon as
a longer action is running, and never takes it from a window you picked during the call.
While you type, the call waits; a visible Toolbag answers in the background too, only
slower (seconds per call).

Scripts that use the client directly can keep Toolbag in front across many calls instead of
flashing it for each one:

```python
from toolbag_mcp.client import ToolbagClient

client = ToolbagClient()
with client.holding_focus():
    for name in ("a", "b", "c"):
        client.call("object.get", {"ref": name})
```

## Requirements

- Marmoset Toolbag 5.0.x on Windows (tested with 5.0.2.2). Toolbag 4 and macOS are untested.
- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) for the server. The plugin
  needs nothing beyond Toolbag's own Python.

## Install

The package is not on PyPI; this Git repository is the release channel.

1. Install the `toolbag-mcp` command with uv:

   ```
   uv tool install git+https://github.com/LandgrafRalle/toolbag-mcp
   ```

   Pin a release by appending `@v0.1.0` to the URL; `uv tool upgrade toolbag-mcp` updates
   later. Without installing, `uvx --from git+https://github.com/LandgrafRalle/toolbag-mcp
   toolbag-mcp ...` runs any command below; from a checkout, `uv run toolbag-mcp ...` does.

2. Install the plugin into Toolbag's user plugin folder
   (`%LOCALAPPDATA%\Marmoset Toolbag 5\plugins\Toolbag MCP`), as a junction to the package
   or with `--copy` as a copy of the files:

   ```
   toolbag-mcp install
   ```

   Run this from a normal shell: packaged (Microsoft Store) apps see `%LOCALAPPDATA%`
   redirected, and a junction made from inside one of them is invisible to Toolbag.

3. In Toolbag: *Edit, Plugins, Refresh*, then *Plugins, Toolbag MCP*. A small window shows
   the port, the call counter and the switch "Allow Python execution". Toolbag can also start
   the plugin directly: `toolbag.exe "<path to>\plugin\__main__.py"`.

4. Register the server with your MCP client. `toolbag-mcp register` prints the lines for
   your installation; for example for Claude Code:

   ```
   claude mcp add --scope user --transport stdio toolbag -- toolbag-mcp
   ```

   Claude Desktop and Cursor take the same command in their JSON config:
   `{"mcpServers": {"toolbag": {"command": "toolbag-mcp", "args": []}}}`. If a client does
   not find the command, give it the full path (`uv tool dir --bin` prints the folder) or
   the `uvx --from ...` form.

5. Check the setup: `toolbag-mcp doctor`. It verifies the installed plugin, finds the
   Toolbag window, reads the plugin's state file and does one round trip.

Another port: `install --port 18771` writes it to `~/.toolbag-mcp/config.json`; the plugin
window has a port field too. The plugin writes its port and per-start token to
`~/.toolbag-mcp/plugin.json`, which the server reads for every call.

## Tools

Tool names as the client sees them, by family:

| Family | Tools |
|---|---|
| Diagnostics | `status`, `capabilities`, `preferences_get`, `preferences_set` |
| Scene and objects | `scene_info`, `scene_new`, `scene_load`, `scene_save`, `scene_list_objects`, `scene_tree`, `scene_import_model`, `scene_export`, `scene_bounds`, `object_get`, `object_find`, `object_rename`, `object_duplicate`, `object_delete`, `object_group`, `object_set_parent`, `object_set_visible`, `object_set_transform`, `object_attributes`, `object_set_attributes`, `object_create`, `selection_get`, `selection_set`, `selection_clear`, `camera_frame`, `mesh_set_backfaces` |
| Materials | `material_list`, `material_get`, `material_create`, `material_duplicate`, `material_delete`, `material_assign`, `material_set_group`, `material_set_subroutine`, `material_set_field`, `material_set_texture`, `material_set_cutout`, `material_import`, `material_export` |
| Baker | `baker_list`, `baker_get`, `baker_create`, `baker_add_group`, `baker_quick_load`, `baker_set_settings`, `baker_set_maps`, `baker_set_cage`, `baker_set_visible`, `baker_isolate`, `baker_bake`, `baker_bake_psd`, `baker_bake_to`, `baker_map_path`, `baker_load_preset`, `baker_save_preset`, `baker_set_texture_set`, `baker_set_udim_tile`, `baker_replace_low` |
| Lights, sky, fog, cameras | `light_list`, `light_create`, `sky_get`, `sky_set`, `sky_load`, `sky_save`, `sky_set_background_image`, `sky_compute_procedural`, `fog_get`, `fog_set`, `camera_list`, `camera_get`, `camera_create`, `camera_set`, `camera_set_active`, `camera_post_effect_file` |
| Render and timeline | `render_get`, `render_set`, `render_set_cameras`, `render_set_passes`, `render_image`, `render_images`, `render_videos`, `render_export_viewer`, `timeline_get`, `timeline_set` |
| Python and lifecycle | `execute_python` (only while allowed in the plugin window), `toolbag_quit` |

Resources: `toolbag://scene`, `toolbag://materials`, `toolbag://baker`, `toolbag://cameras`,
`toolbag://render`.

Objects are referenced by uid or exact name; `scene_tree`, `scene_list_objects` and
`object_find` give them. `object_attributes` shows every setting of any object with its
value, `object_set_attributes` changes them, nested settings included (`{"lens":
{"dofStop": 2.8}}` on a camera). In bake scenes, toggle visibility through
`baker_set_visible` and `baker_isolate`, never on single meshes: that corrupts bakes.

From the shell: `toolbag-mcp actions` lists every action with its parameters,
`toolbag-mcp call scene.info` runs one.

## Safety

- The plugin listens on the loopback address only. Every request needs a marker header, the
  token generated at this plugin start and a `Host` of `127.0.0.1` or `localhost`. CORS
  preflights are never answered, so a web page cannot reach it.
- `execute_python` runs only while "Allow Python execution" is ticked in the plugin window.
  The switch is off at every start and is not persisted.
- Everything else is a fixed, validated action. Actions write files only where you tell
  them to. `scene_new`, `scene_load` and `toolbag_quit` drop unsaved changes without a
  prompt; clients see them flagged as destructive.
- The server talks to nothing but the plugin's loopback port.

## Troubleshooting

- `toolbag-mcp doctor` first.
- No answer: Toolbag must not sit behind a modal dialog or be minimized. While you keep
  typing, calls wait for a pause (see above); `TOOLBAG_MCP_NO_FOCUS=1` turns the focus
  handling off entirely, calls then rely on Toolbag answering in the background.
- "Nothing accepts connections" while Toolbag runs: it is busy (loading or saving a large
  scene stops the plugin for that time). "No longer running": Toolbag closed or crashed;
  Windows records crashes in the event log (Application, source "Application Error").
- Save before `scene_new` or `scene_load` on large scenes: Toolbag 5.0.2 has crashed in
  `newScene` there (see [docs/pitfalls.md](docs/pitfalls.md)).
- A second Toolbag instance cannot bind the port; its plugin window says so.
- Never run Toolbag MCP together with another plugin that sets `onPeriodicUpdate`
  (`doctor` lists them): Toolbag then runs neither.
- Verified traps of Toolbag's Python API, with the differences between 5.0.2 and the docs:
  [docs/pitfalls.md](docs/pitfalls.md).

## Development

```
uv sync
uv run pytest           # tests against a fake mset, no Toolbag needed
uv run ruff check .
uv build                # wheel with the plugin folder inside
```

Test assets: `blender --background --factory-startup --python tests/make_test_assets.py --
<folder>`. CI runs lint, tests and the build on Windows and Ubuntu.

## License

[MIT](LICENSE), copyright (c) 2026 LandgrafRalle.
