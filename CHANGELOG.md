<!-- Budget 8 KB. Keep a Changelog format, newest on top. Full? Move released sections to docs/changelog-archive.md and leave a link. -->
# Changelog

All notable changes to toolbag-mcp. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The Git repository is the release channel; the package is not on PyPI. The README installs
  the command with `uv tool install git+https://github.com/LandgrafRalle/toolbag-mcp`, and
  `register` prints the matching command for a checkout, an installed `toolbag-mcp` or
  `uvx --from git+...`.
- The maintainer's German working notes are no longer part of the repository.
- Focus handling no longer interrupts typing in other windows: Toolbag is brought forward
  only after 1.5 s without keyboard or mouse input, the focus goes back as soon as a longer
  action runs (the plugin reports it in `~/.toolbag-mcp/job.json`), and a window the user
  picked during a call keeps the focus.
- The plugin's connection queue holds 64 instead of 5, so a busy Toolbag (loading a large
  scene) no longer refuses connections.
- Clearer errors: "busy" when Toolbag runs but does not accept connections, "no longer
  running" when its process is gone, also when it vanishes during a call.

### Added

- Edge protect: `baker_skew_preview`, `baker_skew_clear_preview` and `baker_skew_apply` set
  skew masks by edge rule (averaged only along UV seams, hard edges and bent soft edges)
  instead of hand painting. The server computes them with numpy (new dependency) and writes
  them into a new scene file; plugin actions `baker.low_mesh_data`, `baker.skew_preview` and
  `baker.skew_preview_clear` supply mesh data and the preview.
- `ToolbagClient.holding_focus()` (and `focus.holding()`): keep Toolbag in front across
  several calls and hand the focus back once at the end.

### Fixed

- Closing the plugin window with its title bar button crashed Toolbag: the shutdown handler
  wrote to a label of the window being torn down.
- `scene_save` passes paths with forward slashes; backslashes break Toolbag's autosave.

## [0.1.0] - 2026-10-29

First release, built and tested against Marmoset Toolbag 5.0.2 on Windows.

### Added

- Toolbag plugin (`plugin/`): loopback HTTP server with a per-start token, marker header and
  Host check, job queue on `onPeriodicUpdate`, window with port, call counter and the
  "Allow Python execution" switch. Scene operations and quitting run two-phased because
  Toolbag 5.0.2 crashes otherwise.
- MCP server (`toolbag_mcp`): 90 tools with output schemas and 5 resources over stdio.
  Families: diagnostics, scene and objects, materials, baker, lights/sky/fog/cameras, render
  and timeline, gated Python, quitting. Generic `object_attributes`, `object_set_attributes`
  and `object_create` reach every attribute of every object.
- Command line: `install` (junction or copy into Toolbag's plugin folder), `uninstall`,
  `verify`, `doctor`, `call`, `actions`, `register`.
- Focus handling on Windows: Toolbag is brought to the front for the duration of a call and
  the previous window gets the focus back.
- Tests without Toolbag against a fake `mset` (`tests/fake_mset.py`), including the plugin
  core over real HTTP and the MCP layer in-process. CI runs lint, tests and the build.
- `docs/pitfalls.md`: verified traps of Toolbag 5.0.2's Python API and of running a plugin
  server.

### Known limitations

- Focus handling is Windows only; Toolbag 4 and macOS are untested.
- Toolbag 5.0.2 has no `getMapPath`, `geometryMode`, `interactive`, light `shape` or
  physical light units; the tools fall back or report it.
- `render_videos` and `render_export_viewer` are tested against the fake only.
