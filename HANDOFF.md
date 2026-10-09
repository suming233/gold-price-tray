# GoldPriceTray 项目交接文档

> 本机维护版本：3.12.0；最后更新：2026-10-09（Asia/Shanghai）。
> 文档用于说明项目事实和维护方法；后续操作范围以用户当前请求为准。
> v3.10 原源码、旧交接文档和旧安装版 exe 保存在本次交付的回滚压缩包中。

## 0. 应用概况

Windows 金价托盘工具：图标显示元/克报价，悬停约 1 秒显示详情卡，单击或双击优先打开浙商黄金走势图。
默认品种为浙商银行积存金，可以切换伦敦金、纽约黄金、沪金99、黄金T+D。
运行依赖 Python、pystray 0.19.5、Pillow 和 tkinter；打包后为独立单文件 exe。

## 1. 关键路径与版本控制

| 内容 | 路径 |
| --- | --- |
| 权威源码 | `C:\Users\22877\WorkBuddy\gold-price-tray` |
| 带 Git 历史的工作目录 | `C:\Users\22877\WorkBuddy\_gpt_push` |
| 安装目录 | `%LOCALAPPDATA%\GoldPriceTray` |
| 正式运行程序 | `%LOCALAPPDATA%\GoldPriceTray\GoldPriceTray.exe` |
| UTF-8 日志 | `%LOCALAPPDATA%\GoldPriceTray\GoldPriceTray.log` |
| 用户配置 | `%LOCALAPPDATA%\GoldPriceTray\settings.json` |
| 已验证的开发 Python | `C:\Users\22877\.workbuddy\binaries\python\envs\py312\Scripts\python.exe` |
| 本次交付 | `C:\Users\22877\Documents\Codex\2026-10-08\workbuddy-handoff\outputs` |

权威源码目录仍没有 `.git`；本次更改同步到 `_gpt_push`，保留其已有历史与远端配置。
v3.11.1 源码与版本标签发布至 `suming233/gold-price-tray`，标签触发 GitHub Actions 测试、打包和 Release 安装包上传。
修复前 Git 基线为 `84f8262`；仍未移动 `.git`。
后续改动应继续同步两份目录，或在用户决定后统一为一个 Git 工作目录。
旧 `_release` 目录不是本次交付入口。

## 2. 文件职责

| 文件 | 职责 |
| --- | --- |
| `gold_price_tray.py` | 报价解析、托盘菜单、悬停卡、配置、自启、安装与卸载 |
| `quote_http.py` | 显式不使用代理的行情 HTTP 请求 |
| `chart.py` | 默认浙商分时、周、月图，可切伦敦金参考；队列接收后台结果 |
| `tests/` | 30 个确定性回归测试，覆盖菜单、竞态、异常、存储、安装及浙商图表数据 |
| `GoldPriceTray.spec` | PyInstaller 打包配置，包含 chart hidden import |
| `build.bat` | 原有 Windows 打包入口；本次直接使用 Python + PyInstaller |
| `.github/workflows/build.yml` | 构建前运行回归测试；发布仅由用户后续决定 |

## 3. 运行与测试

在源码目录运行：

```powershell
& 'C:\Users\22877\.workbuddy\binaries\python\envs\py312\Scripts\python.exe' gold_price_tray.py
& 'C:\Users\22877\.workbuddy\binaries\python\envs\py312\Scripts\python.exe' -m unittest discover -s tests -v
& 'C:\Users\22877\.workbuddy\binaries\python\envs\py312\Scripts\python.exe' -m PyInstaller --noconfirm GoldPriceTray.spec
```

源码模式和已安装 exe 都使用 Windows 命名互斥体 `Local\GoldPriceTray_SingleInstance`。
正常只有一个托盘图标；PyInstaller onefile 出现父/子两个进程是正常现象。
启动日志带版本号；随后应出现 `first ok` 和 `hover target: icon rect ...`。

单文件入口：安装目录内运行 → 托盘；其他目录双击 → 安装向导；`/silent` → 静默安装；`/uninstall` → 卸载确认。
升级前先结束安装目录内的旧进程，避免文件被占用；安装先完整复制到 `.new`，再替换旧文件。
自启与卸载注册表为 HKCU，无需管理员权限。更新正式 exe 后，卸载项版本应为 3.12.0。

