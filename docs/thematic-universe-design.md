**美股主题与关键材料观察体系设计**

核实日期：2026-10-25（Asia/Shanghai）。这是业务分类与种子研究，供产品实现使用；不是买卖建议，也不把公司产品存在性当成主题收入占比。现有 11 个行业 ETF 只适合作为一级行业视图，不能覆盖用户关注的细分主题。

**分类结构**

保留“行业视图”，新增“主题与产业链”。行业回答公司主要经营分类，主题回答公司与某个技术、材料或需求的关系；同一公司可属于多个主题。主题可以跨行业，例如数据中心电力同时连接发电、配电、功率半导体、UPS 与冷却，mRNA 则属于生物技术分支。

一级行业保留独立入口；主题配置现支持 industry（细分行业）、theme（研究主题）、value_chain（产业链环节）、material（关键材料）四维可编辑标签，可在同一主题同时使用。旧 category 分类兼容保留，不把主题标签当作 GICS 分类。比如“存储 / HBM / DRAM”可同时标半导体行业、AI存储主题与存储芯片环节，不拆开用户已合并的主题。父子导航与 supplies/uses/enables/manufactures 关系图仍属后续设计，例如 AXTI→InP 衬底→LITE/COHR 激光器→AAOI 光模块→光互联；当前未实现关系图、父节点或材料背景实体。

