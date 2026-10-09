# -*- coding: utf-8 -*-
r"""
GoldPriceTray —— Windows 任务栏托盘实时金价小工具 (v3.11.1 自安装版)
==================================================================
- 托盘图标直接显示实时金价数字（默认**浙商银行积存金**，元/克）
- 国际金价（美元/盎司）自动按实时汇率换算为人民币计价（元/克）显示
- 涨红跌绿（相对昨日收盘/昨结）
- 鼠标**停在托盘图标本体上**（精确定位图标矩形 + 停留 1 秒）→ 弹出实时金价悬浮卡：
  详情卡整体放大 1.5 倍，价格 69px 大字号、单独染涨跌色，单位与其余信息用小字
- 悬停期间**主动清空系统原生 tooltip**（鼠标一进图标热区就把 szTip 置空），
  保证屏幕上只有悬浮卡一个界面；移出后恢复文本
- 划到通知区域其他地方不会弹，前台跑全屏程序时一律不弹
- 每 30 秒自动刷新
- 日志有界：只在首次取价 / 涨跌转向 / 出错与恢复 / 每小时心跳时落盘，
  且文件超过 256 KB 自动轮转 —— 长期运行不会把磁盘写满
- 双击/单击托盘图标 → 弹出金价走势折线图（当日分时 / 近一周 / 近一月可切换）
- 右键菜单：查看走势、切换品种、立即刷新、开机自启开关、卸载、退出

【单文件自安装】打包为 --onefile --windowed exe 后：
  - 双击未安装目录里的 exe  →  弹出安装向导（复制自身到 %LOCALAPPDATA%\GoldPriceTray、
    注册开机自启、创建桌面/开始菜单快捷方式、写入"应用和功能"卸载项）
  - 双击安装目录里的 exe    →  正常运行托盘
  - GoldPriceTray.exe /silent    静默安装
  - GoldPriceTray.exe /uninstall 卸载（也可从"应用和功能"或托盘菜单进入）
字体按 %WINDIR% 动态解析并回退，可移植到非中文 Windows。

数据源：浙商银行积存金 —— 京东金融黄金频道公开报价接口
          api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice?productSku=1961543816
       其他品种：新浪财经行情接口 hq.sinajs.cn（hf_XAU / hf_GC / gds_AU9999 / gds_AUTD）
       汇率：新浪外汇 fx_susdcny（在岸人民币 USD/CNY）
换算公式：元/克 = 美元/盎司 × 汇率 ÷ 31.1035（1 盎司 = 31.1035 克）
"""
import re
import os
import sys
import json
import math
import queue
import time
import shutil
import ctypes
import tempfile
import threading
import subprocess
import urllib.request
from quote_http import open_quote


def enable_dpi_awareness():
    """Use physical screen coordinates consistently for tray, pointer and Tk."""
    try:
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        if fn(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

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

# ---------------- 日志策略（有界存储）----------------
# 日志只用于事后排查，旧内容没有保留价值。两条规则共同保证磁盘占用有上限：
#   1) 节流：不再每 30 秒写一行（那是 2880 行/天、约 50 MB/年 的纯噪音），
#      只在"有意义"时落盘 —— 首次成功、涨跌转向、错误、恢复、每小时心跳。
#   2) 轮转：文件超过 LOG_MAX_BYTES 就只保留最后 LOG_KEEP_LINES 行。
# 节流后实际写入约 25 行/天（≈1.2 KB/天），轮转只是兜底，让它"必定"有界。
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_LINES = 800
LOG_HEARTBEAT_SEC = 3600
# "方向翻转"这条也要设最小间隔：金价在昨收附近反复穿越时，方向可能每 30 秒
# 就翻一次，不设闸门最坏会回到 2880 行/天。10 分钟足够记录有意义的方向变化。
LOG_MIN_GAP_SEC = 600

# 悬浮卡"悬停多久才弹出"（毫秒）。仿 QQ / Windows 原生 tooltip 的滞后手感：
# 鼠标移入托盘图标区先不弹，停够这个时长才显示；中途移出则取消，不弹。
HOVER_DELAY_MS = 1000

# 鼠标离开热区后再等这么久才收卡（毫秒）。
# 用途仅一个：鼠标从图标滑向卡片时会短暂穿过两者之间的空隙，
# 没有宽限期就会"隐藏→立刻重显"地闪一下。
# 注意它必须与 _hit_test 的"桥"区域配合 —— 光靠宽限期会显得迟钝。
HIDE_GRACE_MS = 250

# 悬浮行情卡的统一视觉比例；字体、控件间距和定位间距同步缩放。
HOVER_CARD_SCALE = 1.5


def _card_px(value):
    return int(value * HOVER_CARD_SCALE + 0.5)

# 用户可在 %LOCALAPPDATA%\GoldPriceTray\settings.json 覆写悬停延迟，
# 方便不用重新打包 exe 就能调手感（{"hover_delay_ms": 600}）。
_SETTINGS_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
    "GoldPriceTray", "settings.json")

# 当前托盘 tooltip 文案（HoverCard 按名字定位本程序图标时要用）
_g_tray_title = ""


def _set_tray_title(text):
    global _g_tray_title
    _g_tray_title = text


def load_hover_delay_ms():
    """读悬停延迟；文件缺失/损坏时回退默认值。钳制在 0~10s。"""
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            v = int(json.load(f).get("hover_delay_ms", HOVER_DELAY_MS))
        return max(0, min(v, 10000))
    except Exception:
        return HOVER_DELAY_MS


_settings_lock = threading.Lock()


