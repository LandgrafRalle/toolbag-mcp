"""Focus handling (toolbag_mcp/focus.py) against a simulated desktop, and the client's view of
the plugin's job file and of a Toolbag that went away. No Windows API is touched, so this
runs on every platform."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from toolbag_mcp import client, focus

TOOLBAG, EDITOR = 1, 2


class Desktop:
    """Foreground window, the user's idle time and a log of switches."""

    def __init__(self, monkeypatch, idle=10.0):
        self.fg = EDITOR
        self.idle = idle
        self.switches = []
        monkeypatch.setattr(focus, "IS_WINDOWS", True)
        monkeypatch.setattr(focus, "find_toolbag", lambda: TOOLBAG)
        monkeypatch.setattr(focus, "foreground", lambda: self.fg)
        monkeypatch.setattr(focus, "idle_seconds", lambda: self.idle)
        monkeypatch.setattr(focus, "to_front", self.to_front)
        monkeypatch.setattr(focus, "POLL_SECONDS", 0.01)
        monkeypatch.setattr(focus, "RELEASE_SECONDS", 0.05)
        monkeypatch.setattr(focus, "REPLY_SECONDS", 0.1)
        monkeypatch.setattr(focus, "RETRY_SECONDS", 0.0)

    def to_front(self, hwnd):
        self.switches.append(hwnd)
        self.fg = hwnd
        return True


def _job(states, result="ok"):
    """Work that walks through job states (seconds, state) and the matching job_state."""
    box = {"state": None}

    def work():
        for seconds, state in states:
            time.sleep(seconds)
            box["state"] = state
        return result

    return work, lambda: box["state"]


def _call(work, job_state=None):
    return focus.hold_front(work, job_state, poll_seconds=0.01)


def test_idle_user_gets_toolbag_and_the_focus_back(monkeypatch):
    desk = Desktop(monkeypatch)
    work, state = _job([(0.05, "running"), (0.01, "done"), (0.01, "done")])
    assert _call(work, state) == "ok"
    assert desk.switches == [TOOLBAG, EDITOR] and desk.fg == EDITOR


def test_typing_user_is_never_interrupted(monkeypatch):
    desk = Desktop(monkeypatch, idle=0.2)
    work, state = _job([(0.1, "running"), (0.3, "done"), (0.3, None)])
    assert _call(work, state) == "ok"
    assert desk.switches == [] and desk.fg == EDITOR


def test_waits_for_a_pause_in_typing(monkeypatch):
    desk = Desktop(monkeypatch, idle=0.0)
    work, state = _job([(0.3, None)])
    timer = threading.Timer(0.1, lambda: setattr(desk, "idle", 5.0))
    timer.start()
    assert _call(work, state) == "ok"
    timer.join()
    assert desk.switches == [TOOLBAG, EDITOR]


def test_long_action_hands_the_focus_back_while_it_runs(monkeypatch):
    desk = Desktop(monkeypatch)
    seen = []

    def work():
        time.sleep(0.05)
        box["state"] = "running"
        time.sleep(0.3)            # a bake: focus must be back long before it ends
        seen.append(desk.fg)
        box["state"] = "done"
        return "baked"

    box = {"state": None}
    assert _call(work, lambda: box["state"]) == "baked"
    assert seen == [EDITOR] and desk.switches == [TOOLBAG, EDITOR]


def test_slow_answer_after_the_action_takes_toolbag_again(monkeypatch):
    desk = Desktop(monkeypatch)
    work, state = _job([(0.02, "running"), (0.2, "done"), (0.4, "done")])
    assert _call(work, state) == "ok"
    assert desk.switches == [TOOLBAG, EDITOR, TOOLBAG, EDITOR]


def test_user_switching_away_keeps_the_window_they_chose(monkeypatch):
    desk = Desktop(monkeypatch)
    other = 3

    def work():
        time.sleep(0.05)
        desk.fg, desk.idle = other, 0.0   # the user clicks into a third window and types
        time.sleep(0.1)
        return "ok"

    assert _call(work) == "ok"
    assert desk.switches == [TOOLBAG] and desk.fg == other


def test_holding_keeps_toolbag_in_front_across_calls(monkeypatch):
    desk = Desktop(monkeypatch)
    with focus.holding():
        for _ in range(3):
            work, state = _job([(0.02, "running"), (0.1, "done"), (0.01, "done")])
            assert _call(work, state) == "ok"
            assert desk.fg == TOOLBAG
    assert desk.switches == [TOOLBAG, EDITOR] and desk.fg == EDITOR


def test_errors_reach_the_caller(monkeypatch):
    Desktop(monkeypatch)

    def work():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _call(work)


def test_job_state_reads_only_its_own_job(monkeypatch, tmp_path):
    job_file = tmp_path / "job.json"
    monkeypatch.setattr(client, "JOB_FILE", str(job_file))
    assert client.job_state("a") is None
    job_file.write_text(json.dumps({"id": "a", "state": "running"}))
    assert client.job_state("a") == "running" and client.job_state("b") is None
    job_file.write_text("{broken")
    assert client.job_state("a") is None


def test_dead_toolbag_is_named_as_such(monkeypatch, tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert client.process_alive(os.getpid()) is True
    assert client.process_alive(finished.pid) is False
    assert client.process_alive(None) is None
    state_file = tmp_path / "plugin.json"
    state_file.write_text(json.dumps({"token": "t", "port": 1, "pid": finished.pid}))
    monkeypatch.setattr(client, "STATE_FILE", str(state_file))
    with pytest.raises(client.PluginUnavailable, match="no longer running"):
        client.ToolbagClient(use_focus=False).status()
    state_file.write_text(json.dumps({"token": "t", "port": 1, "pid": os.getpid()}))
    with pytest.raises(client.PluginUnavailable, match="busy"):
        client.ToolbagClient(use_focus=False).status()
