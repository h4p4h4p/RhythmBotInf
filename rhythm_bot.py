# rhythm_bot.py - generic rhythm-game autoplayer (pure pixel detection).
#
# No chart/JSON reading. Each lane watches a THIN strip of probes sitting just
# above the hit point. While the strip sees WHITE the key is down (holding the
# note), and when it goes fully BLACK the key is released. The strip stays thin
# so dense streams/jacks show black gaps and every note gets its own press.
# Nothing needs the scroll direction or note data.
#
# CALIBRATION - do this once per game/resolution
#   Press 'c'. An overlay shows colored markers for each lane (D F J K).
#   Left-click each one to place it exactly on the receptors.
#   Right-click = undo. Saved to rhythm_bot.cfg automatically.
#
# CONSOLE CONTROLS:
#   Enter   start          p / Space   pause/resume
#   c       calibrate      q           quit
#
# GLOBAL KEYS (fire anywhere, even while playing):
#   F9      start/pause    [ ]         white threshold
#   - =     press delay    z x         probe distance
#   r       random jitter  o           probe above/below
#   v       probe overlay
#
# A white keybind cheat-sheet is shown at the bottom of the screen while running.
#
#   Run:  python rhythm_bot.py  |  --start | --probe below | --selftest
#
# Tip: run the game windowed/borderless so the desktop is capturable.

import atexit
import ctypes
import ctypes.wintypes
from ctypes import wintypes
import msvcrt
import os
import queue
import random
import sys
import threading
import time
import traceback

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

try:
    import mss
except ImportError:
    sys.exit("Missing dependency: pip install mss pydirectinput")

try:
    import pydirectinput
    pydirectinput.PAUSE = 0.0
except ImportError:
    sys.exit("Missing dependency: pip install mss pydirectinput")

try:
    import keyboard
    HAS_GLOBAL = True
except Exception:
    HAS_GLOBAL = False

try:
    from pynput import mouse as _pynput_mouse
    HAS_MOUSE = True
except Exception:
    HAS_MOUSE = False

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "rhythm_bot.cfg")
LOG_FILE = os.path.join(HERE, "rhythm_bot.log")

# Lane labels in gameplay order: Left / Down / Up / Right  ->  D / F / J / K
LANES = [
    {"key": "d", "label": "LEFT",  "color": (34, 200, 120)},
    {"key": "f", "label": "DOWN",  "color": (66, 135, 245)},
    {"key": "j", "label": "UP",    "color": (180, 90, 255)},
    {"key": "k", "label": "RIGHT", "color": (255, 80, 80)},
]

# LANE SIDES: a THIN strip of probes sits just above the hit point. While the
# strip sees WHITE we hold the key, and when it goes fully BLACK we release.
# The strip is deliberately thin so that in dense spammy streams the gaps
# between notes show up as black, letting the key release and re-press for
# every note. Flip to below the hit point with 'o' for downscroll games.
SIDES = {
    0: ("TOP",    (0, -1)),
    1: ("BOTTOM", (0, 1)),
}
SPACING = 2       # px between the probes in a strip
PROBE_LEN = 3     # probes per strip

KEYMAP = {b"H": "up", b"P": "down", b"K": "left", b"M": "right",
          b"\r": "enter", b"\x08": "backspace", b"\x1b": "esc"}

# Keybind cheat-sheet shown at the bottom of the screen while the bot runs.
HELP_TEXT = "\n".join([
    "ENTER  start       p/space  pause       c  calibrate      q  quit",
    "[ ]  threshold      - =  delay      z x  probe dist     r  jitter",
    "o  probe side      v  overlay      F9  start/pause anywhere",
])


def out(*args, **kwargs):
    if sys.stdout:
        print(*args, **kwargs, flush=True)


def has_console():
    try:
        return bool(sys.stdout) and os.isatty(sys.stdout.fileno())
    except Exception:
        return False


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def get_mouse():
    p = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def readkey():
    if not msvcrt.kbhit():
        return None
    b = msvcrt.getch()
    if b in (b"\x00", b"\xe0"):
        b2 = msvcrt.getch()
        return KEYMAP.get(b2)
    return KEYMAP.get(b, b.decode(errors="replace"))


