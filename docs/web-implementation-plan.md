**TradingAgents Web 实现方案与交付计划**

版本：实现设计 v1.1。日期：2026-10-24。状态：待实施方案，不表示新版功能或前端脚手架已经完成。

本文件与 [功能设计与技术选型方案](web-product-and-architecture-design.md) 配套。以技术选型文档的版本表为统一依赖基线；本文件规定模块边界、实现顺序、数据迁移、质量门禁与验收。首先完成本机个人版本 M1 + M2，朋友使用安排在 M3，权限完成前不开放网络。

**一、实施目标与当前起点**

当前前端是 backend/tradingagents/web/static 内的原生 HTML/CSS/JavaScript，没有 frontend/package.json。核心研究、供应商客户端、数据工具、报告导出和原有 SSE 已存在。新实现保留这些核心能力，通过应用服务连接新 UI，不以行情页面替代 TradingAgents 个股研究。

实施后应走通：创建模型连接 → 输入 API Key → 选择/检测模型 → 绑定角色 → 添加股票 → 查看真实行情 → 启动 TradingAgents → 阅读阶段报告及完整决策 → 导出 → 重启后找回自选和研究历史。

已验证的兼容范围是临时目录中 FastAPI OpenAPI 3.1 → Orval 8.44.0 生成类型、Fetch、TanStack Query 5 和 Zod 4 → TypeScript 7.0.2 严格检查；随后验证了 Oxlint/tsgolint 对 React 19 TSX、Hooks 和 Promise 的检查、故意未处理 Promise 被阻止，以及 Oxfmt 写入/检查。完整 React/Vite 产品应用及项目集成仍需在 M0 验证。

**二、执行边界与目录设计**

采用一个模块化后端、独立研究 worker 和独立行情执行通道。业务数据落数据库，报告和快照以文件存储。前端只访问本系统 API，不直接访问模型厂商或携带供应商密钥。

| 目录 | 职责 |
|---|---|
| frontend/src/app | 路由、主题、布局、查询客户端、全局错误边界 |
| frontend/src/features/research | 发起研究、任务详情、角色过程、报告视图 |
| frontend/src/features/stocks | 报价、图表、周期、事件、研究入口 |
| frontend/src/features/watchlists | 搜索、加入/移除、分组、排序 |
| frontend/src/features/market | 市场脉搏、广度、行业与资金关注 |
| frontend/src/features/models | 连接 CRUD、模型发现、检测、角色绑定 |
| frontend/src/features/settings | 数据源、模板、偏好、能力状态 |
| frontend/src/shared/api | 手写 transport、错误、查询键、SSE 与生成代码边界 |
| frontend/src/shared/api/generated | Orval 生成类型、SDK、Query 和 Zod，禁止手改 |
| frontend/src/shared/ui、shared/charts | 通用组件与图表封装，不含供应商协议 |
| frontend/scripts | API 生成与漂移检查、前端工程脚本 |
| contracts | 经规范化的 OpenAPI 文件及显式事件协议 |
| backend/tradingagents/web/api/v1 | 新版路由和 Pydantic DTO，保持路由薄层 |
| backend/tradingagents/web/application | 研究、模型、数据、自选等用例与事务协调 |
| backend/tradingagents/web/domain | 模型连接、配置解析、任务状态、市场计算、权限策略 |
| backend/tradingagents/web/infrastructure | 数据库仓储、秘密存储、文件、事件、适配器 |
| backend/tradingagents/web/workers | 研究领取/执行/心跳；行情采集与聚合的独立调度 |
| migrations | Alembic schema 版本与数据迁移 |
| scripts | 契约导出、旧记录导入、备份及开发启动入口 |

目录为计划结构。业务成熟后再提取跨入口共用的 application 包，首轮不大范围搬迁 graph、agents、llm_clients、dataflows，避免同时改变导入路径和业务行为。

API 查询与轻量命令在 Web 执行；研究 worker 独立进程运行 LangGraph；行情刷新使用独立有界 I/O 通道和调度，不排在串行研究任务之后。图表轮询和数据计算不触发研究模型调用。

**三、持久化对象和数据库约束**

首期 SQLite WAL + SQLAlchemy + Alembic，短事务、外键开启、数据库位于本机磁盘。业务事务不跨越模型或网络调用。UTC 保存时间，业务显示带交易所时区；金额带币种，零与缺失分开。

