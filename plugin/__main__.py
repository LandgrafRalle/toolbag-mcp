"""Toolbag MCP plugin: runs inside Marmoset Toolbag and executes named actions for the
MCP server that runs outside (package ``toolbag_mcp``).

How it works
------------
* An HTTP server listens on 127.0.0.1:<port> (default 18770) in a background thread.
* Requests are queued; ``mset.callbacks.onPeriodicUpdate`` drains the queue on Toolbag's
  main thread, because ``mset`` is not thread safe. Scene operations (new, load) run as the
  last thing of a tick and report in the next one: Toolbag 5.02 crashes otherwise.
* Toolbag only runs these callbacks while its window has focus. A request then simply
  waits; the MCP server brings Toolbag to the front for the duration of a call.
* Every request must carry ``X-Toolbag-MCP: 1``, ``X-Toolbag-MCP-Token: <token>`` and a
  ``Host`` of 127.0.0.1 or localhost. Browsers cannot send custom headers without a CORS
  preflight, and this server answers no preflight, so web pages cannot reach it.
* The token is generated per start and written, together with the port, to
  ``~/.toolbag-mcp/plugin.json`` (never under %LOCALAPPDATA%, which packaged apps see
  redirected). The file is removed when the plugin stops.
* Free Python (action ``python.exec``) only runs while the checkbox "Allow Python
  execution" in the plugin window is ticked. It is off at every start and not persisted.

Endpoints
---------
``GET /``          plugin status (no scene access, safe from any thread)
``GET /actions``   catalogue of actions with parameters and docstrings
``POST /call``     ``{"action": "scene.info", "params": {...}, "timeout": 60}`` ->
                   ``{"ok": true, "result": ..., "log": "<stdout>", "ms": 12}`` or
                   ``{"ok": false, "error": {"type", "message", "traceback"}, ...}``

Start: Plugins -> Toolbag MCP, or ``toolbag.exe "<this file>"``. Stop: button in the window.
Never run it next to another plugin that sets ``onPeriodicUpdate``: both stop working.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import queue
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mset

PLUGIN_VERSION = "0.1.0"
DEFAULT_PORT = 18770
HEADER_MARK = "X-Toolbag-MCP"
HEADER_TOKEN = "X-Toolbag-MCP-Token"
STATE_DIR = os.path.join(os.path.expanduser("~"), ".toolbag-mcp")
STATE_FILE = os.path.join(STATE_DIR, "plugin.json")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
MAX_BODY = 1 << 20  # 1 MiB of JSON is plenty for parameters
LOG_PREFIX = "[Toolbag MCP] "


def _plugin_dir():
    """Folder of this file: Toolbag reports the running plugin's path, ``__file__`` is the
    fallback when the plugin is started as a script argument."""
    path = None
    try:
        path = mset.getPluginPath()
    except Exception:
        pass
    if not path:
        path = globals().get("__file__")
    if path and os.path.isfile(path):
        return os.path.dirname(os.path.abspath(path))
    if path and os.path.isdir(path):
        return os.path.abspath(path)
    return os.getcwd()


_HERE = _plugin_dir()
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import tbmcp_actions  # noqa: E402  (lives next to this file)

# A plugin restarted inside the same Toolbag session would otherwise keep the old module.
importlib.reload(tbmcp_actions)


def _read_port():
    """Port from TOOLBAG_MCP_PORT, else ~/.toolbag-mcp/config.json, else the default."""
    env = os.environ.get("TOOLBAG_MCP_PORT")
    if env and env.isdigit():
        return int(env)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as handle:
            port = int(json.load(handle).get("port", DEFAULT_PORT))
            if 1024 <= port <= 65535:
                return port
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_PORT


# ---------------------------------------------------------------- state and job queue

class _Job(object):
    __slots__ = ("action", "params", "done", "result", "started")

    def __init__(self, action, params):
        self.action = action
        self.params = params
        self.done = threading.Event()
        self.result = None
        self.started = time.time()


_jobs = queue.Queue()
_server = None
_token = secrets.token_urlsafe(32)
_port = _read_port()
_calls = 0
_toolbag_version = mset.getToolbagVersion()  # read once on the main thread, served by GET /
STATE = tbmcp_actions.STATE
STATE.update({
    "plugin_version": PLUGIN_VERSION,
    "port": _port,
    "allow_python": False,
    "calls": 0,
    "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "plugin_dir": _HERE,
})


def _run_job(job):
    """Runs one action on the main thread and wraps the outcome into the response payload."""
    out = io.StringIO()
    started = time.time()
    payload = {"ok": True, "result": None}
    try:
        with contextlib.redirect_stdout(out):
            payload["result"] = tbmcp_actions.run(job.action, job.params)
    except tbmcp_actions.ActionError as exc:
        payload = {"ok": False, "error": {"type": "ActionError", "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001  (every failure must reach the client)
        payload = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc),
                                          "traceback": traceback.format_exc()}}
    payload["log"] = out.getvalue()
    payload["ms"] = int((time.time() - started) * 1000)
    return payload


def _complete(job, payload):
    global _calls
    job.result = payload
    job.done.set()
    _calls += 1
    STATE["calls"] = _calls
    _lbl_calls.text = "Calls: %d" % _calls
    _lbl_last.text = "Last: %s (%d ms%s)" % (
        job.action, payload.get("ms", 0), "" if payload.get("ok") else ", failed")


_scene_job = None  # job whose scene operation ran in the previous tick
_quit_pending = False


def _drain():
    """Main-thread callback: execute every queued job.

    Scene operations (new, load) tear the scene down under the running callback, and
    Toolbag 5.02 crashes (access violation) when more Python runs in that same tick. So the
    Toolbag call is the last thing this tick does; the next tick reports the new scene.
    Quitting works the same way: answer first, quit in the next tick."""
    global _scene_job, _quit_pending
    if _quit_pending:
        _quit_pending = False
        print(LOG_PREFIX + "quitting Toolbag on request")
        _stop_server()
        mset.quit()
        return
    if _scene_job is not None:
        job, _scene_job = _scene_job, None
        payload = _run_job(_Job("scene.info", {}))
        payload["ms"] = int((time.time() - job.started) * 1000)
        _complete(job, payload)
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        if job.action == tbmcp_actions.QUIT_ACTION:
            _complete(job, {"ok": True, "result": {"quitting": True}, "log": "", "ms": 0})
            _quit_pending = True
            return
        if job.action in tbmcp_actions.SCENE_ACTIONS:
            try:
                operation = tbmcp_actions.prepare_scene_op(job.action, job.params)
            except tbmcp_actions.ActionError as exc:
                _complete(job, {"ok": False, "log": "", "ms": 0,
                                "error": {"type": "ActionError", "message": str(exc)}})
                continue
            job.started = time.time()
            _scene_job = job
            operation()
            return
        _complete(job, _run_job(job))


def _fail_pending(message):
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        job.result = {"ok": False, "error": {"type": "PluginStopped", "message": message},
                      "log": "", "ms": 0}
        job.done.set()


def _status():
    return {
        "ok": True,
        "server": "toolbag-mcp",
        "plugin_version": PLUGIN_VERSION,
        "toolbag_version": _toolbag_version,
        "port": _port,
        "pid": os.getpid(),
        "allow_python": bool(STATE.get("allow_python")),
        "calls": _calls,
        "queued": _jobs.qsize(),
        "started": STATE.get("started"),
        "python": sys.version.split()[0],
    }


# ---------------------------------------------------------------- HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "ToolbagMCP/" + PLUGIN_VERSION
    sys_version = ""

    def _authorized(self):
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        token = self.headers.get(HEADER_TOKEN, "")
        return (self.headers.get(HEADER_MARK) == "1"
                and hostname in ("127.0.0.1", "localhost")
                and secrets.compare_digest(token, _token))

    def _send(self, code, data):
        body = json.dumps(data, default=repr).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, kind, message):
        self._send(code, {"ok": False, "error": {"type": kind, "message": message}})

    def do_OPTIONS(self):  # noqa: N802  (http.server naming)
        # No CORS preflight is ever answered: that is what keeps browsers out.
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if not self._authorized():
            return self._error(403, "Forbidden", "missing or wrong headers")
        if self.path == "/":
            return self._send(200, _status())
        if self.path == "/actions":
            return self._send(200, {"ok": True, "actions": tbmcp_actions.describe()})
        return self._error(404, "NotFound", self.path)

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return self._error(403, "Forbidden", "missing or wrong headers")
        if self.path != "/call":
            return self._error(404, "NotFound", self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("body must be 1 byte to %d bytes, got %d" % (MAX_BODY, length))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            action = data["action"]
            params = data.get("params") or {}
            timeout = float(data.get("timeout", 60))
            if not isinstance(action, str) or not isinstance(params, dict):
                raise ValueError("'action' must be a string and 'params' an object")
        except Exception as exc:  # noqa: BLE001
            return self._error(400, "BadRequest", str(exc))
        if _server is None:
            return self._error(503, "PluginStopped", "the plugin is shutting down")
        job = _Job(action, params)
        _jobs.put(job)
        if job.done.wait(timeout):
            return self._send(200, job.result)
        return self._send(200, {
            "ok": False, "log": "", "ms": int(timeout * 1000),
            "error": {"type": "Timeout", "message": (
                "No result after %.0f s. Toolbag only runs plugin code while its window has "
                "focus; the action still completes later, its result is discarded." % timeout)}})

    def log_message(self, fmt, *args):
        pass  # keep Toolbag's log clean


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # http.server defaults this to True; on Windows that lets a second plugin instance bind
    # the same port silently, and requests then land in whichever instance. Fail loudly.
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # A client that gave up (timeout) closes its socket before the answer is written;
        # socketserver would print a full traceback into Toolbag's log for that.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        print(LOG_PREFIX + "request from %s failed: %r" % (client_address, exc))


# ---------------------------------------------------------------- lifecycle

def _write_state():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"token": _token, "port": _port, "pid": os.getpid(),
                       "plugin_version": PLUGIN_VERSION, "toolbag_version": _toolbag_version,
                       "started": STATE.get("started")}, handle, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        print(LOG_PREFIX + "cannot write %s: %s" % (STATE_FILE, exc))


def _remove_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            mine = json.load(handle).get("pid") == os.getpid()
    except (OSError, ValueError, AttributeError):
        mine = False
    if mine:
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass


def _start_server(port):
    global _server, _port
    srv = _Server(("127.0.0.1", port), _Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                     daemon=True, name="toolbag-mcp-http").start()
    _server = srv
    _port = port
    STATE["port"] = port
    _write_state()
    _lbl_status.text = "Listening on 127.0.0.1:%d" % port
    print(LOG_PREFIX + "listening on 127.0.0.1:%d (plugin %s)" % (port, PLUGIN_VERSION))


def _stop_server():
    global _server
    srv = _server
    _server = None
    _remove_state()
    _fail_pending("the plugin was stopped")
    if srv is not None:
        def _close():
            srv.shutdown()
            srv.server_close()
        threading.Thread(target=_close, daemon=True).start()
        _lbl_status.text = "Stopped"
        print(LOG_PREFIX + "stopped.")


def _on_restart():
    port = int(_port_field.value or 0)
    if not 1024 <= port <= 65535:
        _lbl_status.text = "Port must be between 1024 and 65535"
        return
    _stop_server()
    try:
        _start_server(port)
    except OSError as exc:
        _lbl_status.text = "Cannot listen on port %d: %s" % (port, exc)
        print(LOG_PREFIX + "ERROR: %s" % exc)


def _on_allow_change():
    STATE["allow_python"] = bool(_chk_python.value)
    print(LOG_PREFIX + "Python execution %s" % ("ALLOWED" if _chk_python.value else "blocked"))


def _on_shutdown():
    _stop_server()


def _on_stop_click():
    _stop_server()
    mset.shutdownPlugin()


# ---------------------------------------------------------------- window

_window = mset.UIWindow("Toolbag MCP")
_lbl_status = mset.UILabel("Starting...")
_window.addElement(_lbl_status)
_window.addReturn()

_window.addElement(mset.UILabel("Port"))
_port_field = mset.UITextFieldInt()
_port_field.value = _port
_window.addElement(_port_field)
_btn_restart = mset.UIButton("Restart server")
_btn_restart.onClick = _on_restart
_window.addElement(_btn_restart)
_window.addReturn()

_chk_python = mset.UICheckBox()
_chk_python.label = "Allow Python execution (this session only)"
_chk_python.value = False
_chk_python.onChange = _on_allow_change
_window.addElement(_chk_python)
_window.addReturn()

_lbl_calls = mset.UILabel("Calls: 0")
_window.addElement(_lbl_calls)
_window.addReturn()
_lbl_last = mset.UILabel("Last: -")
_window.addElement(_lbl_last)
_window.addReturn()

_btn_stop = mset.UIButton("Stop")
_btn_stop.onClick = _on_stop_click
_window.addElement(_btn_stop)

# ---------------------------------------------------------------- start

# Only onPeriodicUpdate ("a few times per second"). onFrameUpdate is deliberately not used:
# one callback is enough, and only one plugin may own these callbacks anyway.
mset.callbacks.onPeriodicUpdate = _drain
mset.callbacks.onShutdownPlugin = _on_shutdown

try:
    _start_server(_port)
except OSError as exc:
    _lbl_status.text = "Cannot listen on port %d: %s" % (_port, exc)
    print(LOG_PREFIX + "ERROR: cannot listen on 127.0.0.1:%d: %s" % (_port, exc))
