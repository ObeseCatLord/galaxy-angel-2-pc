"""Send bounded test input to a named game window on a task-owned X display."""
import ctypes
from pathlib import Path
import time


class WindowAttributes(ctypes.Structure):
    _fields_ = [(name, kind) for name, kind in (
        ("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
        ("height", ctypes.c_int), ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("window_class", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p))]


def owned_process(pid, expected_pid):
    for _ in range(10):
        if pid == expected_pid:
            return True
        if pid is None or pid <= 1:
            return False
        try:
            status = Path(f"/proc/{pid}/stat").read_text()
            pid = int(status.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def tap_key(display_name, key, expected_pid, hold_ms=100):
    if key.startswith("keyhold:"):  # "keyhold:KEYSYM:MS": a key held longer than a tap
        _, name, ms = key.split(":")
        key, hold_ms = name, int(ms)
        if not 10 <= hold_ms <= 8000:
            raise ValueError("test key hold must be between 10 and 8000 ms")
    elif not 10 <= hold_ms <= 2000:
        raise ValueError("test key hold must be between 10 and 2000 ms")
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    ptr = ctypes.c_void_p
    window = ctypes.c_ulong
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ptr
    x11.XDefaultRootWindow.argtypes = [ptr]
    x11.XDefaultRootWindow.restype = window
    x11.XQueryTree.argtypes = [ptr, window, ctypes.POINTER(window), ctypes.POINTER(window),
                              ctypes.POINTER(ctypes.POINTER(window)), ctypes.POINTER(ctypes.c_uint)]
    x11.XFetchName.argtypes = [ptr, window, ctypes.POINTER(ctypes.c_void_p)]
    x11.XGetWindowAttributes.argtypes = [ptr, window, ctypes.POINTER(WindowAttributes)]
    x11.XInternAtom.argtypes = [ptr, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XGetWindowProperty.argtypes = [ptr, window, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
                                      ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                      ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                      ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ptr)]
    x11.XFree.argtypes = [ptr]
    x11.XSetInputFocus.argtypes = [ptr, window, ctypes.c_int, ctypes.c_ulong]
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ptr, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XFlush.argtypes = [ptr]
    x11.XCloseDisplay.argtypes = [ptr]
    xtst.XTestFakeKeyEvent.argtypes = [ptr, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        raise RuntimeError("cannot open task display")
    try:
        queue = [(x11.XDefaultRootWindow(display), 0)]
        candidates = []
        titles = []
        pid_atom = x11.XInternAtom(display, b"_NET_WM_PID", 0)
        net_wm_name = x11.XInternAtom(display, b"_NET_WM_NAME", 0)
        utf8_string = x11.XInternAtom(display, b"UTF8_STRING", 0)
        while queue:
            current, depth = queue.pop(0)
            name_ptr = ptr()
            title = None
            if x11.XFetchName(display, current, ctypes.byref(name_ptr)) and name_ptr:
                title = ctypes.string_at(name_ptr).decode("utf-8", errors="replace")
                x11.XFree(name_ptr)
            else:
                # Non-Latin-1 titles (the game's Japanese name) are only in _NET_WM_NAME.
                actual_type, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
                fmt, data = ctypes.c_int(), ptr()
                if x11.XGetWindowProperty(display, current, net_wm_name, 0, 1024, 0, utf8_string,
                                         ctypes.byref(actual_type), ctypes.byref(fmt), ctypes.byref(count),
                                         ctypes.byref(remaining), ctypes.byref(data)) == 0 and data:
                    if fmt.value == 8 and count.value:
                        title = ctypes.string_at(data, count.value).decode("utf-8", errors="replace")
                    x11.XFree(data)
            if title is not None:
                titles.append(title)
                attributes = WindowAttributes()
                visible = x11.XGetWindowAttributes(display, current, ctypes.byref(attributes)) and attributes.map_state == 2
                actual_type, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
                fmt, data = ctypes.c_int(), ptr()
                owner = None
                if x11.XGetWindowProperty(display, current, pid_atom, 0, 1, 0, 6,
                                         ctypes.byref(actual_type), ctypes.byref(fmt), ctypes.byref(count),
                                         ctypes.byref(remaining), ctypes.byref(data)) == 0 and data:
                    if fmt.value == 32 and count.value:
                        owner = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0]
                    x11.XFree(data)
                if visible and owned_process(owner, expected_pid) and attributes.width >= 64 and attributes.height >= 64:
                    rank = 2 if any(token in title for token in ("SLPM", "Galaxy Angel", "ギャラクシー")) else 1
                    candidates.append((rank, current, title or "untitled reference window"))
            if depth < 3:
                root, parent = window(), window()
                children = ctypes.POINTER(window)()
                count = ctypes.c_uint()
                if x11.XQueryTree(display, current, ctypes.byref(root), ctypes.byref(parent),
                                  ctypes.byref(children), ctypes.byref(count)):
                    queue.extend((children[i], depth + 1) for i in range(count.value))
                    if children:
                        x11.XFree(children)
        if not candidates:
            raise RuntimeError(f"no visible game/reference window on task display; titles={titles}")
        _, target, title = max(candidates)
        x11.XSetInputFocus(display, target, 1, 0)
        # Mouse "keys": mouse1/mouse2/mouse3 (left/middle/right), wheelup,
        # wheeldown; the pointer moves to the window centre first.
        buttons = {"mouse1": 1, "mouse2": 2, "mouse3": 3, "wheelup": 4, "wheeldown": 5}
        # Pointer actions at window pixels (the grammar is in tools/run_owned.py):
        # "move:X,Y", "click:X,Y", "rclick:X,Y", "mclick:X,Y", "dclick:X,Y",
        # "wheel:N@X,Y" (N notches, negative = down), "hold:B:X,Y:MS" (B = l/r/m),
        # "drag:B:X1,Y1,X2,Y2[,STEPS[,STEP_MS]]", "press:B:X,Y" and "release:B".
        if ":" in key and not key.startswith(("keydown:", "keyup:")):
            action, _, rest = key.partition(":")
            names = {"l": 1, "m": 2, "r": 3, "b": 8, "f": 9}  # b / f: the mouse's back / forward buttons
            root_x, root_y, child = ctypes.c_int(), ctypes.c_int(), window()
            x11.XTranslateCoordinates.argtypes = [ptr, window, window, ctypes.c_int, ctypes.c_int,
                                                  ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                                  ctypes.POINTER(window)]
            xtst.XTestFakeMotionEvent.argtypes = [ptr, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeButtonEvent.argtypes = [ptr, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

            def pointer_to(wx, wy):
                x11.XTranslateCoordinates(display, target, x11.XDefaultRootWindow(display), int(wx), int(wy),
                                          ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(child))
                xtst.XTestFakeMotionEvent(display, -1, root_x.value, root_y.value, 0)
                x11.XFlush(display)

            def button(number, down):
                xtst.XTestFakeButtonEvent(display, number, 1 if down else 0, 0)
                x11.XFlush(display)

            def pair(text):
                return [float(v) for v in text.split(",")]

            if action in ("move", "click", "rclick", "mclick", "dclick", "bclick", "fclick"):
                wx, wy = pair(rest)
                pointer_to(wx, wy)
                if action != "move":
                    number = {"click": 1, "dclick": 1, "rclick": 3, "mclick": 2, "bclick": 8, "fclick": 9}[action]
                    for repeat in range(2 if action == "dclick" else 1):
                        time.sleep(0.12 if repeat else 0.05)
                        button(number, True)
                        try:
                            time.sleep(0.06 if action == "dclick" else hold_ms / 1000)
                        finally:
                            button(number, False)
            elif action == "wheel":
                count, _, where = rest.partition("@")
                wx, wy = pair(where)
                pointer_to(wx, wy)
                count = int(count)
                for _ in range(abs(count)):
                    number = 4 if count > 0 else 5
                    button(number, True)
                    button(number, False)
                    time.sleep(0.12)
            elif action == "hold":
                name, _, tail = rest.partition(":")
                where, _, ms = tail.rpartition(":")
                wx, wy = pair(where)
                pointer_to(wx, wy)
                time.sleep(0.05)
                button(names[name], True)
                try:
                    time.sleep(float(ms) / 1000)
                finally:
                    button(names[name], False)
            elif action == "drag":
                name, _, tail = rest.partition(":")
                values = pair(tail)
                steps = int(values[4]) if len(values) > 4 else 12
                step_ms = values[5] if len(values) > 5 else 40
                pointer_to(values[0], values[1])
                time.sleep(0.08)
                button(names[name], True)
                try:
                    time.sleep(0.08)
                    for i in range(1, steps + 1):
                        pointer_to(values[0] + (values[2] - values[0]) * i / steps,
                                   values[1] + (values[3] - values[1]) * i / steps)
                        time.sleep(step_ms / 1000)
                    time.sleep(0.1)
                finally:
                    button(names[name], False)
            elif action == "press":
                name, _, where = rest.partition(":")
                wx, wy = pair(where)
                pointer_to(wx, wy)
                time.sleep(0.05)
                button(names[rest.split(":")[0]], True)
            elif action == "release":
                button(names[rest], False)
            else:
                raise ValueError("unknown pointer action")
            return title
        if key in buttons:
            attributes = WindowAttributes()
            x11.XGetWindowAttributes(display, target, ctypes.byref(attributes))
            root_x, root_y, child = ctypes.c_int(), ctypes.c_int(), window()
            x11.XTranslateCoordinates.argtypes = [ptr, window, window, ctypes.c_int, ctypes.c_int,
                                                  ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                                  ctypes.POINTER(window)]
            x11.XTranslateCoordinates(display, target, x11.XDefaultRootWindow(display),
                                      attributes.width // 2, attributes.height // 2,
                                      ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(child))
            xtst.XTestFakeMotionEvent.argtypes = [ptr, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeButtonEvent.argtypes = [ptr, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeMotionEvent(display, -1, root_x.value, root_y.value, 0)
            xtst.XTestFakeButtonEvent(display, buttons[key], 1, 0)
            x11.XFlush(display)
            try:
                time.sleep(hold_ms / 1000)
            finally:
                xtst.XTestFakeButtonEvent(display, buttons[key], 0, 0)
                x11.XFlush(display)
            return title
        # "keydown:KEYSYM" / "keyup:KEYSYM": a key held across other actions (a Ctrl+click).
        edge = None
        if key.startswith(("keydown:", "keyup:")):
            edge, _, key = key.partition(":")
        code = x11.XKeysymToKeycode(display, x11.XStringToKeysym(key.encode("ascii")))
        if not code:
            raise ValueError("unknown test key")
        if edge:
            xtst.XTestFakeKeyEvent(display, code, 1 if edge == "keydown" else 0, 0)
            x11.XFlush(display)
            return title
        xtst.XTestFakeKeyEvent(display, code, 1, 0)
        x11.XFlush(display)
        try:
            time.sleep(hold_ms / 1000)
        finally:
            xtst.XTestFakeKeyEvent(display, code, 0, 0)
            x11.XFlush(display)
        return title
    finally:
        x11.XCloseDisplay(display)
