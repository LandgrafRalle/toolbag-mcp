"""HTTP client for the Toolbag MCP plugin running inside Toolbag.

The plugin writes its port and per-start token to ``~/.toolbag-mcp/plugin.json``; the
client reads that file for every request, so a restarted plugin is picked up without
restarting the MCP server. Calls bring Toolbag forward while they wait for it (see
``focus``), because Toolbag runs plugin code at full speed only while its window has focus;
``ToolbagClient.holding_focus()`` keeps it in front across several calls.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

from . import focus

STATE_DIR = os.path.join(os.path.expanduser("~"), ".toolbag-mcp")
STATE_FILE = os.path.join(STATE_DIR, "plugin.json")
JOB_FILE = os.path.join(STATE_DIR, "job.json")  # the plugin's running action, for focus
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


def job_state(job_id: str) -> str | None:
    """"running" or "done" while the plugin's job file names this call, else None."""
    try:
        with open(JOB_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("id") == job_id:
        return data.get("state")
    return None


def is_listening(port: int, timeout: float = 2.0) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


def process_alive(pid: Any) -> bool | None:
    """Whether a process with this id runs; None when that cannot be told."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    return True


def _gone_hint(state: dict[str, Any]) -> str | None:
    """Explanation when Toolbag's process from plugin.json no longer runs."""
    pid = state.get("pid")
    if process_alive(pid) is False:
        return ("Toolbag (process %s) is no longer running: it was closed or crashed. A crash "
                "is recorded in the Windows event log (Application, source 'Application "
                "Error'); Toolbag empties its own log.txt at the next start. Start Toolbag with "
                "the plugin again." % pid)
    return None


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
        port, token, state = self.endpoint()
        if token is None:
            raise PluginUnavailable(NOT_RUNNING_HINT)
        if not is_listening(port):
            raise PluginUnavailable(_gone_hint(state) or (
                "Nothing accepts connections on 127.0.0.1:%d. Toolbag may be busy (loading or "
                "saving a large scene freezes the plugin, and waiting requests then fill the "
                "port's queue): wait and retry. If it stays so, the plugin was stopped or hangs; "
                "restart it, and if the port stays taken, restart Toolbag." % port))
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
            gone = _gone_hint(state)
            if gone:
                raise PluginUnavailable("Toolbag went away during the call. " + gone) from exc
            raise PluginUnavailable("Could not reach the plugin on port %d: %s"
                                    % (port, exc)) from exc

    def holding_focus(self) -> contextlib.AbstractContextManager[None]:
        """``with client.holding_focus(): ...`` keeps Toolbag in front for all calls inside
        (no flashing per call) and hands the focus back once at the end."""
        return focus.holding() if self.use_focus else contextlib.nullcontext()

    def _get(self, path: str, timeout: float) -> dict:
        # Toolbag slows down all plugin threads, the HTTP thread included, while its window is
        # not in front: even a plain GET goes through the focus handling.
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
        job_id = secrets.token_hex(8)
        body = {"action": action, "params": params or {}, "timeout": timeout, "id": job_id}

        def _do():
            return self._request("POST", "/call", body, timeout=timeout + 15)

        payload = (focus.hold_front(_do, lambda: job_state(job_id)) if self.use_focus
                   else _do())
        if not isinstance(payload, dict):
            raise PluginUnavailable("Unexpected answer from the plugin: %r" % (payload,))
        if not payload.get("ok"):
            error = payload.get("error") or {}
            raise PluginError(error.get("type", "Error"), error.get("message", "unknown error"),
                              error.get("traceback"), payload.get("log", ""))
        return payload
