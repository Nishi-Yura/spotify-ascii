"""Desktop wallpaper: the visualiser's picture drawn behind the desktop icons,
on one, some or all monitors. main.py drives it (the terminal is the remote
control); this module only knows how to put cell frames on the desktop.

How it works
- main.Visualiser produces each frame (cells: character + colours).
- Glyphs come from a small pixel atlas (braille dots and block shapes are
  generated, text uses system fonts) and are drawn 1:1 in screen pixels, so
  the picture is as sharp as the terminal. Only on very large cells (4K) the
  picture is composed at half size and enlarged exactly 2x, with the text
  composed again at full size on top.
- Each window is a layered child of the desktop (Progman) placed just below
  the icon layer, which is what Windows 11 24H2+ needs; older Windows uses
  the classic WorkerW window instead.
- Nothing is drawn on a monitor while another window is maximised / full
  screen there.
"""
import os
import time
import ctypes
import ctypes.wintypes as wt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from render import WIDE_PAD, is_wide

DEFAULT_ROWS = 72          # about as fine as a terminal at a normal font size
MIN_ROWS, MAX_ROWS, SIZE_STEP = 24, 160, 12

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
for _f in ("FindWindowW", "FindWindowExW", "CreateWindowExW", "SetParent", "GetParent",
           "GetDC", "GetAncestor", "MonitorFromWindow", "GetForegroundWindow"):
    getattr(u32, _f).restype = wt.HDC if _f == "GetDC" else (wt.HANDLE if _f == "MonitorFromWindow" else wt.HWND)
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.DefWindowProcW.restype = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
# explicit signatures: handles are 64-bit pointers and must not be passed as C ints
g32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
g32.StretchDIBits.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.DWORD]
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.SetParent.argtypes = [wt.HWND, wt.HWND]
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.IsWindow.argtypes = [wt.HWND]
u32.DestroyWindow.argtypes = [wt.HWND]
u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
u32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                    ctypes.POINTER(ctypes.c_size_t)]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsIconic.argtypes = [wt.HWND]
u32.IsZoomed.argtypes = [wt.HWND]
u32.GetDC.argtypes = [wt.HWND]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
u32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]
ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


# =============================================================================
# glyph atlas
# =============================================================================
def _font(paths, size):
    for p in paths:
        fp = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", p)
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