def load_source():
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            code = json.load(f).get("source")
        return code if isinstance(code, str) and code in SOURCES else DEFAULT_SOURCE
    except Exception:
        return DEFAULT_SOURCE


def save_source(code):
    """Preserve other settings and replace the file atomically."""
    with _settings_lock:
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        settings["source"] = code
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        temporary = _SETTINGS_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(temporary, _SETTINGS_FILE)


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

# 品种：浙商银行积存金（元/克，默认主显示）+ 国际金价（美元/盎司，自动换算）
#      + 国内金价（元/克）
# ZS_JCN 是关键字，不能改 —— fetch_quote() 靠它分流到浙商接口。
SOURCES = {
    "ZS_JCN":     "浙商银行积存金",
    "hf_XAU":     "伦敦金 (现货黄金) →元/克",
    "hf_GC":      "纽约黄金 (COMEX) →元/克",
    "gds_AU9999": "沪金99 (Au99.99) 元/克",
    "gds_AUTD":   "黄金T+D 元/克",
}

DEFAULT_SOURCE = "ZS_JCN"

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
        with open_quote(req, timeout=8) as r:
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


def _fetch_sina(code: str):
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
        with open_quote(req, timeout=8) as r:
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
        if not all(math.isfinite(v) and v > 0 for v in
                   (price, prev, high, low, open_)):
            return {"error": "新浪报价为空或异常"}
        q = {
            "code": code, "name": f[13], "price": price, "prev": prev,
            "high": high, "low": low, "open": open_,
            "time": f[6], "date": f[12],
            "cny": not code.startswith("hf_"), "rate": None, "usd_price": None,
            "unit": "美元/盎司" if code.startswith("hf_") else "元/克",
        }
        if code.startswith("hf_"):
            rate = _get_cny_rate()
            if not rate:
                return {"error": "无法获取美元兑人民币汇率，暂不能换算元/克"}
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
        q["has_ohlc"] = True
        return q
    except Exception as e:
        return {"error": str(e)}


# ---------------- 浙商银行积存金 ----------------
# 京东金融黄金频道对公众开放的实时报价接口（productSku 即浙商银行积存金）。
# 返回 datas：price 当前价 / yesterdayPrice 昨收 / upAndDownAmt 涨跌额 /
#             upAndDownRate 涨跌幅 / time 毫秒时间戳。
# 该接口**不提供 OHLC 与 K 线**，卡片据此隐藏最高/最低/今开行。
ZHESHANG_SKU = "1961543816"
ZHESHANG_URL = ("https://api.jdjygold.com/gw2/generic/jrm/h5/m/"
                f"stdLatestPrice?productSku={ZHESHANG_SKU}")

# 国际金价参照行的缓存：浙商接口每 30s 才取一次，参照价跟它同频即可
_ref_cache = {"quote": None, "ts": 0.0}


def fetch_zheshang_quote():
    """浙商银行积存金实时价（元/克）。

    涨跌以接口给的 yesterdayPrice 为基准（银行牌的"昨收"，非交易所昨结）。
    额外挂一条国际金价参照行，方便判断银行牌价相对国际盘的溢价。"""
    try:
        req = urllib.request.Request(ZHESHANG_URL, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Referer": "https://m.jd.com/",
            "Accept": "application/json, text/plain, */*",
        })
        with open_quote(req, timeout=8) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        d = json.loads(raw)
        if not d.get("success"):
            return {"error": d.get("resultMsg") or "浙商接口返回失败"}
        ds = (d.get("resultData") or {}).get("datas") or {}
        price = float(ds.get("price") or 0)
        prev = float(ds.get("yesterdayPrice") or 0)
        if not (50.0 < price < 100000.0) or not math.isfinite(prev) or prev <= 0:
            return {"error": "浙商积存金报价异常"}
        amt = ds.get("upAndDownAmt")
        amt = float(amt) if amt not in (None, "") else price - prev
        try:
            pct = float(str(ds.get("upAndDownRate") or "").rstrip("%"))
        except ValueError:
            pct = amt / prev * 100
        if not math.isfinite(amt) or not math.isfinite(pct):
            return {"error": "浙商积存金涨跌数据异常"}
        try:
            timestamp = int(ds.get("time") or 0)
            if timestamp <= 0:
                raise ValueError("缺少行情时间")
            dt = time.localtime(timestamp / 1000)
        except Exception:
            dt = time.localtime()
        q = {
            "code": DEFAULT_SOURCE, "name": SOURCES[DEFAULT_SOURCE],
            "price": price, "prev": prev, "open": None,
            "high": None, "low": None,
            "change": amt, "pct": pct,
            "date": time.strftime("%Y-%m-%d", dt),
            "time": time.strftime("%H:%M:%S", dt),
            "cny": True, "rate": None, "usd_price": None,
            "unit": "元/克", "has_ohlc": False,
        }
        q.update(_get_spot_reference())
        return q
    except Exception as e:
        return {"error": str(e)}


def _get_spot_reference():
    """国际金价参照行（伦敦金换算成元/克），5 分钟内复用缓存；失败返回空 dict。

    参照行拿不到不影响主价显示 —— 只让卡片第二行留空。
    只回传 ref_price / ref_name：浙商是人民币品种，第二行给同为"元/克"的
    伦敦金价才方便直接对比；若一并带上 rate/usd_price，卡片会改去显示
    "美元/盎司 + 汇率"，反倒看不出两者的价差。"""
    now = time.time()
    if _ref_cache["quote"] and now - _ref_cache["ts"] < 300:
        return _ref_cache["quote"]
    ref = _fetch_sina("hf_XAU")
    if ref and "price" in ref and ref.get("cny"):
        out = {"ref_price": ref["price"], "ref_name": "伦敦金"}
        _ref_cache["quote"] = out
        _ref_cache["ts"] = now
        return out
    return {}


