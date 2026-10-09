# -*- coding: utf-8 -*-
"""
chart.py —— 金价走势图窗口（tkinter）
"""
import json
import math
import re
import time
import threading
import queue
import urllib.request
import tkinter as tk
from quote_http import open_quote

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
    自动按实时汇率换算为人民币元/克；行情或汇率失败返回 []"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}")
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0"})
        with open_quote(req, timeout=10) as r:
            txt = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"\((\[.*\])\)", txt)
        if not m:
            return []
        data = json.loads(m.group(1))
        rate = _get_cny_rate()
        if not rate:
            return []
        k = rate / OZ_TO_GRAM
        out = []
        for d in data:
            try:
                close = float(d["close"])
                if math.isfinite(close) and close > 0:
                    out.append((str(d["date"]), close * k))
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda row: row[0])
        out = out[-count:]
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
        with open_quote(req, timeout=10) as r:
            txt = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"\((\{.*\})\)", txt)
        if not m:
            return []
        obj = json.loads(m.group(1))
        arr = obj.get("minLine_1d", [])
        rate = _get_cny_rate()
        if not rate:
            return []
        k = rate / OZ_TO_GRAM
        out = []
        for e in arr:
            if len(e) >= 2 and e[1]:
                # 每条的最后元素是完整时间戳 "2026-09-01 06:01:00"（第一条为 10 字段，后续 6 字段）
                ts = e[-1] if isinstance(e[-1], str) and len(e[-1]) >= 10 else str(e[0])
                try:
                    price = float(e[1])
                    if math.isfinite(price) and price > 0:
                        out.append((ts, price * k))
                except (TypeError, ValueError):
                    continue
        return out
    except Exception:
        return []


def fetch_spot_quote():
    """新浪伦敦金 hf_XAU 实时报价（已换算人民币元/克），走势图自用。

    走势图的数据序列取自伦敦金 K 线，因此基准价也必须同源。主界面若切到
    「浙商积存金」或「沪金99」，其昨收与这条序列不是同一个市场，直接拿来画
    虚线会明显错位（浙商昨收 907 对伦敦金序列 889 就是典型）。失败返回 None，
    此时图上不画昨收线，而不是画一条错的。
    """
    url = "https://hq.sinajs.cn/list=hf_XAU"
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
        rate = _get_cny_rate()
        if not rate:
            return None
        k = rate / OZ_TO_GRAM
        price, prev = float(f[0]), float(f[7])
        if not all(math.isfinite(v) and v > 0 for v in (price, prev)):
            return None
        return {"code": "hf_XAU", "name": "伦敦金",
                "price": price * k, "prev": prev * k,
                "unit": "元/克", "date": f[12], "time": f[6]}
    except Exception:
        return None


def fetch_kline_quote(main_quote):
    """让基准价与 k 线序列同源：主品种不是国际盘时，改用伦敦金自己的报价。"""
    if (main_quote and main_quote.get("code") == "hf_XAU"
            and main_quote.get("cny") and not main_quote.get("stale")):
        return main_quote
    return fetch_spot_quote()


