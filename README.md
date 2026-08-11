# XTracker Ledger Terminal V7

一个无需 npm 的本地 XTracker 数据监控终端。视觉系统采用暖纸色背景 + 墨黑侧边栏 + 电青信号蓝的终端配色，参考 Bloomberg Terminal、Stripe Dashboard 与 GitHub Primer 的克制数据美学：信息层级靠对比和字号，而非彩色装饰。

## 启动

Windows：

- `start.bat`：连接 `xtracker.polymarket.com` 公开 API（实时数据）。

浏览器地址：`http://localhost:8787`

## V7 视觉语言

- 暖纸底（#e9e6dd）+ 墨黑侧栏（#14161a）+ 电青信号蓝（#0a4fd6）作为唯一强调色。
- 排版主轴是 Inter / 系统黑体，数字与等宽信息一律用 JetBrains Mono / Consolas。
- 圆角全部收至 4–6px，去掉浮华的毛玻璃和多层辉光，仅保留必要的卡片阴影。
- 热力图改为蓝阶单色：颜色越深代表帖子越活跃；ET 周末用赭色描边标记；当前 BJT 小时为蓝色描边 + 角标。
- 图表/直方图/进度条都使用同一组墨色与强调色，避免每块面板一套色。

## 结构与交互（沿用 V6）

- 左侧栏：编号、双语层级、移动选中指示器、BJT/ET 双时钟。
- 点击左侧栏目后，目标区块停在顶部栏下方；手动滚动会反向同步选中项。
- 热力图固定使用北京时间，覆盖最近 21 个独立自然日，共 504 个小时格。
- 小时标题上方为北京时间，括号内为对应 ET 小时。
- 每个日期行以北京时间为主，并在括号中显示该行映射到的 ET 日期范围。
- 热力图无鼠标悬停放大、行列淡化、跟随光标或弹出浮层。
- 桌面端完整显示 24 小时；移动端仅热力图内部横向滚动。
- Python 服务端只使用标准库，自动查询至少 23 天历史，最多装载 6000 条帖子。

## V7.2 新增

- **自由区间 tab**：把"24H/48H/7D/周期" seg 替换成动态的 tracking 周期 tab。每个周期显示日期范围 + LIVE 标记 + 累计数；点击切换后整个 dashboard（KPI、时间线、预测、Profile）都围绕该周期。子市场桶（如 "700-724 tweets"）会被自动过滤，只保留主周期（"# tweets Jul 15 - Jul 22"）。
- **辅助范围**：保留 近 24H / 近 48H / 近 7D / 全部 作为次要选项，放在 tracking tab 之后。

## V7.1 新增

- **完整预测模型**：同小时习惯 + 近期动量 + 当前速率三权重混合，泊松/负二项分布，给出 P10/P50/P90 区间和"上破下一档"概率。
- **Polymarket YES 参考价**：通过 `/api/market-prices?slug=...` 代理 gamma-api，在预测面板显示当前市场 YES 中间价作为参考。
- **预测快照持久化**：每小时自动保存临近版 + 稳定版快照到 `predictions.json`（同 trackingId + hourKey 去重），前端从 `/api/predictions` 加载历史。
- **Moving Averages**：发帖活动时间线图底部叠加 24H / 7D / 14D 三条 MA 曲线。
- **工作日/周末/节假日分析面板**：按 ET 分类的日均 / 时均 / 峰值对比。
- **每小时预测记录面板**：展示服务端持久化快照，或回退到前端回测。

## API

- `GET /api/dashboard?handle=&platform=` — 主数据
- `GET /api/market-prices?slug=` — Polymarket event 市场列表（带 YES 价）
- `GET /api/predictions?trackingId=&limit=` — 预测快照列表
- `POST /api/predictions` — 写入预测快照（同 trackingId+hourKey 覆盖）

## 文件

- `index.html`：完整前端（HTML + CSS + JS 单文件）。
- `server.py`：静态服务器、同源代理（XTracker + Polymarket gamma-api）。
- `predictions.json`：预测快照本地持久化（首次写入时自动创建，仅含实时数据）。
- `launcher.ps1`：自动寻找 Python。
- `start.bat`：Windows 启动入口。