def fetch_quote(code: str):
    """统一取数入口：浙商积存金走京东金融接口，其余走新浪行情。"""
    if not isinstance(code, str) or code not in SOURCES:
        return {"error": "无效的金价品种"}
    if code == DEFAULT_SOURCE:
        return fetch_zheshang_quote()
    return _fetch_sina(code)

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
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowExW.restype = wintypes.HWND
        user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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


def _tray_icon_hwnd(icon=None):
    """Get the exact rectangle of our own icon, without UIA name matching.

    pystray 0.19.5's Windows backend registers uID=0 with a unique HWND.
    Shell_NotifyIconGetRect works even while the tooltip text is empty.
    Failure returns None; never substitute the entire notification area.
    """
    try:
        from ctypes import wintypes

        class NOTIFYICONIDENTIFIER(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("guidItem", ctypes.c_byte * 16)]

        hwnd = getattr(icon, "_hwnd", None)
        if not hwnd or not icon.visible:
            return None
        identity = NOTIFYICONIDENTIFIER()
        identity.cbSize = ctypes.sizeof(identity)
        identity.hWnd = hwnd
        # _win32.Icon._message passes hID, while the struct field is uID.
        # ctypes leaves the actual uID at zero. Match the registered value.
        identity.uID = 0
        rect = wintypes.RECT()
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                ctypes.POINTER(wintypes.RECT))(
                                    ("Shell_NotifyIconGetRect", ctypes.windll.shell32))
        if fn(ctypes.byref(identity), ctypes.byref(rect)) != 0:
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


def _monitor_rect(pos=None, hwnd=None, work=False):
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    u = ctypes.windll.user32
    if hwnd:
        fn = u.MonitorFromWindow
        fn.argtypes = [wintypes.HWND, wintypes.DWORD]
        fn.restype = wintypes.HANDLE
        monitor = fn(hwnd, 2)
    else:
        fn = u.MonitorFromPoint
        fn.argtypes = [wintypes.POINT, wintypes.DWORD]
        fn.restype = wintypes.HANDLE
        monitor = fn(wintypes.POINT(*(pos or (0, 0))), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("Cannot query monitor")
    rect = info.rcWork if work else info.rcMonitor
    return rect.left, rect.top, rect.right, rect.bottom


def _clamp_card_position(x, y, width, height, pos):
    try:
        left, top, right, bottom = _monitor_rect(pos=pos, work=True)
        x = max(left + 8, min(x, right - width - 8))
        y = max(top + 8, min(y, bottom - height - 8))
    except Exception:
        pass
    return int(x), int(y)


def _is_fullscreen_foreground():
    """Check the foreground window against the monitor containing it."""
    try:
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wintypes.HWND
        hwnd = u.GetForegroundWindow()
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        if not hwnd or not u.IsWindowVisible(hwnd):
            return False
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW(hwnd, buf, 64)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                         "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"):
            return False
        rect = wintypes.RECT()
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        l, t, r, b = _monitor_rect(hwnd=hwnd)
        return rect.left <= l and rect.top <= t and rect.right >= r and rect.bottom >= b
    except Exception:
        return False