def median(vals):
    return sorted(vals)[len(vals) // 2]


# --------------------------------------------------------------------------
#  On-screen overlay (transparent, click-through, always-on-top), pure Win32.
# --------------------------------------------------------------------------

class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class PNT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
ULW_ALPHA = 2
BI_RGB = 0
DIB_RGB_COLORS = 0
OPAQUE = 1
DT_TOP = 0
DT_LEFT = 0
DT_NOCLIP = 0x100
DT_CALCRECT = 0x400

GLYPHS = {
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "F": ["11111", "10000", "11110", "10000", "10000", "10000", "10000"],
    "J": ["00111", "00010", "00010", "00010", "00010", "00010", "11100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
}


class Overlay:
    def __init__(self, w, h, x=0, y=0):
        self.w, self.h = w, h
        self.x, self.y = x, y
        self.pix = None
        self._make_window(w, h, x, y)
        self._make_dib(w, h)

    def _make_window(self, w, h, x, y):
        user32 = ctypes.windll.user32
        LPARAM = ctypes.c_ssize_t if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        hInst = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSW(0, ctypes.cast(self._proc, ctypes.c_void_p), 0, 0,
                       hInst, None, None, None, None, "RhythmBotOv")
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            "RhythmBotOv", "rhythm_bot", WS_POPUP,
            x, y, w, h, None, None, hInst, None)

    @staticmethod
    def _wndproc(hwnd, msg, wp, lp):
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _make_dib(self, w, h):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdcScreen = user32.GetDC(None)
        self.hdcMem = gdi32.CreateCompatibleDC(hdcScreen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = 40
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        pp = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(hdcScreen, ctypes.byref(bmi),
                                           DIB_RGB_COLORS, ctypes.byref(pp), None, 0)
        gdi32.SelectObject(self.hdcMem, self.hbmp)
        self.ptr = ctypes.cast(pp, ctypes.c_void_p)
        self.pix = (ctypes.c_ubyte * (w * h * 4)).from_address(self.ptr.value)
        user32.ReleaseDC(None, hdcScreen)

    def _px(self, x, y, b, g, r, a):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 4
            p = self.pix
            p[i] = b * a // 255
            p[i + 1] = g * a // 255
            p[i + 2] = r * a // 255
            p[i + 3] = a

    def circle(self, cx, cy, rad, rgb, alpha, ring=None):
        r = max(1, int(rad))
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                if d2 <= r * r and (ring is None or d2 >= (r - ring) ** 2):
                    self._px(xx, yy, rgb[0], rgb[1], rgb[2], alpha)

    def letter(self, cx, cy, ch, rgb, alpha, scale=3):
        glyph = GLYPHS.get(ch)
        if not glyph:
            return
        rows, cols = len(glyph), len(glyph[0])
        x0, y0 = cx - cols * scale // 2, cy - rows * scale // 2
        for r, row in enumerate(glyph):
            for c, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            self._px(x0 + c * scale + dx, y0 + r * scale + dy,
                                     rgb[0], rgb[1], rgb[2], alpha)

    @staticmethod
    def measure_text(text, fontname="Segoe UI", fontsize=14):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(None)
        font = gdi32.CreateFontW(fontsize, 0, 0, 0, 400, 0, 0, 0,
                                 1, 0, 0, 5, 0, fontname)
        old = gdi32.SelectObject(hdc, font)
        user32.DrawTextW.argtypes = [wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.POINTER(RECT), wintypes.UINT]
        rc = RECT(0, 0, 0, 0)
        user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), DT_CALCRECT | DT_NOCLIP)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)
        user32.ReleaseDC(None, hdc)
        return rc.right, rc.bottom

    def draw_text_help(self, text, fontname="Segoe UI", fontsize=14):
        # white text on black, then black becomes transparent so only the
        # (anti-aliased) white lettering is visible on the layered window
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        font = gdi32.CreateFontW(fontsize, 0, 0, 0, 400, 0, 0, 0,
                                 1, 0, 0, 5, 0, fontname)
        gdi32.SelectObject(self.hdcMem, font)
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetBkColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        user32.DrawTextW.argtypes = [wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.POINTER(RECT), wintypes.UINT]
        gdi32.SetBkMode(self.hdcMem, OPAQUE)
        gdi32.SetBkColor(self.hdcMem, 0)                    # black background
        gdi32.SetTextColor(self.hdcMem, 0x00FFFFFF)         # white text
        rc = RECT(0, 0, wintypes.LONG(self.w), wintypes.LONG(self.h))
        user32.DrawTextW(self.hdcMem, text, -1, ctypes.byref(rc),
                         DT_TOP | DT_LEFT | DT_NOCLIP)
        gdi32.DeleteObject(font)
        pix = self.pix
        for i in range(0, self.w * self.h * 4, 4):
            a = max(pix[i], pix[i + 1], pix[i + 2])         # white-on-black glow
            pix[i] = pix[i + 1] = pix[i + 2] = 255
            pix[i + 3] = a

    def clear(self):
        ctypes.memset(ctypes.addressof(self.pix), 0, len(self.pix))

    def commit(self, hide=False):
        user32 = ctypes.windll.user32
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(PNT), ctypes.POINTER(SIZE),
            wintypes.HDC, ctypes.POINTER(PNT), wintypes.COLORREF,
            ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        sz = SIZE(self.w, self.h)
        zero = PNT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(zero), ctypes.byref(sz),
                                   self.hdcMem, ctypes.byref(zero), 0, ctypes.byref(blend), ULW_ALPHA)
        user32.ShowWindow(self.hwnd, SW_HIDE if hide else SW_SHOWNOACTIVATE)

    def hide(self):
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_HIDE)
        self.clear()


