"""Bring Toolbag's window forward while a call waits for it, without getting in the user's
way (Windows only).

Toolbag runs plugin code at full speed only while its window has focus ("Toolbag only
performs updates regularly when the app has focus", official docs). In the background a
visible Toolbag still answers, but only every few seconds; minimized or covered it may not
answer at all. So the client brings Toolbag forward while a call waits, with three limits:

* Only when the user has not typed or moved the mouse for ``IDLE_SECONDS``. While they
  work in another window the call waits (and often completes in the background anyway);
  their keystrokes never land in Toolbag because of a call.
* Only until the action runs. The plugin reports a running action in a small file
  (``job_state``); an action that runs longer than ``RELEASE_SECONDS`` keeps running
  without focus, so the focus goes back to where it was. If the answer then does not
  arrive within ``REPLY_SECONDS`` after the action finished, Toolbag is brought forward
  again (still only when the user is idle).
* The focus goes back only if Toolbag is still in front: a window the user picked during
  the call stays in front.

``holding()`` keeps Toolbag in front across several calls (scripts with many calls would
otherwise flash it for every call) and hands the focus back once at the end.

The window is found by process name (toolbag.exe), not by title: browser tabs can carry the
same title. SetForegroundWindow is refused while the user works in another window; attaching
to that window's input thread plus a short Alt press usually convinces Windows, and
WScript.Shell.AppActivate is the fallback that has always worked. The Alt press counts as
input for Windows, so the idle check leaves it out.

Disable with the environment variable TOOLBAG_MCP_NO_FOCUS=1 (calls then wait for Toolbag in
the background). On other platforms the functions are no-ops.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

IS_WINDOWS = sys.platform == "win32"
PROCESS_NAME = "toolbag.exe"
TITLE_FRAGMENT = "Marmoset Toolbag"

IDLE_SECONDS = 1.5     # no keyboard or mouse input for this long before Toolbag is brought forward
RELEASE_SECONDS = 0.5  # an action running this long gets along without focus: hand it back
REPLY_SECONDS = 3.0    # after the action finished, wait this long for the answer in the background
RETRY_SECONDS = 2.0    # a refused switch is tried again at most this often
POLL_SECONDS = 0.1

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    for _name in ("SetForegroundWindow", "BringWindowToTop", "IsIconic", "IsWindowVisible"):
        getattr(_user32, _name).argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.GetTickCount.restype = wintypes.DWORD

    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LastInputInfo)]

    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _SW_RESTORE = 9
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# GetTickCount window of our own Alt press and the user's last input before it.
_own_input = (0, 0, 0)
_holds = 0
_holds_lock = threading.Lock()


def available() -> bool:
    return IS_WINDOWS


def process_name(hwnd: int) -> str:
    """Executable name (lower case) of the process owning a window, '' if unknown."""
    if not IS_WINDOWS:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(handle)


def find_toolbag_windows() -> list[int]:
    """Handles of all visible Toolbag main windows (one per running instance)."""
    if not IS_WINDOWS:
        return []
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):
        buffer = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, buffer, 256)
        if (TITLE_FRAGMENT in buffer.value and _user32.IsWindowVisible(hwnd)
                and process_name(hwnd) == PROCESS_NAME):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_visit, 0)
    return found


def find_toolbag() -> int | None:
    """Handle of Toolbag's main window, None when Toolbag is not running."""
    windows = find_toolbag_windows()
    return windows[0] if windows else None


def foreground() -> int | None:
    return _user32.GetForegroundWindow() if IS_WINDOWS else None


def _last_input_tick() -> int | None:
    info = _LastInputInfo(ctypes.sizeof(_LastInputInfo))
    return info.dwTime if _user32.GetLastInputInfo(ctypes.byref(info)) else None