class HoverCard:
    """鼠标悬停托盘图标时弹出的大字体实时金价悬浮卡。
    系统托盘 tooltip 字体大小不可控，故用 tkinter 无边框置顶窗口实现：
    轮询鼠标位置，在图标上停留超过 HOVER_DELAY_MS 才显示，离开即隐藏；
    行情每 300ms 检查刷新。"""

    # 数据刷新间隔（毫秒）。悬停延迟独立于它，最坏情况多等一个 tick。
    POLL_MS = 300

    BG      = "#1B1D29"
    FG_NAME = "#9AA0B5"
    FG_MAIN = "#F2F3F7"
    FG_SUB  = "#C7CBD9"

    def __init__(self, get_quote, on_active=None, on_zone=None, get_icon=None, on_uninstall=None):
        self.get_quote = get_quote
        self.get_icon = get_icon or (lambda: None)
        self.commands = queue.Queue()
        self.chart_window = None
        self.on_uninstall = on_uninstall
        self.on_active = on_active
        self.on_zone = on_zone      # 进入/离开图标热区时回调（用于抢在系统 tooltip 之前清空它）
        self.zone = False           # 鼠标当前是否落在图标热区内
        self.root = None
        self.visible = False
        self._sig = None
        self._labels = {}
        self.card_rect = None
        self._stop = False
        self.delay_ms = load_hover_delay_ms()
        self._enter_ts = None          # 本次进入图标区的时刻；移出即清零
        self._leave_ts = None          # 本次离开热区的时刻；宽限期用它计时
        self._icon_rect = None         # 本次轮询取得的图标矩形
        self._thread = None

    def start(self):
        if not TK_OK:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def open_chart(self):
        self.commands.put("chart")

    def _drain_commands(self):
        try:
            while True:
                command = self.commands.get_nowait()
                if command == "chart":
                    import chart
                    if self.chart_window and self.chart_window.root.winfo_exists():
                        self.chart_window.root.deiconify()
                        self.chart_window.root.lift()
                        self.chart_window.root.focus_force()
                    else:
                        self.chart_window = chart.ChartWindow(self.get_quote, master=self.root)
                elif command == "uninstall":
                    from tkinter import messagebox
                    if messagebox.askyesno("卸载金价托盘", "将移除程序、自启、快捷方式与设置。确定卸载吗？",
                                           parent=self.root):
                        if self.on_uninstall:
                            self.on_uninstall()
        except queue.Empty:
            pass

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
        except Exception as exc:
            GoldTray._log(f"hover UI error: {exc}")
        finally:
            self.zone = False
            self.visible = False
            if self.on_zone:
                self.on_zone(False)
            if self.root is not None:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            # Release Tk objects on their owning thread.
            self._labels.clear()
            self.chart_window = None
            self.root = None

    def _build(self):
        # v3.7：价格数字放大成主角（27→46px），其余信息相应缩小；
        # 单位"元/克"拆成独立小字贴在数字右下，避免跟着数字一起变大。
        f_price = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-_card_px(46), weight="bold")
        f_unit  = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-_card_px(13))
        f_chg   = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-_card_px(14), weight="bold")
        f_name  = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-_card_px(13))
        f_sub   = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-_card_px(11))
        # pady 用 (上,下) 元组微调纵向：Tk 会把行高取整，均匀 pady 会欠一点高度
        pad = {"padx": _card_px(14), "pady": (_card_px(2), _card_px(4))}

        lb = tk.Label(self.root, bg=self.BG, fg=self.FG_NAME, font=f_name,
                      anchor="w", justify="left", bd=0, highlightthickness=0,
                      pady=_card_px(1))
        lb.pack(fill="x", **pad)
        self._labels["name"] = lb

        # 价格行：大数字 + 贴底对齐的小单位
        pf = tk.Frame(self.root, bg=self.BG)
        pf.pack(fill="x", **pad)
        self._labels["price"] = tk.Label(
            pf, bg=self.BG, fg=self.FG_MAIN, font=f_price,
            anchor="w", bd=0, highlightthickness=0)
        self._labels["price"].pack(side="left")
        self._labels["punit"] = tk.Label(
            pf, bg=self.BG, fg=self.FG_NAME, font=f_unit,
            anchor="w", bd=0, highlightthickness=0)
        self._labels["punit"].pack(side="left", anchor="s", padx=(_card_px(5), 0), pady=(0, _card_px(8)))

        for key, font, fg in (("chg", f_chg, self.FG_MAIN),
                              ("usd", f_sub, self.FG_SUB),
                              ("ohcl", f_sub, self.FG_SUB),
                              ("time", f_sub, self.FG_SUB)):
            lb = tk.Label(self.root, bg=self.BG, fg=fg, font=font, anchor="w",
                          justify="left", bd=0, highlightthickness=0,
                          pady=_card_px(1))
            lb.pack(fill="x", **pad)
            self._labels[key] = lb

    def _icon_rect_cached(self, now):
        """Query the owned icon each tick; never retain a stale hot zone."""
        self._icon_rect = _tray_icon_hwnd(self.get_icon())
        return self._icon_rect

    def _hit_test(self, pos, now):
        """鼠标是否落在"该显示卡片"的区域内。

        热区 = 图标本体 ∪ 卡片本体 ∪ 两者之间的竖直"桥"。

        那个"桥"是关键。卡片浮在托盘上方，与图标之间有约 9px 空隙；没有桥，
        鼠标从图标移向卡片时会有一瞬间落在空白处。早先的版本为了绕过这个空隙，
        干脆写成"只要卡片可见就一直可见"，结果矫枉过正 —— 卡片弹出后再也收不
        回来（鼠标移到哪儿都不消失）。现在用几何把空隙补上，就不需要那种兜底了。

        只认精确的图标矩形，**不回退到"整个通知区域"**：那会让鼠标只要划到屏幕
        右下角就弹卡，玩游戏时严重破坏沉浸感。定位不到就不显示。"""
        if not pos:
            return False
        icon = self._icon_rect_cached(now)
        pad = 2                      # 只给 2px 容差，紧贴图标本体
        if icon:
            l, t, r, b = icon
            if l - pad <= pos[0] <= r + pad and t - pad <= pos[1] <= b + pad:
                return True
        if self.visible and self.card_rect:
            cx1, cy1, cx2, cy2 = self.card_rect
            if cx1 <= pos[0] <= cx2 and cy1 <= pos[1] <= cy2:
                return True
            if icon:
                l, t, r, b = icon
                # 卡片在图标上方时缝隙 = t - cy2，在下方时 = cy1 - b。
                # 只在缝隙很小（确实相邻）时才架桥；万一卡片被定位到别处，
                # 不架桥 —— 否则会凭空造出一大片热区。
                gap = (t - cy2) if cy2 <= t else (cy1 - b)
                if 0 <= gap <= 80:
                    # 桥：x 取二者并集，y 取二者之间的竖直带（上/下两种情形由
                    # min/max 统一表达）
                    bx1, bx2 = min(l, cx1), max(r, cx2)
                    by1, by2 = (cy2, t) if cy2 <= t else (b, cy1)
                    if bx1 <= pos[0] <= bx2 and by1 <= pos[1] <= by2:
                        return True
        return False

    def _poll(self):
        if self._stop:
            if self.root is not None:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            return
        try:
            self._drain_commands()
            now = time.monotonic()
            # 前台是全屏程序（游戏 / 全屏视频）时一律不弹卡，保住沉浸感
            if _is_fullscreen_foreground():
                self._enter_ts = None
                self._leave_ts = None
                self._hide()
                if self.zone:
                    self.zone = False
                    if self.on_zone:
                        self.on_zone(False)
                return
            pos = _cursor_pos()
            r = _get_tray_rect()
            inside = self._hit_test(pos, now)

            # 鼠标进出图标热区的"瞬间"就通知外部清空/恢复系统 tooltip。
            # 必须在这里切换而不是等卡片显示：Windows 原生 tooltip 约 0.5s 就弹出，
            # 等卡片（1s 后）出现再清空已经晚了，屏幕上会同时出现两个提示界面。
            if inside != self.zone:
                self.zone = inside
                if self.on_zone:
                    try:
                        self.on_zone(inside)
                    except Exception:
                        pass

            if inside:
                self._leave_ts = None
                if self._enter_ts is None:
                    self._enter_ts = now
            else:
                # 移出即清零：下次进来重新计时，杜绝"半途进入又立刻弹"
                self._enter_ts = None
                if self._leave_ts is None:
                    self._leave_ts = now

            q = self.get_quote()
            hovered_long_enough = (self._enter_ts is not None and
                                   (now - self._enter_ts) * 1000 >= self.delay_ms)
            # 宽限期：刚离开热区不久，先留着 —— 鼠标图标↔卡片途中穿过空隙时不闪
            grace = (self.visible and self._leave_ts is not None and
                     (now - self._leave_ts) * 1000 < HIDE_GRACE_MS)

            # 去留由 (inside or grace) 决定，self.visible 只负责"进来后别重复触发
            # 1s 延时"这一件事。以前这里写成 `if self.visible or ...`，少了一侧的
            # 约束 —— 卡片一旦显示就永远收不回来，正是"鼠标移开不消失"的根因。
            if (inside or grace) and (self.visible or hovered_long_enough) \
                    and q and "price" in q:
                self._show(q, r, pos)
            else:
                self._hide()
        except Exception as exc:
            GoldTray._log(f"hover poll error: {exc}")
        finally:
            if self.root is not None:
                self.root.after(self.POLL_MS, self._poll)

    def _show(self, q, tray_rect, pos):
        sig = tuple((key, value) for key, value in sorted(q.items()))
        if sig != self._sig:
            self._sig = sig
            self._update_labels(q)
        # 定位：优先通知区域上方偏右；拿不到托盘矩形则跟随鼠标（偏移随卡片实际尺寸动态计算）
        anchor = self._icon_rect or tray_rect
        if anchor:
            x = anchor[2] - self._card_w()
            y = anchor[1] - self._card_h() - _card_px(9)
            if y < 40:
                y = anchor[3] + _card_px(12)
        else:
            x = pos[0] - self._card_w() - _card_px(15)
            y = pos[1] - self._card_h() - _card_px(9)
            if y < 40:
                y = pos[1] + _card_px(18)
        x, y = _clamp_card_position(int(x), int(y), self._card_w(), self._card_h(), pos)
        # '+-100' is an absolute negative coordinate; '-100' anchors to the
        # right edge and would put the card on the wrong monitor.
        self.root.geometry(f"+{x}+{y}")
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
            return _card_px(210)

    def _card_w(self):
        try:
            self.root.update_idletasks()
            return self.root.winfo_reqwidth()
        except Exception:
            return _card_px(217)

    def _update_labels(self, q):
        up = q["change"] > 0
        down = q["change"] < 0
        color = "#EB4D4B" if up else ("#2ECC71" if down else "#C8C8C8")
        arrow = "▲" if up else ("▼" if down else "＝")
        self._labels["name"].configure(text=q["name"])
        # 价格数字单独染色（涨红跌绿），单位作为独立小字贴在其右下
        self._labels["price"].configure(text=f"{q['price']:.2f}", fg=color)
        self._labels["punit"].configure(text=q.get("unit") or "元/克")

        # 第二行：参照行情。浙商积存金挂换算后的伦敦金元/克（同为元/克，可直接
        # 对比出银行牌价溢价）；新浪国际品种挂"美元/盎司 + 汇率"。
        usd, rate, ref = q.get("usd_price"), q.get("rate"), q.get("ref_price")
        if ref:
            sub2 = f"{q.get('ref_name') or '伦敦金'} {ref:.2f} 元/克"
        elif usd and rate:
            sub2 = f"美元 {usd:.2f}/盎司    汇率 {rate:.4f}"
        else:
            sub2 = ""
        self._labels["usd"].configure(text=sub2)

        self._labels["chg"].configure(
            text=f"{arrow} {q['change']:+.2f} ({q['pct']:+.2f}%)", fg=color)
        # 浙商接口不提供 OHLC —— 只显示"昨收 + 相对昨收的价差"，
        # 而不是拿 None 去格式化（那是崩在 _update_labels 里的隐患）
        if q.get("has_ohlc", True):
            self._labels["ohcl"].configure(
                text=f"昨收 {q['prev']:.2f}   今开 {q['open']:.2f}\n"
                     f"最高 {q['high']:.2f}   最低 {q['low']:.2f}")
        else:
            self._labels["ohcl"].configure(
                text=f"昨收 {q['prev']:.2f}\n"
                     f"较昨收 {q['change']:+.2f} 元/克")
        stale = "\n更新失败，显示上次报价" if q.get("stale") else ""
        self._labels["time"].configure(text=f"时间 {q['date']} {q['time']}{stale}")

    def _hide(self):
        self._leave_ts = None
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
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH,
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
            if not _frozen() and target.endswith(".py"):
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                command = f'"{pythonw if os.path.exists(pythonw) else sys.executable}" "{target}"'
            else:
                command = f'"{target}"'
            winreg.SetValueEx(key, AUTOSTART_KEY, 0, winreg.REG_SZ, command)
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
APP_VERSION = "3.11.1"

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
        if not create_shortcut(lnk, INSTALLED_EXE, workdir=INSTALL_DIR,
                               desc="金价托盘 - 托盘实时金价小工具", icon_path=INSTALLED_EXE):
            raise OSError("无法创建快捷方式：" + lnk)


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
        # Stage the complete replacement before touching the installed binary.
        staged = dst + ".new"
        shutil.copy2(src, staged)
        old = dst + ".old"
        if os.path.exists(dst):
            try:
                os.replace(staged, dst)
            except PermissionError:
                os.replace(dst, old)
                try:
                    os.replace(staged, dst)
                except Exception:
                    os.replace(old, dst)
                    raise
        else:
            os.replace(staged, dst)
    if not set_autostart(autostart):
        raise OSError("无法更新开机自启动配置")
    if not _write_uninstall_key():
        raise OSError("无法写入卸载注册信息")
    if desktop_shortcut:
        _make_shortcuts()
    if launch and os.path.exists(dst):
        subprocess.Popen([dst], cwd=INSTALL_DIR,
                         creationflags=subprocess.DETACHED_PROCESS)
    return True


