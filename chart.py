# -*- coding: utf-8 -*-
"""
chart.py —— 金价走势图窗口（tkinter）
"""
import json
import re
import time
import threading
import queue
import urllib.request
import tkinter as tk

# Tk 用字体族名（而非字体文件路径）；跨机器可移植，找不到时 Tk 自动回退默认字体
FONT_BOLD = "Microsoft YaHei UI"
FONT_REG  = "Microsoft YaHei UI"
# 国际现货黄金（伦敦金）——24h 连续交易，全球黄金基准价
KLINE_SYMBOL = "XAU"
OZ_TO_GRAM = 31.1035  # 1 盎司 = 31.1035 克


def fetch_usdcny():
    """新浪美元兑人民币(在岸)实时汇率；失败返回 None（与主程序同逻辑，独立避免循环导入）"""
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


def fetch_daily_kline(symbol=KLINE_SYMBOL, count=30):
    """新浪全球期货日K线（伦敦金等外盘），返回 [(date, close)] 最近 count 条；
    自动按实时汇率换算为人民币元/克；汇率失败则原样返回美元价；失败返回 []"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}")
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"\((\[.*\])\)", txt)
        if not m:
            return []
        data = json.loads(m.group(1))
        rate = _get_cny_rate()
        k = (rate / OZ_TO_GRAM) if rate else 1.0
        out = [(d["date"], float(d["close"]) * k) for d in data[-count:]]
        return out
    except Exception:
        return []


def fetch_minute_kline(symbol=KLINE_SYMBOL, mtype=5):
    """新浪全球期货分钟线（伦敦金），返回 [(datetime_str, close)]，价格已换算人民币元/克；
    失败返回 []。minLine_1d 每条: [时间, 价格, ..., 完整时间戳]"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20t=/GlobalFuturesService.getGlobalFuturesMinLine?symbol={symbol}&type={mtype}")
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"\((\{.*\})\)", txt)
        if not m:
            return []
        obj = json.loads(m.group(1))
        arr = obj.get("minLine_1d", [])
        rate = _get_cny_rate()
        k = (rate / OZ_TO_GRAM) if rate else 1.0
        out = []
        for e in arr:
            if len(e) >= 2 and e[1]:
                # 每条的最后元素是完整时间戳 "2026-09-01 06:01:00"（第一条为 10 字段，后续 6 字段）
                ts = e[-1] if len(e[-1]) >= 10 else e[0]
                try:
                    out.append((ts, float(e[1]) * k))
                except ValueError:
                    continue
        return out
    except Exception:
        return []


