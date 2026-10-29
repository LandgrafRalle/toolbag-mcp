"""HTTP client for the Toolbag MCP plugin running inside Toolbag.

The plugin writes its port and per-start token to ``~/.toolbag-mcp/plugin.json``; the
client reads that file for every request, so a restarted plugin is picked up without
restarting the MCP server. Calls keep Toolbag in the foreground (see ``focus``), because
Toolbag only runs plugin code while its window has focus.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

from . import focus

STATE_DIR = os.path.join(os.path.expanduser("~"), ".toolbag-mcp")
STATE_FILE = os.path.join(STATE_DIR, "plugin.json")
HEADER_MARK = "X-Toolbag-MCP"
HEADER_TOKEN = "X-Toolbag-MCP-Token"
DEFAULT_PORT = 18770

NOT_RUNNING_HINT = (
    "The Toolbag MCP plugin is not running: no %s. In Toolbag open Plugins -> Toolbag MCP "
    "(install it with `toolbag-mcp install`), or start Toolbag with the plugin file as "
    "argument. Run `toolbag-mcp doctor` for details." % STATE_FILE)


class PluginUnavailable(Exception):
    """The plugin cannot be reached: not running, wrong port, token mismatch."""


class PluginError(Exception):
    """The plugin ran the action and reported a failure."""

    def __init__(self, kind: str, message: str, traceback: str | None = None, log: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.traceback = traceback
        self.log = log

    def describe(self) -> str:
        parts = ["%s: %s" % (self.kind, self.message)]
        if self.traceback:
            parts.append(self.traceback.rstrip())
        if self.log:
            parts.append("Toolbag output:\n" + self.log.rstrip())
        return "\n".join(parts)


def read_state() -> dict[str, Any] | None:
    """Contents of plugin.json, None when the plugin is not running (or never ran)."""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("token") else None


def is_listening(port: int, timeout: float = 2.0) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


class ToolbagClient:
    def __init__(self, port: int | None = None, token: str | None = None,
                 use_focus: bool | None = None):
        self.port = port
        self.token = token
        if use_focus is None:
            switch = os.environ.get("TOOLBAG_MCP_NO_FOCUS", "").lower()
            use_focus = switch not in ("1", "true", "yes")
        self.use_focus = use_focus

    def endpoint(self) -> tuple[int, str | None, dict[str, Any]]:
        state = read_state() or {}
        port = int(self.port or state.get("port") or DEFAULT_PORT)
        token = self.token or state.get("token")
        return port, token, state

    def _request(self, method: str, path: str, body: Any = None, timeout: float = 10.0) -> dict:
        port, token, _ = self.endpoint()
        if token is None:
            raise PluginUnavailable(NOT_RUNNING_HINT)
        if not is_listening(port):
            raise PluginUnavailable(
                "Nothing listens on 127.0.0.1:%d. The plugin wrote %s but is gone (Toolbag "
                "closed?) or hangs; if it hangs, restart Toolbag, a plugin restart does not "
                "free the port." % (port, STATE_FILE))
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
            headers={HEADER_MARK: "1", HEADER_TOKEN: token,
                     "Content-Type": "application/json", "Host": "127.0.0.1:%d" % port})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                payload = {"ok": False,
                           "error": {"type": "HTTP %d" % exc.code, "message": str(exc)}}
            if exc.code == 403:
                raise PluginUnavailable(
                    "The plugin on port %d refused the request (403): the token in %s does not "
                    "match. Restart the plugin in Toolbag (it rewrites the file) or check that "
                    "nothing else uses the port." % (port, STATE_FILE)) from exc
            return payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PluginUnavailable("Could not reach the plugin on port %d: %s"
                                    % (port, exc)) from exc

    def _get(self, path: str, timeout: float) -> dict:
        # Toolbag freezes all plugin threads, the HTTP thread included, while its window is
        # not in front: even a plain GET needs the focus handling.
        def _do():
            return self._request("GET", path, timeout=timeout)

        return focus.hold_front(_do) if self.use_focus else _do()

    def status(self, timeout: float = 5.0) -> dict:
        return self._get("/", timeout)

    def actions(self, timeout: float = 10.0) -> dict:
        return self._get("/actions", timeout)

    def call(self, action: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        """Run one action; returns the plugin's payload (``result``, ``log``, ``ms``).
        Raises PluginError when the action failed, PluginUnavailable when nothing answers."""
        body = {"action": action, "params": params or {}, "timeout": timeout}

        def _do():
            return self._request("POST", "/call", body, timeout=timeout + 15)

        payload = focus.hold_front(_do) if self.use_focus else _do()
        if not isinstance(payload, dict):
            raise PluginUnavailable("Unexpected answer from the plugin: %r" % (payload,))
        if not payload.get("ok"):
            error = payload.get("error") or {}
            raise PluginError(error.get("type", "Error"), error.get("message", "unknown error"),
                              error.get("traceback"), payload.get("log", ""))
        return payload
