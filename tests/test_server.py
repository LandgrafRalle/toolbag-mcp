"""The MCP layer in-process, with the plugin client replaced: parameters reach the plugin
as actions, results come back to an MCP client, plugin failures become tool errors."""

from __future__ import annotations

import json

import anyio
from mcp import Client

from toolbag_mcp import server as srv
from toolbag_mcp.client import PluginError, PluginUnavailable


def _call_tool(name, arguments):
    async def go():
        async with Client(srv.server) as client:
            return await client.call_tool(name, arguments)
    return anyio.run(go)


def _payload(result):
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_result_reaches_the_client(monkeypatch):
    calls = []

    def fake_call(action, params=None, timeout=60.0):
        calls.append((action, params, timeout))
        return {"ok": True, "result": {"toolbag_version": 5022, "objects": 4}, "log": "", "ms": 1}

    monkeypatch.setattr(srv.client, "call", fake_call)
    result = _call_tool("status", {})
    assert result.is_error is False
    assert calls == [("status", None, srv.SHORT)]
    assert _payload(result)["toolbag_version"] == 5022
    # dict[str, Any] return annotations give clients structured content; a bare dict does not
    assert result.structured_content == {"toolbag_version": 5022, "objects": 4}


def test_parameters_pass_through_and_omitted_ones_are_dropped(monkeypatch):
    calls = []

    def fake_call(action, params=None, timeout=60.0):
        calls.append((action, params, timeout))
        return {"ok": True, "result": {"groups": {}}, "log": "", "ms": 0}

    monkeypatch.setattr(srv.client, "call", fake_call)
    _call_tool("baker_set_cage", {"groups": "Part", "estimate": True})
    _call_tool("baker_bake", {})
    _call_tool("scene_load", {"path": "C:/x/scene.tbscene"})
    assert calls[0] == ("baker.set_cage", {"groups": "Part", "estimate": True}, srv.SHORT)
    assert calls[1] == ("baker.bake", {}, srv.LONG)
    assert calls[2] == ("scene.load", {"path": "C:/x/scene.tbscene",
                                       "download_missing_library_assets": False}, srv.LONG)


def test_plugin_failure_becomes_a_tool_error(monkeypatch):
    def failing(action, params=None, timeout=60.0):
        raise PluginError("ActionError", "No bake project 'x'", None, "")

    monkeypatch.setattr(srv.client, "call", failing)
    result = _call_tool("baker_get", {"baker": "x"})
    assert result.is_error is True
    assert "No bake project 'x'" in result.content[0].text


def test_missing_plugin_is_explained(monkeypatch):
    def down(action, params=None, timeout=60.0):
        raise PluginUnavailable("The Toolbag MCP plugin is not running")

    monkeypatch.setattr(srv.client, "call", down)
    result = _call_tool("scene_info", {})
    assert result.is_error is True
    assert "not running" in result.content[0].text


def test_python_log_is_returned(monkeypatch):
    def fake_call(action, params=None, timeout=60.0):
        return {"ok": True, "result": {"result": 4}, "log": "hello\n", "ms": 0}

    monkeypatch.setattr(srv.client, "call", fake_call)
    payload = _payload(_call_tool("execute_python", {"code": "print('hello'); result = 4"}))
    assert payload == {"result": {"result": 4}, "log": "hello\n"}


def test_tool_catalogue_and_annotations():
    async def go():
        async with Client(srv.server) as client:
            listed = await client.list_tools()
            return getattr(listed, "tools", listed)

    tools = anyio.run(go)
    names = {t.name for t in tools}
    assert len(names) == 61
    assert {"status", "scene_load", "baker_bake", "material_set_cutout", "execute_python",
            "toolbag_quit"} <= names

    def hint(tool, snake, camel):
        annotations = tool.annotations
        assert annotations is not None, tool.name
        return getattr(annotations, snake if hasattr(annotations, snake) else camel)

    by_name = {t.name: t for t in tools}
    assert hint(by_name["status"], "read_only_hint", "readOnlyHint") is True
    assert hint(by_name["toolbag_quit"], "destructive_hint", "destructiveHint") is True
    assert hint(by_name["baker_bake"], "destructive_hint", "destructiveHint") is False
    for tool in tools:
        assert tool.description, tool.name