| 表/对象 | 关键字段与约束 |
|---|---|
| users、workspaces、memberships | 预置本机用户/工作区；membership 按 workspace_id + user_id 唯一 |
| instruments、symbol_aliases | 标准证券 ID；别名按 source + alias + venue 等适用条件去重；记录类型、币种、交易所 |
| watchlists、watchlist_items | 列表属于用户/工作区；watchlist_id + instrument_id 唯一；顺序显式保存 |
| model_connections | owner/workspace、provider、auth_type、credential_ref、endpoint、enabled、revision；不存密钥明文 |
| model_profiles | connection_id、精确 model_id、参数 JSON、能力/检测状态、config_hash、revision |
| data_connections | 来源、feed、能力、延迟、额度、许可使用范围及凭据引用 |
| research_template_versions、role_bindings | template_id + version 唯一；version + role 唯一；发布版本不可原地改写 |
| runs、run_manifests | run_id、所有者、标的、状态、阶段、data_quality；manifest 保存不可变输入、版本及快照引用 |
| run_attempts | run_id + attempt_no 唯一；记录 lease_owner、lease_token、lease_until、heartbeat、恢复 checkpoint_ref |
| run_events | run_id + event_seq 唯一且递增；含 attempt_id、schema_version、type、time、payload |
| reports、evidence、data_snapshots | 报告章节与结构化结果；文件引用/哈希、来源、公开时间、采集时间、快照版本 |
| legacy_imports | 原 run_id + 源记录哈希等导入标识唯一，记录导入状态、警告、文件映射 |
| theme_versions、market_snapshots、flow_observations | 行业/主题版本、股票池覆盖、计算方法、窗口、source/feed、实际/估算标记 |

创建研究使用工作区与调用者作用域内的幂等键，并保存请求哈希；相同键不同输入返回冲突。配置更新使用 revision 乐观锁。事件序号分配与事件写入在同一事务内完成。

报告及行情文件先写临时文件并原子落盘，再更新数据库索引。运行事件只追加；历史列表分页返回摘要，正文按需读取。快照清理尊重报告引用，不清理仍被保留研究引用的证据。

**四、模型连接与角色模型改造**

改造入口主要为 llm_clients/factory.py、各厂商 client、graph/trading_graph.py、graph/setup.py；保留 CLI 原有默认行为和 quick/deep 配置。

1. 定义 ProviderDefinition、ModelConnection、ModelProfile、CredentialResolver 与 RoleModelResolver。
2. 通过 CredentialResolver 根据当前连接返回凭据；传给客户端显式参数，不临时修改 os.environ，不把密钥纳入配置快照。
3. 修正“先检查环境变量、后处理显式凭据”的路径：显式连接存在时以它为准，密钥失效应返回该连接错误，不静默使用进程级其他账号。
4. RoleModelResolver 根据 RunManifest 为每个角色选择模型。未指定覆盖时映射到现有 quick/deep；节点工厂通过解析器获取客户端。
5. 检测结果绑定配置哈希。更换地址、模型或凭据后，旧检测结果失效。已保存、身份可用、模型可调用、能力验证分别表示。
6. 默认模型和角色覆盖在模板层管理；实际备用切换记录为运行事件和报告信息。对每个连接设置有界并发、重试、超时。

已有环境变量/登录配置可显示为只读的“现有本机连接”，不自动复制出密钥；用户另建可编辑连接。服务模式下凭据必须明确归属；本机已有授权不能自动共享给朋友。

秘密存储采用接口封装：本机系统钥匙串，服务部署使用独立主密钥的加密存储。密钥更新只写不读；API 返回掩码和状态。模型发现依厂商能力实现，无发现接口时允许手填 model_id。

验收至少包含两条不同厂商连接，以及同厂商两个连接的凭据隔离；假适配器用于稳定回归，真实调用另外执行并注明条件。保存配置无需编辑 .env 或重启应用。

**五、研究任务、检查点与事件协议**

Run 状态为 queued、running、cancelling、cancelled、completed、failed、interrupted；研究阶段与数据质量独立。创建任务在数据库提交 manifest 与排队状态后返回 202。

Worker 在短事务内原子领取，创建 attempt、生成递增 lease_token 并设置租约。心跳更新租约；写入运行状态、报告索引、事件时校验当前 attempt 和 token，过期 worker 不能覆盖新结果。首版一个研究 worker，单研究内部仍由现有图并行分析。

