**TradingAgents Web 前端**

当前包含 M0 工程基线、M1 模型配置/自选管理/持久化研究，以及 M2 美股行情图表、市场快照、行业与主题观察、ETF申赎/期权/新闻样本情绪、按需市场简报与数据中心。React 工作台在 /workbench/，原有本机 8000 端口入口保留。所有金融数值由后端真实数据接口提供，缺失时明确展示，不使用模拟行情替代。

本批入口（路径均相对 /workbench/）：themes 为“主题与产业链”，可新增、复制、编辑成员和证据、归档及查看版本；insights/flows、insights/options、insights/news 分别查看ETF申赎、期权和新闻样本情绪；settings/sources 管理已有数据账号。iShares免费估算目前仅适配SOXX/IVV，Yahoo期权保留未知报价时刻和缺失字段；Intrinio、Massive、Alpaca按已有账号和端点权限读取。新闻样本情绪、成交关注和ETF申赎分别展示，不能据此声称全市场情绪或机构净买入。

settings/models 的“服务预设/供应商”与“API协议”独立配置。自定义连接可填写服务名称、Base URL、任意模型ID，选择OpenAI Chat Completions、OpenAI Responses、Anthropic Messages或Google原生协议，并选择API Key、自定义请求头或无需认证。协议须与服务实际支持的端点匹配；其他协议需要增加适配器。密钥与自定义请求头值保存在系统密钥库，GET只返回配置状态；写入凭据直接调用SDK，不放入TanStack Query的MutationCache。保存模型后分别检测聊天和工具调用，再绑定研究角色。

行情图使用 Lightweight Charts 5，支持蜡烛图/分时收盘线/成交量、SMA/EMA/BOLL/RSI/MACD/ATR、来源允许的周期与历史窗口、盘前盘后、价格调整及 ET/UTC。日周月以交易日为横轴，分钟使用 UTC 时间戳并按选定时区格式化；缺失 OHLC/指标/成交量保留空白，数据时间与接收时间分开标注。参考价格来自 K 线收盘字段，不声称实时或 NBBO。

图表由独立控制器持有画布与序列；同来源/证券/周期/时段/调整身份刷新仅更新序列并保留逻辑缩放，历史范围扩展保留时间锚点。只有身份变化或用户点击适应范围才主动重置；销毁时取消十字光标订阅。所有周期、范围、时段、调整、时区、图样、指标参数与 SPY 开关写入 URL，非法值按已声明能力收敛。指标参数有整数/范围与 MACD 快慢关系校验，并进入查询身份。历史范围显式受来源支持和 5,000 根上限约束，不声称无限分页。

SPY 比较只取同来源/feed/周期/时段/调整/历史窗口的双方共同完整 K 线，以共同首点为 0% 绘制独立百分比窗格；任一方缺失不补值，显示共同起止和覆盖数量，不承诺总回报。当前标的最近已完成研究可直接打开报告，读取列表不会启动模型。

市场快照以共同截止的完整交易日为依据，ECharts 显示行业 ETF 收益与相对 SPY 差值。广度只代表明确版本的观察样本；RVOL 使用同 feed 的完整日成交量及此前 20 日均量；估算成交额不代表资金净流入。ETF 申赎等缺失能力显式列出。模型简报仅在点击按钮后生成，附数值证据引用，不从个股报告文本提取虚构点位。

设置中心的“数据中心”通过版本校验保存缓存时效、聚合并发和超时，展示 active/pending/restart 状态并可重试应用。缓存清理只针对可重建行情，保留冻结市场证据、研究记录与凭据。原有 FRED / Alpha Vantage / TypeSafe Jev / SEC EDGAR 状态仅表示后端检测到的环境配置存在性，不表示连接验证或已作为行情来源。

**本地开发**