# --------------------------------------------------------------------------
#  Bot
# --------------------------------------------------------------------------

class Bot:
    def __init__(self):
        self.sct = mss.MSS()
        mon = self.sct.monitors[1]
        self.w, self.h = mon["width"], mon["height"]

        cx, cy = self.w // 2, self.h // 2 + 30
        self.lanes = []
        for i, cfg in enumerate(LANES):
            self.lanes.append({
                "key": cfg["key"],
                "label": cfg["label"],
                "color": cfg["color"],
                "x": cx + (i - 1.5) * 100,
                "y": cy,
                "base": [],
                "state": 0,      # 0 idle, 1 press-pending, 2 held, 3 release-pending
                "_live": [False, False],
                "_last": [],
                "press_at": 0.0,
                "release_at": 0.0,
            })

        self.axis = 0                # 0 = probe strip ABOVE the hit point, 1 = below
        self.probe_dist = 12         # top probe sits this far above the hit point
        self.threshold = 170         # pixel "whiteness" (min R,G,B) to call a note
        self.delay_ms = 0
        self.min_hold = 0.012        # fast release so jacks don't get stuck
        self.randomize = True        # human-like jitter on every press
        self.rand_ms = 12            # jitter magnitude (+/- ms on timing)

        self.enabled = False
        self.quit = False
        self.lock = threading.Lock()
        self.mouse_q = queue.Queue()
        self.cmd_q = queue.Queue()
        self.cal_idx = -1
        self.overlay_visible = False
        self.overlay = None
        self.help_ov = None
        try:
            hw, hh = Overlay.measure_text(HELP_TEXT, fontsize=14)
            pad = 8
            self.help_ov = Overlay(hw + pad * 2, hh + pad * 2,
                                   (self.w - (hw + pad * 2)) // 2,
                                   self.h - hh - pad * 2 - 60)
            log("help overlay ready")
        except Exception as e:
            self.help_ov = None
            log("help overlay failed: %r" % e)
        self._last_hud = 0.0
        self._last_overlay = 0.0
        self._black_warned = False
        self._last_any_present = time.monotonic()
        self._buf = None
        self._region = (0, 0, 0, 0)
        self._imgw = 0
        self._imgh = 0

        self.load_cfg()
        try:
            self.init_baselines()
        except Exception as e:
            log("boot baseline failed: %r" % e)

    # ---- config -----------------------------------------------------------

    def load_cfg(self):
        if not os.path.exists(CFG_FILE):
            return
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == "axis" and len(parts) == 2:
                        # legacy key kept so old configs still load (0 = TOP)
                        self.axis = int(parts[1]) % 2
                    elif parts[0] == "probe" and len(parts) == 2:
                        self.probe_dist = max(4, int(parts[1]))
                    elif parts[0] == "threshold" and len(parts) == 2:
                        self.threshold = int(parts[1])
                    elif parts[0] == "delay" and len(parts) == 2:
                        self.delay_ms = int(parts[1])
                    elif parts[0] == "rand" and len(parts) == 2:
                        self.rand_ms = max(0, int(parts[1]))
                    elif len(parts) == 3:
                        for lane in self.lanes:
                            if lane["key"] == parts[0]:
                                lane["x"], lane["y"] = int(parts[1]), int(parts[2])
        except Exception as e:
            log("config load failed: %r" % e)

    def save_cfg(self):
        try:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                f.write("axis %d\n" % self.axis)
                f.write("probe %d\n" % self.probe_dist)
                f.write("threshold %d\n" % self.threshold)
                f.write("delay %d\n" % self.delay_ms)
                f.write("rand %d\n" % self.rand_ms)
                for lane in self.lanes:
                    f.write("%s %d %d\n" % (lane["key"], int(lane["x"]), int(lane["y"])))
        except Exception as e:
            log("config save failed: %r" % e)

    # ---- pixels -----------------------------------------------------------

    def sample_px(self, x, y, size=3):
        s = size
        img = self.sct.grab({"left": int(x) - s // 2, "top": int(y) - s // 2,
                             "width": s, "height": s})
        buf = img.bgra
        stride = img.width * 4
        b = g = r = 0
        n = 0
        for yy in range(img.height):
            for xx in range(img.width):
                off = yy * stride + xx * 4
                b += buf[off]
                g += buf[off + 1]
                r += buf[off + 2]
                n += 1
        return (b // n, g // n, r // n)

    def probes(self, lane):
        # thin strip of probes from y-12 up to y-8 above the hit point
        # (or mirrored below in BOTTOM mode)
        _, offset = SIDES[self.axis]
        return [(int(lane["x"]),
                 int(lane["y"]) + offset[1] * (self.probe_dist - i * SPACING))
                for i in range(PROBE_LEN)]

    def grab_region(self):
        # ONE full-desktop grab per tick covering every probe - much faster
        # than a separate mss grab per pixel, letting the loop keep up with
        # fast 120fps note movement.
        x0 = y0 = 1 << 30
        x1 = y1 = -1
        for lane in self.lanes:
            for px, py in self.probes(lane):
                x0 = min(x0, px); y0 = min(y0, py)
                x1 = max(x1, px); y1 = max(y1, py)
        pad = 2
        x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
        self._region = (x0, y0, x1 + 1, y1 + 1)
        img = self.sct.grab({"left": x0, "top": y0,
                             "width": x1 - x0 + 1, "height": y1 - y0 + 1})
        self._buf = img.bgra
        self._imgw = img.width
        self._imgh = img.height

    def px_buf(self, px, py, size=3):
        # 3x3 average read straight out of the grabbed region buffer
        if self._buf is None:
            return (0, 0, 0)
        x0, y0 = self._region[0], self._region[1]
        b = g = r = 0
        n = 0
        for dx in range(size):
            sx = px + dx - size // 2 - x0
            for dy in range(size):
                sy = py + dy - size // 2 - y0
                if 0 <= sx < self._imgw and 0 <= sy < self._imgh:
                    off = sy * (self._imgw * 4) + sx * 4
                    b += self._buf[off]; g += self._buf[off + 1]; r += self._buf[off + 2]
                    n += 1
        return (b // n, g // n, r // n)

    def init_baselines(self):
        samples = [[] for _ in self.lanes]
        for _ in range(30):
            for li, lane in enumerate(self.lanes):
                samples[li].append([self.sample_px(px, py) for px, py in self.probes(lane)])
            time.sleep(0.016)
        for li, lane in enumerate(self.lanes):
            n = len(self.probes(lane))
            lane["base"] = [
                (median([s[p][0] for s in samples[li]]),
                 median([s[p][1] for s in samples[li]]),
                 median([s[p][2] for s in samples[li]])) for p in range(n)]
            lane["state"] = 0
            lane["_live"] = [False] * PROBE_LEN
        log("baselines reset")

    @staticmethod
    def diff(color, base):
        return abs(color[0] - base[0]) + abs(color[1] - base[1]) + abs(color[2] - base[2])

    # ---- keys -------------------------------------------------------------

    def press(self, key):
        try:
            pydirectinput.keyDown(key)
        except Exception:
            pass

    def release(self, key):
        try:
            pydirectinput.keyUp(key)
        except Exception:
            pass

    def release_all(self):
        for lane in self.lanes:
            try:
                pydirectinput.keyUp(lane["key"])
            except Exception:
                pass

    def set_enabled(self, on):
        with self.lock:
            was_off = self.enabled and not on
            self.enabled = on
        if was_off:
            self.release_all()
        log("enabled=%s" % ("ON" if on else "OFF"))

    def toggle(self):
        with self.lock:
            self.enabled = not self.enabled
            was_off = not self.enabled
        if was_off:
            self.release_all()
        out("status:", "ON" if self.enabled else "OFF")
        return self.enabled

    def adjust_threshold(self, d):
        self.threshold = min(400, max(10, self.threshold + d))
        out("threshold =", self.threshold)
        log("threshold=%d" % self.threshold)

    def adjust_delay(self, d):
        self.delay_ms = min(2000, max(-100, self.delay_ms + d))
        out("delay =", self.delay_ms, "ms")
        log("delay=%dms" % self.delay_ms)

    def adjust_probe(self, d):
        self.probe_dist = min(400, max(4, self.probe_dist + d))
        out("probe dist =", self.probe_dist)
        log("probe_dist=%d" % self.probe_dist)
        self.show_overlay(self.overlay_visible)

    def toggle_random(self):
        self.randomize = not self.randomize
        out("random jitter", "ON" if self.randomize else "OFF")
        log("jitter=%s" % ("ON" if self.randomize else "OFF"))

    def toggle_axis(self):
        self.axis = (self.axis + 1) % len(SIDES)
        out("probe ->", SIDES[self.axis][0])
        log("axis=%s" % SIDES[self.axis][0])
        self.show_overlay(self.overlay_visible)

    def toggle_overlay_view(self):
        self.show_overlay(not self.overlay_visible)
        out("probe overlay", "ON" if self.overlay_visible else "OFF")

    # ---- detection --------------------------------------------------------

    def tick(self, now):
        if not self.lanes or not self.lanes[0]["base"]:
            return
        try:
            self.grab_region()
        except Exception:
            return
        any_present = False
        hi = max(12, self.threshold)          # "white" level
        lo = max(10, hi * 3 // 5)             # "black" level (hysteresis)
        for lane in self.lanes:
            probes = self.probes(lane)
            colors = [self.px_buf(px, py) for px, py in probes]
            lane["_last"] = colors

            # white anywhere in the bar = the note is here, all black = passed
            present = any(min(c[0], c[1], c[2]) >= hi for c in colors)
            black = all(max(c[0], c[1], c[2]) <= lo for c in colors)
            lane["_live"] = [present, present, present]
            if present:
                any_present = True

            # baselines still follow slowly (only used for the black-screen check)
            if lane["state"] == 0:
                for i in range(len(colors)):
                    col = colors[i]
                    b = lane["base"][i]
                    lane["base"][i] = (b[0] * 0.98 + col[0] * 0.02,
                                       b[1] * 0.98 + col[1] * 0.02,
                                       b[2] * 0.98 + col[2] * 0.02)

            delay = self.delay_ms / 1000.0
            if self.randomize and self.rand_ms > 0:
                delay += random.uniform(-self.rand_ms, self.rand_ms) / 1000.0
            if lane["state"] == 0:                             # white -> press
                if present and self._enabled():
                    lane["state"] = 1
                    lane["press_at"] = now + delay
            elif lane["state"] == 1:
                if not present:
                    lane["state"] = 0
                elif lane["press_at"] <= now:
                    self.press(lane["key"])
                    lane["state"] = 2
                    lane["release_at"] = now + self.min_hold
            elif lane["state"] == 2:                           # hold while white
                hold_ms = self.min_hold
                if self.randomize:
                    hold_ms *= random.uniform(0.5, 1.3)
                lane["release_at"] = now + hold_ms
                if black:
                    lane["state"] = 3
            elif lane["state"] == 3:                           # black -> release
                if present:
                    # next spam note is already in the strip before we let go -
                    # re-press and hold it instead of dropping the centre note
                    self.press(lane["key"])
                    lane["state"] = 2
                    lane["release_at"] = now + self.min_hold
                elif now >= lane["release_at"]:
                    self.release(lane["key"])
                    lane["state"] = 0

        if any_present:
            self._last_any_present = now

    def _enabled(self):
        with self.lock:
            return self.enabled

    # ---- calibration ------------------------------------------------------

    def begin_calibrate(self):
        if not HAS_MOUSE:
            log("pynput missing - calibration needs it: pip install pynput")
            out("pynput not installed:  pip install pynput")
            return
        self.set_enabled(False)
        self.cal_idx = 0
        self.show_overlay(True)
        if self.help_ov:                        # keybind cheat-sheet during calibration
            self.help_ov.draw_text_help(HELP_TEXT, fontsize=14)
            self.help_ov.commit()
        out("CLICK each marker. Left-click = set, right-click = undo (%d total)."
            % len(self.lanes))
        log("calibration started")

    def finish_calibrate(self):
        self.cal_idx = -1
        self.show_overlay(self.overlay_visible)
        if self.help_ov:
            self.help_ov.hide()
        self.save_cfg()
        out("Calibration saved. Markers show the hit points.")
        log("calibration saved")

    def handle_mouse_events(self):
        try:
            while True:
                x, y, button, pressed = self.mouse_q.get_nowait()
                if not pressed:
                    continue
                if self.cal_idx < 0:
                    continue
                if button == "left":
                    lane = self.lanes[self.cal_idx]
                    lane["x"], lane["y"] = x, y
                    log("lane %s -> %d,%d" % (lane["key"], x, y))
                    out("  %s (%s) set at %d,%d" % (lane["label"], lane["key"], x, y))
                    self.cal_idx += 1
                    if self.cal_idx >= len(self.lanes):
                        self.finish_calibrate()
                elif button == "right":
                    self.cal_idx = max(0, self.cal_idx - 1)
                    out("  undone - click the previous marker again")
        except queue.Empty:
            pass

    # ---- overlay ----------------------------------------------------------

    def handle_cmds(self):
        # global hotkeys only ENQUEUE work; everything runs on the bot thread
        # here so GDI/overlay calls never race the main render loop
        try:
            while True:
                fn = self.cmd_q.get_nowait()
                fn()
        except queue.Empty:
            pass

    def show_overlay(self, visible):
        self.overlay_visible = visible
        if not self.overlay and visible:
            try:
                self.overlay = Overlay(self.w, self.h)
                log("overlay created")
            except Exception as e:
                self.overlay = None
                log("overlay FAILED: %r" % e)
                out("overlay failed: %r" % e)
        self.render_overlay(force=True)

    def render_overlay(self, force=False):
        ov = self.overlay
        if ov is None:
            return
        now = time.monotonic()
        if not force and now - self._last_overlay < 0.03:
            return
        self._last_overlay = now

        show = self.cal_idx >= 0 or self.overlay_visible
        if not show:
            ov.hide()
            return

        ov.clear()
        for idx, lane in enumerate(self.lanes):
            if self.cal_idx >= 0 and idx > self.cal_idx:
                break
            x, y = int(lane["x"]), int(lane["y"])
            col = lane["color"]
            if self.cal_idx == idx:
                ov.circle(x, y, 32, (255, 220, 60), 220, ring=4)
                ov.circle(x, y, 20, col, 200)
            elif self.cal_idx >= 0:
                ov.circle(x, y, 20, col, 160)
            else:
                ov.circle(x, y, 14, col, 120, ring=4)
            ov.letter(x, y, lane["key"].upper(), (255, 255, 255), 176, scale=3)

            for pi, (px, py) in enumerate(self.probes(lane)):
                if pi < len(lane["_live"]) and lane["_live"][pi]:
                    ov.circle(px, py, 9, (80, 255, 120), 235)
                else:
                    ov.circle(px, py, 9, col, 180, ring=3)
        ov.commit()

    # ---- diagnostics ------------------------------------------------------

    def diagnostic(self, now):
        if not self._enabled():
            return
        if now - self._last_any_present < 2.0:
            self._black_warned = False
            return
        tot = sum(sum(c) for lane in self.lanes for c in lane["_last"])
        n = sum(len(lane["_last"]) for lane in self.lanes)
        avg = tot / max(1, n)
        if avg <= 24:
            if not self._black_warned:
                self._black_warned = True
                log("BLACK SCREEN - use windowed/borderless so the desktop is capturable")
                out("WARNING: I'm capturing black - run the game windowed/borderless.")
        else:
            self._black_warned = False

    # ---- console ----------------------------------------------------------

    def handle_console(self):
        k = readkey()
        if not k:
            return
        if k == "c":
            self.begin_calibrate()
        elif k == "enter":
            self.start()
        elif k in ("p", " "):
            self.toggle()
        elif k == "q":
            self.quit = True

    def start(self):
        self.set_enabled(False)
        self.init_baselines()
        self.set_enabled(True)
        out("GO - F9/p/Space pauses. Markers flash green as notes hit.")

    # ---- HUD --------------------------------------------------------------

    def hud(self):
        parts = []
        for lane in self.lanes:
            mark = ("H" if lane["state"] == 2 else ("#" if lane["state"] in (1, 3) else "."))
            parts.append("%s%s%s" % (lane["label"][0], lane["key"].upper(), mark))
        out("probe=%s %s th=%d dly=%dms rand=%dms  %s"
            % (SIDES[self.axis][0], "ON " if self.enabled else "OFF",
               self.threshold, self.delay_ms,
               self.rand_ms if self.randomize else 0, "  ".join(parts))
            + " " * 20, end="\r")

    # ---- worker loop ------------------------------------------------------

    def bot_loop(self):
        last_hud = 0.0
        last_diag = 0.0
        last_ov = 0.0
        try:
            while not self.quit:
                self.handle_cmds()
                self.handle_mouse_events()
                if has_console():
                    self.handle_console()
                now = time.monotonic()
                self.tick(now)
                if now - last_ov > 0.033:      # overlay at ~30fps so it can't
                    self.render_overlay()      # slow down the detection loop
                    last_ov = now
                if has_console() and now - last_hud > 0.2:
                    self.hud()
                    last_hud = now
                if now - last_diag > 1.0:
                    self.diagnostic(now)
                    last_diag = now
                time.sleep(0.00005)
        except Exception:
            self.quit = True
            log("FATAL in bot loop:\n" + traceback.format_exc())
        finally:
            self.set_enabled(False)
            if self.overlay:
                self.overlay.hide()
            if self.help_ov:
                self.help_ov.hide()
            self.save_cfg()


def parse_args(app):
    if "--probe" in sys.argv:
        name = sys.argv[sys.argv.index("--probe") + 1].lower()
        app.axis = 0 if name.startswith(("a", "t", "up")) else 1
    elif "--axis" in sys.argv:  # legacy: old axis flag -> vertical probe above
        app.axis = 0
    if "--dir" in sys.argv:  # legacy flag, we ignore direction (auto handles it)
        out("note: scroll direction is auto - no --dir needed anymore")


def selftest():
    app = Bot()
    out("self-test: creating overlay (3s)...")
    app.show_overlay(True)
    time.sleep(3)
    if app.overlay:
        app.overlay.hide()
    if app.help_ov:
        app.help_ov.hide()
    px = app.sample_px(app.lanes[0]["x"], app.lanes[0]["y"])
    out("pixel sample at first hit point:", px, " OK")
    out("self-test passed")


def main():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\rhythm_bot_mutex")
    if ctypes.windll.kernel32.GetLastError() == 183:
        out("Another rhythm bot instance is already running.")
        sys.exit(1)
    atexit.register(lambda: ctypes.windll.kernel32.CloseHandle(mutex))

    if "--selftest" in sys.argv:
        selftest()
        return

    app = Bot()

    def _cleanup():
        app.set_enabled(False)
        app.save_cfg()
        if HAS_GLOBAL:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
    atexit.register(_cleanup)

    parse_args(app)

    out("=" * 66)
    out("  RHYTHM BOT")
    out("-" * 66)
    out("  START / PAUSE   ENTER  (type it here)   or   F9 (anywhere)")
    out("  CALIBRATE        C       QUIT          Q")
    out("-" * 66)
    out("  GLOBAL KEYS (work even while you're in the game):")
    out("    [ ]  white threshold      - =  press delay ms")
    out("    z x  probe distance       r    random jitter")
    out("    o    probe above/below    v    probe overlay")
    out("-" * 66)
    out("  First time: press C then CLICK each of the 4 markers (D F J K).")
    out("  Run the game windowed/borderless so the desktop is capturable.")
    out("=" * 66)

    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            out("  NOTE: not running as admin - if the game runs elevated,")
            out("  global keys will be ignored in-game. Use rhythm_bot.cmd")
            out("  to auto-run as administrator.")
    except Exception:
        pass

    if HAS_GLOBAL:
        def hk(fn):
            return lambda: app.cmd_q.put(fn)

        binds = {
            "[": lambda: hk(lambda: app.adjust_threshold(-10)),
            "]": lambda: hk(lambda: app.adjust_threshold(10)),
            "-": lambda: hk(lambda: app.adjust_delay(-10)),
            "=": lambda: hk(lambda: app.adjust_delay(10)),
            "z": lambda: hk(lambda: app.adjust_probe(-5)),
            "x": lambda: hk(lambda: app.adjust_probe(5)),
            "r": hk(lambda: app.toggle_random()),
            "o": hk(lambda: app.toggle_axis()),
            "v": hk(lambda: app.toggle_overlay_view()),
            "f9": hk(app.toggle),
        }
        for key, fn in binds.items():
            try:
                keyboard.add_hotkey(key, fn)
                log("hotkey %s" % key)
            except Exception:
                out("global key %r not bound" % key)

    if HAS_MOUSE:
        lst = _pynput_mouse.Listener(
            on_click=lambda x, y, button, pressed:
                app.mouse_q.put((int(x), int(y),
                                 "left" if str(button) == "Button.left"
                                 else ("right" if str(button) == "Button.right" else "other"),
                                 pressed)))
        lst.daemon = True
        lst.start()
    else:
        out("pynput not installed - calibration needs it:  pip install pynput")

    if "--start" in sys.argv:
        app.start()
    app.bot_loop()
    out("\nbye")


if __name__ == "__main__":
    main()