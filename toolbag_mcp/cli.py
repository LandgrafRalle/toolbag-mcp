"""Command line: ``toolbag-mcp`` serves MCP over stdio; subcommands install the plugin and
diagnose the setup. Everything but the MCP protocol goes to stderr."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, focus, install
from .client import PluginError, PluginUnavailable, ToolbagClient, is_listening, read_state


def _say(text: str = "") -> None:
    print(text, file=sys.stderr)


def _dump(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _pid_alive(pid: int) -> bool | None:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def cmd_serve(_args) -> int:
    from .server import run
    run()
    return 0


def cmd_install(args) -> int:
    try:
        report = install.install(copy=args.copy, version=args.toolbag_version,
                                 plugins_dir=args.plugins_dir)
    except Exception as exc:  # noqa: BLE001
        _say("install failed: %s" % exc)
        return 1
    if args.port:
        config = Path(install.os.path.expanduser("~")) / ".toolbag-mcp" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"port": int(args.port)}, indent=1), encoding="utf-8")
        report["config"] = str(config)
    _dump(report)
    return 0


def cmd_uninstall(args) -> int:
    _dump(install.uninstall(version=args.toolbag_version, plugins_dir=args.plugins_dir))
    return 0


def cmd_verify(args) -> int:
    report = install.verify(version=args.toolbag_version, plugins_dir=args.plugins_dir)
    _dump(report)
    return 0 if report["ok"] else 1


def cmd_doctor(args) -> int:
    lines: list[tuple[str, str]] = []
    problems = 0

    def ok(text):
        lines.append(("OK  ", text))

    def warn(text):
        lines.append(("WARN", text))

    def fail(text):
        nonlocal problems
        problems += 1
        lines.append(("FAIL", text))

    ok("toolbag-mcp %s, Python %s" % (__version__, sys.version.split()[0]))
    try:
        verify = install.verify(version=args.toolbag_version, plugins_dir=args.plugins_dir)
        if verify["installed"] and verify["ok"]:
            ok("plugin installed as %s at %s" % (verify["mode"], verify["target"]))
        elif verify["installed"]:
            warn("plugin installed but differs: %s (run `toolbag-mcp install`)"
                 % ", ".join(verify["differences"]))
        else:
            warn("plugin not installed at %s (run `toolbag-mcp install`)" % verify["target"])
        foreign = install.foreign_update_plugins(version=args.toolbag_version,
                                                 plugins_dir=args.plugins_dir)
        if foreign:
            warn("other plugins use onPeriodicUpdate/onFrameUpdate, never run them together with "
                 "Toolbag MCP: %s" % ", ".join(foreign))
    except Exception as exc:  # noqa: BLE001
        warn("could not inspect Toolbag's plugin folder: %s" % exc)

    if focus.available():
        hwnd = focus.find_toolbag()
        if hwnd:
            ok("Toolbag window found (focus handling active)")
        else:
            warn("no Toolbag window found: is Toolbag running?")
    else:
        warn("not Windows: focus handling unavailable, keep Toolbag in front during calls")

    state = read_state()
    if not state:
        fail("plugin state file missing: the plugin is not running (Plugins -> Toolbag MCP)")
    else:
        alive = _pid_alive(int(state.get("pid", 0)))
        if alive is False:
            fail("plugin state file is stale (Toolbag pid %s is gone); it is replaced at the "
                 "next plugin start" % state.get("pid"))
        else:
            ok("plugin %s started %s in Toolbag %s (pid %s), port %s" % (
                state.get("plugin_version"), state.get("started"), state.get("toolbag_version"),
                state.get("pid"), state.get("port")))
        port = int(state.get("port") or 0)
        if port and is_listening(port):
            ok("port %d accepts connections" % port)
            client = ToolbagClient()
            try:
                status = client.status()
                ok("plugin answers: %d calls so far, Python execution %s" % (
                    status.get("calls", 0), "allowed" if status.get("allow_python") else "blocked"))
            except PluginUnavailable as exc:
                fail(str(exc))
            try:
                result = client.call("status", {}, timeout=10)
                scene = result["result"].get("scene_path") or "(unsaved scene)"
                ok("action round trip works, scene: %s" % scene)
            except PluginError as exc:
                fail("status action failed: %s" % exc.describe())
            except PluginUnavailable as exc:
                fail("no answer within 10 s although Toolbag was brought to the front (%s); "
                     "if a modal dialog is open in Toolbag, close it" % exc)
        elif port:
            fail("nothing listens on port %d although the state file exists" % port)

    for tag, text in lines:
        _say("%s %s" % (tag, text))
    return 1 if problems else 0


def cmd_call(args) -> int:
    try:
        params = json.loads(args.params) if args.params else {}
    except ValueError as exc:
        _say("params must be JSON: %s" % exc)
        return 2
    client = ToolbagClient(use_focus=not args.no_focus)
    try:
        payload = client.call(args.action, params, timeout=args.timeout)
    except PluginUnavailable as exc:
        _say(str(exc))
        return 1
    except PluginError as exc:
        _say(exc.describe())
        return 1
    if payload.get("log"):
        _say(payload["log"].rstrip())
    _dump(payload.get("result"))
    _say("(%d ms)" % payload.get("ms", 0))
    return 0


def cmd_actions(args) -> int:
    client = ToolbagClient()
    try:
        catalogue = client.actions()["actions"]
    except PluginUnavailable as exc:
        _say(str(exc))
        return 1
    if args.json:
        _dump(catalogue)
        return 0
    for name, entry in catalogue.items():
        params = ", ".join(
            p if spec["required"] else "%s=%s" % (p, json.dumps(spec["default"]))
            for p, spec in entry["params"].items())
        print("%s(%s)" % (name, params))
        doc = entry["doc"].strip().splitlines()
        if doc:
            print("    " + doc[0])
    return 0


def cmd_register(args) -> int:
    """Print how to register this server with common MCP clients."""
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "pyproject.toml").is_file():
        command, cmd_args = "uv", ["run", "--directory", str(checkout), "toolbag-mcp"]
    else:
        command, cmd_args = "uvx", ["toolbag-mcp"]
    quoted = " ".join('"%s"' % a if " " in a else a for a in cmd_args)
    _say("Claude Code (user scope):")
    _say("  claude mcp add --scope user --transport stdio toolbag -- %s %s" % (command, quoted))
    _say("  (long bakes: raise Claude Code's MCP_TOOL_TIMEOUT in its settings env block)")
    _say("")
    _say("Claude Desktop / Cursor (JSON):")
    _dump({"mcpServers": {"toolbag": {"command": command, "args": cmd_args}}})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toolbag-mcp",
                                     description="MCP server for Marmoset Toolbag 5. Without a "
                                                 "subcommand it serves MCP over stdio.")
    parser.add_argument("--version", action="version", version="toolbag-mcp %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="serve MCP over stdio (default)")

    def _plugin_args(p):
        p.add_argument("--toolbag-version", type=int, default=5,
                       help="major version, default 5")
        p.add_argument("--plugins-dir",
                       help="Toolbag's plugin folder, when not the default")

    p_install = sub.add_parser("install", help="install the plugin into Toolbag's plugin folder")
    p_install.add_argument("--copy", action="store_true",
                           help="copy the files instead of a junction")
    p_install.add_argument("--port", type=int,
                           help="write ~/.toolbag-mcp/config.json with this port")
    _plugin_args(p_install)
    _plugin_args(sub.add_parser("uninstall", help="remove the installed plugin"))
    _plugin_args(sub.add_parser("verify", help="compare the installed plugin with this package"))
    _plugin_args(sub.add_parser("doctor",
                                help="check installation, Toolbag, port, token and a round trip"))

    p_call = sub.add_parser("call", help="run one action directly, e.g. call scene.info")
    p_call.add_argument("action")
    p_call.add_argument("params", nargs="?", help="JSON object with parameters")
    p_call.add_argument("--timeout", type=float, default=60.0)
    p_call.add_argument("--no-focus", action="store_true", help="do not bring Toolbag to the front")

    p_actions = sub.add_parser("actions", help="list the plugin's actions")
    p_actions.add_argument("--json", action="store_true")

    sub.add_parser("register", help="print how to register the server with MCP clients")

    args = parser.parse_args(argv)
    handlers = {None: cmd_serve, "serve": cmd_serve, "install": cmd_install,
                "uninstall": cmd_uninstall, "verify": cmd_verify, "doctor": cmd_doctor,
                "call": cmd_call, "actions": cmd_actions, "register": cmd_register}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