## 4. 报价及线程约定

- 浙商实时报价接口：`api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice?productSku=1961543816`，提供当前价、昨收、涨跌和时间，未提供 OHLC。
- v3.12.0 从京东官方浙商产品页面的 gold-chart 组件确认历史接口；不能再从“实时接口未提供 K 线”推断整个产品没有历史接口。
- 新浪接口：`hq.sinajs.cn/list=<code>`，带新浪 Referer，GBK 解码；国内品种原生元/克，国际品种按汇率/31.1035 换算。
- 所有行情请求通过 `quote_http.open_quote`，不继承桌面启动器或系统环境代理。
- 汇率失败且没有缓存时，国际报价和 K 线返回失败；不会把美元值显示成元/克。
- 使用单一刷新循环和事件唤醒；重复点击刷新合并请求，不为每次点击新建线程。
- 请求记录品种代次；用户切换后，之前请求的响应被丢弃。
- 同一品种更新失败时保留上次有效报价，图标变灰，菜单/tooltip/悬停卡标记更新失败；新品种加载时清空旧品种报价。
- `settings.json` 可包含 `source` 和 `hover_delay_ms`；品种选择原子保存，同时保留其他字段。

统一行情字段：`code/name/price/prev/change/pct/date/time/unit/cny/has_ohlc`；OHLC 可为 None；可选 `usd_price/rate/ref_price/ref_name/stale`。

## 5. 托盘菜单与悬停

菜单 action 必须符合 pystray 的 `(icon, item)` 调用契约。品种代码通过闭包捕获：

```python
def select(icon, item):
    self.set_source(code)
```

**禁止恢复** `lambda i, c=c: self.set_source(c)`：第二个参数会被 MenuItem 覆盖。
这是本轮 ASCII 编码异常及随后右键菜单失效的直接根因，而非中文字体问题。
品种入口和行情入口均校验代码，菜单勾选直接读取当前品种。
原生菜单在右键弹出前由托盘线程重新生成，避免显示上次刷新的价格。

图标定位已由 UIA 名字匹配改为 Windows `Shell_NotifyIconGetRect`。
pystray 0.19.5 Windows 后端实际注册的 `uID=0`（它传入 hID，但结构字段叫 uID）；定位使用独立 HWND + uID=0。
**升级 pystray 时必须重验图标身份**，因此依赖固定为 0.19.5，并有相应回归测试。
每 300ms 查询本图标坐标；定位失败立即取消旧矩形，不回退整个通知区域。
程序在创建 UI 前启用 DPI awareness，确保 Shell 坐标、鼠标坐标和 Tk 使用相同尺度。

悬停延时 1000ms，隐藏宽限 250ms。热区仍为本图标、可见卡片及二者缝隙的桥；桥只覆盖缝隙。
进入热区清空系统 tooltip，离开恢复；进入全屏时清掉悬停状态并恢复 tooltip。
卡片锚定本图标并限制在所在显示器工作区；负坐标使用 Tk 的绝对偏移语法 `+-100`。

v3.11.1 按用户要求将详情卡字体、间距及定位间隙按 1.5 倍缩放，统一比例为 `HOVER_CARD_SCALE`。
主价格从 46px 改为 69px；其余字号按整数像素取整，窗口由内容自然撑开。
与用户截图相同内容的 Tk 实际预览由 222×210 变为 338×315；宽度有字体像素取整误差，无裁切。

## 6. 图表