| 场景 | 实现语义 |
|---|---|
| 取消排队 | 条件更新 queued → cancelled，不发起模型调用 |
| 取消执行 | 先标记 cancelling；在节点/工具边界检查，SDK 可中止时传递取消信号；记录最终已保存部分结果 |
| worker 失联 | 租约到期后标记 interrupted，记录不确定的外部调用，不把活跃浏览器连接当心跳 |
| 兼容恢复 | 新建 attempt，记录被选检查点与兼容签名，恢复原 manifest 与数据边界 |
| 重新研究 | 新建 run；可使用新配置/数据，与继续原运行分开 |
| 重复请求 | 幂等键防止重复建 run；内部结果写入幂等，外部调用不能保证恰好一次 |

现有 checkpointer 以 ticker/date/签名组织，需要新增工作区、run 与 attempt 归属，避免同代码同日期串用。每个 attempt 使用独立写入命名空间；resume 显式引用已接受的兼容检查点，由适配器恢复完整 checkpoint 状态、通道版本与待执行信息，再在新 attempt 中接续。不得只复制报告文本或以“相同 ticker/date”推断兼容；旧版检查点首期不自动接续。

检查点兼容签名至少覆盖执行图/状态 schema、模型绑定、提示词、模板、证券类型、研究时间和快照/记忆策略。M0/M1 用现有 LangGraph 存取接口验证安全恢复路径；无法完整恢复的图状态显示不可恢复原因并提供重新研究。过期 worker 写入的检查点与新 attempt 隔离，不以它覆盖已确认的恢复点。

SSE 有单独 EventEnvelope：event_id/seq、run_id、attempt_id、type、timestamp、schema_version、payload。阶段、报告更新、统计、状态、错误具有显式 discriminated union schema，并导出为契约。仅展示公开阶段报告和安全摘要，不转发可能携带秘密的原始工具/模型消息。

SSE transport 独立封装帧处理、心跳、退避重连、Last-Event-ID/游标和销毁清理；默认同源 Cookie 会话可使用原生 EventSource，需要自定义请求头时使用 fetch 流读取并支持 AbortSignal。使用生成/显式 Zod 校验事件。普通 Fetch SDK 不被当成完整 SSE 客户端。事件不足以补齐时发送 resync_required，客户端获取最新状态，避免把缺失事件当不存在。

**六、API 契约、Orval 与前端数据层**

FastAPI/Pydantic 为单一契约源。为原先直接返回 dict 的接口增加明确请求/响应 DTO，再导出 contracts/openapi.json；事件协议单独注册导出，不能指望仅靠 text/event-stream 响应自动推导全部事件类型。

契约导出使用专门应用工厂或纯路由 schema 构建入口，不启动研究、不访问真实模型、不实例化会创建/重写旧任务文件的 RunStore，不包含凭据与用户数据。规范化仅做稳定排序等非语义处理；生成结果随代码提交。

Orval 使用固定版本和仓库配置，生成类型、Fetch 函数与需要的 Query/Zod 产物。生成目录禁止手改，排除 Oxlint/Oxfmt；仍参与 TypeScript 7 检查。改动 API 后重新生成；CI 在临时目录重生成并比对，发现漂移失败。重生成不得读取实时外网接口，使用仓库契约文件。

shared/api/transport 负责同源会话、请求 ID、取消信号和非 2xx 错误标准化。Orval 配置必须通过 mutator/明确适配让生成 SDK 实际调用它，非 2xx 抛出 ApiError；不直接使用可能把 status/data 联合值 resolve 为成功结果的默认 Fetch 行为。TanStack Query 的 TError 声明不是运行时抛错保证。契约验收包含 401/422/429、网络失败和 AbortSignal，分别验证错误分类、重试策略与取消行为。GET 的有限重试与 POST 的提交/幂等分别处理。

Orval 默认 queryKey 不必然含工作区。应用层使用统一 query key factory，纳入 user/workspace、instrument、interval、session、adjustment、source/feed 等维度；在手写 hook 包装层覆盖生成选项。切换工作区或退出时先取消请求、关闭 SSE 和行情订阅，再清除私有缓存，避免迟到响应写回旧数据；不能只切一个全局 workspace 变量继续使用旧查询结果。

服务端数据由 TanStack Query 管理；Zustand 保存面板、选中项等 UI 状态；证券、周期等可分享状态写 URL。研究提交生成不可变 manifest 后，未保存的表单变化不影响已经排队的运行。

**七、自选、行情与研究页面的实现**

先完成真实自选 CRUD，再接图表。证券搜索返回标准 ID、别名、类型、交易所和币种；同组重复加入返回现有项或明确提示。批量排序采用列表 revision 检查；撤销移除重建关注关系，不修改历史报告。

