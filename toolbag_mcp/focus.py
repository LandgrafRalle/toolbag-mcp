"""Keep Toolbag's window in front while a call runs (Windows only).

Toolbag runs plugin callbacks only while its window has focus ("Toolbag only performs
updates regularly when the app has focus", official docs). Without focus a call does not
fail, it simply never returns. So the client brings Toolbag forward for the duration of a
call, again every two seconds if the user clicks elsewhere, and hands focus back afterwards.

The window is found by process name (toolbag.exe), not by title: browser tabs can carry the
same title. SetForegroundWindow is refused while the user works in another window; attaching
to that window's input thread plus a short Alt press usually convinces Windows, and
WScript.Shell.AppActivate is the fallback that has always worked.

Disable with the environment variable TOOLBAG_MCP_NO_FOCUS=1 (then keep Toolbag in front
yourself). On other platforms the functions are no-ops.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

IS_WINDOWS = sys.platform == "win32"
PROCESS_NAME = "toolbag.exe"
TITLE_FRAGMENT = "Marmoset Toolbag"

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

    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _SW_RESTORE = 9
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


def to_front(hwnd: int) -> bool:
    """Bring a window to the foreground; True when it is in front afterwards."""
    if not IS_WINDOWS or not hwnd:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    me = _kernel32.GetCurrentThreadId()
    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    attached = bool(fg_thread and fg_thread != me
                    and _user32.AttachThreadInput(me, fg_thread, True))
    try:
        _user32.keybd_event(_VK_MENU, 0, 0, 0)
        _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    finally:
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


def hold_front(work: Callable[[], Any], poll_seconds: float = 2.0) -> Any:
    """Run ``work()`` in a worker thread while keeping Toolbag in the foreground; afterwards
    the previously active window gets the focus back. Returns the work's result, re-raises
    its exception. Without Toolbag's window (or off Windows) it just runs the work."""
    if not IS_WINDOWS:
        return work()
    previous = foreground()
    toolbag = find_toolbag()
    if not toolbag:
        return work()
    box: dict[str, Any] = {}

    def _run():
        try:
            box["result"] = work()
        except BaseException as exc:  # noqa: BLE001  (re-raised in the caller's thread)
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True, name="toolbag-mcp-call")
    worker.start()
    try:
        while worker.is_alive():
            if foreground() != toolbag:
                to_front(toolbag)
            worker.join(poll_seconds)
    finally:
        if previous and previous != toolbag:
            to_front(previous)
    if "error" in box:
        raise box["error"]
    return box["result"]