class Atlas:
    """char -> uint8 alpha mask (ch, cw). Wide (CJK) characters are split into
    a left half (the char) and a right half (key ('R', char))."""

    def __init__(self, cw, ch):
        self.cw, self.ch = cw, ch
        self.fnt = _font(["CascadiaMono.ttf", "consola.ttf"], max(6, int(ch * 0.82)))
        self.cjk = _font(["meiryo.ttc", "YuGothM.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf"],
                         max(6, int(ch * 0.82)))
        self.sym = _font(["seguisym.ttf"], max(6, int(ch * 0.8)))
        self.index = {" ": 0}
        self.masks = [np.zeros((ch, cw), np.uint8)]

    def _add(self, key, m):
        self.index[key] = len(self.masks)
        self.masks.append(m)
        self._stack = None
        return self.index[key]

    def get(self, key):
        i = self.index.get(key)
        if i is not None:
            return i
        if isinstance(key, tuple):                 # right half of a wide char
            self.get(key[1])
            # the left half may have been overdrawn by a narrow glyph: blank
            return self.index.get(key, 0)
        c = key
        o = ord(c)
        if 0x2800 <= o <= 0x28FF:
            return self._add(c, self._braille(o - 0x2800))
        if c in QUAD:
            return self._add(c, self._quad(QUAD[c]))
        if c in BOX:
            return self._add(c, self._box(c))
        if is_wide(c):
            img = Image.new("L", (self.cw * 2, self.ch), 0)
            self._draw(img, c, self.cjk, self.cw * 2)
            a = np.asarray(img)
            i = self._add(c, a[:, :self.cw].copy())
            self._add(("R", c), a[:, self.cw:].copy())
            return i
        img = Image.new("L", (self.cw, self.ch), 0)
        font = self.fnt if o < 0x2000 or c in "♪♫…—" else self.sym
        self._draw(img, c, font, self.cw)
        return self._add(c, np.asarray(img).copy())

    def _baseline(self, font):
        """Vertical offset that centres the font's whole line (capitals and
        descenders) in the cell: every glyph of a font shares one baseline,
        like in a terminal, instead of each glyph being centred on its own."""
        cache = self.__dict__.setdefault("_base", {})
        if id(font) not in cache:
            d = ImageDraw.Draw(Image.new("L", (8, 8)))
            try:
                ref = "漢あ国" if font is self.cjk else "Hgjy|"
                _, t, _, b = d.textbbox((0, 0), ref, font=font)
            except Exception:
                t, b = 0, self.ch
            cache[id(font)] = round((self.ch - (b - t)) / 2 - t)
        return cache[id(font)]

    def _draw(self, img, c, font, width):
        d = ImageDraw.Draw(img)
        try:
            l, t, r, b = d.textbbox((0, 0), c, font=font)
        except Exception:
            l, t, r, b = 0, 0, width, self.ch
        if font is self.sym:                       # symbols: centre the shape itself
            x = (width - (r - l)) / 2 - l
            y = (self.ch - (b - t)) / 2 - t
        else:                                      # text: monospaced advance, shared baseline
            try:
                adv = font.getlength(c)
            except Exception:
                adv = r - l
            x = (width - adv) / 2
            y = self._baseline(font)
        d.text((round(x), round(y)), c, fill=255, font=font)

    def _braille(self, bits):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        s = max(1, min(cw // 2, ch // 4) - 1)
        order = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3)]   # dot1..dot8
        for k, (cx, ry) in enumerate(order):
            if bits >> k & 1:
                x0 = int(round(cx * cw / 2 + (cw / 2 - s) / 2))
                y0 = int(round(ry * ch / 4 + (ch / 4 - s) / 2))
                m[y0:y0 + s, x0:x0 + s] = 255
        return m

    def _quad(self, q):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        hx, hy = cw // 2, ch // 2
        if q & 1: m[:hy, :hx] = 255
        if q & 2: m[:hy, hx:] = 255
        if q & 4: m[hy:, :hx] = 255
        if q & 8: m[hy:, hx:] = 255
        return m

    def _box(self, c):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        my, mx = ch // 2, cw // 2
        t = 2 if c == "━" else 1
        left = c in "─━┐┘"
        right = c in "─━┌└"
        up = c in "│┘└"
        down = c in "│┌┐"
        if left: m[my:my + t, :mx + 1] = 255
        if right: m[my:my + t, mx:] = 255
        if up: m[:my + 1, mx:mx + 1] = 255
        if down: m[my:, mx:mx + 1] = 255
        return m

    def stack(self):
        if getattr(self, "_stack", None) is None:
            self._stack = np.stack(self.masks)
        return self._stack


QUAD = {c: i for i, c in enumerate(" ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█") if c != " "}
BOX = set("─━│┌┐└┘")


def compose(fr, atlas):
    """Cell frame -> BGRA image (h*ch, w*cw, 4) uint8, ready for the screen."""
    h, w = fr.h, fr.w
    chars = fr.chars
    flat = chars.ravel()
    uniq, inv = np.unique(flat, return_inverse=True)
    lut = np.empty(len(uniq), np.int64)
    pad_pos = None
    for i, c in enumerate(uniq):
        if c == WIDE_PAD:
            lut[i] = 0
            pad_pos = i
        else:
            lut[i] = atlas.get(c)
    idx = lut[inv].reshape(h, w)
    if pad_pos is not None:                     # right halves of wide chars
        ys, xs = np.nonzero(chars == WIDE_PAD)
        for y, x in zip(ys.tolist(), xs.tolist()):
            if x > 0:
                idx[y, x] = atlas.get(("R", chars[y, x - 1]))
    fg = np.empty((h, w, 4), np.uint8)
    bg = np.empty((h, w, 4), np.uint8)
    fg[..., :3] = fr.fg[..., ::-1]
    bg[..., :3] = fr.bg[..., ::-1]
    fg[..., 3] = bg[..., 3] = 255
    fg32, bg32 = fg.view(np.uint32)[..., 0], bg.view(np.uint32)[..., 0]
    masks = atlas.stack()[idx]                                       # h, w, ch, cw
    # most pixels are fully on/off: just pick fg or bg (one 32-bit pixel at a time)
    out = np.where(masks >= 128, fg32[:, :, None, None], bg32[:, :, None, None])
    out = out.transpose(0, 2, 1, 3).reshape(h * atlas.ch, w * atlas.cw)
    img = out.view(np.uint8).reshape(h * atlas.ch, w * atlas.cw, 4)
    # anti-aliased text edges: blend only those few pixels
    part = (masks > 0) & (masks < 255)
    if part.any():
        ci, cj, py, px = np.nonzero(part)
        al = masks[ci, cj, py, px].astype(np.uint16)[:, None]
        f = fg[ci, cj].astype(np.uint16)
        g = bg[ci, cj].astype(np.uint16)
        img[ci * atlas.ch + py, cj * atlas.cw + px] = ((g * (255 - al) + f * al) // 255).astype(np.uint8)
    return img


# =============================================================================
# desktop window
# =============================================================================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def monitors():
    """[(left, top, right, bottom, is_primary)] in virtual-screen pixels."""
    out = []
    MONPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, lprc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        u32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        out.append((r.left, r.top, r.right, r.bottom, bool(mi.dwFlags & 1)))
        return True

    u32.EnumDisplayMonitors(None, None, MONPROC(cb), 0)
    return out


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def desktop_parent():
    """-> (parent hwnd, insert-after hwnd or None)."""
    progman = u32.FindWindowW("Progman", None)
    res = ctypes.c_size_t()
    # ask Explorer to create the wallpaper WorkerW layer
    u32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    u32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    defview = u32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if defview:
        # Windows 11 24H2+: icons and wallpaper live inside Progman;
        # our window goes right below the icon layer
        return progman, defview
    # classic: the WorkerW that follows the one hosting the icons
    found = []
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if u32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            w = u32.FindWindowExW(None, h, "WorkerW", None)
            if w:
                found.append(w)
        return True

    u32.EnumWindows(ENUM(cb), 0)
    return (found[0] if found else progman), None


@WNDPROC
def _wndproc(h, m, w, l):
    if m == 0x0014:                            # WM_ERASEBKGND: we paint everything
        return 1
    return u32.DefWindowProcW(h, m, w, l)


_CLASS = "SpotifyAsciiWallpaper"
_wc = None


def _register_class():
    """One window class for every monitor's window (registered once; the
    class and its window procedure must live as long as the process)."""
    global _wc
    if _wc is not None:
        return
    _wc = WNDCLASS()
    _wc.lpfnWndProc = _wndproc
    _wc.hInstance = k32.GetModuleHandleW(None)
    _wc.lpszClassName = _CLASS
    u32.RegisterClassW(ctypes.byref(_wc))


def pump():
    """Handle the wallpaper windows' messages (call often)."""
    msg = wt.MSG()
    while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))