行情适配定义 Quote/Bar/IndicatorSeries，保留 UTC 与交易所时区。分钟数据不得走已有日线归一/填补流程。缓存分区包含 source/feed、instrument、interval、session、adjustment、日期与版本。

| 实现单元 | 输入 | 输出与验收 |
|---|---|---|
| 日线数值 API | 现有来源原始数据 | 原始缺失不补虚假成交；返回来源、调整、延迟、截止时间 |
| 分钟适配器 | 实际账号/feed 能力 | 保留分钟时间戳、时段、未完成 bar；无权限返回受限而非模拟柱 |
| 指标服务 | 带口径的 bars + 参数 | 数值序列、预热期和缺失；与研究用指标口径有可比说明 |
| 图表封装 | 标准序列与标记 | 增量更新、历史分页、十字线、基准；不重建整图、不发模型请求 |
| 研究联动 | 当前证券 + 最近报告 | 顶部发起/最近研究，点击证据定位事件或有效结构化标记 |

布局从“自选—图表—事件/研究—报告”展开，保留核心一级研究入口。颜色、数字、ET 时间、交易状态、延迟、源/feed 在同一设计系统内处理。加载、空、缺失、过期、断线和受限分别渲染。

交易日历测试覆盖夏令时切换、周末/节假日、提前收市、跨时区日期，以及常规/扩展时段。基准比较采用共同时间与同类调整口径，不能把一个源的总回报和另一个源的价格收益直接相减。

**八、市场聚合、资金与数据前置项**

先落实数据能力再实现展示：用小批真实证券/日期验证数据窗口、feed、额度、延迟和授权范围；保存检查结果作为能力矩阵。页面有入口不等于功能完成。

M2 最低真实集为：可用分钟图；11 行业 ETF 或明示行业代理池的真实收益与相对 SPY 收益；同 feed 的成交关注；有来源的波动数据；一个明确版本股票池的真实广度；基于这些快照生成的市场简报。数据来源未落实时 M2 不能宣称完成。

市场快照记录 window_start/end、as_of、source/feed、股票池版本、coverage、quality、method_version。广度使用有效样本分母并显示覆盖，不能把只拿到的少数股票包装成全市场。

RVOL 使用同一 feed、时段和历史同一 ET 时刻的有效样本；样本不足不算。行业强弱、成交关注和 ETF 净申赎分别建模。ETF 净申赎、期权、13F 等专门数据在来源、口径及使用范围验证后开放；没有数据时明确缺口，不用 AUM 变化或成交额替代实际净流入。

市场简报调用读取冻结的 MarketSnapshot，并输出带 evidence_id 的结构化解释。部分证据缺失时保留分歧与覆盖信息。行情与聚合的独立执行通道必须在长研究运行时仍能刷新。

**九、Oxlint/Oxfmt 与质量门禁设计**

采用 Oxlint 1.89.0 + oxlint-tsgolint 7.0.2005，格式化使用 Oxfmt 0.74.0；TypeScript 7.0.2 的 tsc 保持独立正式类型检查。Python 沿用 Ruff 0.17.2 和 pytest 9.1.1。精确版本以技术文档和锁文件统一管理。

使用稳定 JSON 配置 .oxlintrc.json、.oxfmtrc.json。Oxlint 启用 TypeScript、React 等原生规则，jsx-a11y/import/vitest 按需要显式启用。重点包括 rules-of-hooks、exhaustive-deps、only-export-components、无悬空 Promise、错误异步处理和可访问交互。不把任意 ESLint 插件视为已经等价覆盖；发现规则缺口先记录并选择小范围补充策略。

前端拟定 scripts（均为待实现接口）：

| 脚本 | 作用 |
|---|---|
| api:generate | 由仓库契约生成 Orval 产物 |
| api:generate:check | 临时重生成，检测契约/生成代码漂移 |
| lint:quick | oxlint --max-warnings 0 .，用于本地快速普通规则检查 |
| lint | oxlint --type-aware --max-warnings 0 .，范围为 frontend，配置排除生成目录、dist、coverage、node_modules |
| lint:fix | 同配置执行安全自动修复，复杂修复仍需审查 |
| fmt | oxfmt 写入受管源码和工程配置 |
| fmt:check | oxfmt --check，生成目录和产物排除 |
| typecheck:app | tsc -p tsconfig.app.json --noEmit，包含 src 下应用、单测与全部生成代码 |
| typecheck:tools | tsc -p tsconfig.node.json --noEmit，包含工程配置、生成/构建脚本与 E2E 配置/测试 |
| typecheck | 顺序执行 typecheck:app 与 typecheck:tools，不对空的根 solution 配置空跑 |
| test:unit | Vitest 非交互运行 |
| test:e2e | Playwright 验证关键真实 API/测试适配场景 |
| build | Vite 正式构建 |

