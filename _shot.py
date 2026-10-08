# -*- coding: utf-8 -*-
"""抓取 ChartWindow 与 HoverCard 的真实渲染截图（PrintWindow，窗口被遮挡也能抓）。"""
import os
import sys
import time
import ctypes
from ctypes import wintypes, byref

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
os.makedirs(DOCS, exist_ok=True)
sys.path.insert(0, BASE)

import tkinter as tk
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

PW_RENDERFULLCONTENT = 0x00000002


def find_hwnd(title):
    return user32.FindWindowW(None, title)


def capture(hwnd, path):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False, (w, h)

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth = w
    bi.biHeight = -h          # 负值 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, byref(bi), 0)

    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

    if not got:
        return False, (w, h)
    img = Image.frombytes("RGB", (w, h), buf.raw, "raw", "BGRX")
    img.save(path)
    return True, (w, h)


def pump(root, seconds):
    """非阻塞地推进 Tk 事件循环若干秒。"""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def shoot_chart(mode="day", out="docs_chart.png"):
    import chart
    import gold_price_tray as g

    code = g.DEFAULT_SOURCE
    cw = chart.ChartWindow(lambda: g.fetch_quote(code))
    cw.root.title("CHART_CAPTURE")
    cw.root.deiconify()
    cw.root.lift()
    pump(cw.root, 1.0)
    hwnd = find_hwnd("CHART_CAPTURE")
    print("chart hwnd:", hwnd)
    if not hwnd:
        cw.root.destroy()
        return
    # 等首屏数据加载（_poll 里会把 title_line 从"加载中…"换成真实标题）
    for _ in range(60):
        pump(cw.root, 0.5)
        if cw.data and cw.title_line and "加载" not in cw.title_line:
            break
    if mode != "day":
        cw.switch(mode)
        for _ in range(60):
            pump(cw.root, 0.5)
            if cw.data and cw.title_line and "加载" not in cw.title_line:
                break
    pump(cw.root, 1.5)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out)
    ok, size = capture(hwnd, out_path)
    # 裁掉标题栏与窗口边框：客户区在窗口位图中的偏移
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, byref(rc))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, byref(pt))
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, byref(wr))
    lo, to_ = pt.x - wr.left, pt.y - wr.top
    Image.open(out_path).crop((lo, to_, lo + rc.right, to_ + rc.bottom)).save(out_path)
    print(f"chart[{mode}] captured:", ok, size, "-> client crop", (rc.right, rc.bottom),
          "| title:", cw.title_line, "| points:", len(cw.data))
    cw.root.destroy()


def shoot_card():
    import gold_price_tray as g

    code = g.DEFAULT_SOURCE
    card = g.HoverCard(lambda: g.fetch_quote(code))
    q = g.fetch_quote(code)
    if not q:
        print("quote fetch failed")
        return
    # 手动复刻 _run() 的初始化（不走内部线程，便于主线程推进事件循环）
    card.root = tk.Tk()
    card.root.overrideredirect(True)
    card.root.attributes("-topmost", True)
    card.root.configure(bg=card.BG)
    card._build()
    card._show(q, None, (400, 300))
    root = card.root
    if root is None:
        print("card root is None")
        return
    root.title("CARD_CAPTURE")
    root.deiconify()
    root.lift()
    pump(root, 2.5)
    hwnd = find_hwnd("CARD_CAPTURE")
    print("card hwnd:", hwnd)
    if not hwnd:
        root.destroy()
        return
    out_path = os.path.join(DOCS, "screenshot_hover.png")
    ok, size = capture(hwnd, out_path)
    print("card captured:", ok, size, "->", out_path)
    root.destroy()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "chart"
    if which == "chart":
        # 用法: _shot.py chart [day|week|month] [输出文件名]
        mode = sys.argv[2] if len(sys.argv) > 2 else "day"
        out = sys.argv[3] if len(sys.argv) > 3 else "docs/screenshot_chart.png"
        shoot_chart(mode, out)
    else:
        shoot_card()
    # Tk 退出时偶发残留线程导致进程不结束，截图已落盘，直接强制退出
    sys.stdout.flush()
    os._exit(0)
