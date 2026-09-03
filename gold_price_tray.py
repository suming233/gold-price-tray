# -*- coding: utf-8 -*-
r"""
GoldPriceTray —— Windows 任务栏托盘实时金价小工具 (v3.5 自安装版)
==================================================================
- 托盘图标直接显示实时金价数字（默认伦敦金 XAU/USD，24h 连续交易）
- 国际金价（美元/盎司）自动按实时汇率换算为人民币计价（元/克）显示
- 涨红跌绿（相对昨日收盘/昨结）
- 鼠标悬停托盘图标 → 弹出大字体实时金价悬浮卡（系统 tooltip 字体不可控，
  故用自定义置顶窗口实现，价格 27px 大字体，含美元价与汇率参考）
- 每 30 秒自动刷新
- 双击/单击托盘图标 → 弹出金价走势折线图（当日分时 / 近一周 / 近一月可切换）
- 右键菜单：查看走势、切换品种、立即刷新、开机自启开关、卸载、退出

【单文件自安装】打包为 --onefile --windowed exe 后：
  - 双击未安装目录里的 exe  →  弹出安装向导（复制自身到 %LOCALAPPDATA%\GoldPriceTray、
    注册开机自启、创建桌面/开始菜单快捷方式、写入"应用和功能"卸载项）
  - 双击安装目录里的 exe    →  正常运行托盘
  - GoldPriceTray.exe /silent    静默安装
  - GoldPriceTray.exe /uninstall 卸载（也可从"应用和功能"或托盘菜单进入）
字体按 %WINDIR% 动态解析并回退，可移植到非中文 Windows。

数据源：新浪财经行情接口 hq.sinajs.cn（hf_XAU / hf_GC / gds_AU9999 / gds_AUTD）
       汇率：新浪外汇 fx_susdcny（在岸人民币 USD/CNY）
换算公式：元/克 = 美元/盎司 × 汇率 ÷ 31.1035（1 盎司 = 31.1035 克）
"""
import re
import os
import sys
import time
import shutil
import ctypes
import tempfile
import threading
import subprocess
import urllib.request

# --windowed 单文件模式下没有 stderr，faulthandler 需跳过
try:
    import faulthandler
    if getattr(sys, "stderr", None) is not None:
        faulthandler.enable()
except Exception:
    pass

import pystray
from PIL import Image, ImageDraw, ImageFont

# 悬浮行情卡依赖 tkinter（无 GUI 环境时降级为仅系统 tooltip）
try:
    import tkinter as tk
    import tkinter.font as tkfont
    TK_OK = True
except Exception:
    TK_OK = False

# ---------------- 配置 ----------------
REFRESH_INTERVAL = 30
AUTOSTART_KEY = "GoldPriceTray"


def _find_system_font(names):
    r"""跨机器可移植：在 %WINDIR%\Fonts 下按优先级取第一个存在的字体。
    中文 Windows 必有微软雅黑(msyh)；其他环境回退 Segoe UI / Arial。"""
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    fd = os.path.join(windir, "Fonts")
    for n in names:
        p = os.path.join(fd, n)
        if os.path.exists(p):
            return p
    return None


FONT_BOLD = _find_system_font(["msyhbd.ttc", "msyh.ttc", "simhei.ttf",
                               "segoeuib.ttf", "arialbd.ttf"])
FONT_REG  = _find_system_font(["msyh.ttc", "simhei.ttf", "segoeui.ttf", "arial.ttf"])

# 品种：国际金价（美元/盎司，24h 连续交易，自动换算人民币元/克）+ 国内金价（元/克）
# 默认伦敦金 hf_XAU —— 24h 实时 + 人民币计价，随时可切换
SOURCES = {
    "hf_XAU":     "伦敦金 (现货黄金) →元/克",
    "hf_GC":      "纽约黄金 (COMEX) →元/克",
    "gds_AU9999": "沪金99 (Au99.99) 元/克",
    "gds_AUTD":   "黄金T+D 元/克",
}

OZ_TO_GRAM = 31.1035  # 1 盎司 = 31.1035 克

COLOR_UP   = (235, 77, 75, 255)
COLOR_DOWN = (46, 204, 113, 255)
COLOR_FLAT = (200, 200, 200, 255)
COLOR_ERR  = (150, 150, 150, 255)