class ChartWindow:
    W, H = 760, 460
    PAD_L, PAD_R, PAD_T, PAD_B = 62, 20, 46, 36

    def __init__(self, get_quote):
        self.get_quote = get_quote
        self.mode = "day"
        self.data = []
        self.title_line = "加载中…"
        self.color = (255, 200, 80)
        self.prev_line = None
        self.data_time = ""
        self.unit = "元/克"
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.title("金价走势 · GoldPriceTray")
        self.root.geometry(f"{self.W}x{self.H}")
        self.root.configure(bg="#1e1f22")
        self.root.resizable(True, True)
        self._build()
        self.load_data()
        self.root.after(80, self._poll)

    def _build(self):
        top = tk.Frame(self.root, bg="#1e1f22")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.lbl_title = tk.Label(top, text="加载中…", bg="#1e1f22",
                                  fg="#e8e8e8", font=(FONT_BOLD, 13))
        self.lbl_title.pack(side="left")
        self.lbl_price = tk.Label(top, text="", bg="#1e1f22",
                                  font=(FONT_BOLD, 15))
        self.lbl_price.pack(side="right")

        btns = tk.Frame(self.root, bg="#1e1f22")
        btns.pack(fill="x", padx=10)
        self.btn_day = self._mk_btn(btns, "当日分时", "day")
        self.btn_week = self._mk_btn(btns, "近一周", "week")
        self.btn_month = self._mk_btn(btns, "近一月", "month")
        self._refresh_btn_style()

        self.canvas = tk.Canvas(self.root, bg="#1e1f22", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Motion>", self._on_motion)

        self.lbl_status = tk.Label(self.root, text="", bg="#1e1f22",
                                   fg="#9aa0a6", font=(FONT_REG, 9), anchor="w")
        self.lbl_status.pack(fill="x", padx=12, pady=(0, 6))

    def _mk_btn(self, parent, text, mode):
        b = tk.Button(parent, text=text, command=lambda: self.switch(mode),
                      relief="flat", padx=12, pady=3,
                      font=(FONT_REG, 10), cursor="hand2")
        b.pack(side="left", padx=(0, 8))
        return b

    def _refresh_btn_style(self):
        active = {"day": self.btn_day, "week": self.btn_week, "month": self.btn_month}
        for mode, b in active.items():
            if mode == self.mode:
                b.configure(bg="#3d4050", fg="#ffffff")
            else:
                b.configure(bg="#2a2b30", fg="#c8c8c8")

    def switch(self, mode):
        self.mode = mode
        self._refresh_btn_style()
        self.load_data()

    def load_data(self):
        self.title_line = "加载中…"
        self.data = []
        self.prev_line = None
        self.lbl_price.configure(text="")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            if self.mode == "day":
                self._fetch_day()
            elif self.mode == "week":
                self._fetch_daily(5)
            else:
                self._fetch_daily(22)
        except Exception as e:
            self.q.put(("error", str(e)))

    def _fetch_day(self):
        q = self.get_quote()
        rows = fetch_minute_kline(KLINE_SYMBOL, 5)
        today = time.strftime("%Y-%m-%d")
        # 国际金价 24h 连续交易（北京时间 06:00 起），保留当日全部数据
        rows = [(t, p) for t, p in rows if t.startswith(today)]
        if not rows:
            rows = fetch_minute_kline(KLINE_SYMBOL, 5)[-24:]
        if not rows:
            self.q.put(("error", "无法获取当日分时数据"))
            return
        labels = [t[11:16] for t, _ in rows]
        prev = q.get("prev") if q and "prev" in q else None
        name = q.get("name") if q else "沪金99"
        cur = q.get("price") if q and "price" in q else rows[-1][1]
        unit = q.get("unit", "美元/盎司") if q else "美元/盎司"
        tstr = (f"{q.get('date', '')} {q.get('time', '')}"
                if q and q.get("time") else time.strftime("%Y-%m-%d %H:%M:%S"))
        if prev:
            chg = cur - prev
            pct = chg / prev * 100
            self.title_line = f"{name} 当日分时"
            self.prev_line = (prev, f"昨收 {prev:.2f}")
        else:
            chg = pct = 0
            self.title_line = f"{name} 当日分时"
        self.q.put(("day", labels, [p for _, p in rows], cur, chg, pct, name, tstr, unit))

    def _fetch_daily(self, n):
        rows = fetch_daily_kline(KLINE_SYMBOL, n)
        if not rows:
            self.q.put(("error", "无法获取日K数据"))
            return
        dates = [d for d, _ in rows]
        closes = [c for _, c in rows]
        q = self.get_quote()
        name = q.get("name") if q else "沪金99"
        cur = closes[-1]
        prev = closes[-2] if len(closes) > 1 else cur
        chg, pct = cur - prev, (cur - prev) / prev * 100 if prev else 0
        unit = q.get("unit", "美元/盎司") if q else "美元/盎司"
        tstr = (f"{q.get('date', '')} {q.get('time', '')}"
                if q and q.get("time") else time.strftime("%Y-%m-%d %H:%M:%S"))
        span = "近一周" if n <= 5 else "近一月"
        self.title_line = f"{name} {span}"
        self.prev_line = (prev, f"昨收 {prev:.2f}")
        self.q.put(("daily", dates, closes, cur, chg, pct, name, tstr, unit))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "error":
                    self.title_line = f"数据获取失败: {item[1]}"
                    self.data = []
                    self._redraw()
                elif item[0] == "day":
                    _, labels, prices, cur, chg, pct, name, tstr, unit = item
                    self.data = list(zip(labels, prices))
                    self.latest = (cur, chg, pct, name)
                    self.data_time = tstr
                    self.unit = unit
                    self._redraw()
                elif item[0] == "daily":
                    _, dates, closes, cur, chg, pct, name, tstr, unit = item
                    labels = [d[5:] for d in dates]
                    self.data = list(zip(labels, closes))
                    self.latest = (cur, chg, pct, name)
                    self.data_time = tstr
                    self.unit = unit
                    self._redraw()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _redraw(self):
        if self.data:
            cur, chg, pct, name = self.latest
            if chg > 0:
                c = "#eb4d4b"
            elif chg < 0:
                c = "#2ecc71"
            else:
                c = "#c8c8c8"
            self.color = c
            self.lbl_price.configure(
                text=f"{cur:.2f}   {chg:+.2f} ({pct:+.2f}%)", fg=c)
            self.lbl_title.configure(text=self.title_line)
            self.lbl_status.configure(
                text=f"数据时间 {self.data_time}" if self.data_time else "")
        else:
            self.lbl_title.configure(text=self.title_line)
            self.lbl_price.configure(text="")
        self.draw()

    def draw(self):
        cv = self.canvas
        cv.delete("all")
        w = max(cv.winfo_width(), 200)
        h = max(cv.winfo_height(), 200)
        if not self.data:
            cv.create_text(w / 2, h / 2, text=self.title_line,
                           fill="#9aa0a6", font=(FONT_REG, 12))
            return
        prices = [p for _, p in self.data]
        lo, hi = min(prices), max(prices)
        if self.prev_line:
            lo = min(lo, self.prev_line[0])
            hi = max(hi, self.prev_line[0])
        span = (hi - lo) or 1.0
        lo -= span * 0.08
        hi += span * 0.08
        span = hi - lo

        pl, pr, pt, pb = self.PAD_L, self.PAD_R, self.PAD_T, self.PAD_B
        cw, ch = w - pl - pr, h - pt - pb

        n_y = 5
        for i in range(n_y + 1):
            val = hi - span * i / n_y
            y = pt + ch * i / n_y
            cv.create_line(pl, y, w - pr, y, fill="#2e2f34", width=1)
            cv.create_text(pl - 8, y, text=f"{val:.0f}", anchor="e",
                           fill="#9aa0a6", font=(FONT_REG, 9))
        n = len(self.data)
        max_xticks = 8 if n > 8 else n
        for i in range(max_xticks):
            idx = round(i * (n - 1) / max(1, max_xticks - 1))
            lab = self.data[idx][0]
            x = pl + cw * idx / max(1, n - 1)
            cv.create_text(x, pt + ch + 14, text=lab, anchor="n",
                           fill="#9aa0a6", font=(FONT_REG, 9))

        def px(i): return pl + cw * i / max(1, n - 1)
        def py(v): return pt + ch * (hi - v) / span

        if self.prev_line:
            y0 = py(self.prev_line[0])
            cv.create_line(pl, y0, w - pr, y0, fill="#5a5d66", width=1,
                           dash=(4, 3))
            cv.create_text(w - pr, y0 - 4, text=self.prev_line[1], anchor="ne",
                           fill="#8a8f98", font=(FONT_REG, 9))

        pts = [(px(i), py(p)) for i, (_, p) in enumerate(self.data)]
        if len(pts) >= 2:
            # 直线直连相邻数据点（不用 smooth，否则会变成偏离数据点的曲线）
            cv.create_line([c for p in pts for c in p], fill=self.color,
                           width=2)
        step = max(1, n // 60)
        for i, (x, y) in enumerate(pts):
            if i % step == 0:
                r = 3.0
                cv.create_oval(x - r, y - r, x + r, y + r, fill=self.color,
                               outline="#1e1f22", width=1)
        cv.create_rectangle(pl, pt, w - pr, pt + ch, outline="#3a3b40")

    def _on_motion(self, ev):
        if not self.data:
            return
        w = max(self.canvas.winfo_width(), 200)
        h = max(self.canvas.winfo_height(), 200)
        pl, pr, pt, pb = self.PAD_L, self.PAD_R, self.PAD_T, self.PAD_B
        cw = w - pl - pr
        n = len(self.data)
        if n < 2:
            return
        x = ev.x - pl
        idx = max(0, min(n - 1, round(x / cw * (n - 1))))
        lab, price = self.data[idx]
        unit = getattr(self, "unit", "美元/盎司")
        self.lbl_status.configure(text=f"{lab}   价格 {price:.2f} {unit}")

    def run(self):
        self.root.mainloop()


def run_chart(get_quote):
    cw = ChartWindow(get_quote)
    cw.run()
