# 研衡 · ThesisFlow

**让每一次投资判断，都有证据与纪律。**

研衡（ThesisFlow）是一个面向个人投资者的美股投研工作台，将发现机会、研究验证、投资决策和跟踪复盘连接起来。项目在 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 研究引擎上持续扩展，提供独立的产品界面、账号工作区和研究管理功能，保留上游来源与 Apache-2.0 许可。

项目仓库：[ninthmonee/thesisflow](https://github.com/ninthmonee/thesisflow) · [来源与许可](#来源与许可) · [命名说明](docs/brand.md)

![研衡研究工作台：市场背景、研究进度、关注证券与个人纪律](docs/images/readme/dashboard.png)

## 目录

- [项目功能与界面展示](#项目功能与界面展示)
- [安装与启动](#安装与启动)
- [首次设置与账号登录](#首次设置与账号登录)
- [第一次使用：完成一轮投研](#第一次使用完成一轮投研)
- [功能使用详解](#功能使用详解)
- [模型与数据源配置](#模型与数据源配置)
- [本地 Agent 独立研究](#本地-agent-独立研究)
- [日常维护与备份](#日常维护与备份)
- [常见问题](#常见问题)
- [项目结构与开发](#项目结构与开发)
- [进一步阅读](#进一步阅读)

## 项目功能与界面展示

研衡围绕一条工作流组织功能：**发现机会 → 研究验证 → 形成个人决策 → 跟踪复盘**。你可以先使用行情、自选和计划管理，再按需接入模型进行研究。

| 功能入口 | 可以做什么 |
| --- | --- |
| 研究工作台 | 查看市场背景、最近研究、进行中的任务、关注证券和到期复核计划 |
| 自选行情 | 搜索并添加股票 / ETF，管理分组与收藏，记录备注，查看价格、成交量和个股图表 |
| 市场发现 | 浏览市场概览、11 个行业 ETF 代理、主题与产业链，以及期权、ETF 份额流和新闻样本 |
| 新建研究 | 明确研究问题，选择内置 12 角色或本地 Agent，设置深度、分析师、预算和补充资料 |
| 研究库 | 重开历史报告，核对结论、证据、过程与个人观点，导出报告或进行同股研究对照 |
| 投资决策 | 保存个人投资体系、个股计划、情景假设、风险边界和手动复盘记录 |
| 设置中心 | 管理模型连接、角色配置、运行环境、数据来源、缓存及个人账号 |

默认入口是 Open Design 版深色工作台。模型、数据源、完整图表和部分专业管理功能通过工作台内的配置浮层打开，也可使用 `/workbench/advanced/` 下的完整 React 页面。

<details>
<summary>展开查看 8 张主要功能截图（点击图片可查看原图）</summary>

| 账号登录与独立工作区 | 自选行情与个股图表 |
| :---: | :---: |
| [![账号登录界面](docs/images/readme/login.png)](docs/images/readme/login.png) | [![自选证券、NVDA 图表、来源时点和研究备注](docs/images/readme/watchlist.png)](docs/images/readme/watchlist.png) |

| 市场发现与行业观察 | 明确研究问题 |
| :---: | :---: |
| [![市场概览、行业 ETF 表现和固定样本广度](docs/images/readme/market.png)](docs/images/readme/market.png) | [![研究标的、美东分析日期、问题、期限和语言](docs/images/readme/research.png)](docs/images/readme/research.png) |

| 研究方案与执行预算 | 个人投资体系 |
| :---: | :---: |
| [![内置 12 角色、本地 Agent、研究深度和执行预算](docs/images/readme/research-modes.png)](docs/images/readme/research-modes.png) | [![八项投资原则、个人目标与风险边界](docs/images/readme/investment.png)](docs/images/readme/investment.png) |

| 个股决策计划 | 模型连接配置 |
| :---: | :---: |
| [![公司与周期、估值情景、仓位风险和手动复盘编辑入口](docs/images/readme/plan.png)](docs/images/readme/plan.png) | [![模型供应商连接、协议、认证方式和接口地址表单](docs/images/readme/model-settings.png)](docs/images/readme/model-settings.png) |

</details>

以上截图于 **2026-11-02** 从实际运行并登录的独立演示工作区采集。自选和标注“演示”的计划通过真实后端保存；行情以画面中的来源、日期和质量标识为准。本次展示未启动真实模型研究，待配置状态和空研究库保留原样。

## 安装与启动

### 环境要求

| 依赖 | 要求与用途 |
| --- | --- |
| Python | 推荐 3.13；后端包声明的最低版本为 3.11，锁文件以已验收环境为准 |
| Node.js | 24.x，用于安装和构建前端 |
| npm | 前端锁文件对应 npm 11.19.1 |
| uv | 创建 Python 虚拟环境并安装后端依赖 |
| 浏览器 | 使用现代浏览器访问本机工作台 |
| 模型 / 数据账号 | 行情与计划管理可先使用；模型研究及部分专业数据需要自行配置连接 |

下面的命令在 macOS / Linux 终端执行。配置示例见 [`backend/.env.example`](backend/.env.example)；网页中保存的模型和数据凭据由后端使用系统密钥库管理。

### 1. 获取源码并安装后端

```bash
git clone https://github.com/ninthmonee/thesisflow.git
cd thesisflow

uv venv --python 3.13 backend/.venv
uv pip install --python backend/.venv/bin/python -r backend/requirements.lock
uv pip install --python backend/.venv/bin/python -e 'backend[web,dev]'
```

已有 `backend/.venv` 时跳过创建步骤。`backend/requirements.lock` 记录已验收的精确依赖；其他平台的依赖与运行说明见[本机运行文档](docs/web-local-operation.md)。

### 2. 安装和构建前端

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run vendor:patch
backend/.venv/bin/python backend/scripts/export_openapi.py --check
backend/.venv/bin/python backend/scripts/package_workbench.py
```

每次 `npm ci` 后都要执行 `vendor:patch`，应用仓库中已审查的第三方类型声明补丁。打包脚本会构建前端并复制新版与旧版界面的发布资源。

### 3. 启动工作台

macOS 在项目根目录执行，也可双击 `start-workbench.command`：

```bash
./start-workbench.command
```

Linux 可直接运行同一个 Python 启动器：

```bash
backend/.venv/bin/python backend/scripts/run_workbench.py
```

浏览器打开 **[http://127.0.0.1:8001/workbench/](http://127.0.0.1:8001/workbench/)**。启动器同时管理 API 和独立研究 worker，在启动终端按 **Ctrl+C** 停止两者。关闭浏览器不会停止已经提交的研究。

默认状态目录为 `data/web/development/`，数据库位于 `data/web/development/web/workbench.sqlite3`。需要另一套独立工作区或其他端口时：

```bash
backend/.venv/bin/python backend/scripts/run_workbench.py \
  --port 8002 \
  --state-dir data/web/another-workspace
```

此时访问 `http://127.0.0.1:8002/workbench/`，初始化命令也须使用该状态目录下的数据库。

## 首次设置与账号登录

### 创建首个管理员

项目**没有预设用户名和默认密码**。首次打开未初始化的实例时，页面进入“创建管理员账号”：

1. 保持工作台启动，在本机另一个终端进入项目根目录。
2. 执行下方命令取得一次性初始化凭证。
3. 将凭证直接填入页面，设置自己的用户名、显示名称与密码。
4. 创建成功后进入工作区，凭证随即失效；之后使用用户名和密码登录。

```bash
backend/.venv/bin/python backend/scripts/account_setup.py \
  --database data/web/development/web/workbench.sqlite3 \
  --show-token
```

初始化凭证仅在本机使用，不要加入截图、聊天记录或代码仓库。自定义状态目录时替换数据库路径。首个管理员会接管该实例原有的本机自选、研究历史和连接配置。

### 注册与日常登录

- 管理员完成初始化后，默认开放注册；在登录页点击“立即注册”即可创建独立账号，无需邀请码。
- 用户名为 3–32 位英文字母、数字、点、下划线或短横线，首位为字母或数字，保存为小写。
- 新密码为 12–128 个字符，不能全为空白；显示名称可选。
- 新注册账号自动登录，从空自选和独立研究记录开始，模型及数据连接由本人配置。
- 顶栏账号入口或“设置中心 → 个人账号 → 账号与安全”可修改密码、退出当前会话或退出所有会话；管理员还可开放 / 关闭注册。

账号之间独立保存业务记录和凭据。新账号不会继承主人的环境密钥、模型连接或本机订阅授权。当前服务仅监听 `127.0.0.1`，开放注册用于本机账号切换；远程访问、共享工作区和邮箱找回尚未交付。

## 第一次使用：完成一轮投研

以跟踪一家公司为例，可以按下面的顺序操作：

1. **配置研究模型**：进入“设置中心 → 模型与研究 → 编辑配置”，保存供应商连接，新增模型，检测聊天 / 工具能力，再保存默认研究配置。
2. **建立关注列表**：进入“自选行情 → 添加证券”，搜索 `NVDA` 等公司代码，选中结果后加入关注，填写需要验证的问题或备注。
3. **提出具体问题**：点击“研究这家公司”或首页“新建研究”，例如“需求能否持续？哪些财报证据能够验证或推翻增长假设？”
4. **核对方案并启动**：确认美东分析日期、观察期限、语言、执行方式和预算，按需关联个人投资体系及计划，最后点击“启动研究”。
5. **阅读并核验结果**：在“研究库”打开记录，依次查看“结论、证据、过程、我的决策”，核对数据时点、缺失项和原始模型判断。
6. **保存个人决策并复盘**：建立个股计划，记录自己的论点、失效条件、情景和复核日期；到期后补充新证据并手动记录复盘。

不接入模型时，可以先完成自选、市场浏览和手动计划管理。模型能力检测、正式研究和主动生成的市场简报可能使用供应商额度；填写表单或查看已有报告不会自动重新研究。

## 功能使用详解

### 自选行情与个股工作区

在左侧选择“自选行情”，可以在“跟踪列表”和“个股工作区”间切换：

- **添加与组织**：搜索代码或公司名添加股票 / ETF；通过“完整行情与分组管理”维护分组、新增成员和排序。
- **筛选与记录**：按分组、收藏、代码、公司或备注查找标的；收藏和备注按当前账号与证券保存，可跨分组使用。
- **查看图表**：主界面提供日 / 周 K 线、价格 / 成交量与均线观察；更多分钟周期、技术指标参数和 SPY 对比位于完整行情面板。
- **接续工作**：个股侧栏可打开最近研究、已有计划，或直接发起该公司的研究。

完整图表支持 SMA、EMA、BOLL、RSI、MACD、ATR 等指标，并按来源能力提供周期、历史范围、时段和调整选项。查看条件保存在 URL 中，刷新可恢复；分钟历史受来源窗口和最多 5,000 根 K 线约束。

列表价格使用**最近完整交易日收盘**，图表可能包含尚未完成的当日 K 线。行情显示来源、数据时点和质量状态；SPY 对比使用双方共同完整 K 线计算价格收益。缺失值不会补成虚假价格，机械趋势与波动参考也不代表已验证的预测。

### 市场发现、行业与主题

| 页签 | 使用方式与口径 |
| --- | --- |
| 市场概览 | 查看 SPY、QQQ、IWM 与 VIX 背景、行业表现和固定观察样本广度；广度样本不代表全市场 |
| 行业轮动 | 用 11 个行业 ETF 代理观察价格收益、相对 SPY 表现及成交关注，切换 1D / 5D / 1M 窗口 |
| 主题产业链 | 搜索预置主题，查看成员与关系证据；在完整管理页新建、复制、编辑、置顶、删除或恢复主题 |
| 专业数据 | 按来源查看 ETF 份额流估算、期权链和新闻情绪样本，先检查所需账号权限与覆盖 |

当前提供 30 个预置主题，自建主题与个股关系可逐步维护。主题观察按当前成员等权比较，手工编辑的关系保留待核实标识。

需要把市场背景带入研究时，主动使用“附加到研究”，在创建研究时确认或移除已冻结的来源快照。历史研究不能使用晚于研究日期才捕获的资料。市场模型简报需在完整面板主动生成。

### 三步创建研究

| 步骤 | 需要核对的内容 |
| --- | --- |
| 1. 研究问题 | 公司股票代码、美东分析日期、要验证的问题、观察期限和报告语言 |
| 2. 研究方案 | 内置 12 角色 / 本地 Agent；内置流程可选深度，并展开分析师和执行预算设置 |
| 3. 纪律与资料 | 是否应用个人投资体系、关联同股决策计划、保留哪些补充来源快照，最后核对摘要并提交 |

分析日期以 **America/New_York** 为准，可能与上海日期不同。ETF 和指数用于行情及市场背景，公司基本面研究应选择具体公司股票。

内置研究保留以下角色分工：

| 阶段 | 角色与作用 |
| --- | --- |
| 分析取证 | 技术、基本面、新闻、情绪分析师；所选分析师分别取证后汇合 |
| 多空研究 | 多头与空头研究员交换论点，研究经理汇总分歧 |
| 交易提案 | 交易员整理研究形成计划提案 |
| 风险讨论 | 激进、保守、中立风险分析员审视不同风险偏好 |
| 最终汇总 | 组合经理综合前述材料形成研究输出 |

快速、标准、深入研究分别对应 1、2、3 轮辩论。**12 种角色不等于固定 12 次模型调用**，工具循环、补充响应和反思也可能产生请求。模型请求上限、执行时长及完整页面提供的其他预算项须在提交前核对。

已提交的研究由独立 worker 执行，可以离开页面后返回查看。取消在可中止边界生效，不能撤销已经发生的用量。每个账号最多同时保留 3 个待处理研究；恢复执行需要兼容的完整检查点，否则通过“重新研究”核对后创建新任务。

### 研究库、报告与证据

在“研究库”中搜索和筛选自己的记录，打开报告后按四个页签阅读：

| 页签 | 重点查看 |
| --- | --- |
| 结论 | 已保存的报告章节、研究日期、数据资格、有效判断与原始模型判断 |
| 证据 | 本次捕获的来源、方法、数据时点和状态；“完整证据与附件”提供后续分页与正文查看 |
| 过程 | 执行阶段、实际事件、错误、模型 / 运行配置和已记录用量 |
| 我的决策 | 个人论点、立场、失效条件、复核日期与确认记录 |

“执行完成”表示任务结束；数据资格和个人确认各自记录。证据不足或未核验时，原始报告仍可阅读，系统不会据此授予有效评级。

可以导出 Markdown 报告，在完整研究详情中查看 / 导出 HTML，并对同一股票的不同研究进行对照。“重新研究”先回到表单核对原参数或当前配置，不会直接开始新一轮调用。当前证据保存范围有界，尚不支持保证全部原始输入一致的公平复跑。

### 投资体系、决策计划与手动复盘

进入“投资决策 → 个人体系”，先查看八项原则，再保存自己的长期目标、单股仓位上限、最低现金比例和默认复核间隔。更多原则、操作规则与风险字段可在[完整投资体系页面](http://127.0.0.1:8001/workbench/advanced/investment)维护。空阈值表示尚未配置。

点击“新增计划”，或从研究报告建立计划，按四个章节逐步记录：

1. **公司与周期**：公司如何赚钱、所处行业周期、护城河与经营质量，以及计划名称。
2. **估值与情景**：个人参考价格、公允价值假设、悲观 / 基准 / 乐观情景及概率。
3. **仓位与风险**：计划仓位与失效条件，并核对个人风险边界。
4. **纪律与复盘**：下次手动复核日期、计划状态和复盘记录；填写记录后可标记本次已完成复盘。

三情景价格和概率完整、概率合计 100% 后才计算毛期望收益；费用未知时净期望收益保持未知。手填价格、概率与仓位都是个人假设，保存完整度与判断正确性分别展示。

体系每次保存形成新版本，计划和研究保留各自采用的规则快照。修改当前体系不会改写历史研究。复核到期会显示在页面，不会自动发起研究、发送后台提醒或下单；归档计划可以恢复。

## 模型与数据源配置

### 模型连接：先连接，再模型，最后研究配置

入口：“设置中心 → 模型与研究 → 编辑配置”。一个通过检测的模型即可作为全体角色的默认模型，之后再按需覆盖角色：

1. 点击“新增连接”，填写连接名称，选择服务预设或自定义服务。
2. 核对 **API 协议**、API 基础地址和认证方式。项目适配 Chat Completions、Responses、Anthropic Messages、Google GenAI；自定义私有协议需要额外适配。
3. 保存连接，再点击“新增模型”，填写该服务实际可用的模型 ID。服务名称或预设不保证账号能调用某个模型。
4. 分别检测聊天和工具能力。检测会发出真实请求，可能占用模型额度；更换地址、模型或凭据后需重测。
5. 在“基础研究配置”选择默认模型、分析师、深度和语言，保存配置；混用模型时展开“12 角色模型覆盖”。

| 字段 | 填写说明 |
| --- | --- |
| 连接名称 | 用于区分账号或用途，例如“我的研究模型” |
| 供应商显示名称 | 可自定义厂商 / 网关名称，不改变底层协议 |
| 接口地址 | 填 API 基础地址；不要填单次生成请求的完整路径 |
| 认证方式 | 按服务选择 API Key、自定义请求头或明确无需认证 |
| 模型 ID | 以服务实际提供的名称为准，聊天与工具能力单独检测 |
| 默认模型 / 角色覆盖 | 先设一个默认模型，再按需要为特定角色覆盖 |

API Key 与请求头值保存在后端系统密钥库，不回显、不写入浏览器持久缓存。新任务冻结创建时的模板版本；后续修改模板不会替换旧任务配置。网页连接与 CLI 的环境配置是不同的配置入口。

原本机所有者还可选择已支持的本机订阅授权路径；按页面提示自行在终端登录，再保存模型并检测能力。工作台账号登录、供应商授权和模型能力检测是三个独立步骤，普通注册账号不能借用主人的授权。具体条件见[本机运行说明](docs/web-local-operation.md)。

### 数据源：行情、研究资料和专业账号分别管理

入口：“设置中心 → 数据来源 → 管理数据源”。缓存时效、并发、超时及可重建行情缓存位于“缓存与数据设置”。

| 来源 | 当前用途 | 配置要点 |
| --- | --- | --- |
| Yahoo | 个人本机试用的历史行情、部分公开新闻 / 公司资料与期权链 | 注意来源时点、缺失字段、历史窗口和限流；无实时延迟保证 |
| SEC EDGAR | 美国公司披露与财报 | 无需 API Key，须自行配置应用名称及联系身份 |
| FRED | 宏观经济序列 | 在研究数据连接中保存自己的 Key |
| Alpha Vantage | 研究数据适配 | 取决于账号权限、额度和具体接口覆盖 |
| TypeSafe Jev | 可选的社交帖文筛选 | 配置 Key、适配入口和模型；未配置时不视为已接通 |
| iShares 公开文件 | 当前 SOXX / IVV 的日级份额流估算 | 使用份额变化与前日 NAV 估算，不等同精确现金净申赎 |
| Massive / Intrinio / Alpaca | 供应商新闻、期权或 ETF 等专业数据 | 使用自己的现有账号；可用范围取决于套餐、feed 和实际权限 |

先保存本人凭据，再检测并检查返回的覆盖与状态。保存连接不等于检测通过，系统不会自动购买服务。期权指标仅代表本次返回合约，新闻情绪仅代表来源样本，成交量或估算成交额不等于资金净流入。

研究会冻结所用连接与资料引用；未配置、停用或清除的 Web 数据连接不会偷偷使用环境中的旧 Key。缓存清理只针对可重建行情，保留研究记录与冻结证据。详细口径见[数据来源说明](docs/data-source-research-2026-10-25.md)和[运行文档](docs/web-local-operation.md)。

## 本地 Agent 独立研究

入口：“设置中心 → 电脑与运行环境 → 管理运行环境”。当前首个适配器使用 Codex app-server 的 stdio 协议，**仅原本机工作区主人可配置和执行**，受支持的程序版本以环境检测为准。

1. 新建运行环境，填写连接名称、实际模型 ID；程序路径留空自动发现，或填写受支持可执行文件的真实路径。
2. 选择“本连接专属 Codex 登录”，保存后按该连接提供的命令在本人终端完成授权；也可绑定自己已检测通过的 Responses 模型连接。
3. 启用连接并点击“检测环境”，确认程序、协议、授权、模型和工具能力。环境检测不执行正式研究。
4. 在新建研究第二步选择“本地 Agent”，核对运行环境、补充要求与预算，再显式启动。
5. 在报告中查看实际执行事件、请求 / 实际模型、金融工具证据和独立研究产物。

此模式独立执行，不套用内置 12 角色流程。工具限定为受控的只读金融取证和已冻结资料，不执行券商交易。当前不支持 Runtime 会话恢复，“重新研究”会创建新任务；模型内部未知用量保留未知。完整授权、工具与版本条件见 [Runtime 使用说明](docs/web-local-operation.md#codex-runtime-独立研究)。

## 日常维护与备份

### 更新与重新启动

安装完成后，日常直接运行启动器即可。更新前先完成或取消正在执行的研究，再停止启动器：

- 后端依赖有变化时，更新虚拟环境中的依赖。
- 前端依赖有变化时，重新执行 `npm ci --ignore-scripts` 和 `vendor:patch`。
- 前端源码有变化时，重新执行 `backend/scripts/package_workbench.py`，再启动并刷新页面。
- API 与 worker 使用同一个数据库和研究目录；推荐统一使用启动器管理。

单独启动 `tradingagents-web` 默认使用 8000 端口，它与推荐的 8001 工作台启动方式不同。仓库当前的 Docker Compose 提供 CLI 入口，不会自动开放这个工作台的 Web 端口。

### 备份、校验与恢复

先完成或取消队列任务。在项目根执行，重复备份时使用新的文件名：

```bash
mkdir -p backups

backend/.venv/bin/python backend/scripts/workbench_recovery.py backup \
  --state-root data/web/development/web \
  --output backups/workbench-backup.zip

backend/.venv/bin/python backend/scripts/workbench_recovery.py verify \
  --bundle backups/workbench-backup.zip

backend/.venv/bin/python backend/scripts/workbench_recovery.py restore \
  --bundle backups/workbench-backup.zip \
  --destination data/web/restored/web
```

恢复目标必须是尚不存在的新目录，不覆盖当前工作区。确认恢复结果后，使用独立端口启动恢复实例：

```bash
backend/.venv/bin/python backend/scripts/run_workbench.py \
  --port 8002 \
  --state-dir data/web/restored
```

备份包含数据库及其引用的报告、证据、附件和已发布检查点，并校验文件哈希；不包含 `.env`、系统密钥库或 OAuth 授权。恢复后保留账号与业务历史，撤销旧会话并停用连接，需要原密码重新登录、重新绑定凭据并检测。更多说明见[完整备份与恢复](docs/web-local-operation.md)。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 默认账号和密码是什么？ | 没有默认账号。首次用一次性初始化凭证创建管理员，之后自行注册或登录 |
| 初始化凭证无效或过期？ | 确认命令使用启动实例的数据库；未完成初始化时可按运行文档使用 `--rotate` 重新生成 |
| 注册入口不可用？ | 先确认管理员已完成初始化，以及注册策略是否开放 |
| 新账号看不到原来的自选和报告？ | 每个账号拥有独立工作区；原本机资料属于首个管理员，不自动复制 |
| “启动研究”不可点击？ | 完成连接 → 模型 → 能力检测 → 默认研究配置；本地 Agent 还需运行环境就绪 |
| 模型检测失败？ | 检查协议、基础地址、认证、实际模型 ID 及聊天 / 工具权限，更正后重新检测 |
| 行情出现 `—`、缺失或过期？ | 查看来源和质量说明，检查网络、限流及账号权限；不能把未知当作 0 |
| 研究一直排队？ | 确认使用启动器运行了独立 worker，且 API / worker 的数据库路径一致；API 存活不代表 worker 就绪 |
| 端口 8001 被占用？ | 使用启动器的 `--port 8002`，并访问对应地址；不要为同一个状态目录重复启动 worker |
| 修改前端后没有变化？ | 重新打包工作台资源，刷新浏览器；开发时使用 Vite 的 5173 入口 |
| 已完成却没有有效评级？ | 执行状态、证据资格与原始判断分开记录；到“证据”页核对缺口 |
| 能自动交易或自动定时复盘吗？ | 当前不执行交易，复核依赖页面提醒和本人手动操作 |

健康检查地址为 [`/api/v1/health`](http://127.0.0.1:8001/api/v1/health)；登录后的 `/api/v1/readiness` 另检查 worker 心跳与队列。会话最长 7 天，连续 12 小时无业务访问后失效，需要重新登录。

## 项目结构与开发

中文名为 **研衡**，英文名为 **ThesisFlow**。Python 引擎包名 `tradingagents`、既有环境变量和兼容接口继续保留；CLI 同时声明 `thesisflow` 与 `tradingagents` 命令别名。

### 技术栈与目录

前端使用 React 19、TypeScript 7、Vite 8、Ant Design 6、TanStack Query、Lightweight Charts 和 ECharts。后端使用 Python、FastAPI、SQLAlchemy、SQLite / Alembic，以及基于 LangGraph 的 TradingAgents 研究图。

| 路径 | 内容 |
| --- | --- |
| `frontend/public/design/` | 默认深色工作台的页面、样式与交互 |
| `frontend/src/` | React 认证宿主、业务 SDK、功能页面与图表 |
| `frontend/legacy/` | 保留的旧版浏览器界面源码 |
| `backend/tradingagents/` | 研究引擎、Web API、领域服务、数据适配与 worker |
| `backend/scripts/` | 打包、契约导出、账号初始化、导入与备份恢复工具 |
| `backend/tests/` | 后端离线回归与集成测试 |
| `contracts/openapi.json` | 前后端共用 API 契约 |
| `docs/` | 产品设计、运行指南与实施记录 |
| `docs/images/readme/` | 经检查的公开 README 展示截图 |
| `data/web/development/` | 本机账号、数据库、研究和行情状态，不提交 |

浏览器通过同源 API 使用当前账号与工作区；API 持久化任务，独立 worker 读取队列并执行研究。凭据由后端保管。`backend/tradingagents/web/ui/` 和 `static/` 是生成资源，应修改 `frontend/` 源码后重新打包。

### 开发模式

先用启动器运行 8001 端口的后端，再在另一终端运行：

```bash
npm --prefix frontend run dev
```

访问 `http://127.0.0.1:5173/workbench/`。Vite 将 `/api` 代理至本机 8001 后端，开发和发布入口使用同一套账号权限。

契约变更时，在项目根导出后重新生成客户端，不手动修改生成文件：

```bash
backend/.venv/bin/python backend/scripts/export_openapi.py
npm --prefix frontend run api:generate
```

### 检查命令

```bash
npm --prefix frontend run check
PYTHON_DOTENV_DISABLED=1 backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/python -m ruff check backend
```

前端检查包含补丁与生成文件一致性、格式、类型感知 lint、TypeScript、单元测试和构建。后端离线回归禁用本机 `.env`，集成测试需按标记显式选择。命令细节见[前端说明](frontend/README.md)与[后端说明](backend/README.md)。

CLI 和 Python 库仍可使用；交互命令及帮助可通过项目环境运行：

```bash
backend/.venv/bin/python -m cli.main --help
backend/.venv/bin/python -m cli.main
```

CLI 使用的环境变量示例在 `backend/.env.example`，实际配置在 `backend/.env`；网页保存的连接不自动变成 CLI 环境变量。Python 调用、回测与上游框架参考保留在下方。

## 进一步阅读

- [本机安装、账号、凭据、数据口径与恢复](docs/web-local-operation.md)
- [前端开发说明](frontend/README.md) · [后端开发说明](backend/README.md)
- [产品与技术设计](docs/web-product-and-architecture-design.md)
- [主题与产业链设计](docs/thematic-universe-design.md)
- [数据来源、权限与覆盖](docs/data-source-research-2026-10-25.md)
- [本地 Agent Runtime 设计](docs/local-agent-runtime-design.md)
- [实现与验收状态](docs/web-execution-status.md)

当前交付以本机个人工作区为范围。共享协作、远程部署、邮箱找回、后台自动复核、券商交易和全部原始输入公平复跑尚未实现；设计文档中的后续计划以实施状态为准。

## 来源与许可

研衡 · ThesisFlow 基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 二次开发。本项目扩展工作台、账号和连接管理、市场与主题观察、研究证据、投资计划及人工复核；上游框架名称、论文引用和原有署名保留。许可全文见 [LICENSE](LICENSE)，来源说明见 [NOTICE](NOTICE)。

## 上游 TradingAgents 框架说明

以下保留 TradingAgents 框架介绍、论文引用及 CLI / Web 兼容参考。此处的 TradingAgents、Tauric Research、上游链接和 `tradingagents` 命令均指原框架或沿用的内部接口；研衡工作台的安装与入口以上文及本项目运行文档为准。

<p align="center">
  <img src="backend/tradingagents/assets/tauric-logo.svg" width="60%" alt="Tauric Research">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>
<br>
<div align="center">
  <a href="https://github.com/TauricResearch" target="_blank"><img alt="TradingAgents #1 Repository of the Day" src="https://trendshift.io/api/badge/repositories/16192" width="250" height="55"/></a>
</div>
<br>
<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

### TradingAgents: Multi-Agents LLM Financial Trading Framework

#### News

<!-- news:start -->
- [2026-10] **TradingAgents v0.7.0** released with runs on your own ChatGPT Plus or Pro plan, backtests scored from the first close after each decision, a note at start-up when a newer release is out, and a wheel attached to each GitHub release.
- [2026-10] **TradingAgents v0.6.0** released with reports saved as one HTML page, a provider per model tier so the managers and analysts can run on different models, past decisions settled for every ticker while the analysts work, and company news read from Yahoo search while Yahoo's news feed is down.
- [2026-09] **TradingAgents v0.5.2** released with parallel analysts for a faster analysis, a CLI that runs without prompts from flags such as `--ticker` and `--date`, the run's settings recorded in every report, and backtests that see only data published by each analysis date.

Full release notes are in [CHANGELOG.md](CHANGELOG.md).

<details>
<summary>Earlier news</summary>

- [2026-09] **TradingAgents v0.5.1** released with a package layout organised by what each module holds (import paths moved), optional Jev screening of social posts, GPT-6 Sol and Luna as the default models, and fixes to run isolation and SEC EDGAR statements.
- [2026-09] **TradingAgents v0.5.0** released with point-in-time integrity across every dated path, SEC EDGAR fundamentals served as filed, backtesting over a ticker and date grid, portfolio-aware runs, and current model lineups across every provider.
- [2026-08] **TradingAgents v0.4.0** released with look-ahead / point-in-time fixes across FRED macro, social sentiment, and the decision-log memory; clearer decision signals; working CLI checkpoint resume; Trader price grounding; and the GPT-5.6 and GLM-5.3 models.
- [2026-07] **TradingAgents v0.3.1** released with correctness and stability fixes: Alpha Vantage look-ahead filtering, graph-router crash-safety, graph-shape-aware checkpoint resume, working crypto sentiment sources, a configurable LLM retry budget, Bedrock API-key auth, and Claude Sonnet 5 / Fable 5 support.
- [2026-06] **TradingAgents v0.3.0** released with a verified data-access contract, an expanded provider registry (NVIDIA, Kimi, Groq, Mistral, Bedrock, and any OpenAI-compatible endpoint), FRED and Polymarket data vendors, a current-generation model catalog, and a CI gate.
- [2026-05] **TradingAgents v0.2.5** released with the grounded Sentiment Analyst, GPT-5.5 etc. model coverage, Qwen/GLM/MiniMax dual-region support, `TRADINGAGENTS_*` env-var configurability with API-key auto-detection, remote Ollama support, non-US alpha benchmarks, and ticker path-traversal hardening.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager), LangGraph checkpoint resume, persistent decision log, DeepSeek/Qwen/GLM/Azure provider support, Docker, and a Windows UTF-8 encoding fix.
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models, unified model catalog, backtesting date fidelity, and proxy support.
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

</details>
<!-- news:end -->

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

#### TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles.

##### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

The selected analysts work at the same time, each on its own tools, and the research debate starts once all of their reports are in.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

##### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

##### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

##### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

#### Installation and CLI

##### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

TradingAgents needs Python 3.11 or later. Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.13
conda activate tradingagents
```

Or with [uv](https://docs.astral.sh/uv/):
```bash
uv venv --python 3.13 backend/.venv
source backend/.venv/bin/activate
```

Install the backend package and its dependencies (`uv pip install ./backend` with uv):
```bash
pip install ./backend
```

##### Docker

Alternatively, run with Docker:
```bash
cp backend/.env.example backend/.env  # add your API keys
docker compose run --rm tradingagents
```

After updating the repository, rebuild the image with `docker compose build`.

Results, reports, the memory log and the cache live in the `tradingagents_data` volume. To keep them in a folder on the host instead, create the folder and point `TRADINGAGENTS_DATA_DIR` at it, in `.env` or the shell: `mkdir -p data && TRADINGAGENTS_DATA_DIR=./data docker compose run --rm tradingagents`.

For local models with Ollama:
```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

##### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen (international, dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen (China, dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax (global, api.minimax.io)
export MINIMAX_CN_API_KEY=...      # MiniMax (China, api.minimaxi.com)
export OPENROUTER_API_KEY=...      # OpenRouter
export MISTRAL_API_KEY=...         # Mistral
export MOONSHOT_API_KEY=...        # Kimi (Moonshot)
export GROQ_API_KEY=...            # Groq
export NVIDIA_API_KEY=...          # NVIDIA NIM
export FRED_API_KEY=...            # FRED macro data (free, optional)
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
export TYPESAFE_API_KEY=...        # Jev social-post screening (optional)
```

For Azure OpenAI, copy `backend/.env.enterprise.example` to `backend/.env.enterprise` and fill in your credentials.

To run on your ChatGPT Plus or Pro plan instead of an API key, choose "Subscription" in the CLI's provider menu, or set `llm_provider: "chatgpt"`. Sign in once with `tradingagents login chatgpt`: it prints an address to open in your browser, which returns to this machine on `127.0.0.1`. When the browser runs on another machine, or TradingAgents runs in Docker (`docker compose run --rm tradingagents login chatgpt`), paste the address the browser ends up on. The sign-in is kept in `~/.tradingagents/chatgpt-auth.json`, and `tradingagents logout chatgpt` revokes it. Runs count toward your plan's usage limits, and the plan does not accept `temperature` or `max_tokens`.

For AWS Bedrock, install the extra with `pip install "./backend[bedrock]"`, set `llm_provider: "bedrock"`, configure AWS credentials (environment variables, `~/.aws/credentials`, or an IAM role) and `AWS_DEFAULT_REGION`, and use a Bedrock model ID, e.g. `us.anthropic.claude-opus-5-5`.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

For any other OpenAI-compatible server (vLLM, LM Studio, llama.cpp, or a custom relay), use `llm_provider: "openai_compatible"` and set the endpoint via `backend_url` (or `TRADINGAGENTS_LLM_BACKEND_URL`), e.g. `http://localhost:8000/v1` for vLLM or `http://localhost:1234/v1` for LM Studio. The model is whatever your server serves. No key is needed for local servers; set `OPENAI_COMPATIBLE_API_KEY` when the endpoint requires one.

With `TYPESAFE_API_KEY` set, the Sentiment Analyst screens StockTwits and Reddit posts with TypeSafe's Jev before reading them. Posts that are not about the company are dropped, and each source opens with a count of the remaining posts by stance: bullish, bearish, neutral, or unclear. Without the key, posts pass through unscreened. `jev-latest` moves with new releases; set `TYPESAFE_DEFAULT_MODEL` to a versioned ID such as `jev-1.13.0` to hold it fixed across runs. To reach Jev through OpenRouter, put an OpenRouter key in `TYPESAFE_API_KEY` and set `TYPESAFE_BASE_URL=https://openrouter.ai/api`.

Alternatively, copy `backend/.env.example` to `backend/.env` and fill in your keys:
```bash
cp backend/.env.example backend/.env
```

##### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative after installing ./backend
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more. Your previous run's answers come back as the defaults, so pressing Enter accepts them. The `TRADINGAGENTS_*` variables in `backend/.env` still skip their step entirely.

To run without questions, for a scheduled job or a script, answer the per-run steps with flags and the rest with `TRADINGAGENTS_*` variables:
```bash
export TRADINGAGENTS_LLM_PROVIDER=openai TRADINGAGENTS_QUICK_THINK_LLM=gpt-6-luna TRADINGAGENTS_DEEP_THINK_LLM=gpt-6-sol
export TRADINGAGENTS_OUTPUT_LANGUAGE=English TRADINGAGENTS_MAX_DEBATE_ROUNDS=1 TRADINGAGENTS_MAX_RISK_ROUNDS=1
tradingagents --ticker NVDA --date 2026-09-23 --analysts market,news,fundamentals --save --no-show
```
Each flag skips only its own question. Run without a terminal, a missing answer stops the run before it starts and names the flag or variable to set.

At start-up the interactive CLI mentions a newer release and notices from Tauric Research. It checks at most once a day, in the background, and sends nothing about your runs; set `TRADINGAGENTS_NO_UPDATE_CHECK=1` to turn it off. Runs without a terminal never check.

A saved report also includes `complete_report.html`, the report as one page with its sections listed beside the text, for reading in a browser, on a phone or in print. Answering the save question at the prompt also asks about the page and can open it in your browser; `--no-html` skips it, and so does `ta.save_reports(state, "NVDA", html=False)` from Python.

##### Local Web Workspace

The browser workspace runs the same analysis graph as the CLI, with a Chinese
interface for choosing a ticker, analysis date, analyst team and research depth.
It shows progress and model/tool usage, saves completed reports, and lets you
reopen runs from history.

Install the Web extra and start the local server:

```bash
pip install -e "backend[web]"
tradingagents-web
```

Open `http://127.0.0.1:8000` in your browser. On macOS, after installing into the
project's `backend/.venv`, you can also double-click `start-web.command`. To use another
port, run `tradingagents-web --port 8001`.

Web access requires a local account. On first launch, the machine owner completes
the one-time setup shown in the terminal; later accounts can register without an
invitation while registration is open. Each account owns a separate workspace
and configures its own model and data credentials. New accounts do not inherit the
machine owner's environment keys or ChatGPT subscription authorization. Credentials
stay on the Python backend. The server listens on loopback for local use; closing
the browser does not stop an active analysis. Use the launcher below for the
persistent queue and independent research worker.

##### React research workbench

Backend Python sources, tests, dependencies, migrations and tooling are under
`backend/`; React sources and Node tooling are under `frontend/`. The original
HTML/CSS/JavaScript interface is maintained in `frontend/legacy/`. Both interfaces
are copied into the Python package as generated assets for distribution. Shared contracts
are under `contracts/`, documentation under `docs/`, and user runtime state under
`data/`. The root `.command` files are launch shortcuts.

The new US equity workbench uses React 19, TypeScript 7, Vite 8, Ant Design 6,
TanStack Query, Lightweight Charts and ECharts. Oxlint/tsgolint and Oxfmt check
the frontend. It retains the full TradingAgents research graph and adds model
connections and role templates, persistent watchlists, numeric charts and market
evidence. See [implementation status](docs/web-execution-status.md) for verified
capabilities and [local operation](docs/web-local-operation.md) for commands.

After installing `backend[web,dev]`, install the locked frontend dependencies,
apply the reviewed vendor declaration patches, then build and run:

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run vendor:patch
backend/.venv/bin/python backend/scripts/package_workbench.py
./start-workbench.command
```

Open `http://127.0.0.1:8001/workbench/`. The launcher starts the API and one
independent research worker against `data/web/development/web/workbench.sqlite3`.
The old Web/CLI state remains separate. Model keys entered in the new UI are
stored through the operating system keyring and scoped to the owning account.
The first owner retains the existing local workspace; newly registered accounts
start with an empty watchlist and their own research records. Common preset themes
remain available. The owner's ChatGPT OAuth sign-in and environment credentials
are not shared with those accounts.

First-owner setup requires a one-time token. Run this command yourself in your
local terminal and enter its output directly into the setup form:

```bash
backend/.venv/bin/python backend/scripts/account_setup.py --database data/web/development/web/workbench.sqlite3 --show-token
```

Do not commit or send the token in chat. The token is consumed after setup. Open
registration needs no token or invitation. The account page supports password
changes and signing out all sessions; administrators can close or reopen
registration. Restoring a backup revokes saved sessions and requires fresh sign-in
and credential binding. This launch mode remains local only; shared workspaces,
email recovery and public hosting are not implemented.

##### Markets and tickers

TradingAgents works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

#### TradingAgents Package

##### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, Azure OpenAI for enterprise, and your own ChatGPT plan.

##### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `python backend/main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
state, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)

# the same report tree the CLI saves, under results_dir/reports
ta.save_reports(state, "NVDA")
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # e.g. openai, google, anthropic, deepseek, groq, ollama; openai_compatible covers any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp, ...)
config["deep_think_llm"] = "gpt-6-sol"    # Model for complex reasoning
config["quick_think_llm"] = "gpt-6-luna"   # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)
```

The quick model serves the analysts, researchers, debaters and trader; the deep model serves the research and portfolio managers. Each can run on its own provider, for example the managers on Claude while the rest run on OpenAI:

```python
config["deep_think_provider"] = "anthropic"
config["deep_think_llm"] = "claude-opus-5-5"
```

A tier on its own provider uses that provider's key and default endpoint; set `quick_think_backend_url` or `deep_think_backend_url` for a local or relay endpoint. The `TRADINGAGENTS_DEEP_THINK_PROVIDER` and `TRADINGAGENTS_QUICK_THINK_PROVIDER` variables set them for the CLI, together with the tier's model variable.

See `backend/tradingagents/default_config.py` for all configuration options.

##### Fundamentals as filed

US company statements come from SEC EDGAR, which records the date every figure was filed. A run dated in the past reads the statements exactly as they stood that day: a fiscal year that has ended but has not been filed yet is not served, and a figure restated later still reads as first reported. Apple's 2008 total assets were filed as $39.6B and restated to $36.2B in 2010, so a run dated in between reads $39.6B. EDGAR needs no account or API key.

Other companies' statements come from Yahoo Finance, which dates a statement by the period it covers rather than by when it was published. A run dated today reads them; a run dated in the past is told they are withheld, since Yahoo cannot say which figures were public by then.

Insider trades are dated by when they happened, not when they were filed, so a run dated in the past is told they are withheld as well.

SEC asks callers to identify themselves and refuses requests that carry no contact address, so a default one is sent. Set your own so SEC can reach you rather than the project:

```bash
SEC_EDGAR_USER_AGENT="Your Name your@email.com"
```

It covers companies that file with the SEC, including foreign companies listed in the US. Anything else, such as Hong Kong or A-share listings, falls through to the next vendor in the chain. EDGAR's machine-readable filings begin in 2009, and a fourth quarter is reported as unavailable rather than derived, because filers publish it only inside the annual figure.

##### Current holdings

By default the agents do not know what you hold, so their guidance is written for a reader who applies it to their own position. Pass a portfolio to have the trader, the risk analysts and the portfolio manager work against your actual book.

```python
from tradingagents.portfolio import PortfolioContext

portfolio = PortfolioContext.model_validate({
    "cash": 25000.0,
    "currency": "USD",
    "positions": [{"ticker": "NVDA", "quantity": 120, "average_price": 150.0}],
})
_, decision = ta.propagate("NVDA", "2026-09-01", portfolio=portfolio)
```

The CLI takes the same content as a JSON file: `tradingagents --portfolio my_book.json`.

An empty `positions` list means a flat book, which is different from passing nothing. A run without a portfolio is never treated as flat.

#### Persistence and Recovery

TradingAgents persists two kinds of state across runs.

##### Memory log

The memory log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. While the analysts of a later run work, TradingAgents settles every logged decision whose holding period has passed: it fetches the realised return (raw, and alpha against the instrument's regional benchmark) and generates a one-paragraph reflection. The Portfolio Manager then reads the most recent decisions for the same ticker plus recent lessons from other tickers, so each analysis carries forward what worked and what didn't. If settling fails, the run goes on and its report says so.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

To settle decisions without running an analysis, for a scheduled job, call `ta.settle_all_pending()`; it returns the decisions it settled and any it could not.

##### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. The run view says whether it resumed a saved run or started fresh. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
tradingagents --checkpoint           # enable for this run
tradingagents --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = TradingAgentsGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
```

#### Evaluating decisions over time

One run gives one decision, which cannot tell you whether the system decides well. `run_backtest` runs the same pipeline over a grid of tickers and dates, writes to a memory log of its own, and scores the decisions whose holding window has since traded.

```python
from tradingagents.backtest import iter_grid, run_backtest, summarize

dates = iter_grid("2026-06-01", "2026-08-01", every_n_days=7)
result = run_backtest(["NVDA", "AAPL"], dates, config, selected_analysts=["market", "news"])
print(summarize(result).render())
```

From the CLI:

```bash
tradingagents backtest NVDA,AAPL --start 2026-06-01 --end 2026-08-01 --every 7
```

Each cell is scored on realized alpha against the instrument's regional benchmark, grouped by rating. A decision is entered at the first close after its date, because a run reads news published after that day's close. Your own memory log is never written to, and re-running the same grid with `run_id=result.run_id` skips the cells that already ran, so an interrupted sweep continues where it stopped.

#### Reproducibility

TradingAgents is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-6 family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

To reduce variation you can lower the sampling temperature. Set `temperature` in your config (or `TRADINGAGENTS_TEMPERATURE` in `.env`); lower values make models that honor it more repeatable. The current curated models are reasoning-first and largely ignore temperature, so for tighter reproducibility name a non-reasoning model in your config, or in `TRADINGAGENTS_DEEP_THINK_LLM` and `TRADINGAGENTS_QUICK_THINK_LLM`. Any model ID your provider serves is accepted, whether or not the picker lists it.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["temperature"] = 0.0
# Reasoning models ignore temperature. For tighter reproducibility, name a
# non-reasoning model in deep_think_llm / quick_think_llm.
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

#### Contributing

Contributions are welcome: bug fixes, documentation, and feature ideas; past contributions are credited per release in [`CHANGELOG.md`](CHANGELOG.md).

#### Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```
