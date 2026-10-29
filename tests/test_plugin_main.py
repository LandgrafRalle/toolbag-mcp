"""The plugin core (plugin/__main__.py) against the fake Toolbag: it starts its HTTP server
on a free port, writes its state file into a temporary home, answers only with the right
headers, runs actions on the (simulated) main thread, performs scene operations and quitting
two-phased, and cleans up on shutdown."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import fake_mset
import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOOLBAG_MCP_PORT", str(_free_port()))
    fake = fake_mset.build()
    fake.newScene()
    monkeypatch.setitem(sys.modules, "mset", fake)
    monkeypatch.delitem(sys.modules, "tbmcp_actions", raising=False)
    spec = importlib.util.spec_from_file_location("tbmcp_plugin_under_test",
                                                  PLUGIN_DIR / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fake = fake
    module.home = home
    yield module
    module._stop_server()


def _request(plugin, method, path, body=None, headers=None, token=None):
    state = json.loads((plugin.home / ".toolbag-mcp" / "plugin.json").read_text())
    base = {"X-Toolbag-MCP": "1", "X-Toolbag-MCP-Token": token or state["token"],
            "Content-Type": "application/json"}
    base.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (state["port"], path), data=data,
                                     method=method, headers=base)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _call(plugin, action, params=None, ticks=3, timeout=5):
    """POST /call in a thread while the test thread plays Toolbag's periodic callback."""
    box = {}

    def _post():
        box["result"] = _request(plugin, "POST", "/call",
                                 {"action": action, "params": params or {}, "timeout": timeout})

    worker = threading.Thread(target=_post)
    worker.start()
    for _ in range(ticks):
        worker.join(0.2)
        plugin.fake.callbacks.onPeriodicUpdate()
    worker.join(timeout)
    return box["result"]


def test_state_file_status_and_catalogue(plugin):
    state_file = plugin.home / ".toolbag-mcp" / "plugin.json"
    state = json.loads(state_file.read_text())
    assert state["port"] == plugin._port and len(state["token"]) > 30
    assert plugin.fake.callbacks.onPeriodicUpdate is plugin._drain
    assert plugin.fake.callbacks.onFrameUpdate is None
    status, payload = _request(plugin, "GET", "/")
    assert status == 200 and payload["server"] == "toolbag-mcp" and payload["calls"] == 0
    status, payload = _request(plugin, "GET", "/actions")
    assert status == 200 and "scene.info" in payload["actions"]


def test_requests_without_the_right_headers_are_refused(plugin):
    assert _request(plugin, "GET", "/", token="wrong")[0] == 403
    assert _request(plugin, "GET", "/", headers={"X-Toolbag-MCP": "0"})[0] == 403
    assert _request(plugin, "GET", "/", headers={"Host": "evil.example:1"})[0] == 403
    assert _request(plugin, "GET", "/nothing")[0] == 404
    status, payload = _request(plugin, "POST", "/call", {"params": {}})
    assert status == 400 and payload["error"]["type"] == "BadRequest"


def test_actions_run_on_the_callback_and_errors_come_back(plugin):
    status, payload = _call(plugin, "scene.info")
    assert status == 200 and payload["ok"] is True and payload["result"]["objects"] == 4
    assert payload["log"] == "" and payload["ms"] >= 0
    status, payload = _call(plugin, "object.get", {"ref": "Nobody"})
    assert payload["ok"] is False and payload["error"]["type"] == "ActionError"
    status, payload = _call(plugin, "scene.explode")
    assert "Unknown action" in payload["error"]["message"]
    assert plugin._calls == 3 and plugin.STATE["calls"] == 3


def test_timeout_when_nobody_drains(plugin):
    status, payload = _request(plugin, "POST", "/call",
                               {"action": "scene.info", "params": {}, "timeout": 0.3})
    assert status == 200 and payload["ok"] is False and payload["error"]["type"] == "Timeout"


def test_scene_operations_take_two_ticks(plugin, tmp_path):
    box = {}

    def _post():
        box["result"] = _request(plugin, "POST", "/call",
                                 {"action": "scene.new", "params": {}, "timeout": 5})

    worker = threading.Thread(target=_post)
    worker.start()
    worker.join(0.3)
    plugin._drain()               # tick 1: only mset.newScene() runs
    assert worker.is_alive() and plugin._scene_job is not None
    plugin._drain()               # tick 2: scene.info is reported
    worker.join(5)
    status, payload = box["result"]
    assert payload["ok"] is True and payload["result"]["objects"] == 4
    status, payload = _call(plugin, "scene.load", {"path": str(tmp_path / "missing.tbscene")})
    assert payload["ok"] is False and "File not found" in payload["error"]["message"]


def test_python_switch_and_quit(plugin):
    status, payload = _call(plugin, "python.exec", {"code": "result = 1"})
    assert payload["ok"] is False and "disabled" in payload["error"]["message"]
    plugin._chk_python.value = True
    plugin._on_allow_change()
    status, payload = _call(plugin, "python.exec", {"code": "print('hi'); result = 2 + 2"})
    assert payload["ok"] is True and payload["result"] == {"result": 4} and payload["log"] == "hi\n"
    status, payload = _call(plugin, "toolbag.quit", ticks=1)
    assert payload["ok"] is True and payload["result"] == {"quitting": True}
    assert plugin.fake._state["quit"] is None
    plugin._drain()               # the next tick quits
    assert plugin.fake._state["quit"] == 0
    assert not (plugin.home / ".toolbag-mcp" / "plugin.json").exists()