def do_uninstall():
    """移除自启/卸载项/快捷方式；程序文件由延迟脚本在进程退出后删除"""
    expected = os.path.abspath(os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"), APP_NAME))
    if os.path.normcase(os.path.realpath(INSTALL_DIR)) != os.path.normcase(os.path.realpath(expected)):
        raise ValueError("拒绝删除非预期的安装路径")
    script = None
    if os.path.isdir(INSTALL_DIR):
        fd, script = tempfile.mkstemp(prefix="GoldPriceTray_uninstall_", suffix=".ps1")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(_uninstall_script(INSTALL_DIR, INSTALLED_EXE))
        powershell = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                 "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        # Launch before removing registrations; failed launches must be visible.
        subprocess.Popen([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", script], creationflags=subprocess.CREATE_NO_WINDOW)
    set_autostart(False)
    _delete_uninstall_key()
    _remove_shortcuts()


def _uninstall_script(directory, executable):
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    return ("$ErrorActionPreference = 'Stop'\n"
            f"$targetDirectory = {literal(os.path.abspath(directory))}\n"
            f"$targetExe = {literal(os.path.abspath(executable))}\n"
            "Start-Sleep -Seconds 3\n"
            "Get-CimInstance Win32_Process -Filter \"Name = 'GoldPriceTray.exe'\" | "
            "Where-Object { $_.ExecutablePath -eq $targetExe } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\n"
            "for ($attempt = 0; $attempt -lt 10; $attempt++) {\n"
            "  if (!(Test-Path -LiteralPath $targetDirectory)) { break }\n"
            "  $resolvedTarget = (Resolve-Path -LiteralPath $targetDirectory).ProviderPath\n"
            "  if ($resolvedTarget -ne $targetDirectory) { throw 'Unexpected uninstall path' }\n"
            "  try { Remove-Item -LiteralPath $resolvedTarget -Recurse -Force; break }\n"
            "  catch { Start-Sleep -Seconds 1 }\n"
            "}\n"
            "Remove-Item -LiteralPath $PSCommandPath -Force\n")


_instance_mutex = None


def _single_instance_ok():
    """已有一个托盘实例运行时，再次双击直接退出，避免双图标"""
    try:
        from ctypes import wintypes
        global _instance_mutex
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel.CreateMutexW(None, False, "Local\\GoldPriceTray_SingleInstance")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        duplicate = ctypes.get_last_error() == 183
        if duplicate:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            return False
        _instance_mutex = handle
        return True
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
class PriceIcon(pystray.Icon):
    """Build dynamic menu text on the tray thread immediately before opening."""
    def _on_notify(self, wparam, lparam):
        if lparam == 0x0205:  # WM_RBUTTONUP
            self.update_menu()
        return super()._on_notify(wparam, lparam)


class GoldTray:
    _log_lock = threading.Lock()

    def __init__(self):
        self.source = load_source()
        self.quote = None
        self.last_error = None
        self.icon = None
        self.running = True
        self._last_title = ""
        self._state_lock = threading.RLock()
        self._fetch_lock = threading.Lock()
        self._refresh_event = threading.Event()
        self._refresh_event.set()
        self._source_generation = 0
        # 日志节流状态：_log_ts 为上次"心跳类"日志的时刻（0 表示还没写过）；
        # _last_dir 为上次的涨跌方向，用于识别转向；_err_streak 为连续失败次数，
        # 保证一次断网只写一行而不是每 30 秒刷一行。
        self._log_ts = 0.0
        self._last_dir = None
        self._err_streak = 0
        self.hover = HoverCard(lambda: self.quote, self._on_hover, self._on_zone,
                               lambda: self.icon, self._confirmed_uninstall)

    def _sync_tray_title(self):
        """统一维护系统原生 tooltip：只在"鼠标不在图标上"时才保留它。

        鼠标一进图标热区就把 szTip 清空 —— pystray 走 NIM_MODIFY + NIF_TIP，
        空串即移除 tooltip（MSDN 标准做法），这样能抢在 Windows 弹出它之前生效，
        屏幕上就只剩我们自己的悬浮卡一个界面。离开后恢复文本，是因为
        鼠标移出后恢复系统 tooltip。图标定位独立于提示文字。"""
        if not self.icon:
            return
        suppress = self.hover.zone or self.hover.active
        try:
            self.icon.title = "" if suppress else self._last_title[:127]
        except Exception:
            pass
        _set_tray_title(self._last_title)

    def _on_hover(self, active):
        """悬浮卡显示/隐藏时同步 tooltip 状态"""
        self._sync_tray_title()

    def _on_zone(self, inside):
        """鼠标进入/离开图标热区时同步 tooltip 状态（抢在系统 tooltip 弹出之前）"""
        self._sync_tray_title()

    def menu(self):
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
                *[pystray.MenuItem(name, self._source_action(c),
                                   checked=lambda i, c=c: self.source == c,
                                   radio=True)
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

    def _source_action(self, code):
        # pystray calls actions with (icon, item); capture code in a closure,
        # never in a second positional default that the MenuItem overwrites.
        def select(icon, item):
            self.set_source(code)
        return select

    def _price_str(self):
        if self.quote and "price" in self.quote:
            q = self.quote
            if q.get("cny"):
                return (f"{q['price']:.2f} 元/克  {q['change']:+.2f} "
                        f"({q['pct']:+.2f}%)" + ("（更新失败）" if q.get("stale") else ""))
            unit = "美元/盎司" if q["code"].startswith("hf_") else "元/克"
            return f"{q['price']:.2f} {unit}  {q['change']:+.2f} ({q['pct']:+.2f}%)"
        if self.last_error:
            return "获取失败"
        return "加载中…"

    def _autostart_checked(self, item):
        return get_autostart_status()

    def refresh_now(self, icon=None, item=None):
        self._refresh_event.set()

    def set_source(self, code):
        if not isinstance(code, str) or code not in SOURCES:
            self._log("rejected invalid source selection")
            return False
        with self._state_lock:
            if code != self.source:
                self.source = code
                self._source_generation += 1
                self.quote = None
                self.last_error = None
                self._err_streak = 0
                self._log_ts = 0.0
                self._last_dir = None
                self._last_title = f"{SOURCES[code]} 加载中…"
                if self.icon:
                    self.icon.icon = render_icon("…", COLOR_FLAT)
                    self._sync_tray_title()
        try:
            save_source(code)
        except Exception as exc:
            self._log(f"settings save failed: {exc}")
            self._toast("品种已切换，但保存设置失败")
        self.refresh_now()
        return True

    def toggle_autostart(self, icon, item):
        enable = not get_autostart_status()
        if not set_autostart(enable):
            self._toast("修改开机自启动失败，请查看日志")
            self._log("autostart update failed")
            return
        self._toast("开机自启动已" + ("开启" if enable else "关闭"))
        icon.update_menu()

    def show_about(self, icon, item):
        icon.notify(
            f"金价托盘 GoldPriceTray v{APP_VERSION}\n"
            "托盘实时金价 + 走势图 · 涨红跌绿\n"
            "默认显示浙商银行积存金（元/克）\n"
            "可切换 伦敦金 / 纽约黄金 / 沪金99 / 黄金T+D\n"
            "鼠标悬停托盘图标看大字行情，双击看走势",
            "金价小工具")

    def uninstall_self(self, icon, item):
        """托盘菜单卸载：先记下要删的东西，停止自身后交给延迟脚本收尾"""
        if TK_OK and self.hover.root is not None:
            self.hover.commands.put("uninstall")
        else:
            self._toast("卸载确认界面尚未就绪，请稍后重试")

    def _confirmed_uninstall(self):
        do_uninstall()
        self.running = False
        self._refresh_event.set()
        self.hover.stop()
        if self.icon:
            self.icon.stop()

    def quit(self, icon, item):
        self.running = False
        self._refresh_event.set()
        self.hover.stop()
        icon.stop()

    def _toast(self, msg):
        if self.icon:
            try:
                self.icon.notify(msg, "金价小工具")
            except Exception:
                pass

    def open_chart(self, icon=None, item=None):
        if TK_OK and self.hover.root is not None:
            self.hover.open_chart()
        else:
            self._toast("走势图界面尚未就绪，请稍后重试")

    def _refresh_worker(self):
        # One request at a time; discard responses for superseded selections.
        with self._fetch_lock:
            with self._state_lock:
                source = self.source
                generation = self._source_generation
            try:
                q = fetch_quote(source)
            except Exception as exc:
                q = {"error": str(exc)}
            with self._state_lock:
                if not self.running or generation != self._source_generation:
                    return
                self._apply_quote(q)

    def _apply_quote(self, q):
        if q is None or "error" in q:
            self.last_error = (q or {}).get("error", "未知错误")
            if self.quote:
                self.quote = dict(self.quote, stale=True)
            # 只记一次失败的开头。原先这里完全不写日志 —— 最该留痕的断网反而
            # 没有任何记录；而每 30 秒刷一行的"成功"日志却写了满盘。
            if self._err_streak == 0:
                self._log(f"fetch failed: {self.last_error}")
            self._err_streak += 1
            if self.icon:
                try:
                    if not self.quote:
                        self.icon.icon = render_icon("--", COLOR_ERR)
                        self._last_title = f"{SOURCES[self.source]} 金价获取失败: {self.last_error}"
                    else:
                        price = self.quote['price']
                        txt = f"{price:.1f}" if price < 1000 else f"{price:.0f}"
                        self.icon.icon = render_icon(txt, COLOR_ERR)
                        self._last_title = (f"{SOURCES[self.source]} {self.quote['price']:.2f} 元/克\n"
                                            f"更新失败，显示上次报价 {self.quote['date']} {self.quote['time']}")
                    self._sync_tray_title()
                except Exception as e:
                    self._log(f"icon update failed: {e}")
            return
        if self._err_streak:
            self._log(f"recovered after {self._err_streak} failed fetch(es)")
            self._err_streak = 0
        self.quote = q
        self.last_error = None
        # 人民币计价：<1000 显示 1 位小数(如 947.5)，>=1000 显示整数(如 1012)；美元显示整数
        if q.get("cny"):
            txt = f"{q['price']:.1f}" if q["price"] < 1000 else f"{q['price']:.0f}"
        else:
            txt = f"{q['price']:.0f}"

        # 只在有意义的时刻落盘：首次取到价 / 涨跌方向翻转 / 每小时心跳。
        # 平常那种"还是这个价"不写 —— 托盘图标本身就是实时显示，
        # 日志里再抄一遍只会以约 50 MB/年 的速度堆垃圾。
        now = time.time()
        direction = 1 if q["change"] > 0 else (-1 if q["change"] < 0 else 0)
        gap = now - self._log_ts
        if self._log_ts == 0.0:
            tag = "first ok"
        elif direction != self._last_dir and gap >= LOG_MIN_GAP_SEC:
            tag = "direction flip"
        elif gap >= LOG_HEARTBEAT_SEC:
            tag = "heartbeat"
        else:
            tag = None
        if tag:
            self._log(f"{tag}: {txt} {q['change']:+.2f} ({q['pct']:+.2f}%)"
                      f"  [{self.source}]")
            self._log_ts = now
        self._last_dir = direction

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
                if q.get("ref_price"):
                    mid = f"{q.get('ref_name') or '伦敦金'} {q['ref_price']:.2f} 元/克\n"
                elif q.get("usd_price") and q.get("rate"):
                    mid = (f"美元 {q['usd_price']:.2f}/盎司  "
                           f"汇率 {q['rate']:.4f}\n")
                else:
                    mid = ""
                title = (
                    f"{q['name']} {q['price']:.2f} {unit} {arrow} {q['pct']:+.2f}%\n"
                    f"{mid}时间 {q['date'][5:]} {q['time']}"
                )
                self._last_title = title
                self._sync_tray_title()
            except Exception as e:
                self._log(f"icon update failed: {e}")

    @staticmethod
    def _log_path():
        """日志固定写入安装目录（可移植版为 %LOCALAPPDATA%\\GoldPriceTray）；
        目录不可写时退回临时目录。"""
        try:
            os.makedirs(INSTALL_DIR, exist_ok=True)
            return os.path.join(INSTALL_DIR, "GoldPriceTray.log")
        except Exception:
            return os.path.join(tempfile.gettempdir(), "GoldPriceTray.log")

    @classmethod
    def _log(cls, msg):
        with cls._log_lock:
            cls._write_log(msg)

    @classmethod
    def _write_log(cls, msg):
        try:
            path = cls._log_path()
            msg = str(msg).replace("\r", " ").replace("\n", " ")[:2000]
            entry = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n".encode("utf-8")
            # Limit bytes as well as line count, including oversized error lines.
            try:
                size = os.path.getsize(path)
                if size + len(entry) > LOG_MAX_BYTES:
                    budget = min(LOG_MAX_BYTES // 2, LOG_MAX_BYTES - len(entry))
                    with open(path, "rb") as f:
                        start = max(0, size - budget)
                        f.seek(start)
                        keep = f.read().splitlines(keepends=True)
                    if start and keep:
                        keep = keep[1:]
                    with open(path, "wb") as f:
                        f.write(b"".join(keep[-LOG_KEEP_LINES:]))
            except OSError:
                pass
            with open(path, "ab") as f:
                f.write(entry)
        except Exception:
            pass

    def _loop(self):
        while self.running:
            self._refresh_event.wait(REFRESH_INTERVAL)
            self._refresh_event.clear()
            if not self.running:
                return
            try:
                self._refresh_worker()
            except Exception as exc:
                self._log(f"refresh worker error: {exc}")

    def _make_icon(self):
        return PriceIcon("GoldPriceTray",
                            render_icon("…", COLOR_FLAT),
                            "金价小工具…", self.menu())

    def _selfcheck(self):
        """启动 5 秒后自检一次：悬停热区能否定位到托盘图标本体。

        v3.6 起定位失败就不再回退到"整片通知区域"（那会导致鼠标划到右下角
        就弹卡）。因此这一步的结论直接决定悬浮卡是否可用 —— 写进日志便于排查。"""
        time.sleep(5)
        try:
            r = _tray_icon_hwnd(self.icon)
        except Exception:
            r = None
        if r:
            self._log(f"hover target: icon rect {r} "
                      f"({r[2] - r[0]}x{r[3] - r[1]}px), delay {load_hover_delay_ms()}ms")
        else:
            self._log("hover target: ICON LOCATE FAILED — 悬浮卡将不会弹出")

    def run(self):
        """主入口：pystray 消息循环偶发静默退出（GetMessage 返回 0/-1），
        这里包一层自动重启，保证托盘图标始终存活。刷新线程只启动一次。"""
        self._log(f"startup v{APP_VERSION} [{self.source}]")
        self.hover.start()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._selfcheck, daemon=True).start()
        while self.running:
            self.icon = self._make_icon()
            try:
                self.icon.run(lambda icon: self._icon_ready(icon))
            except Exception as e:
                self._log(f"icon loop error: {e}")
            if not self.running:
                break
            self._log("icon loop exited unexpectedly, restarting in 3s…")
            time.sleep(3)
        if self.hover._thread:
            self.hover._thread.join(timeout=2)

    def _icon_ready(self, icon):
        with self._state_lock:
            icon.visible = True
            if self.quote:
                error = self.last_error
                self._apply_quote(dict(self.quote))
                if error:
                    self._apply_quote({"error": error})
            else:
                self._last_title = f"{SOURCES[self.source]} 加载中…"
                self._sync_tray_title()

if __name__ == "__main__":
    enable_dpi_awareness()
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
        if _single_instance_ok():
            GoldTray().run()