class ChartWindow:
    W, H = 760, 460
    PAD_L, PAD_R, PAD_T, PAD_B = 62, 20, 46, 36

    def __init__(self, get_quote, master=None):
        self.get_quote = get_quote
        self.mode = "day"
        self.data = []
        self.title_line = "加载中…"
        self.color = (255, 200, 80)
        self.prev_line = None
        self.data_time = ""
        self.unit = "元/克"
        self.q = queue.Queue()
        self._request_id = 0
        self._closed = False
        self._poll_id = None
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        scale = max(1.0, self.root.winfo_fpixels('1i') / 96.0)
        self.PAD_L, self.PAD_R, self.PAD_T, self.PAD_B = (
            round(value * scale) for value in (62, 20, 46, 36))
        self.root.title("金价走势 · GoldPriceTray")
        self.root.geometry(f"{round(self.W * scale)}x{round(self.H * scale)}")
        self.root.minsize(round(600 * scale), round(360 * scale))
        self.root.configure(bg="#1e1f22")
        self.root.resizable(True, True)
        self._build()
        self.load_data()
        self._poll_id = self.root.after(80, self._poll)

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
        self._request_id += 1
        request_id, mode = self._request_id, self.mode
        self.title_line = "加载中…"
        self.data = []
        self.prev_line = None
        self.lbl_price.configure(text="")
        self._redraw()
        threading.Thread(target=self._fetch_worker, args=(request_id, mode), daemon=True).start()

    def _fetch_worker(self, request_id, mode):
        try:
            if mode == "day":
                result = self._fetch_day()
            elif mode == "week":
                result = self._fetch_daily(5)
            else:
                result = self._fetch_daily(22)
        except Exception as e:
            result = {"error": str(e)}
        self.q.put((request_id, result))

    def _fetch_day(self):
        q = fetch_kline_quote(self.get_quote())
        rows = fetch_minute_kline(KLINE_SYMBOL, 5)
        if not rows:
            return {"error": "无法获取分时数据或人民币汇率"}
        today = time.strftime("%Y-%m-%d")
        # 国际金价 24h 连续交易（北京时间 06:00 起），保留当日全部数据
        current_rows = [(t, p) for t, p in rows if t.startswith(today)]
        data_date = today if current_rows else rows[-1][0][:10]
        rows = current_rows or [(t, p) for t, p in rows if t.startswith(data_date)]
        labels = [t[11:16] for t, _ in rows]
        prev = q.get("prev") if q and "prev" in q else None
        name = "伦敦金参考行情"
        cur = q.get("price") if q and "price" in q else rows[-1][1]
        unit = "元/克"
        tstr = rows[-1][0]
        # An old trading session must not use today's spot quote as its endpoint.
        if not q or q.get("date") != data_date:
            q, cur, prev = None, rows[-1][1], None
        if prev:
            chg = cur - prev
            pct = chg / prev * 100
            prev_line = (prev, f"昨收 {prev:.2f}")
        else:
            chg = pct = 0
            prev_line = None
        title = f"{name} 当日分时" if data_date == today else f"{name} 最近交易日 {data_date}"
        return dict(data=list(zip(labels, [p for _, p in rows])), latest=(cur, chg, pct, name),
                    title=title, prev_line=prev_line, data_time=tstr, unit=unit)

    def _fetch_daily(self, n):
        rows = fetch_daily_kline(KLINE_SYMBOL, n)
        if not rows:
            return {"error": "无法获取日K数据或人民币汇率"}
        dates = [d for d, _ in rows]
        closes = [c for _, c in rows]
        name = "伦敦金参考行情"
        cur = closes[-1]
        prev = closes[-2] if len(closes) > 1 else cur
        chg, pct = cur - prev, (cur - prev) / prev * 100 if prev else 0
        unit = "元/克"
        tstr = dates[-1]
        span = "近一周" if n <= 5 else "近一月"
        return dict(data=list(zip([d[5:] for d in dates], closes)), latest=(cur, chg, pct, name),
                    title=f"{name} {span}", prev_line=(prev, f"前一交易日 {prev:.2f}"),
                    data_time=tstr, unit=unit)

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                request_id, result = self.q.get_nowait()
                if request_id != self._request_id:
                    continue
                if "error" in result:
                    self.title_line = f"数据获取失败: {result['error']}"
                    self.data = []
                    self.prev_line = None
                    self.data_time = ""
                else:
                    self.title_line = result["title"]
                    self.data = result["data"]
                    self.latest = result["latest"]
                    self.prev_line = result["prev_line"]
                    self.data_time = result["data_time"]
                    self.unit = result["unit"]
                self._redraw()
        except queue.Empty:
            pass
        self._poll_id = self.root.after(80, self._poll)

    def close(self):
        self._closed = True
        self._request_id += 1
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()

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
                text=f"伦敦金参考行情 · 元/克 · 数据时间 {self.data_time}")
        else:
            self.lbl_title.configure(text=self.title_line)
            self.lbl_price.configure(text="")
            self.lbl_status.configure(text="伦敦金参考行情 · 元/克")
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