class DesktopWindow:
    def __init__(self, rect):
        self.rect = rect                       # monitor rect in virtual-screen pixels
        _register_class()
        WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x80000, 0x08000000, 0x80
        self.hwnd = u32.CreateWindowExW(WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                                        _CLASS, "spotify-ascii wallpaper",
                                        0x80000000, 0, 0, 10, 10, None, None, _wc.hInstance, None)
        if not self.hwnd:
            raise OSError("could not create the wallpaper window")
        u32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2)
        self.attach()
        self.hdc = u32.GetDC(self.hwnd)
        g32.SetStretchBltMode(self.hdc, 3)     # COLORONCOLOR: crisp, nearest pixel

    def attach(self):
        parent, after = desktop_parent()
        self.parent = parent
        u32.SetParent(self.hwnd, parent)
        style = (u32.GetWindowLongW(self.hwnd, -16) & 0xFFFFFFFF)
        style = (style & ~0x80000000) | 0x40000000 | 0x10000000     # -POPUP +CHILD +VISIBLE
        u32.SetWindowLongW(self.hwnd, -16, ctypes.c_int32(style & 0xFFFFFFFF).value)
        # position in the parent's client coordinates (the parent spans the virtual screen)
        pr = wt.RECT()
        u32.GetWindowRect(parent, ctypes.byref(pr))
        l, t, r, b = self.rect
        self.w, self.h = r - l, b - t
        u32.SetWindowPos(self.hwnd, after or None, l - pr.left, t - pr.top, self.w, self.h,
                         0x0010 | 0x0040)      # NOACTIVATE | SHOWWINDOW

    def alive(self):
        return bool(u32.IsWindow(self.hwnd)) and bool(u32.IsWindow(self.parent))

    def blit(self, bgra, dest):
        """Draw a BGRA image to dest=(x, y, w, h) in window pixels."""
        ih, iw = bgra.shape[:2]
        if not bgra.flags.c_contiguous:
            bgra = np.ascontiguousarray(bgra)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = iw, -ih    # top-down
        bmi.biPlanes, bmi.biBitCount = 1, 32
        dx, dy, dw, dh = dest
        g32.StretchDIBits(self.hdc, dx, dy, dw, dh, 0, 0, iw, ih,
                          bgra.ctypes.data_as(ctypes.c_void_p), ctypes.byref(bmi), 0, 0x00CC0020)

    def close(self):
        try:
            u32.ReleaseDC(self.hwnd, self.hdc)
            u32.DestroyWindow(self.hwnd)
        except Exception:
            pass