# ---------------- 数据获取 ----------------
def fetch_usdcny():
    """新浪美元兑人民币(在岸)实时汇率；失败返回 None。
    fx_susdcny 字段：[0]时间 [1]当前汇率 [2]买价 [3]卖价 ... [9]名称 ..."""
    url = "https://hq.sinajs.cn/list=fx_susdcny"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode("gbk", errors="ignore")
        m = re.search(r'"([^"]*)"', raw)
        if not m or not m.group(1).strip():
            return None
        f = m.group(1).split(",")
        rate = float(f[1])
        return rate if 1.0 < rate < 20.0 else None
    except Exception:
        return None


# 汇率缓存（5 分钟内不重复请求；失败时沿用上次值，保证换算不中断）
_rate_cache = {"rate": None, "ts": 0.0}


def _get_cny_rate():
    now = time.time()
    if _rate_cache["rate"] and now - _rate_cache["ts"] < 300:
        return _rate_cache["rate"]
    rate = fetch_usdcny()
    if rate:
        _rate_cache["rate"] = rate
        _rate_cache["ts"] = now
    return _rate_cache["rate"]


def fetch_quote(code: str):
    """请求新浪实时行情。gds（国内）与 hf（国际）系列字段索引一致：
      [0]最新价 [1]预留 [2]买价 [3]卖价 [4]最高 [5]最低
      [6]时间 [7]昨收/昨结 [8]今开 [9]持仓 [10][11]买卖量
      [12]日期 [13]名称
    例：hf_XAU（伦敦金）4380.11,...,19:36:00,4449.00,4450.55,...,2026-09-01,伦敦金（现货黄金）
    国际品种自动换算为人民币元/克（×汇率÷31.1035），原始美元价保存在 usd_price。
    """
    url = f"https://hq.sinajs.cn/list={code}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode("gbk", errors="ignore")
        m = re.search(r'"([^"]*)"', raw)
        if not m or not m.group(1).strip():
            return None
        f = m.group(1).split(",")
        if len(f) < 14:
            return None
        price = float(f[0]) if f[0] else 0.0
        prev  = float(f[7]) if f[7] else 0.0
        high  = float(f[4]) if f[4] else 0.0
        low   = float(f[5]) if f[5] else 0.0
        open_ = float(f[8]) if f[8] else 0.0
        q = {
            "code": code, "name": f[13], "price": price, "prev": prev,
            "high": high, "low": low, "open": open_,
            "time": f[6], "date": f[12],
            "cny": False, "rate": None, "usd_price": None,
            "unit": "美元/盎司" if code.startswith("hf_") else "元/克",
        }
        if code.startswith("hf_"):
            rate = _get_cny_rate()
            if rate:
                k = rate / OZ_TO_GRAM
                q["usd_price"] = price
                q["rate"] = rate
                q["price"] = price * k
                q["prev"]  = prev  * k
                q["high"]  = high  * k
                q["low"]   = low   * k
                q["open"]  = open_ * k
                q["cny"] = True
                q["unit"] = "元/克"
        q["change"] = q["price"] - q["prev"]
        q["pct"] = (q["change"] / q["prev"] * 100) if q["prev"] else 0.0
        return q
    except Exception as e:
        return {"error": str(e)}

# ---------------- 图标渲染 ----------------
def _font(path, size):
    try:
        if path:
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    return ImageFont.load_default()

def render_icon(text: str, color) -> Image.Image:
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 动态字号适配 32px 图标：3 位 15px / 4 位 13px / 5 位(如 947.5) 11px / 更长 10px
    n = len(text)
    size = 15 if n <= 3 else (13 if n == 4 else (11 if n == 5 else 10))
    font = _font(FONT_BOLD, size)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((32 - tw) / 2 - bbox[0], (32 - th) / 2 - bbox[1] + 1), text,
           font=font, fill=color)
    return img

