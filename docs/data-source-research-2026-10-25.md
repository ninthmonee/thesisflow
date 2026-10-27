**美股专业数据源调查与实施口径**

核查日期：2026-10-25。按“免费来源或已有账号”实施，不购买套餐、不代开金融账户、不自动开启试用。本文价格是核查日的个人套餐标价，账号实际权限、税费和商业授权以供应商为准。

**本轮选择**

| 需求 | 默认实现 | 凭据与费用 | 当前覆盖与边界 |
| --- | --- | --- | --- |
| ETF份额流变化 | iShares官方历史文件 | 无Key、公开下载 | SOXX、IVV；按同一文件的相邻交易日NAV/份额计算估算值 |
| 个股/ETF期权链 | Yahoo公开接口，经yfinance | 无Key、个人试用 | 可返回Bid/Ask、IV、OI、成交量；无Greeks；不保证实时或持续可用 |
| 市场新闻样本情绪 | Massive股票新闻 | 已有API Key；Stocks Basic免费 | 新闻样本及ticker情绪，不宣称覆盖所有媒体/美股公司 |
| 已报告ETF资金流 | Intrinio ETF Global | 已有Key及该数据集权限 | 保留供应商fund_flow，不通过成交额推断 |
| 更多期权字段 | Alpaca indicative；Massive快照 | 已有账号；权限分别检测 | indicative不是OPRA；Massive免费Options Basic不含链快照 |

未配置的账号来源返回`data_configuration_required`，没有假新闻、模拟期权或假造的连接成功。数据连接页面提供供应商、Key、Alpaca Secret/feed、停用、轮换及逐能力检测；保存Key不代表验证了数据权限。

**ETF：免费发行商文件与供应商已报告值分开**