def refresh_wallpaper():
    """Ask Explorer to repaint the desktop so the normal wallpaper shows again.
    (Only a redraw: the wallpaper setting itself is never touched, so
    slideshows and Windows Spotlight keep working.)"""
    u32.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    progman = u32.FindWindowW("Progman", None)
    flags = 0x0001 | 0x0004 | 0x0080 | 0x0100                  # INVALIDATE | ERASE | ALLCHILDREN | UPDATENOW
    u32.RedrawWindow(progman, None, None, flags)
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if _cls(h) == "WorkerW":
            u32.RedrawWindow(h, None, None, flags)
        return True

    u32.EnumWindows(ENUM(cb), 0)


def covered(rect):
    """True if a maximised / full-screen window hides this monitor."""
    l, t, r, b = rect
    hit = [False]
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    skip = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

    def cb(h, lp):
        if not u32.IsWindowVisible(h) or u32.IsIconic(h):
            return True
        cloaked = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)
        if cloaked.value or _cls(h) in skip:
            return True
        wr = wt.RECT()
        u32.GetWindowRect(h, ctypes.byref(wr))
        ix = max(0, min(r, wr.right) - max(l, wr.left))
        iy = max(0, min(b, wr.bottom) - max(t, wr.top))
        if ix * iy >= 0.92 * (r - l) * (b - t) or (u32.IsZoomed(h) and ix * iy > 0.5 * (r - l) * (b - t)):
            hit[0] = True
            return False
        return True

    u32.EnumWindows(ENUM(cb), 0)
    return hit[0]


def sorted_monitors():
    """Monitors numbered 1, 2, ... from left to right."""
    return sorted(monitors(), key=lambda m: (m[0], m[1]))