def idle_seconds() -> float:
    """Seconds since the user's last keyboard or mouse input; the Alt press of ``to_front``
    does not count. Infinite off Windows or when Windows does not tell."""
    if not IS_WINDOWS:
        return float("inf")
    last = _last_input_tick()
    if last is None:
        return float("inf")
    before, after, real = _own_input
    if before <= last <= after:
        last = real
    return ((_kernel32.GetTickCount() - last) & 0xFFFFFFFF) / 1000.0


def to_front(hwnd: int) -> bool:
    """Bring a window to the foreground; True when it is in front afterwards."""
    global _own_input
    if not IS_WINDOWS or not hwnd:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    me = _kernel32.GetCurrentThreadId()
    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    attached = bool(fg_thread and fg_thread != me
                    and _user32.AttachThreadInput(me, fg_thread, True))
    real = _last_input_tick() or 0
    before = _kernel32.GetTickCount()
    try:
        _user32.keybd_event(_VK_MENU, 0, 0, 0)
        _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    finally:
        _own_input = (before, _kernel32.GetTickCount(), real)
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)
    if _user32.GetForegroundWindow() == hwnd:
        return True
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(New-Object -ComObject WScript.Shell).AppActivate(%d)" % pid.value],
            capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return _user32.GetForegroundWindow() == hwnd


def _give_back(toolbag: int, previous: int | None) -> None:
    """Return the focus to ``previous``, but only while Toolbag is still in front: a window
    the user switched to in the meantime keeps it."""
    if previous and previous != toolbag and foreground() == toolbag:
        to_front(previous)


@contextlib.contextmanager
def holding() -> Iterator[None]:
    """Keep Toolbag in front across several calls (no flashing per call); the window that was
    in front before gets the focus back at the end. Calls still bring Toolbag forward only
    while the user is idle. Nests; off Windows it does nothing."""
    global _holds
    previous = foreground()
    with _holds_lock:
        _holds += 1
    try:
        yield
    finally:
        with _holds_lock:
            _holds -= 1
            last = _holds == 0
        toolbag = find_toolbag() if last and IS_WINDOWS else None
        if toolbag:
            _give_back(toolbag, previous)


def hold_front(work: Callable[[], Any], job_state: Callable[[], str | None] | None = None,
               poll_seconds: float = POLL_SECONDS) -> Any:
    """Run ``work()`` in a worker thread and bring Toolbag forward while it waits for Toolbag
    (see the module docstring for when). ``job_state`` returns None, "running" or "done" for
    this call's action as the plugin reports it. Returns the work's result, re-raises its
    exception. Without Toolbag's window (or off Windows) it just runs the work."""
    if not IS_WINDOWS:
        return work()
    toolbag = find_toolbag()
    if not toolbag:
        return work()
    previous = foreground()
    box: dict[str, Any] = {}

    def _run():
        try:
            box["result"] = work()
        except BaseException as exc:  # noqa: BLE001  (re-raised in the caller's thread)
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True, name="toolbag-mcp-call")
    worker.start()
    took = False          # this call brought Toolbag forward and still owes the focus back
    running_since = done_since = None
    last_try = float("-inf")
    try:
        while worker.is_alive():
            state = job_state() if job_state else None
            now = time.monotonic()
            if state == "running" and running_since is None:
                running_since = now
            if state == "done" and done_since is None:
                done_since = now
            held = _holds > 0
            if (took and not held and running_since is not None and done_since is None
                    and now - running_since >= RELEASE_SECONDS):
                _give_back(toolbag, previous)
                took = False
            if held or state is None:
                wanted = True
            elif state == "done":
                wanted = now - done_since >= REPLY_SECONDS
            else:
                wanted = False
            if (wanted and now - last_try >= RETRY_SECONDS and foreground() != toolbag
                    and idle_seconds() >= IDLE_SECONDS):
                last_try = now
                took = to_front(toolbag) or took
            worker.join(poll_seconds)
    finally:
        if took and _holds == 0:
            _give_back(toolbag, previous)
    if "error" in box:
        raise box["error"]
    return box["result"]