--type-check 当前仍为实验能力，不把它设为替代 tsc 的唯一质量门禁。M0 验证类型感知规则实际生效、配置解析正常、TS7 编译独立通过，不只验证命令退出码。

tsconfig.app.json 的 include 明确包含 src/**（含 shared/api/generated），tsconfig.node.json 明确列出 vite/vitest/playwright/orval 配置、scripts 和 tests/e2e。相关环境类型显式指定；工程脚本优先采用 TS，若保留 JS/MJS 并声称纳入类型门禁，则配置 allowJs/checkJs 和对应 include。根 tsconfig 可仅用于编辑器项目引用，不承担实际检查入口。M0 在应用、生成文件、工程配置各放一个临时类型反例，确认对应 typecheck 脚本失败后移除反例；不能因根配置的 files 为空而误报全量通过。

CI 流程为依赖锁定安装 → 导出契约/漂移检查 → Oxfmt check → Oxlint 类型感知检查 → tsc → 单元测试 → Vite build → 关键 E2E。生成后互不依赖的只读检查可并行；CI 运行完整 lint 时不重复 lint:quick。Python 并行执行 Ruff 与选定 pytest 回归。CI 不运行自动修复，不在未知真实凭据上自动触发计费研究。

**十、旧任务与报告迁移**

当前旧记录位于配置 results_dir 下的 web/<run_id>/run.json 与报告目录。导入器只读扫描用户配置的旧根，不实例化当前 RunStore；它会在启动时把旧活跃任务改写为 interrupted，不适合只读迁移。

迁移流程：验证路径/UUID/文件类型 → 校验 JSON → 生成导入清单及哈希 → 导入摘要、事件和报告索引 → 校验数量与文件引用 → 标记导入完成。跳过符号链接、越界路径和损坏项，汇总原因，不让单个损坏记录阻断全部历史。

旧 completed/failed/interrupted 保留原状态来源；旧 pending/running 作为 legacy interrupted 导入，不自动执行。不能从旧报告猜测或补造当时缺失的模型/数据 manifest；显示“历史配置不完整”。原始输入和事件仅在校验后导入，凭据与私人错误字段不进入 API。

报告可保留只读路径引用或复制到新版受管存储，统一选择一种并记入清单。HTML 展示优先通过现有安全渲染策略，不能把任意历史文件当可信脚本执行。导入命令支持 dry-run 和幂等重跑；源 JSON、HTML、Markdown 不被修改或删除。

CLI 独立生成的报告与旧 memory log 采用单独导入类型，不自动混入个人研究记忆。历史导入只增加索引，不参与模型反思/经验结算，除非之后显式配置范围。

**十一、新旧 API 兼容与切换**

新接口放 /api/v1，旧 /api/runs 在迁移期保留适配层。新旧提交都进入同一 ResearchService/数据库/worker，不能同时维护两套会各自调度的 RunStore。

迁移时先冻结旧提交入口，等待或明确中断旧执行任务并保存报告；完成导入和新服务启动后切换统一提交路径。旧查询返回兼容字段，新版字段通过 v1 暴露。对旧接口无法表达的排队/取消等状态提供文档化映射，不把 cancelled 伪装成 completed。

新 UI 先使用独立路径或功能开关验收。旧页面退役条件为核心研究章节、导出、历史、任务可靠性和数据迁移全部通过；切换默认入口不删除旧源文件。回滚优先回到兼容服务/只读旧页面，不让旧程序直接写新版 schema，也不自动破坏性数据库降级。

**十二、分批任务和依赖顺序**

| 批次 | 前置输入 | 实现输出 | 验收 |
|---|---|---|---|
| M0.1 工程基线 | 技术版本表、现有环境 | frontend、锁文件、Ox 配置、TS/Vite、最小主题 | 无 peer 绕过安装；lint/format/typecheck/build 实际通过 |
| M0.2 契约与存储 | 路由/对象清单 | API DTO、OpenAPI 导出、Orval、Alembic、默认用户/工作区 | 纯导出无服务副作用；生成漂移失败可检测；DB 可迁移 |
| M1.1 连接与模板 | M0.2、供应商适配 | 显式凭据解析、模型 CRUD/检测、角色注入、模板版本 | 同厂商多连接不串密钥；默认+覆盖可运行 |
| M1.2 自选与基础数据 | M0.2、证券/来源能力 | 自选 CRUD、基础检测、日线数值 API | 搜索/增删/排序可持久化；缺失不补零 |
| M1.3 可靠任务 | M1.1、存储/事件协议 | worker、attempt、租约、取消/中断/兼容恢复、SSE | 重启/失联/重复提交/断线有明确结果 |
| M1.4 核心研究页面 | M1.2/M1.3 | 发起、过程、完整报告、历史/导出、导入器 | 全链角色章节保留；真实研究和重启持久化通过 |
| M2.1 专业行情 | M1、实际分钟 feed | 图表、时段、指标、基准、研究联动 | 真分钟数据；时间/调整正确；图表不触发模型 |
| M2.2 市场与行业 | 股票池/波动/行业真实源 | 广度、强弱、成交关注、独立聚合通道 | 实际覆盖和同 feed 口径正确；长研究不阻塞刷新 |
| M2.3 市场简报与完整数据中心 | M2.2、快照/证据 | 结构化简报、来源路由/缓存/质量、可用资金适配 | 解释能回到证据；缺资金源不宣称已实现资金流 |
| M3.1 朋友身份与权限 | M1/M2、部署与数据许可条件 | 登录/会话、成员、对象权限、私有/共享、配额 | 越权读取/提交/下载/SSE 均被阻止 |
| M3.2 部署和恢复 | M3.1 | TLS/反代、受限监听、备份/恢复、运行维护 | 先通过隔离和恢复演练再开放网络 |

每批产出可演示的小闭环，完成验收后进入依赖它的任务。M1 是核心迁移完成点；M1 + M2 构成本次完整个人版本。数据账号、来源与使用范围是 M2 的前置输入，不能靠 UI placeholder 结束验收。

**十三、故障与回归验收矩阵**

| 场景 | 必须验证的结果 |
|---|---|
| 刷新/关闭页面 | 任务继续；重进拿到持久化状态与阶段报告 |
| API 重启 | 排队任务和查询恢复；研究 worker 状态依租约，不因 API 重启重复执行 |
| worker 崩溃 | 识别 interrupted；已提交结果保留；不确定调用有标记 |
| 旧 worker 延迟写入 | 条件写失败，不覆盖新 attempt 的任务/报告；检查点命名空间隔离 |
| 检查点不兼容 | 说明具体不兼容项，创建新研究，不静默套旧状态 |
| 双击启动/请求重试 | 同幂等键不重复建任务；不同输入冲突可见 |
| 取消 | 排队取消无调用；执行取消有边界与部分结果；不伪装成功报告 |
| SSE 断线 | 序号补齐，不重复消费同事件；归档缺口触发 resync |
| 凭据过期/限流/错误地址 | 安全错误信息与检测状态；不退回其他用户凭据 |
| 断网/来源缺失 | 行情过期/受限明确；不显示模拟替代真实，不把零当缺失 |
| 时间/证券类型 | 夏令时、提前收市、ETF/普通股工具能力、调整与数据截止时间正确 |
| 原有核心 | 四类分析、多空、经理、交易、三类风险、最终决策与导出不丢失 |
| 导入与备份恢复 | 重跑不重复导入、源文件不变、报告引用可读、损坏项有清单 |
| 朋友隔离 | Query cache、API、SSE、报告文件、凭据、记忆均不跨用户泄露 |

功能逻辑用假适配器保证稳定测试，真实供应商冒烟单独执行；两种结果明确区分。验证范围随改动选择，不为纯展示调整增加重复实现式测试。

**十四、数据前置与交付记录**

每批提交记录实现范围、schema/契约版本、锁文件、数据能力、测试命令及结果、未满足的外部条件。M0 保留单独的兼容验证记录，不能把临时最小 TS7 验证当成完整应用已通过。

ETF 净申赎、期权结构、历史成分股和事件源在具体接口/权限/公开时间规则落实前列为待验证项。若真实分钟、波动或指定股票池广度不足，M2 标记未完成并列出具体缺口；不通过改名或预置数值把缺口隐藏。

首次完整交付还应包含启动方式、模型/数据配置说明、备份恢复步骤和故障定位说明。实际安装、代码迁移和部署在后续实施阶段执行；本文仅新增实现设计文档。