def set_dpi_aware():
    """Real pixels on every monitor (no blurry scaling by Windows)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            u32.SetProcessDPIAware()
        except Exception:
            pass


class Grid:
    """Cell grid for a monitor. Cells are a whole number of screen pixels and
    the picture is drawn 1:1 (centred; the margin of less than one cell is
    filled with the picture's edge), so dots and text are as sharp as in a
    terminal. On very large cells (4K) the picture is composed at half size
    and enlarged exactly 2x, and the rows that carry text are composed again
    at full size and drawn on top."""

    def __init__(self, mw, mh, rows_wanted):
        # rows counted on the short side, so a portrait monitor gets the
        # same cell size as a landscape one
        ch = max(8, min(mw, mh) // max(10, rows_wanted))
        k = 2 if ch >= 26 else 1
        ch -= ch % k
        cw = max(4, ch // 2)
        cw -= cw % k
        self.k, self.cell_w, self.cell_h = k, cw, ch
        self.mw, self.mh = mw, mh
        self.cols, self.rows = mw // cw, mh // ch
        self.ox = (mw - self.cols * cw) // 2
        self.oy = (mh - self.rows * ch) // 2
        self.pic = Atlas(cw // k, ch // k)
        self.full = self.pic if k == 1 else Atlas(cw, ch)
        self.key = (mw, mh, rows_wanted)

    def images(self, fr, rects):
        """-> [(bgra, dest rect)] that make up the monitor's picture."""
        k, cw, ch = self.k, self.cell_w, self.cell_h
        img = compose(fr, self.pic)
        # pad (in composed pixels) so the picture reaches every screen edge
        pl = -(-self.ox // k)
        pt = -(-self.oy // k)
        pr = -(-(self.mw - self.ox - self.cols * cw) // k)
        pb = -(-(self.mh - self.oy - self.rows * ch) // k)
        if pl or pt or pr or pb:
            img = np.pad(img, ((pt, pb), (pl, pr), (0, 0)), mode="edge")
        out = [(img, (self.ox - pl * k, self.oy - pt * k, img.shape[1] * k, img.shape[0] * k))]
        if k == 1:
            return out                         # already full resolution, text included
        for y0, y1, x0, x1 in rects:
            y0, y1 = max(0, y0), min(fr.h, y1)
            x0, x1 = max(0, x0), min(fr.w, x1)
            if y0 >= y1 or x0 >= x1:
                continue
            part = compose(_View(fr, y0, y1, x0, x1), self.full)
            out.append((part, (self.ox + x0 * cw, self.oy + y0 * ch, (x1 - x0) * cw, (y1 - y0) * ch)))
        return out


class _View:
    """Rectangular window into a cell frame (what compose() needs)."""

    def __init__(self, fr, y0, y1, x0, x1):
        self.chars = fr.chars[y0:y1, x0:x1]
        self.fg = fr.fg[y0:y1, x0:x1]
        self.bg = fr.bg[y0:y1, x0:x1]
        self.h, self.w = y1 - y0, x1 - x0
        # a wide character cut in half at the left edge has no left part
        if x0 > 0:
            self.chars = self.chars.copy()
            self.chars[:, 0][self.chars[:, 0] == WIDE_PAD] = " "


def text_rows(fr, extra=()):
    """Cell rectangles that contain text: the title box (top right) and the
    seek bar / info line (bottom), plus any extra rows."""
    rects = []
    top = fr.chars[1:4]
    cols = np.flatnonzero((top == "┌") | (top == "┐") | (top == "└") | (top == "┘"))
    if cols.size:
        rects.append((1, 4, int(cols.min()), int(cols.max()) + 1))
    rects.append((fr.h - 2, fr.h, 0, fr.w))
    for y in extra:
        rects.append((y, y + 1, 0, fr.w))
    return rects


# =============================================================================
class Wallpaper:
    """The picture on the desktop of the chosen monitors.

    selection: "all", or a list of monitor numbers (1 = leftmost); anything
    else (or only unplugged monitors) means the primary monitor."""

    def __init__(self, rows=DEFAULT_ROWS, selection=None):
        self.rows = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
        self.selection = selection
        self.mons = sorted_monitors()
        self.views = {}                # monitor number -> [DesktopWindow, Grid, covered]
        self.last_check = 0.0
        self.apply()

    # --- which monitors ---------------------------------------------------------
    def primary(self):
        return next((i + 1 for i, m in enumerate(self.mons) if m[4]), 1)

    def shown(self):
        n = len(self.mons)
        if self.selection == "all":
            return list(range(1, n + 1))
        sel = self.selection if isinstance(self.selection, list) else []
        nums = sorted(i for i in sel if isinstance(i, int) and 1 <= i <= n)
        return nums or [self.primary()]

    def toggle(self, i):
        """Show / hide monitor i. False if that is not possible (no such
        monitor, or it is the last one shown)."""
        if not 1 <= i <= len(self.mons):
            return False
        sel = set(self.shown())
        if i in sel:
            if len(sel) == 1:
                return False
            sel.discard(i)
        else:
            sel.add(i)
        self.selection = sorted(sel)
        self.apply()
        return True

    def only(self, i):
        if not 1 <= i <= len(self.mons):
            return False
        self.selection = [i]
        self.apply()
        return True

    def show_all(self):
        self.selection = "all"
        self.apply()

    def apply(self):
        want = set(self.shown())
        closed = False
        for i in list(self.views):
            if i not in want:
                self.views.pop(i)[0].close()
                closed = True
        for i in sorted(want):
            if i not in self.views:
                rect = self.mons[i - 1][:4]
                self.views[i] = [DesktopWindow(rect), self._grid(rect), False]
        if closed:
            refresh_wallpaper()
        self.last_check = 0.0

    def _grid(self, rect):
        return Grid(rect[2] - rect[0], rect[3] - rect[1], self.rows)

    def set_rows(self, rows):
        self.rows = max(MIN_ROWS, min(MAX_ROWS, rows))
        for v in self.views.values():
            v[1] = self._grid(v[0].rect)

    # --- drawing ---------------------------------------------------------------
    def check(self, now):
        """Every half second: monitors plugged in / out, Explorer restarted,
        monitors hidden by a maximised window."""
        if now - self.last_check < 0.5:
            return
        self.last_check = now
        mons = sorted_monitors()
        if mons != self.mons:
            for v in self.views.values():
                v[0].close()
            self.views = {}
            self.mons = mons
            self.apply()
            refresh_wallpaper()
        for v in self.views.values():
            if not v[0].alive():                       # Explorer restarted
                v[0].attach()
            v[2] = covered(v[0].rect)

    def draw(self, vis):
        """Draw one frame on every visible monitor (monitors of the same size
        share one picture). Returns False if all of them are hidden."""
        pump()
        self.check(time.time())
        frames, done = {}, {}
        for win, grid, hidden in self.views.values():
            if hidden:
                continue
            if grid.key not in done:
                size = (grid.cols, grid.rows)
                if size not in frames:
                    frames[size] = vis.frame(*size)
                fr = frames[size]
                extra = range(fr.h // 2 - 4, fr.h // 2 + 1) if vis.state == "idle" else ()
                done[grid.key] = grid.images(fr, text_rows(fr, extra))
            for img, dest in done[grid.key]:
                win.blit(img, dest)
        return bool(done)

    def close(self):
        for v in self.views.values():
            v[0].close()
        self.views = {}
        refresh_wallpaper()
