"""Native Windows input; only the selected, foreground Minecraft process is controlled."""

import ctypes
import os
import time
from ctypes import wintypes as w
from pathlib import Path

KEYS = {
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "backspace": 0x08,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "f1": 0x70,
    "f2": 0x71,
    "f5": 0x74,
}
ALLOWED_EXES = {"minecraft.windows.exe"}


class DesktopUnavailable(RuntimeError):
    pass


class Desktop:
    def __init__(self, stop_file, hwnd=None):
        if os.name != "nt":
            raise DesktopUnavailable(
                "Real-client control requires Windows 10/11 with Minecraft Bedrock installed"
            )
        self.stop_file = Path(stop_file)
        self.hwnd = hwnd
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.version = ctypes.WinDLL("version", use_last_error=True)
        self.enum_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": ([self.enum_type, w.LPARAM], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetForegroundWindow": ([], w.HWND),
            "SetForegroundWindow": ([w.HWND], w.BOOL),
            "ShowWindow": ([w.HWND, ctypes.c_int], w.BOOL),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "SetCursorPos": ([ctypes.c_int, ctypes.c_int], w.BOOL),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
            "MapVirtualKeyW": ([w.UINT, w.UINT], w.UINT),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.user, name)
            fn.argtypes, fn.restype = args, result
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            w.HANDLE,
            w.DWORD,
            w.LPWSTR,
            ctypes.POINTER(w.DWORD),
        ]
        self.kernel.QueryFullProcessImageNameW.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.version.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.DWORD)]
        self.version.GetFileVersionInfoSizeW.restype = w.DWORD
        self.version.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID]
        self.version.GetFileVersionInfoW.restype = w.BOOL
        self.version.VerQueryValueW.argtypes = [
            w.LPCVOID,
            w.LPCWSTR,
            ctypes.POINTER(w.LPVOID),
            ctypes.POINTER(w.UINT),
        ]
        self.version.VerQueryValueW.restype = w.BOOL
        # DPI awareness makes screenshot and input coordinates agree on scaled displays.
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            self.user.SetProcessDPIAware()

        class Mouse(ctypes.Structure):
            _fields_ = [
                ("dx", w.LONG),
                ("dy", w.LONG),
                ("data", w.DWORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Keyboard(ctypes.Structure):
            _fields_ = [
                ("vk", w.WORD),
                ("scan", w.WORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Union(ctypes.Union):
            _fields_ = [("mouse", Mouse), ("key", Keyboard)]

        class Input(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", w.DWORD), ("value", Union)]

        self.Input, self.Mouse, self.Keyboard = Input, Mouse, Keyboard
        self.user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = w.UINT

    def process_path(self, hwnd):
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(len(buffer))
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        finally:
            self.kernel.CloseHandle(handle)
        return None

    def file_version(self, path):
        unused = w.DWORD()
        size = self.version.GetFileVersionInfoSizeW(path, ctypes.byref(unused))
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not self.version.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        pointer, length = w.LPVOID(), w.UINT()
        if not self.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        values = ctypes.cast(pointer, ctypes.POINTER(w.DWORD))
        if length.value < 16 or values[0] != 0xFEEF04BD:
            return None
        return f"{values[2] >> 16}.{values[2] & 65535}.{values[3] >> 16}.{values[3] & 65535}"

    def windows(self):
        result = []

        @self.enum_type
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                path = self.process_path(hwnd)
                if path and Path(path).name.lower() in ALLOWED_EXES:
                    title = ctypes.create_unicode_buffer(512)
                    self.user.GetWindowTextW(hwnd, title, len(title))
                    result.append(
                        {
                            "hwnd": hwnd,
                            "title": title.value,
                            "exe": path,
                            "file_version": self.file_version(path),
                        }
                    )
            return True

        self.user.EnumWindows(visit, 0)
        return result

    def select(self, hwnd=None):
        windows = self.windows()
        if hwnd is not None:
            windows = [item for item in windows if item["hwnd"] == hwnd]
        if len(windows) != 1:
            raise DesktopUnavailable("Open one Minecraft window, or select an exact hwnd from list_windows")
        self.hwnd = windows[0]["hwnd"]
        return windows[0]

    def guard(self, foreground=True):
        if self.stop_file.exists() or self.user.GetAsyncKeyState(0x77) & 0x8000:  # F8
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.touch()
            raise DesktopUnavailable("Stopped. Remove the STOP file only when ready to resume")
        path = self.process_path(self.hwnd) if self.hwnd else None
        if not path or Path(path).name.lower() not in ALLOWED_EXES:
            raise DesktopUnavailable("Selected Minecraft process is no longer available")
        if foreground and (self.user.GetForegroundWindow() != self.hwnd or self.user.IsIconic(self.hwnd)):
            raise DesktopUnavailable("Minecraft must be visible and in the foreground; input stopped")

    def focus(self):
        self.guard(False)
        self.user.ShowWindow(self.hwnd, 9)
        self.user.SetForegroundWindow(self.hwnd)
        time.sleep(0.2)
        self.guard()

    def wait(self, seconds):
        if not 0 <= seconds <= 15:
            raise ValueError("Wait must be between 0 and 15 seconds")
        deadline = time.monotonic() + seconds
        self.guard()
        while time.monotonic() < deadline:
            self.guard()
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))

    def send(self, event):
        if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(self.Input)) != 1:
            raise DesktopUnavailable(
                "Windows rejected input; run the tester and game at the same privilege level"
            )

    def key_event(self, key, up=False):
        scan = self.user.MapVirtualKeyW(KEYS[key], 0)
        flags = 0x0008 | (0x0002 if up else 0) | (1 if key in {"left", "right", "up", "down"} else 0)
        value = self.Input(type=1)
        value.key = self.Keyboard(0, scan, flags, 0, 0)
        self.send(value)

    def press(self, keys, seconds=0.1):
        if not keys or len(keys) > 4 or len(set(keys)) != len(keys) or any(k not in KEYS for k in keys):
            raise ValueError("Use 1–4 distinct keys: " + ", ".join(KEYS))
        if "alt" in keys or ("ctrl" in keys and "esc" in keys):
            raise ValueError("OS-level shortcuts are not supported")
        if not 0.02 <= seconds <= 10:
            raise ValueError("Key hold must be 0.02–10 seconds")
        self.guard()
        held = []
        try:
            for key in keys:
                self.guard()
                self.key_event(key)
                held.append(key)
            self.wait(seconds)
        finally:
            for key in reversed(held):
                self.key_event(key, up=True)

    def type_text(self, text):
        if len(text) > 512 or any(ord(c) < 32 for c in text):
            raise ValueError("Text must be one line of at most 512 characters")
        for i in range(0, len(text.encode("utf-16-le")), 2):
            self.guard()
            code = int.from_bytes(text.encode("utf-16-le")[i : i + 2], "little")
            for flags in (0x0004, 0x0004 | 0x0002):
                value = self.Input(type=1)
                value.key = self.Keyboard(0, code, flags, 0, 0)
                self.send(value)

    def bounds(self):
        self.guard()
        rect, point = w.RECT(), w.POINT(0, 0)
        if not self.user.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise DesktopUnavailable("Cannot read game window rectangle")
        if not self.user.ClientToScreen(self.hwnd, ctypes.byref(point)):
            raise DesktopUnavailable("Cannot resolve game screen coordinates")
        if rect.right <= 0 or rect.bottom <= 0:
            raise DesktopUnavailable("Game window has no visible client area")
        return point.x, point.y, point.x + rect.right, point.y + rect.bottom

    def mouse_event(self, flags, dx=0, dy=0, data=0):
        value = self.Input(type=0)
        value.mouse = self.Mouse(dx, dy, data, flags, 0, 0)
        self.send(value)

    def click(self, button="left", x=None, y=None, seconds=0.1):
        if button not in {"left", "right", "middle"} or not 0.02 <= seconds <= 10:
            raise ValueError("Invalid mouse button or hold duration")
        if (x is None) != (y is None):
            raise ValueError("Supply both x and y, or neither")
        self.guard()
        if x is not None:
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("Menu click coordinates must be normalized between 0 and 1")
            left, top, right, bottom = self.bounds()
            if not self.user.SetCursorPos(
                left + round(x * (right - left - 1)), top + round(y * (bottom - top - 1))
            ):
                raise DesktopUnavailable("Cannot move the cursor")
        down, up = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}[button]
        self.guard()
        try:
            self.mouse_event(down)
            self.wait(seconds)
        finally:
            self.mouse_event(up)

    def look(self, dx, dy):
        if not (-2000 <= dx <= 2000 and -2000 <= dy <= 2000):
            raise ValueError("Relative look limited to 2000 input units per axis")
        self.guard()
        self.mouse_event(0x0001, int(dx), int(dy))

    def scroll(self, notches):
        if type(notches) is not int or not -10 <= notches <= 10:
            raise ValueError("Scroll must be an integer between -10 and 10")
        self.guard()
        self.mouse_event(0x0800, data=(notches * 120) & 0xFFFFFFFF)

    def capture(self, path):
        from PIL import ImageGrab

        bounds = self.bounds()
        ImageGrab.grab(bbox=bounds, all_screens=True).save(path)
        self.guard()  # A focus change during capture invalidates the evidence.
        return {"path": str(path), "bounds": bounds, "hwnd": self.hwnd}


def launch():
    if os.name != "nt":
        raise DesktopUnavailable("Launching Bedrock requires Windows")
    os.startfile("minecraft://")