[SOXX官方产品页](https://www.ishares.com/us/products/239705/ishares-phlx-semiconductor-etf)和[IVV官方产品页](https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf)的Data Download指向BlackRock基金文件。实际下载验证为Spreadsheet 2003 XML，Historical工作表具有`As Of`、`NAV per Share`、`Shares Outstanding`列，可取得连续历史，避免只能从今天开始采样。

固定端点：

```text
GET https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document
appSubType=ISHARES
appType=PRODUCT_PAGE
component=fundDownload
locale=en_US
portfolioId=239705  # SOXX；IVV为239726
targetSite=us-ishares
userType=individual
```

实现规则：

1. 仅读取Historical工作表的命名列，不执行公式；拒绝DTD/实体、冲突日期和超大响应。对该官方文件 prose 中的裸`&`做限定修复。
2. 过滤文件中的周末/假日沿用值；交易日以`XNYS`日历为准，只有`session_close <= 当前UTC时刻`的已结束交易日才纳入，包含提前收盘规则，窗口日期采用美东日期。只有相邻完整交易日才计算`(份额_t − 份额_t-1) × NAV_t-1`。
3. 输出`flow_kind=estimated`和公式，称“份额变化估算”；前日NAV是本实现明示的计价约定，官方历史文件未证明份额计数与实际创赎结算时点相同，因此不宣称精确现金流、发行商已报告净申赎或机构净流入。份额不变的有效观察可为0；缺失为null。
4. 在同一下载文件内计算。供应商可能重述历史拆股基准，因此不拼接不同下载批次、不再盲目叠加其他来源拆股因子。份额单日比例超出0.75–1.25、NAV超出0.65–1.5时暂停估算，标记`action_required`；该守卫不能替代完整公司行动核验。
5. 保留原始XML及其版权说明和SHA-256。文件条款限定个人非商业下载；朋友共享/产品化需要另核相应展示、缓存和再分发许可。公开可下载不等于商业API授权。

SOXX官网已公告2026-11月公司行动，说明拆股处理是实际需求。当前免费适配没有声称覆盖全部ETF发行商。State Street的[SPY页面](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy)可公开查看NAV/份额，但页面份额显示精度、历史入口和许可需独立适配；不把不同时日字段混算。

已有Intrinio权限时调用[ETF Global Fund Flows端点](https://docs.intrinio.com/documentation/web_api/get_etf_global_etf_fund_flows_v2)：

```text
GET https://api-v2.intrinio.com/etfg/etfs/{ticker}/fund_flows
start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&page_size=100&next_page=...
Authorization: Bearer <API_KEY>
```

取`fund_flows[].fund_flow/nav/shares_outstanding`；`date`是文件日，`as_of_date`是底层数据有效日，两者分别保存，缺失有效日不以文件日冒充。采用官方支持的[Header认证](https://docs.intrinio.com/documentation/api_v2/authentication)，不把Key放到URL。分页上限4页并公开截断标志；[额度依套餐变化](https://docs.intrinio.com/documentation/api_v2/limits)。ETF Global数据包及历史/再分发需按[发行方数据说明](https://media.etfg.com/files/Data%20Brochure/ETF%20Global%20Data%20Package%20-%20U.S.%20Listed%20-%202025%20-%201.1.25.pdf)询价；Intrinio通用入门价格不能当作这个数据集的报价。

**期权：免费试用与OPRA分别标注**

默认通过[yfinance option_chain](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.option_chain.html)读取Yahoo公开期权。已实际验证NVDA到期日和call/put链。保留`contractSymbol/strike/bid/ask/lastPrice/volume/openInterest/impliedVolatility/lastTradeDate`。报价时刻、OI有效日期、合约乘数、Greeks没有可靠字段时均为null；最后交易时刻不代替报价时刻。默认最近1个到期日，最多3个。

Put/call只聚合本次返回合约：任一组缺失数量或call分母为0即返回null；不能冒称全市场put/call，更不能从OI变化直接判断买卖方向。公开接口可限流或变动，产品化需要有许可和服务承诺的来源。

Alpaca已有账号可使用[Option chain端点](https://docs.alpaca.markets/us/reference/optionchain)：

```text
GET https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}
feed=indicative&limit=1000&expiration_date=YYYY-MM-DD&page_token=...
APCA-API-KEY-ID: <KEY_ID>
APCA-API-SECRET-KEY: <SECRET>
```

返回`snapshots[OCC符号].latestQuote/latestTrade/greeks/impliedVolatility`。Basic免费indicative报价为派生指示值、成交为延迟15分钟的派生数据，不能标成OPRA/NBBO；[官方说明](https://docs.alpaca.markets/us/docs/historical-option-data)也将历史起点列为2024-02。[个人计划](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)Basic免费，Algo Trader Plus为$99/月，OPRA需已有相应权限；本轮默认indicative，不升级账号。最多2页，每页1000；明确显示截断。

Massive已有权限可用[Option Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot)：

```text
GET https://api.massive.com/v3/snapshot/options/{symbol}
limit=250&expiration_date=YYYY-MM-DD&sort=expiration_date&order=asc
Authorization: Bearer <API_KEY>
```

包含`details/day/open_interest/implied_volatility/greeks`，报价、交易字段受套餐影响，Greeks也可能缺失。核心报价/时间、IV、OI/有效日或Greeks任一缺失时标记`partial`并统计缺失合约数，不因有返回行就标为完整。Options Basic免费但不含此端点；Starter $29/月、Developer $79/月为15分钟延迟，Advanced $199/月为实时。仅配置已有权限，最多2页，返回范围不是完整全链保证。[官方价格入口](https://massive.com/options)。

Tradier可作为后续已有账户适配：[官方FAQ](https://docs.tradier.com/overview/faq)说明账户API和sandbox/production的区别。sandbox延迟、生产权限及个人Token的使用范围不能当作免费商业再分发许可，本轮不替用户开户或接入交易端点。

**新闻情绪：供应商样本，不是全市场全量**

[Massive News官方文档](https://massive.com/docs/rest/stocks/news)确认Stocks Basic免费包含新闻、按小时更新、免费历史2年，需已有API Key。调用：

```text
GET https://api.massive.com/v2/reference/news
published_utc.gte=<UTC>&published_utc.lte=<UTC>&sort=published_utc&order=desc&limit=100
# ticker可选；不传则取得供应商未指定ticker的新闻样本
Authorization: Bearer <API_KEY>
```

保留文章ID、标题、原文链接、publisher、published_utc、tickers以及`insights[].ticker/sentiment`。不保存全文。按文章ID去重，再按文章×ticker去重；缺失情绪归未分类。显示正/负/中性观察数、文章数、媒体数、ticker数和`(正面−负面)/已分类观察数`，有分页截断即显示。

免费Basic低额度需保守请求，最多2页、服务端缓存30分钟；429保持原缓存的过期标记，不伪装新数据。情绪数据从[官方宣布的2024-07-02起提供](https://massive.com/knowledge-base/article/how-far-back-does-massives-news-and-sentiment-data-go)。文章发布时间不代表收到/评分时间，系统另存`received_at`，这不是可无偏回测的点时数据。媒体覆盖来自[供应商整合来源](https://massive.com/knowledge-base/article/where-does-massive-source-ticker-news-from)，不将新闻情绪等同投资者持仓或资金净流入。

[Alpha Vantage NEWS_SENTIMENT](https://www.alphavantage.co/documentation/#news-sentiment)当前文档将该端点列为Premium，不能因为一般免费API有日额度就宣称该新闻端点免费。EODHD/Finnhub/Benzinga可列后续候选，但免费权限、样本口径、历史可用性和商业展示需逐端点核验；本轮没有虚构账号连通结果。

**实现与验证边界**

- `market_data_connections`仅保存workspace/owner、供应商、feed、revision和系统Keychain引用，引用以`data/{workspace}/{owner}/...`隔离。GET永不返回Key或引用；Key轮换/测试/停用采用revision比较写入，其他成员不能读此用户连接。
- 全部连接调用为只读数据GET；来源地址固定，禁止自定义数据URL。Massive分页仅接受同官方host、HTTPS和完全相同path，移除认证query；其他供应商只用token续页。禁止重定向携带凭据到其他地址。
- 新缓存目录独立于旧用户数据，原子写入；不读取旧模拟数据。缓存包含source/feed、scope、as_of、received_at、quality、delay_label、coverage、warnings；401/403不以旧缓存隐藏认证失败。显式检测强制新请求，不能靠缓存显示成功。
- 已增加31项隔离测试，覆盖估算公式/缺失/已收盘交易日/极端份额、UTC跨日/盘前/盘中/提前收盘、IV和Greeks空值、新闻去重范围、凭据隔离/CAS/Keyring不可用、截断分页/SSRF、429过期缓存、显式检测及非美股资产输入拦截；前端另有4项缺失值/调整合约/原文链接转换测试。
- 无Key来源已做真实服务级验证：SOXX和IVV各返回21个日级观察（截至2026-10-23），NVDA返回21个可选到期日、最近到期日77份合约。Massive、Intrinio、Alpaca尚无用户提供的相应Key，只完成适配与隔离测试，不宣称已通过真实账号授权验证。

新增发行商时需要固定基金映射、字段/日期一致性、公司行动规则和来源许可；新增供应商需要独立认证和能力检测。前端继续消费同一类型契约，不通过改名把估算切换为已报告值。

**ETF净申赎的研究价值与使用方式**

本项目的判断是：值得保留，用于观察行业或资产配置的持续变化；不适合单独充当个股买卖或日内择时信号。份额创设/赎回属于一级市场，交易所内投资者之间的ETF买卖属于二级市场，两者不是同一统计。多数ETF交易发生在二级市场，不能用净申赎代替整个市场的交易需求。[ICI 2025机制研究](https://www.ici.org/files/2025/per31-07.pdf)

授权参与者（AP）创建/赎回份额的原因还包括套利、库存管理、客户交易与借券。创赎可以交换证券篮子，不能把美元计价的净创设直接称为同额新增现金或“机构看多买入”。[State Street官方机制说明](https://www.ssga.com/us/en/individual/insights/master-the-mechanics-of-etf-trading)

建议的后续分析口径（尚未全部实现）：

- 同时看1日、5日、20日已完成交易日的流量与持续性，不凭单日大额变化判定方向。
- 结合期初AUM计算归一化流量比率，与价格相对强弱、成交量和新闻交叉观察。AUM变化还包含资产价格变化，不能直接等同净流量。
- 在同类ETF间比较，先检查重复持仓、不同发行商间的产品切换、杠杆/反向产品及统计日期，不直接把所有ETF金额相加当主题净买入。
- 使用持仓快照映射主题暴露，并标注估算及重叠；SOXX等宽口径不能直接代表磷化铟、氦气或单只材料公司的资金动向。

当前免费实现只覆盖SOXX与IVV，适合辅助观察半导体和大盘，尚不足以做全行业资金轮动排名。相比购买付费流量，现阶段更应先扩大可核查的来源覆盖、确认公司行动处理与统一时间口径，再验证这些指标是否改善研究判断。
