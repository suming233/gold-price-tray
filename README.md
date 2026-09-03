# 金价托盘 GoldPriceTray

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)](#)
[![Python](https://img.shields.io/badge/python-3.8%2B-green)](#)
[![Downloads](https://img.shields.io/github/downloads/suming233/gold-price-tray/total?label=downloads)](releases)

**Windows 任务栏实时金价小工具**——托盘图标直接显示金价数字，鼠标悬停弹出大字体详情卡，双击查看走势图。

[English](#english) | 中文

![当日分时走势](docs/screenshot_chart.png)

| 悬停详情卡 | 近一月走势 |
| --- | --- |
| ![悬浮卡](docs/screenshot_hover.png) | ![近一月走势](docs/screenshot_month.png) |

## 功能

- **托盘图标显示实时金价**，默认伦敦金换算成人民币元/克
- **悬停详情卡**：大字号价格、涨跌幅、美元原价、当前汇率
- **走势图**：双击图标打开，可切换当日分时 / 近一周 / 近一月
- **品种切换**：伦敦金 XAU/USD、纽约黄金、沪金 99、黄金 T+D
- **涨红跌绿**，符合国内行情习惯
- 每 30 秒自动刷新，支持开机自启
- 单实例互斥，重复启动不会多出图标

## 安装

### 方式一：用现成的安装包（推荐）

从本仓库 [Releases](releases) 页面下载
`GoldPriceTray_Setup.exe`（约 19MB），双击运行即可。

安装程序会自动完成：

- 安装到 `%LOCALAPPDATA%\GoldPriceTray`
- 注册开机自启
- 创建桌面和开始菜单快捷方式
- 写入系统的"应用和功能"卸载列表

这个 exe 有三种身份：

| 场景 | 行为 |
| --- | --- |
| 在没装过的机器上双击 | 弹出安装向导 |
| 加 `/silent` 参数 | 静默安装，无窗口 |
| 双击已安装目录里的 exe | 正常启动托盘 |

### 方式二：从源码运行

```bash
pip install -r requirements.txt
python gold_price_tray.py
```

### 卸载

三个入口任选：托盘右键菜单 → 卸载并移除 / 系统的"应用和功能" / 重新运行安装包点卸载。

## 打包自己的 exe

```bash
pip install pyinstaller
build.bat
```

产物在 `dist/GoldPriceTray.exe`。

## 数据源

新浪财经公开行情接口：

| 用途 | 接口 |
| --- | --- |
| 伦敦金（美元/盎司，24h 连续） | `hq.sinajs.cn/list=hf_XAU` |
| 美元/人民币汇率 | `hq.sinajs.cn/list=fx_susdcny` |
| 日 K 线（2006 年至今） | `GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU` |
| 分时（1 分钟粒度） | `GlobalFuturesService.getGlobalFuturesMinLine?symbol=XAU&type=5` |

换算公式：

```
元/克 = 美元/盎司 × 汇率 ÷ 31.1035
```

（1 金衡盎司 = 31.1035 克）

## 免责声明

行情数据来自第三方公开接口，仅供个人参考，不构成投资建议，不保证实时性与准确性。

## English

**GoldPriceTray** is a lightweight Windows tray app that shows the live gold
price right on your taskbar — international spot gold (XAU/USD) converted to
CNY per gram using the real-time USD/CNY exchange rate.

- Tray icon shows the live price; hover for a detailed pop-up card
- Double-click the icon for intraday / 1-week / 1-month charts
- Multiple symbols: London gold, COMEX gold, SHFE gold, Gold T+D
- Refreshes every 30 s; optional start-up with Windows; single-instance guard
- One-file installer: double-click to install (silent mode: `/silent`),
  uninstall from the tray menu or Windows "Apps & features"

Download `GoldPriceTray_Setup.exe` from the
[Releases](releases) page and double-click — no Python required.

Data source: Sina Finance public quotes. For personal reference only, not
investment advice.

## License

[MIT](LICENSE)