# ---------------- 悬浮行情卡（大字体） ----------------
def _get_tray_rect():
    """获取 Windows 通知区域（托盘图标区）屏幕矩形 (l, t, r, b)；失败返回 None"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        tray = user32.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return None
        notify = user32.FindWindowExW(tray, 0, "TrayNotifyWnd", None)
        if not notify:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(notify, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def _cursor_pos():
    """获取鼠标屏幕坐标 (x, y)；失败返回 None"""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        return None


class HoverCard:
    """鼠标悬停托盘图标时弹出的大字体实时金价悬浮卡。
    系统托盘 tooltip 字体大小不可控，故用 tkinter 无边框置顶窗口实现：
    轮询鼠标位置，进入通知区域即显示，离开即隐藏；行情每 300ms 检查刷新。"""
    BG      = "#1B1D29"
    FG_NAME = "#9AA0B5"
    FG_MAIN = "#F2F3F7"
    FG_SUB  = "#C7CBD9"

    def __init__(self, get_quote, on_active=None):
        self.get_quote = get_quote
        self.on_active = on_active
        self.root = None
        self.visible = False
        self._sig = None
        self._labels = {}
        self.card_rect = None
        self._stop = False

    def start(self):
        if not TK_OK:
            return
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    @property
    def active(self):
        return self.visible

    def _run(self):
        try:
            self.root = tk.Tk()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.configure(bg=self.BG)
            self._build()
            self.root.withdraw()
            self._poll()
            self.root.mainloop()
        except Exception:
            pass

    def _build(self):
        # v3.4：在 v3.3（等比例 1/2）基础上整体再放大 1.5 倍
        # 像素字号（负值=像素）：18→27、10→15、9→14、8→12
        f_price = tkfont.Font(family="Microsoft YaHei UI", size=-27, weight="bold")
        f_chg   = tkfont.Font(family="Microsoft YaHei UI", size=-15, weight="bold")
        f_name  = tkfont.Font(family="Microsoft YaHei UI", size=-14)
        f_sub   = tkfont.Font(family="Microsoft YaHei UI", size=-12)
        # pady 用 (上,下) 元组微调纵向：全 2 时高比仅 1.459，(2,3) 可得 1.514 更贴近 1.5
        pad = {"padx": 14, "pady": (2, 3)}
        rows = {
            "name":  (f_name,  self.FG_NAME),
            "price": (f_price, self.FG_MAIN),
            "chg":   (f_chg,   self.FG_MAIN),
            "usd":   (f_sub,   self.FG_SUB),
            "ohcl":  (f_sub,   self.FG_SUB),
            "time":  (f_sub,   self.FG_SUB),
        }
        for key, (font, fg) in rows.items():
            lb = tk.Label(self.root, bg=self.BG, fg=fg, font=font, anchor="w",
                          justify="left", bd=0, highlightthickness=0)
            lb.pack(fill="x", **pad)
            self._labels[key] = lb

    def _poll(self):
        if self._stop:
            if self.root is not None:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            return
        try:
            pos = _cursor_pos()
            r = _get_tray_rect()
            inside = False
            if r and pos:
                l, t, rr, b = r
                pad = 10
                inside = (l - pad <= pos[0] <= rr + pad and
                          t - pad <= pos[1] <= b + pad)
            # 卡片已显示时把卡片矩形并入热区，避免鼠标移到卡片上闪动
            if self.visible and pos and self.card_rect:
                cx1, cy1, cx2, cy2 = self.card_rect
                if cx1 <= pos[0] <= cx2 and cy1 <= pos[1] <= cy2:
                    inside = True
            q = self.get_quote()
            if inside and q and "price" in q:
                self._show(q, r, pos)
            else:
                self._hide()
        except Exception:
            pass
        finally:
            if self.root is not None:
                self.root.after(300, self._poll)

    def _show(self, q, tray_rect, pos):
        sig = (q.get("date"), q.get("time"), round(q.get("price", 0), 3))
        if sig != self._sig:
            self._sig = sig
            self._update_labels(q)
        # 定位：优先通知区域上方偏右；拿不到托盘矩形则跟随鼠标（偏移随卡片实际尺寸动态计算）
        if tray_rect:
            x = tray_rect[2] - self._card_w() - 36
            y = tray_rect[1] - self._card_h() - 9
            if y < 40:
                y = tray_rect[3] + 12
        else:
            x = pos[0] - self._card_w() - 15
            y = pos[1] - self._card_h() - 9
            if y < 40:
                y = pos[1] + 18
        self.root.geometry(f"+{max(8, int(x))}+{int(y)}")
        self.root.update_idletasks()
        try:
            w = self.root.winfo_reqwidth()
            h = self.root.winfo_reqheight()
            self.card_rect = (int(x), int(y), int(x) + w, int(y) + h)
        except Exception:
            self.card_rect = None
        if not self.visible:
            self.root.deiconify()
            self.root.lift()
            self.visible = True
            if self.on_active:
                try:
                    self.on_active(True)
                except Exception:
                    pass

    def _card_h(self):
        try:
            self.root.update_idletasks()
            return self.root.winfo_reqheight()
        except Exception:
            return 168

    def _card_w(self):
        try:
            self.root.update_idletasks()
            return self.root.winfo_reqwidth()
        except Exception:
            return 270

    def _update_labels(self, q):
        up = q["change"] > 0
        down = q["change"] < 0
        color = "#EB4D4B" if up else ("#2ECC71" if down else "#C8C8C8")
        arrow = "▲" if up else ("▼" if down else "＝")
        self._labels["name"].configure(text=q["name"])
        if q.get("cny"):
            self._labels["price"].configure(text=f"{q['price']:.2f} 元/克", fg=color)
            self._labels["usd"].configure(
                text=f"美元 {q['usd_price']:.2f}/盎司    汇率 {q['rate']:.4f}")
        else:
            unit = q.get("unit", "美元/盎司")
            self._labels["price"].configure(text=f"{q['price']:.2f} {unit}", fg=color)
            self._labels["usd"].configure(text="")
        self._labels["chg"].configure(
            text=f"{arrow} {q['change']:+.2f} ({q['pct']:+.2f}%)", fg=color)
        self._labels["ohcl"].configure(
            text=f"昨收 {q['prev']:.2f}   今开 {q['open']:.2f}\n"
                 f"最高 {q['high']:.2f}   最低 {q['low']:.2f}")
        self._labels["time"].configure(text=f"时间 {q['date']} {q['time']}")

    def _hide(self):
        if self.visible and self.root is not None:
            try:
                self.root.withdraw()
            except Exception:
                pass
            self.visible = False
            if self.on_active:
                try:
                    self.on_active(False)
                except Exception:
                    pass
        self.card_rect = None

# ---------------- 开机自启 ----------------
def get_autostart_status() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        winreg.QueryValueEx(key, AUTOSTART_KEY)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def set_autostart(enable: bool) -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH,
                             0, winreg.KEY_SET_VALUE)
        if enable:
            # 优先注册安装目录里的正式 exe（安装器运行时自身位置不是最终位置）；
            # 旧部署目录若仍有 start_hidden.vbs 则沿用（兼容 v3.4 及更早版本）
            if _frozen() and os.path.exists(INSTALLED_EXE):
                target = INSTALLED_EXE
            else:
                base = os.path.dirname(sys.executable) if _frozen() \
                    else os.path.dirname(os.path.abspath(__file__))
                vbs = os.path.join(base, "start_hidden.vbs")
                target = vbs if os.path.exists(vbs) else (
                    sys.executable if _frozen() else os.path.abspath(__file__))
            winreg.SetValueEx(key, AUTOSTART_KEY, 0, winreg.REG_SZ, f'"{target}"')
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_KEY)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

# ---------------- 自安装 / 卸载（单文件移植版） ----------------
APP_NAME    = "GoldPriceTray"
APP_TITLE   = "金价托盘 GoldPriceTray"
APP_VERSION = "3.5.0"

RUN_KEY_PATH       = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GoldPriceTray"

INSTALL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    APP_NAME)
INSTALLED_EXE = os.path.join(INSTALL_DIR, APP_NAME + ".exe")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _com_guid(s):
    g = _GUID()
    if ctypes.windll.ole32.IIDFromString(s, ctypes.byref(g)) != 0:
        raise ValueError(s)
    return g


def _frozen():
    return bool(getattr(sys, "frozen", False))


def _is_installed():
    """当前进程是否正运行在安装目录内"""
    return (_frozen() and os.path.exists(INSTALLED_EXE)
            and os.path.normcase(os.path.abspath(sys.executable))
            == os.path.normcase(os.path.abspath(INSTALLED_EXE)))


def _known_folder(guid_str):
    """SHGetKnownFolderPath：正确处理被 OneDrive 重定向的桌面等特殊路径"""
    try:
        g = _com_guid(guid_str)
        p = ctypes.c_void_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(g), 0, None, ctypes.byref(p)) != 0 or not p.value:
            return None
        s = ctypes.wstring_at(p.value)
        ctypes.windll.ole32.CoTaskMemFree(p)
        return s
    except Exception:
        return None


def create_shortcut(lnk_path, target, workdir="", desc="", icon_path=None):
    """通过 COM IShellLinkW/IPersistFile 创建 .lnk 快捷方式（纯 ctypes，无第三方依赖）"""
    try:
        from ctypes import byref, c_void_p, c_int, sizeof
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        try:
            clsid = _com_guid("{00021401-0000-0000-C000-000000000046}")   # CLSID_ShellLink
            iid_l = _com_guid("{000214F9-0000-0000-C000-000000000046}")   # IID_IShellLinkW
            iid_f = _com_guid("{0000010b-0000-0000-C000-000000000046}")   # IID_IPersistFile
            p_link = ctypes.c_void_p()
            # CLSCTX_INPROC_SERVER = 1
            if ole32.CoCreateInstance(byref(clsid), None, 1,
                                      byref(iid_l), byref(p_link)) != 0 \
                    or not p_link.value:
                return False
            iface = p_link.value
            vtbl = ctypes.cast(iface, ctypes.POINTER(c_void_p)).contents.value

            def fn(vbase, index, restype, *argtypes):
                # 先从 vtable 槽位读出函数地址，再按该地址构造调用指针
                # （不能把槽位地址直接当函数地址，否则跳进数据区直接崩）
                slot = ctypes.c_void_p.from_address(
                    vbase + index * sizeof(c_void_p)).value
                return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(slot)

            fn(vtbl, 7,  ctypes.HRESULT, ctypes.c_wchar_p)(iface, desc)      # SetDescription
            fn(vtbl, 9,  ctypes.HRESULT, ctypes.c_wchar_p)(
                iface, workdir or os.path.dirname(target))                   # SetWorkingDirectory
            fn(vtbl, 11, ctypes.HRESULT, ctypes.c_wchar_p)(iface, "")        # SetArguments
            fn(vtbl, 15, ctypes.HRESULT, c_int)(iface, 1)                    # SetShowCmd
            fn(vtbl, 17, ctypes.HRESULT, ctypes.c_wchar_p, c_int)(
                iface, icon_path or target, 0)                               # SetIconLocation
            fn(vtbl, 20, ctypes.HRESULT, ctypes.c_wchar_p)(iface, target)    # SetPath

            p_file = ctypes.c_void_p()
            if fn(vtbl, 0, ctypes.HRESULT, ctypes.POINTER(_GUID),
                  ctypes.POINTER(c_void_p))(iface, byref(iid_f), byref(p_file)) != 0 \
                    or not p_file.value:
                return False
            fiface = p_file.value
            fvtbl = ctypes.cast(fiface, ctypes.POINTER(c_void_p)).contents.value
            hr = fn(fvtbl, 6, ctypes.HRESULT, ctypes.c_wchar_p,
                    ctypes.c_int)(fiface, lnk_path, 1)                       # IPersistFile::Save
            fn(fvtbl, 2, ctypes.c_ulong)(fiface)                             # Release
            fn(vtbl, 2, ctypes.c_ulong)(iface)                               # Release
            return hr == 0
        finally:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
    except Exception:
        return False


def _shortcut_paths():
    r"""桌面 + 开始菜单\Programs 快捷方式完整路径"""
    paths = []
    desktop = _known_folder("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")     # FOLDERID_Desktop
    if desktop:
        paths.append(os.path.join(desktop, "金价托盘.lnk"))
    progs = _known_folder("{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}")       # FOLDERID_Programs
    if progs:
        paths.append(os.path.join(progs, "金价托盘.lnk"))
    return paths


def _make_shortcuts():
    for lnk in _shortcut_paths():
        create_shortcut(lnk, INSTALLED_EXE, workdir=INSTALL_DIR,
                        desc="金价托盘 - 托盘实时金价小工具", icon_path=INSTALLED_EXE)


def _remove_shortcuts():
    for lnk in _shortcut_paths():
        try:
            if os.path.exists(lnk):
                os.remove(lnk)
        except Exception:
            pass


def _write_uninstall_key():
    """写入"应用和功能"（控制面板卸载列表）条目"""
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY_PATH)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, INSTALL_DIR)
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, INSTALLED_EXE)
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ,
                          f'"{INSTALLED_EXE}" /uninstall')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _delete_uninstall_key():
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY_PATH)
    except Exception:
        pass


def do_install(autostart=True, desktop_shortcut=True, launch=True):
    """把自身复制到 %LOCALAPPDATA%\\GoldPriceTray 并完成全部注册"""
    os.makedirs(INSTALL_DIR, exist_ok=True)
    src = os.path.abspath(sys.executable)
    dst = INSTALLED_EXE
    if os.path.normcase(src) != os.path.normcase(dst):
        # 清掉历史残留（升级安装时旧 exe 可能被占用）
        old = dst + ".old"
        if os.path.exists(old):
            try:
                os.remove(old)
            except Exception:
                pass
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except PermissionError:
                try:
                    os.replace(dst, old)
                except Exception:
                    pass
        shutil.copy2(src, dst)
    set_autostart(autostart)
    _write_uninstall_key()
    if desktop_shortcut:
        _make_shortcuts()
    if launch and os.path.exists(dst):
        subprocess.Popen([dst], cwd=INSTALL_DIR,
                         creationflags=subprocess.DETACHED_PROCESS)
    return True


def do_uninstall():
    """移除自启/卸载项/快捷方式；程序文件由延迟脚本在进程退出后删除"""
    set_autostart(False)
    _delete_uninstall_key()
    _remove_shortcuts()
    if os.path.isdir(INSTALL_DIR):
        bat = os.path.join(tempfile.gettempdir(), "GoldPriceTray_uninstall.cmd")
        with open(bat, "w", encoding="gbk", errors="ignore") as f:
            f.write("@echo off\r\n")
            f.write("ping -n 2 127.0.0.1 >nul\r\n")                # 先等卸载器退出
            f.write("taskkill /F /IM GoldPriceTray.exe >nul 2>&1\r\n")
            f.write("ping -n 3 127.0.0.1 >nul\r\n")
            f.write(f'rd /S /Q "{INSTALL_DIR}"\r\n')
            f.write('del "%~f0"\r\n')
        subprocess.Popen(["cmd.exe", "/c", bat],
                         creationflags=subprocess.CREATE_NO_WINDOW)


def _single_instance_ok():
    """已有一个托盘实例运行时，再次双击直接退出，避免双图标"""
    try:
        h = ctypes.windll.kernel32.CreateMutexW(
            None, False, "GoldPriceTray_SingleInstance")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def run_installer_window():
    """双击安装向导（tkinter，默认选项勾好，点一下即装完）"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    already = os.path.exists(INSTALLED_EXE)
    root = tk.Tk()
    root.title("金价托盘 - 安装")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frm = ttk.Frame(root, padding=20)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="金价托盘 GoldPriceTray",
              font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    ttk.Label(frm, text="任务栏托盘实时金价 · 人民币计价 · 走势图\n安装后开机自动运行，全程无需管理员权限",
              foreground="#666666").pack(anchor="w", pady=(2, 10))
    ttk.Label(frm, text=f"安装位置：{INSTALL_DIR}").pack(anchor="w")

    var_auto = tk.BooleanVar(value=True)
    var_link = tk.BooleanVar(value=True)
    var_run = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="开机自动启动", variable=var_auto).pack(anchor="w", pady=(10, 0))
    ttk.Checkbutton(frm, text="创建桌面和开始菜单快捷方式", variable=var_link).pack(anchor="w")
    ttk.Checkbutton(frm, text="安装完成后立即启动", variable=var_run).pack(anchor="w")

    btns = ttk.Frame(frm)
    btns.pack(fill="x", pady=(14, 0))

    def _install():
        try:
            do_install(var_auto.get(), var_link.get(), var_run.get())
        except Exception as e:
            messagebox.showerror("安装失败", f"安装过程中出现错误：\n{e}", parent=root)
            return
        messagebox.showinfo(
            "安装完成",
            "金价托盘已安装到：\n" + INSTALL_DIR +
            "\n\n开机自启：" + ("已开启" if var_auto.get() else "未开启") +
            "\n\n提示：单文件版首次启动需解压，托盘图标约 2~5 秒后出现。",
            parent=root)
        root.destroy()

    def _uninstall():
        if messagebox.askyesno(
                "卸载确认",
                "将移除开机自启、快捷方式与程序文件。\n确定要卸载金价托盘吗？",
                parent=root):
            do_uninstall()
            messagebox.showinfo("已卸载", "金价托盘已从本机移除。", parent=root)
            root.destroy()

    btn_i = ttk.Button(btns, text="重新安装" if already else "立即安装", command=_install)
    btn_i.pack(side="left", padx=(0, 8))
    btn_i.focus_set()
    if already:
        ttk.Button(btns, text="卸载", command=_uninstall).pack(side="left", padx=(0, 8))
    ttk.Button(btns, text="退出", command=root.destroy).pack(side="right")

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}"
                  f"+{(root.winfo_screenheight() - h) // 3}")
    root.mainloop()