原有兼容界面的 HTML/CSS/JavaScript 源码位于 `legacy/`，现代 React 界面位于 `src/`。`backend/scripts/package_workbench.py` 将两者打包到后端生成资产目录；不要直接编辑后端的 `web/static/` 或 `web/ui/`。`legacy/**` 保留原有内容，不纳入现代前端的 Oxlint/Oxfmt 门禁，功能由后端原有 Web 测试覆盖；React 与工程配置继续执行完整严格检查。

运行环境为 Node 24。在 frontend 目录执行 npm ci，然后显式执行 npm run vendor:patch。使用项目 Python 环境启动 8001 端口的后端，再运行 npm run dev，访问 http://127.0.0.1:5173/workbench/。Vite 只监听回环地址；/api 代理到 127.0.0.1:8001，开发代理将 Origin 固定为目标本机来源以满足后端同源校验。

**契约生成**

在项目根运行 backend/.venv/bin/python backend/scripts/export_openapi.py，之后在 frontend 运行 npm run api:generate。生成过程仅使用仓库 contracts/openapi.json，不启动后端、不读取模型凭据。

Orval 生成代码位于 src/shared/api/generated，禁止手改。npm run api:generate:check 会在临时目录重生成并检查文件内容及多余文件。生成文件不参与手写代码 lint/format，但参加 TypeScript 7 检查和正式构建。生成 Fetch 函数通过 shared/api/transport.ts 处理非 2xx 错误，使查询正确进入 error 状态。

研究事件使用独立 fetch 流 transport；由生成的 GetResearchEventsResponse 派生单事件 Zod schema，验证帧归属、事件 ID 和序号。断线用游标恢复，状态查询轮询兜底；SSE 不走普通 JSON SDK。完整报告 iframe 禁止脚本，Markdown 下载使用同源受控端点。界面测试应连接隔离的测试适配服务，不自动触发真实模型计费。

**检查入口**

npm run check 依次执行 vendor 补丁状态检查、生成漂移检查、Oxfmt、Oxlint 类型感知规则、应用与工程配置的 TS7 检查、Vitest 和 Vite 构建。lint:quick 只用于本地快速反馈；CI 不与完整 lint 重复运行。CI 不做自动修复。test:e2e 为后续真实业务场景入口，M0 浏览器验收单独记录。

**上游类型声明补丁**

Ant Design 6.6.7 当前锁定 @rc-component/image 1.10.0 与 @rc-component/picker 1.12.2。两者已发布的声明存在继承冲突，严格检查会报告 TS2430。本项目保持 skipLibCheck=false；修复仅作用于四份 .d.ts，不修改运行时 JavaScript，也不放宽业务类型。

| 包与文件（es/lib 各一份）       | 最小修复                                                        |
| ------------------------------- | --------------------------------------------------------------- |
| image / PreviewGroup.d.ts       | GroupPreviewConfig 继承时排除重新定义的 imageRender             |
| picker / PickerPanel/index.d.ts | BasePickerPanelProps 继承时排除 SharedTimeProps 的 defaultValue |

picker 修复与官方 1.16.0 发布包采用的 Omit<SharedTimeProps<DateType>, 'defaultValue'> 一致。image 修复保持组预览回调的 current 必填语义，使用 Omit<InternalPreviewConfig, 'imageRender'> 后再声明自己的回调。

严格检查工程配置时，Orval 的发布声明会引用其可选插件类型，因此开发依赖包含 @faker-js/faker 10.6.0、@modelcontextprotocol/server 2.4.0；fdir 的声明需要 @types/picomatch 4.0.3。这些仅用于工具声明完整性，不启用业务 MCP、不生成 mock 数据，也不进入浏览器应用依赖图。

scripts/patch-vendor-types.ts 校验精确包版本、原始/补丁后 SHA-256 及唯一目标片段，先验证全部文件再写入，重复运行幂等。遇到未知版本或内容立即失败，不下载任何文件。npm run vendor:check 只校验、不写入。npm ci 会恢复上游原文件，因此每次安装后必须显式 vendor:patch；不使用隐式 postinstall。升级依赖时应优先移除已获官方修复的补丁，重新做严格检查。