“关键/不可或缺”应带用途和来源。InP 对特定高速激光和光电器件重要，不表示所有 AI 系统都用相同路线；SiC/GaN 是功率器件材料体系，不能统称 AI 算力芯片衬底。DOE 在 2026-10-23 将氦列入其关键材料名单，给出了半导体制造与供应风险依据，这与公司股票的纯度、估值和收益是不同问题。[DOE 原文](https://www.energy.gov/articles/energy-department-designates-helium-critical-material-strengthen-american-manufacturing)

**首批可落地主题**

下表为代表性观察样本，不是全行业成分。低成员数主题显示“样本不足/单一公司暴露”，不伪造广度或集中度分散。

当前全新工作区种子共 **30 个主题、73 条主题成员关系、41 个不同美股代码**；其中 4 个材料主题保留空成员待补证据。一个代码可出现在多个主题，关系数不等于公司数。AI 已拆分为训练/推理芯片、网络/交换互联、云/模型基础设施和模型/应用安全，并与既有存储、供配电、冷却及关键材料主题相互关联；这些仍是可编辑代表样本，不能声称覆盖“全部AI公司”。

| 主题 | 首批成员候选 | 边界与主要证据 |
|---|---|---|
| 半导体产业链 | NVDA、AMD、AVGO、TSM、ASML、AMAT、LRCX、KLAC、MU | 设计/晶圆制造/设备/存储分别标环节；可用 SMH 作为额外 ETF 代理，但自定义篮子不冒充 SMH 指数。[VanEck 当期持仓](https://www.vaneck.com/us/en/investments/semiconductor-etf-smh?redirectVE=generic) |
| AI训练/推理芯片 | NVDA、AMD、AVGO | NVDA/AMD为GPU设计与训练/推理加速平台；AVGO为客户定制ASIC/XPU协同设计，标分散业务暴露，不能把芯片公司全部业务当AI收入。[NVIDIA Blackwell](https://www.nvidia.com/en-us/data-center/technologies/blackwell-architecture/)、[AMD Instinct](https://www.amd.com/en/products/accelerators/instinct/mi350.html)、[Broadcom/Meta MTIA与XPU](https://investors.broadcom.com/node/64236/pdf) |
| AI网络/交换互联 | AVGO、ANET | AVGO的以太网互联技术与ANET的Etherlink交换系统分角色；两家公司只构成初始观察样本，不是完整网络行业或AI网络收入指数。[Broadcom AI互联](https://investors.broadcom.com/node/64236/pdf)、[Arista AI Networking](https://www.arista.com/en/solutions/ai-networking) |
| AI云/模型基础设施 | MSFT、AMZN、GOOG | 三家均标“多元业务暴露”；证据仅确认Azure/Microsoft Foundry、AWS/Bedrock及Google Cloud模型训练/部署平台的相关产品，不把全公司收入、市值或成交额当成AI业务规模。GOOG采用Alphabet美国上市Class C代码。[Microsoft Foundry](https://azure.microsoft.com/en-us/products/ai-foundry/)、[Amazon Bedrock](https://aws.amazon.com/bedrock/)、[Google Cloud模型/Agent平台](https://cloud.google.com/products/gemini-enterprise-agent-platform) |
| 磷化铟 InP | AXTI、COHR、LITE | AXTI 为衬底；COHR/LITE 为光子器件、激光相关，不把整个公司称为纯 InP 材料股。[AXT 产品](https://www.axt.com/products)、[Coherent InP 项目](https://ir.coherent.com/news-releases/news-release-details/coherent-announces-chips-letter-intent-50-million-expand-world)、[Lumentum 工厂](https://investor.lumentum.com/financial-news-releases/news-details/2026/Lumentum-Announces-New-U-S--Manufacturing-Facility-to-Produce-Advanced-Lasers-for-the-Worlds-Largest-AI-Data-Centers/default.aspx) |
| 砷化镓 GaAs/锗 Ge 衬底 | AXTI | 初始只有一个已核实的上市样本，不能展示成完整行业。[AXT 产品](https://www.axt.com/products) |
| SiC/GaN 功率器件 | NVTS、ON | 两种材料分别保留子标签，关注配电/功率转换而非所有 AI 芯片。[Navitas IR FAQ](https://ir.navitassemi.com/shareholder-resources/investor-faqs)、[onsemi 数据中心方案](https://www.onsemi.com/solutions/computing/data-center) |
| 存储 / HBM / DRAM | MU、SNDK、WDC、STX | 统一管理四个去重成员，以角色区分DRAM/HBM、NAND/SSD与HDD；独立HBM/DRAM不再是默认主题，NAND/SSD与HDD子主题保留。WDC 与 Sandisk 分拆后不能继续把 WDC 作为其原 NAND 业务代理。[Micron 2026 产品说明](https://investors.micron.com/news/press-release/2026/Micron-Technology-Inc--Reports-Record-Fiscal-Fourth-Quarter-and-Full-Year-2026-Results/default.aspx)、[Sandisk 分拆](https://www.sandisk.com/en-se/company/newsroom/press-releases/2025/sandisk-celebrates-nasdaq-listing-after-completing-separation)、[WDC 分拆说明](https://www.westerndigital.com/de-de/company/newsroom/press-releases/2025/2025-02-24-western-digital-completes-planned-company-separation)、[Seagate HAMR](https://www.seagate.com/stories/articles/seagate-delivers-industrys-highest-capacity-hard-drives-with-next-generation-mozaic-4/) |
| NAND/SSD | MU、SNDK | 不与 HDD 混为同一技术；保留晶圆/控制器/系统等后续环节。[Micron](https://investors.micron.com/news/press-release/2026/Micron-Technology-Inc--Reports-Record-Fiscal-Fourth-Quarter-and-Full-Year-2026-Results/default.aspx)、[Sandisk](https://www.sandisk.com/en-se/company/newsroom/press-releases/2025/sandisk-celebrates-nasdaq-listing-after-completing-separation) |
| HDD/大容量数据存储 | WDC、STX | 公司生产硬盘是业务证据，不等于全部业务收入来自 AI。[WDC](https://blog.westerndigital.com/wd-one-year-independent-innovation-ai-era/)、[STX](https://www.seagate.com/stories/articles/seagate-delivers-industrys-highest-capacity-hard-drives-with-next-generation-mozaic-4/) |
| 光互联/高速光模块 | COHR、LITE、AAOI、FN | 激光、收发器、制造服务分角色；FN 是制造服务商，不能给其“自有光模块设计商”标签。[Coherent 平台](https://ir.coherent.com/news-releases/news-release-details/coherent-launches-photonlinktm-integrated-optics-platform-ai)、[AAOI 1.6T](https://newsroom.ao-inc.com/news-releases/aoi-receives-first-volume-order-of-1-6t-data-center-transceivers-from-major-hyperscale-customer/)、[Fabrinet](https://fabrinet.com/markets/opticalcommunications) |
| 光纤/光缆/连接 | GLW | 起步一个明确美股公司暴露；后续可扩展国际供应商，不复制光互联篮子冒充光纤制造。[Corning 产品/Meta 合作](https://investor.corning.com/news-and-events/news/news-details/2026/Corning-and-Meta-Announce-Multiyear-up-to-6-Billion-Agreement-to-Accelerate-US-Data-Center-Buildout/default.aspx) |
| 硅光/CPO 相关 | COHR、TSM | 器件与集成平台分角色；在研平台与已量产业务分开标注。GLW在光连接主题观察，不因其提供光纤就自动加入硅光/CPO。[Coherent](https://ir.coherent.com/news-releases/news-release-details/coherent-launches-photonlinktm-integrated-optics-platform-ai)、[TSMC 3DFabric](https://www.tsmc.com/english/dedicatedFoundry/technology/platform_HPC_tech_WLSI) |
| 半导体材料与洁净工艺 | ENTG、Q、AXTI、GLW、LIN | 这是跨材料总览，须同时展示具体子主题及重复成员。[Entegris](https://www.entegris.com/en/home/our-science/by-industry/microelectronics/semiconductor.html)、[Qnity](https://www.qnityelectronics.com/semiconductor-fabrication-and-packaging-materials.html) |
| 光刻胶/光刻材料 | Q | 2026 年初始样本采用 Qnity，不能继续沿用分拆前 DuPont DD 的电子材料归属。[Qnity 10-K](https://ir.qnityelectronics.com/sec-filings/all-sec-filings/content/0002058873-26-000010/q-20251231.htm)、[上市说明](https://ir.qnityelectronics.com/resources/faq) |
| CMP/抛光/清洗 | ENTG、Q | 区分浆料、抛光垫、清洗与过滤；不是笼统原材料商品价格指数。[Entegris CMP](https://www.entegris.com/en/home/our-science/by-industry/microelectronics/semiconductor/cmp.html)、[Qnity 10-K](https://ir.qnityelectronics.com/sec-filings/all-sec-filings/content/0002058873-26-000010/q-20251231.htm) |
| 电子特气与稀有气体 | LIN、APD | 综合气体企业的分散业务暴露；氦/氖/氩/氪/氙各自保留材料标签。[Linde 特气](https://www.linde-gas.com/products-and-services/gases/specialty-gases-for-electronics)、[Linde 稀有气体](https://www.linde-gas.com/products-and-services/gases/rare-gases-for-electronics)、[APD 氦](https://www.airproducts.com/-/media/files/en/354/354-15-009-glb-secure-and-reliable-helium-supply.pdf) |
| 氦气 | LIN、APD | 不宣称纯氦气上市公司；收入占比未核实为 null，不能用整个公司市值表示氦气市场规模。[Linde](https://www.linde-gas.com/products-and-services/gases/rare-gases-for-electronics)、[APD](https://www.airproducts.com/-/media/files/en/354/354-15-009-glb-secure-and-reliable-helium-supply.pdf) |
| 石英/高纯熔融石英 | GLW | 对应光学材料/特定光刻用途，不把 Corning 称为纯石英矿业公司。[Corning HPFS](https://www.corning.com/worldwide/en/products/advanced-optics/product-materials/semiconductor-laser-optic-components/high-purity-fused-silica.html) |
| 先进封装/异构集成 | TSM、AMKR、Q、ENTG | 服务、封装材料、工艺纯度分别标注。[TSMC](https://3dfabric.tsmc.com/english/dedicatedFoundry/technology/cowos.htm)、[Amkor](https://ir.amkor.com/)、[Qnity](https://www.qnityelectronics.com/semiconductor-fabrication-and-packaging-materials.html)、[Entegris](https://www.entegris.com/en/home/our-science/by-industry/microelectronics/semiconductor.html) |
| AI 应用/模型安全 | PANW、ZS | 特指模型/应用/Agent 安全；“用 AI 做传统网络安全”另设主题，不把两者合并。[Prisma AIRS](https://www.paloaltonetworks.com/ai-security/prisma-airs)、[Zscaler AI Security](https://www.zscaler.com/products-and-solutions/zscaler-ai-security) |
| 发电/核电与数据中心用电 | CEG、VST | 电力供应与配电设备分开；可查具体 PPA，不按整个公司收入假设 AI 比例。[CEG/Meta](https://investors.constellationenergy.com/node/9241/pdf)、[VST/Meta](https://investor.vistracorp.com/2026-01-09-Vistra-and-Meta-Announce-Agreements-to-Support-Nuclear-Plants-in-PJM-and-Add-New-Nuclear-Generation-to-the-Grid) |
| 数据中心供配电/UPS | ETN、VRT、NVTS、ON | 系统设备与芯片分环节，原理上不与发电篮子等同。[Eaton](https://www.eaton.com/us/en-us/company/news-insights/news-releases/2025/eaton-unveils-next-generation-architecture.html)、[Vertiv](https://www.vertiv.com/en-asia/about/news-and-events/news-releases/2026/vertiv-brings-converged-physical-infrastructure-to-nvidia-vera-rubin-dsx-ai-factories/)、[Navitas](https://ir.navitassemi.com/shareholder-resources/investor-faqs)、[onsemi](https://www.onsemi.com/solutions/computing/data-center) |
| 冷却/液冷与热管理 | VRT | 初始单公司样本；不等于液冷全产业链。[Vertiv](https://www.vertiv.com/en-asia/about/news-and-events/news-releases/2026/vertiv-brings-converged-physical-infrastructure-to-nvidia-vera-rubin-dsx-ai-factories/) |
| 生物制药/生物技术 | AMGN、VRTX、REGN、MRNA、BNTX、ARCT | 非完整行业成分；IBB 可作额外ETF代理，不能把其持仓权重拷贝给自定义等权篮子。[Amgen](https://www.amgen.com/en/about)、[Vertex](https://investors.vrtx.com/news-releases/news-release-details/vertex-reports-second-quarter-2026-financial-results)、[Regeneron](https://investor.regeneron.com/news-releases/news-release-details/regeneron-reports-second-quarter-2026-financial-and-operating) |
| mRNA 药物/疫苗 | MRNA、BNTX、ARCT | 以技术平台和管线为证据，不把所有疫苗公司都归 mRNA；临床阶段不得推断为商业收入。[Moderna](https://www.modernatx.com/en-us/research/product-pipeline)、[BioNTech](https://www.biontech.com/int/en/home/pipeline-and-products/platforms/mrna-platforms.html)、[Arcturus](https://arcturusrx.com/mrna-medicines-pipeline/) |

**必须保留但不能强塞美股成员的材料**

硅晶圆/SOI、ABF 绝缘膜/先进基板、氖/氪/氙、铜互连/铜箔、钽/钨/钴/钌等互连或工艺材料，都可建立材料节点与新闻/事件追踪。首批没有核实美股主板直接标的时，成员为空并显示“待补证据”，不能为了热力图凑公司。SUMCO 的官方产品确认硅晶圆用途；味之素官方资料确认 ABF 在高性能芯片基板的绝缘用途，二者可以做产业链背景实体，但不能捏造 NYSE/Nasdaq 标的。[SUMCO](https://www.sumcosi.com/english/products/lineup.html)、[Ajinomoto ABF](https://www.ajinomoto.com/innovation/our_innovation/buildupfilm)

本批范围为美股与美股 ADR/ADS（如 TSM/BNTX）；拒绝已知海外后缀、指数、外汇、期货和加密资产。搜索可使用现有证券目录，手填代码仍须核实，格式通过不等于交易所上市状态已验证。OTC 与海外市场的独立开关、币种和流动性规则属于后续设计。ADR 比例与原股不能直接混算股数或市值。

**可配置化与版本**

已实现稳定 id、名称、别名、说明、分类、四维 taxonomy_tags、owner/workspace、revision、不可变版本、创建/更新时间与归档。标签最多24项，同一维度不允许重复；同名标签可分属不同维度。成员保存代码、显示名称、角色、暴露标签、公开证据 URL 与维护者核查日期。成员默认 user_unverified；维护者可主动补齐业务关系、日期和链接。只读 evidence_quality 分为 documented（维护者证据齐全）、incomplete（已声明关系但日期/链接待补）、unverified（尚未核查关系），不表示平台验证、收入纯度或持续有效；关系完整度与行情数据质量分开显示。分析采用等权观察篮子和固定 SPY 比较。

用户可创建、复制、重命名、归档主题，搜索或手填并增删成员、编辑角色与证据、预览覆盖、查看版本。内置主题可直接编辑或先复制，保存均生成新版本，种子更新不覆盖用户修改或恢复已归档主题。成员删除不删除公司历史研究或行情缓存。revision 冲突保留草稿，需载入新版本后重新编辑。

存量存储默认值只在 `memory-storage` 与 `hbm-dram` 两条原主题均为配置版本1、未删除且名称/描述/别名/成员/证据完全匹配旧默认时升级：保留原存储主题ID与历史，新建“存储 / HBM / DRAM”版本；原HBM主题以新版本软删除，历史可读且可恢复。任一配置改写则整组跳过；纯置顶元数据不影响资格，原HBM置顶会转移到合并主题，旧主题本身也保留偏好。恢复原HBM后重启不会再次删除。因保留用户配置与历史，存量工作区数量可以不同于全新工作区种子数量。

英文名、父节点、包含/排除规则、自定义基准/权重、生效区间、完整 instrument 主数据引用、证据标题/发布时间和主题收入占比是后续字段与功能目标，当前没有对应编辑能力。产品证据不等于收入纯度。

主题详情可选择历史配置版本并只读查看，统计请求固定 version，响应保留 membership_effective_from；窗口始终是最近完整交易日的1D/5D/1M（window_basis=latest_completed_sessions），不是所选版本生效时的行情回放或历史回测。历史版本固定成员，不证明当时可得成分。documented_only 仅筛选维护者资料已补齐的成员，响应区分原始成员数、筛选排除数及筛后行情覆盖，缺行情不补零。归档版本保留配置记录，不计算统计；可显式选择此前活动版本。真正历史成分研究还需独立的当时可得信息与历史行情截断能力。

**数值、情绪和资金展示**

默认先算等权价格变化、相对SPY变化、上涨/下跌/缺失数、有效覆盖、完整日RVOL中位数和成员估算成交额；集中度后续按可核实权重扩展。使用相同来源/feed、调整口径和共同交易日；不足预热则缺失。样本少于3只仍可显示该观察篮子的数值，但强调小样本，不作“全行业广度/主题热度排名”；单一成员的篮子等同该股票。主题有重叠时不把多个主题的成交额相加成全市场总额。

成分股市值涨跌、成交额和ETF净申赎是不同指标。ETF流入只能归到对应ETF；要映射主题，必须冻结ETF持仓快照、标记穿透估算方法及重复持仓，不能把ETF流入按主题名字直接分摊给股票。新闻情绪要显示样本来源、条数、去重、语言、时间窗和被覆盖成员数；“未取到新闻”是缺失，不是中性。

当前 UI 提供独立“一级行业”与“主题与产业链”入口，后者可搜索主题/成员/标签、按分类或四维标签筛选、编辑、查看热力与相对强弱、覆盖、独立关系证据目录及历史版本，并跳转行情/研究。配置版本与证据筛选保存于URL，置顶不改变当前查看版本。新闻/期权/ETF 申赎有独立入口，尚未穿透合并进主题分析。“行业 / 主题 / 产业链 / 我的主题”四视图、来源策略、权重和有效期高级面板仍属后续设计。

**实施顺序与验收**

1. 建表与纯契约：主题版本、成员、证据；CRUD、revision冲突、默认种子只创建一次。下游通过注入现有 MarketDataService 聚合，禁止对每次渲染重新拉全量。
2. 首批启用表内证据已核实候选，接真实证券目录/上市状态校验；无覆盖成员保持缺失。移除硬编码11行业作为唯一板块来源。
3. 前端管理与查看：新增/复制/增删成员/排序/停用、覆盖/来源/方法、行情与研究跳转。空主题是正常状态，不渲染假柱。
4. 后续补独立材料背景实体/关系图、供应链事件和ETF穿透。种子JSON只代表证据候选配置，不替代交易所证券主数据。
5. 必测：多主题重复成员、删除不丢报告、用户修改不被种子覆盖、历史版本不可变、部分/过期数据、极小样本、异源数据排除、ADR/OTC范围、分拆后代码变化、当前成员回看标识。