def run_uninstall_window():
    """由"应用和功能"的卸载入口调用"""
    import tkinter as tk
    from tkinter import messagebox
    r = tk.Tk()
    r.withdraw()
    if messagebox.askyesno("卸载金价托盘",
                           "将移除开机自启、快捷方式与程序文件。\n确定卸载吗？"):
        do_uninstall()
        messagebox.showinfo("已卸载", "金价托盘已从本机移除。")
    r.destroy()

# ---------------- 应用主体 ----------------
class GoldTray:
    def __init__(self):
        self.source = "hf_XAU"
        self.quote = None
        self.last_error = None
        self.icon = None
        self.running = True
        self._last_title = ""
        self.hover = HoverCard(lambda: self.quote, self._on_hover)

    def _on_hover(self, active):
        """悬浮卡显示/隐藏时切换：显示大卡期间清空系统小字 tooltip，避免重叠"""
        if not self.icon:
            return
        try:
            self.icon.title = "" if active else self._last_title
        except Exception:
            pass

    def menu(self):
        src = self.source
        return pystray.Menu(
            pystray.MenuItem("金价小工具 (每30秒刷新)", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("查看金价走势…", self.open_chart, default=True),
            pystray.MenuItem(
                lambda item: f"{SOURCES[self.source]}  {self._price_str()}",
                self.refresh_now),
            pystray.MenuItem("立即刷新", self.refresh_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("显示品种", pystray.Menu(
                *[pystray.MenuItem(name, lambda i, c=c: self.set_source(c),
                                   checked=lambda i, c=c: src == c)
                  for c, name in SOURCES.items()]
            )),
            pystray.MenuItem("开机自启动", self.toggle_autostart,
                             checked=self._autostart_checked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("关于", self.show_about),
            pystray.MenuItem("卸载并移除…", self.uninstall_self,
                             visible=lambda item: _frozen()),
            pystray.MenuItem("退出", self.quit),
        )

    def _price_str(self):
        if self.quote and "price" in self.quote:
            q = self.quote
            if q.get("cny"):
                return (f"{q['price']:.2f} 元/克  {q['change']:+.2f} "
                        f"({q['pct']:+.2f}%)")
            unit = "美元/盎司" if q["code"].startswith("hf_") else "元/克"
            return f"{q['price']:.2f} {unit}  {q['change']:+.2f} ({q['pct']:+.2f}%)"
        if self.last_error:
            return "获取失败"
        return "加载中…"

    def _autostart_checked(self, item):
        return get_autostart_status()

    def refresh_now(self, icon=None, item=None):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def set_source(self, code):
        self.source = code
        self.refresh_now()

    def toggle_autostart(self, icon, item):
        enable = not get_autostart_status()
        set_autostart(enable)
        self._toast("开机自启动已" + ("开启" if enable else "关闭"))
        icon.update_menu()

    def show_about(self, icon, item):
        icon.notify(
            f"金价托盘 GoldPriceTray v{APP_VERSION}\n"
            "国际/国内金价实时托盘显示 + 走势图\n"
            "默认伦敦金 XAU/USD，自动换算人民币元/克\n"
            "可切换 纽约黄金 / 沪金99 / 黄金T+D\n"
            "涨红跌绿 · 双击图标查看走势",
            "金价小工具")

    def uninstall_self(self, icon, item):
        """托盘菜单卸载：先记下要删的东西，停止自身后交给延迟脚本收尾"""
        self.running = False
        self.hover.stop()
        do_uninstall()
        icon.stop()

    def quit(self, icon, item):
        self.running = False
        self.hover.stop()
        icon.stop()

    def _toast(self, msg):
        if self.icon:
            try:
                self.icon.notify(msg, "金价小工具")
            except Exception:
                pass

    def open_chart(self, icon=None, item=None):
        def _run():
            try:
                import chart
                chart.run_chart(lambda: self.quote or fetch_quote(self.source))
            except Exception as e:
                self._log(f"chart error: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _refresh_worker(self):
        try:
            q = fetch_quote(self.source)
        except Exception as e:
            self._log(f"fetch exception: {e}")
            q = None
        if q is None or "error" in q:
            self.last_error = (q or {}).get("error", "未知错误")
            self.quote = None
            if self.icon:
                try:
                    self.icon.icon = render_icon("--", COLOR_ERR)
                    self.icon.title = f"金价获取失败: {self.last_error}"
                except Exception as e:
                    self._log(f"icon update failed: {e}")
            return
        self.quote = q
        self.last_error = None
        # 人民币计价：<1000 显示 1 位小数(如 947.5)，>=1000 显示整数(如 1012)；美元显示整数
        if q.get("cny"):
            txt = f"{q['price']:.1f}" if q["price"] < 1000 else f"{q['price']:.0f}"
        else:
            txt = f"{q['price']:.0f}"
        if q["change"] > 0:
            color = COLOR_UP
        elif q["change"] < 0:
            color = COLOR_DOWN
        else:
            color = COLOR_FLAT
        if self.icon:
            try:
                self.icon.icon = render_icon(txt, color)
                unit = q.get("unit", "元/克" if self.source.startswith("gds_") else "美元/盎司")
                arrow = "▲" if q["change"] > 0 else ("▼" if q["change"] < 0 else "＝")
                # 系统 tooltip 上限 128 字符，超长会抛异常——这里只放精简版，
                # 完整行情（OHLC/汇率等）由大字体悬浮卡展示
                if q.get("cny"):
                    title = (
                        f"{q['name']} {q['price']:.2f} 元/克 {arrow} {q['pct']:+.2f}%\n"
                        f"美元 {q['usd_price']:.2f}/盎司  汇率 {q['rate']:.4f}\n"
                        f"时间 {q['date'][5:]} {q['time']}"
                    )
                else:
                    title = (
                        f"{q['name']} {q['price']:.2f} {unit} {arrow} {q['pct']:+.2f}%\n"
                        f"时间 {q['date'][5:]} {q['time']}"
                    )
                self._last_title = title
                self.icon.title = title if not self.hover.active else ""
                self._log(f"updated: {txt} {q['change']:+.2f} ({q['pct']:+.2f}%)")
            except Exception as e:
                self._log(f"icon update failed: {e}")

    @staticmethod
    def _log(msg):
        try:
            # 日志固定写入安装目录（可移植版为 %LOCALAPPDATA%\GoldPriceTray）；
            # 目录不可写时退回临时目录
            try:
                os.makedirs(INSTALL_DIR, exist_ok=True)
                d = INSTALL_DIR
            except Exception:
                d = tempfile.gettempdir()
            with open(os.path.join(d, "GoldPriceTray.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass

    def _loop(self):
        while self.running:
            self._refresh_worker()
            for _ in range(REFRESH_INTERVAL):
                if not self.running:
                    return
                time.sleep(1)

    def _make_icon(self):
        return pystray.Icon("GoldPriceTray",
                            render_icon("…", COLOR_FLAT),
                            "金价小工具…", self.menu())

    def run(self):
        """主入口：pystray 消息循环偶发静默退出（GetMessage 返回 0/-1），
        这里包一层自动重启，保证托盘图标始终存活。刷新线程只启动一次。"""
        self.hover.start()
        threading.Thread(target=self._loop, daemon=True).start()
        while self.running:
            self.icon = self._make_icon()
            try:
                self.icon.run()
            except Exception as e:
                self._log(f"icon loop error: {e}")
            if not self.running:
                break
            self._log("icon loop exited unexpectedly, restarting in 3s…")
            time.sleep(3)

if __name__ == "__main__":
    if _frozen():
        # 单文件版分派：/uninstall 卸载 · /silent 静默安装 · 不在安装目录 → 安装向导
        arg = (sys.argv[1].lower() if len(sys.argv) > 1 else "")
        if arg in ("/uninstall", "-uninstall", "--uninstall"):
            run_uninstall_window()
        elif arg in ("/silent", "/s", "-silent"):
            do_install(True, True, True)
        elif arg in ("/install", "-install", "--install") or not _is_installed():
            run_installer_window()
        elif _single_instance_ok():
            GoldTray().run()
        # 检测到已有托盘实例时静默退出，避免双图标
    else:
        GoldTray().run()