默认与再次打开均优先展示 **浙商银行积存金**；顶部浙商按钮排第一，伦敦金参考排第二，仅手动选择。
托盘选择其他品种不会改变图表的默认浙商数据源；当日分时、近一周、近一月均同源，不套用其他市场昨收。
浙商历史请求为 POST 表单 `reqData` JSON，基础路径 `https://api.jdjygold.com/gw2/generic/hj/h5/m/`：
- 分时 `cfGetPriceTrendChart`：`productSku=1961543816`、`appChannel=11`、`priceType=buy`、`beginTime=''`。
- 月历史 `cfGetQuotesPriceKLine`：相同 SKU，`periodType=m1`。周视图取该历史最近 5 个报价日期。
- 校验业务成功、日期、有限正价格；排序去重。失败明确提示，不自动替换伦敦曲线。
- 当日分时仅使用同日浙商昨收；新实时报价可追加末点。周/月涨跌相对区间首日，不冒充日涨跌。
图表每 30 秒更新，请求捕获周期与市场并编号；旧市场/旧周期结果丢弃。
手动伦敦金参考仍使用其自身报价与当前汇率换算，近周/月为 5/22 个交易日。

全部 Tk UI 在悬停线程的同一个解释器中运行，图表为 Toplevel。
重复打开图表复用已有窗口，关闭时取消轮询，之后可再次打开。
后台线程只计算结果并入队；周期请求带编号，过期结果不影响当前周期。
窗口随 DPI 调整尺寸，并设置最小尺寸以防坐标轴与按钮挤压。

## 7. 安装、卸载及日志

安装复制失败保留旧程序；自启、快捷方式、卸载项注册失败不再报告成功。
源码模式自启使用 Python/Pythonw + 脚本的完整命令，而非只登记 `.py` 文件。
托盘卸载也先确认；新流程使用隐藏 PowerShell 辅助脚本，支持 Unicode 路径。
辅助脚本仅结束安装路径内的程序，验证实际删除路径，重试删除并自清理。
已在工作区内含中文及单引号的临时目录执行验证；未卸载真实用户应用。

日志写入加锁，单条消息限制长度；轮转同时约束行数和字节数，总大小不超过 256KB。
只记启动、首次成功、转向、错误/恢复、心跳与界面错误。没有记录代理值或认证信息。

## 8. 本轮验证范围

v3.12.0：30 个回归测试通过；官方浙商分时/周/月实际取数通过，三个 Tk 图表及参考行情切换渲染通过。

v3.11.1：24 个回归测试通过；悬浮卡在本机 DPI 下渲染并检查放大后布局。
以下为 v3.11.0 系统排查与修复时的验证记录：

- 24 个回归测试全部通过，源码编译通过。
- 五个实时品种、汇率、分钟线和日线正常；无效地址及中文环境代理不影响取数。
- 实际运行 pystray 回调，依次切换五个品种：价格正确返回，勾选正确，原生菜单句柄保留。
- 本机 Win11、150% DPI 下定位成功，真实图表分时/月线完成视觉检查。
- 临时安装替换、COM 快捷方式、Unicode 路径的安全卸载辅助脚本均通过。
- 正式 exe 打包后替换本机安装版，保留原有用户配置与自启状态。

## 9. 仍有的限制

| 限制 | 说明 |
| --- | --- |
| 浙商历史跨度 | 当前接入官方分时与一个月价格曲线；未扩展更长周期或完整 OHLC 蜡烛图 |
| 第三方行情依赖 | 网络或服务端故障仍可能造成更新失败；应用保留并标记旧报价 |
| 行情时间可能滞后 | 展示接口提供的时间；休市与假日可能返回上个交易日 |
| 轮询误差 | 悬停延时仍有约 300ms 的轮询量化误差 |
| 其他机器 | Win10、多显示器、负坐标显示器、任务栏折叠层未逐一实机验收；逻辑已按精确图标与显示器坐标处理 |
| 版本控制两份目录 | 本次已同步，尚未统一成一个 Git 工作目录 |

这些是当前能力边界，不应写成已经完成的功能。

## 10. 后续工作

系统排查与修复已完成；随后按用户要求将悬浮详情卡整体放大 1.5 倍，并将所有图表默认改为浙商优先。
设置界面、自定义品种、历史存储、其他数据源、统一 Git 目录均留待后续决定。
后续每次修复都应更新版本、测试、HANDOFF；变更品种/图表线程时运行已有回归测试。

## 11. 技术参考

- [pystray 菜单回调与动态菜单](https://pystray.readthedocs.io/en/stable/reference.html)
- [Windows 精确图标矩形](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyicongetrect)
- [NOTIFYICONIDENTIFIER](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyiconidentifier)
