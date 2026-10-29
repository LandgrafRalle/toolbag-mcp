"""Install the plugin into Toolbag's user plugin folder, as a junction (default) or a copy.

Toolbag loads user plugins from %LOCALAPPDATA%\\Marmoset Toolbag 5\\plugins (not from
Documents); a folder with a ``__main__.py`` appears in the Plugins menu under the folder's
name. A junction keeps the plugin in sync with this package; a copy is self-contained.
After installing: Toolbag -> Edit -> Plugins -> Refresh, or restart Toolbag.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_NAME = "Toolbag MCP"
PLUGIN_FILES = ("__main__.py", "tbmcp_actions.py")


def plugin_source_dir() -> Path:
    """The plugin folder: inside the installed package (wheel) or next to it (checkout)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "plugin", here.parent / "plugin"):
        if (candidate / "__main__.py").is_file():
            return candidate
    raise FileNotFoundError("plugin folder not found next to %s" % here)


def toolbag_plugins_dir(version: int = 5, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise RuntimeError("LOCALAPPDATA is not set")
        return Path(base) / ("Marmoset Toolbag %d" % version) / "plugins"
    raise RuntimeError("Only Windows is known; pass --plugins-dir with Toolbag's plugin folder")


def is_junction(path: Path) -> bool:
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(path)
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def _make_junction(source: Path, target: Path) -> None:
    try:
        import _winapi
        _winapi.CreateJunction(str(source), str(target))
        return
    except (ImportError, AttributeError, OSError):
        pass
    subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)],
                   check=True, capture_output=True)


def _remove_target(target: Path) -> str:
    """Remove an existing plugin folder: a junction or symlink is unlinked (its target stays),
    a real folder is deleted. Returns what was removed."""
    if not target.exists() and not target.is_symlink() and not is_junction(target):
        return "nothing"
    if target.is_symlink() or is_junction(target):
        os.rmdir(target)
        return "junction"
    shutil.rmtree(target)
    return "copy"


def install(copy: bool = False, version: int = 5, plugins_dir: str | None = None,
            name: str = PLUGIN_NAME) -> dict:
    source = plugin_source_dir()
    folder = toolbag_plugins_dir(version, plugins_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    removed = _remove_target(target)
    if copy:
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        mode = "copy"
    else:
        _make_junction(source, target)
        mode = "junction"
    return {"target": str(target), "source": str(source), "mode": mode, "replaced": removed,
            "files": sorted(p.name for p in target.iterdir() if p.is_file()),
            "next": "In Toolbag: Edit -> Plugins -> Refresh (or restart Toolbag), then "
                    "Plugins -> %s" % name}


def uninstall(version: int = 5, plugins_dir: str | None = None, name: str = PLUGIN_NAME) -> dict:
    target = toolbag_plugins_dir(version, plugins_dir) / name
    removed = _remove_target(target)
    return {"target": str(target), "removed": removed}


def verify(version: int = 5, plugins_dir: str | None = None, name: str = PLUGIN_NAME) -> dict:
    """Compare the installed plugin with this package's plugin folder."""
    source = plugin_source_dir()
    target = toolbag_plugins_dir(version, plugins_dir) / name
    report = {"target": str(target), "source": str(source), "installed": target.is_dir(),
              "mode": None, "ok": False, "differences": []}
    if not target.is_dir():
        report["differences"].append("not installed")
        return report
    report["mode"] = "junction" if is_junction(target) else "copy"
    for file in PLUGIN_FILES:
        src, dst = source / file, target / file
        if not dst.is_file():
            report["differences"].append("%s missing" % file)
        elif not filecmp.cmp(src, dst, shallow=False):
            report["differences"].append("%s differs from the package" % file)
    report["ok"] = not report["differences"]
    return report


def foreign_update_plugins(version: int = 5, plugins_dir: str | None = None,
                           name: str = PLUGIN_NAME) -> list[str]:
    """Other installed plugins that set onPeriodicUpdate: only one plugin may, so these must
    never run together with Toolbag MCP."""
    try:
        folder = toolbag_plugins_dir(version, plugins_dir)
    except RuntimeError:
        return []
    if not folder.is_dir():
        return []
    hits = []
    for entry in folder.iterdir():
        if entry.name == name:
            continue
        if entry.suffix == ".py":
            files = [entry]
        elif entry.is_dir():
            files = list(entry.glob("*.py"))
        else:
            files = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "onPeriodicUpdate" in text or "onFrameUpdate" in text:
                hits.append(entry.name)
                break
    return sorted(hits)
