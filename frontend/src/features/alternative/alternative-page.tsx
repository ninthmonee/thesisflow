import { lazy, Suspense, useState } from "react";
import { Alert, Button, Collapse, Form, Input, Select, Spin, Table, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  getETFFlows,
  getMarketNewsSentiment,
  getMarketOptions,
  listDataConnections,
} from "../../shared/api/generated/client";
import type {
  AlternativeMeta,
  BootstrapResponse,
  DataConnectionView,
  ETFFlowPoint,
  GetETFFlowsProvider,
  GetMarketOptionsProvider,
  OptionContract,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import {
  amount,
  decimal,
  externalArticle,
  finite,
  flowSummary,
  qualityNames,
  volatility,
} from "./alternative-utils";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./alternative.module.css";
import { alternativeViewUrl, validAlternativeSymbol, validFlowSymbols } from "./navigation-context";
import { AttachResearchContextButton } from "../research/research-context";

const FlowsChart = lazy(() =>
  import("./alternative-charts").then((module) => ({ default: module.FlowsChart })),
);
const OptionsChart = lazy(() =>
  import("./alternative-charts").then((module) => ({ default: module.OptionsChart })),
);
const NewsChart = lazy(() =>
  import("./alternative-charts").then((module) => ({ default: module.NewsChart })),
);

type View = "flows" | "options" | "news";
type SharedProps = {
  scope: QueryScope;
  connections: DataConnectionView[];
  connectionsPending: boolean;
};

function time(value: string | null | undefined) {
  if (!value) return "未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("zh-CN", { timeZone: "America/New_York", hour12: false }) + " ET";
}

function message(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "数据暂时不可用";
}

function direction(value: unknown) {
  return finite(value) && value !== 0 ? (value > 0 ? styles.positive : styles.negative) : undefined;
}

function Quality({ value }: { value: string }) {
  return (
    <Tag
      color={
        value === "complete"
          ? "green"
          : value === "action_required" || value === "stale"
            ? "volcano"
            : "gold"
      }
    >
      {qualityNames[value] ?? value}
    </Tag>
  );
}

function Metadata({ meta }: { meta: AlternativeMeta }) {
  return (
    <>
      <div className={styles.metadata}>
        <span>SOURCE {meta.source}</span>
        <span>FEED {meta.feed}</span>
        <span>AS OF {meta.as_of ?? "未知"}</span>
        <span>RECEIVED {time(meta.received_at)}</span>
        <span>CACHE {meta.cache}</span>
        <Quality value={meta.quality} />
      </div>
      <p className={shared.note}>
        {meta.delay_label} · {meta.scope}
      </p>
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: "method",
            label: "数据口径、覆盖与限制",
            children: (
              <div className={shared.muted}>
                {(meta.warnings ?? []).map((text, index) => (
                  <p key={index}>{text}</p>
                ))}
                <div className={shared.mono}>
                  {Object.entries(meta.coverage ?? {}).map(([key, value]) => (
                    <div key={key}>
                      {key}: {value == null ? "未知" : String(value)}
                    </div>
                  ))}
                </div>
              </div>
            ),
          },
        ]}
      />
    </>
  );
}

function NeedConnection({ provider, pending }: { provider: string; pending: boolean }) {
  return pending ? (
    <div className={styles.empty}>
      <Spin />
      正在读取本机数据配置
    </div>
  ) : (
    <div className={styles.empty}>
      <strong>{provider} 需要你已有的API账号</strong>
      <span>当前没有可用连接。公开期权与iShares份额估算可以直接使用。</span>
      <Link to="/settings/sources">配置供应商与 API Key</Link>
    </div>
  );
}

function Failure({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <Alert
      showIcon
      type="warning"
      title={message(error)}
      description={
        <div className={shared.actions}>
          <Button size="small" onClick={retry}>
            重试读取
          </Button>
          <Link to="/settings/sources">检查数据连接与权限</Link>
        </div>
      }
    />
  );
}

function Accounts({
  provider,
  connections,
  selected,
  onChange,
}: {
  provider: string;
  connections: DataConnectionView[];
  selected: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <label className={styles.field} htmlFor={`data-account-${provider}`}>
      已有账号连接
      <Select
        id={`data-account-${provider}`}
        aria-label="数据账号连接"
        value={selected}
        placeholder="选择数据连接"
        onChange={onChange}
        options={connections
          .filter((item) => item.enabled && item.provider === provider)
          .map((item) => ({
            value: item.id,
            label: `${item.name}${item.provider === "alpaca" ? " · " + item.feed : ""}`,
          }))}
        notFoundContent={<Link to="/settings/sources">添加已有账号</Link>}
      />
    </label>
  );
}

function chosenConnection(
  connections: DataConnectionView[],
  provider: string,
  requested: string | null,
) {
  const matching = connections.filter((item) => item.enabled && item.provider === provider);
  return requested ? matching.find((item) => item.id === requested) : matching[0];
}

function FlowsView({ scope, connections, connectionsPending }: SharedProps) {
  const [params, setParams] = useSearchParams();
  const provider: GetETFFlowsProvider =
    params.get("provider") === "intrinio" ? "intrinio" : "ishares";
  const symbols = validFlowSymbols(params.get("symbols")) ?? "";
  const lookback = [7, 30, 90, 365].includes(Number(params.get("days")))
    ? Number(params.get("days"))
    : 30;
  const connection = chosenConnection(connections, provider, params.get("connection"));
  const [metric, setMetric] = useState<"flow" | "nav">("flow");
  const [researchTicker, setResearchTicker] = useState("");
  const change = (values: Record<string, string | undefined>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
  const query = useQuery({
    queryKey: queryKeys.resource(scope, "etf-flows", {
      provider,
      symbols,
      lookback_days: lookback,
      connection_id: connection?.id,
      connection_revision: connection?.revision,
    }),
    queryFn: ({ signal }) =>
      getETFFlows(
        {
          symbols,
          provider,
          lookback_days: lookback,
          ...(provider === "intrinio" && connection ? { connection_id: connection.id } : {}),
        },
        { signal },
      ),
    enabled: !!symbols && (provider === "ishares" || !!connection),
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    retry: shouldRetryQuery,
  });
  return (
    <>
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <h2>ETF申赎研究 · 日级</h2>
          <Button
            icon={<ReloadOutlined />}
            loading={query.isFetching}
            disabled={!symbols || (provider !== "ishares" && !connection)}
            onClick={() => {
              void query.refetch();
            }}
          >
            读取最新缓存
          </Button>
        </div>
        <div className={shared.body}>
          <div className={styles.controls}>
            <label className={styles.field} htmlFor="flow-provider">
              数据来源
              <Select
                id="flow-provider"
                aria-label="ETF流量来源"
                value={provider}
                onChange={(value) => change({ provider: value, connection: undefined })}
                options={[
                  { value: "ishares", label: "iShares · 免费份额估算" },
                  { value: "intrinio", label: "Intrinio · 已有ETF Global权限" },
                ]}
              />
            </label>
            {provider === "intrinio" && (
              <Accounts
                provider={provider}
                connections={connections}
                selected={connection?.id}
                onChange={(value) => change({ connection: value })}
              />
            )}
            <Form
              key={symbols}
              initialValues={{ symbols }}
              layout="inline"
              onFinish={(values: { symbols: string }) =>
                change({ symbols: values.symbols.trim().toUpperCase().replace(/\s/g, "") })
              }
            >
              <Form.Item
                name="symbols"
                label="ETF代码"
                rules={[
                  { required: true, pattern: /^[A-Za-z0-9.-]+(?:\s*,\s*[A-Za-z0-9.-]+){0,9}$/ },
                ]}
              >
                <Input aria-label="ETF代码，逗号分隔" placeholder="SOXX,IVV" />
              </Form.Item>
              <Form.Item>
                <Button htmlType="submit">查询</Button>
              </Form.Item>
            </Form>
            <label className={styles.field} htmlFor="flow-window">
              历史窗口
              <Select
                id="flow-window"
                aria-label="ETF历史窗口"
                value={lookback}
                onChange={(value) => change({ days: String(value) })}
                options={[7, 30, 90, 365].map((value) => ({ value, label: `${value}个自然日` }))}
              />
            </label>
          </div>
        </div>
        <p className={shared.note}>
          {provider === "ishares"
            ? "免费适配当前覆盖SOXX、IVV。估算公式：(本日份额−前日份额)×前日NAV；仅相邻XNYS交易日计算。极端份额/NAV变动暂停估算，需核对公司行动。仅供个人非商业使用。"
            : "读取已有账号的ETF Global fund_flow，文件日和数据有效日分别展示；保存连接不会购买数据权限。"}
        </p>
        <div className={shared.body}>
          <label htmlFor="etf-background-research-ticker">
            将ETF资料作为哪家公司的研究背景？
            <Input
              id="etf-background-research-ticker"
              value={researchTicker}
              onChange={(event) => setResearchTicker(event.target.value.toUpperCase())}
              placeholder="目标公司股票代码，如 AMD；不会自动研究ETF"
              maxLength={32}
            />
          </label>
          <p className={shared.muted}>
            填写后在对应ETF下点击附加。ETF份额流只代表该基金，不等于该公司的资金流入。
          </p>
        </div>
      </section>
      {!symbols && (
        <Alert
          showIcon
          type="info"
          title={params.get("symbols") ? "ETF代码无效，请修改后查询" : "请选择需要查询的ETF"}
          description={
            params.get("symbol")
              ? `已保留证券上下文 ${params.get("symbol")}；个股不会自动转换为ETF。请明确填写ETF代码，iShares当前支持SOXX、IVV。`
              : "尚未读取任何ETF申赎数据。请明确填写ETF代码；iShares当前支持SOXX、IVV。"
          }
        />
      )}
      {provider !== "ishares" && !connection ? (
        <NeedConnection provider="Intrinio / ETF Global" pending={connectionsPending} />
      ) : (
        <>
          {query.isError && (
            <Failure
              error={query.error}
              retry={() => {
                void query.refetch();
              }}
            />
          )}
          {query.isFetching && !query.data && (
            <div className={styles.empty}>
              <Spin />
              正在读取发行商或供应商原始数据
            </div>
          )}
          {query.data && (
            <>
              <div className={styles.metrics}>
                {query.data.funds.map((fund) => {
                  const summary = flowSummary(fund);
                  return (
                    <div className={styles.metric} key={fund.symbol}>
                      <small>
                        {fund.symbol} ·{" "}
                        {fund.meta.is_estimated ? "份额流估算" : "供应商已报告净流量"}
                      </small>
                      <strong className={direction(summary.latest?.net_flow_usd)}>
                        {amount(summary.latest?.net_flow_usd, true)}
                      </strong>
                      <p>
                        有效日 {summary.latest?.as_of_date ?? "未知"} · {summary.validCount}个有效点
                        / {summary.missingCount}个缺失或待核对点
                      </p>
                      <p>窗口已知值合计 {amount(summary.knownSum, true)}，缺失未补零</p>
                    </div>
                  );
                })}
              </div>
              <section className={shared.panel}>
                <div className={shared.toolbar}>
                  <h2>日级数据可视化</h2>
                  <Select
                    aria-label="ETF图表指标"
                    value={metric}
                    onChange={setMetric}
                    options={[
                      { value: "flow", label: "日级流量 · USD" },
                      { value: "nav", label: "NAV · USD/份" },
                    ]}
                  />
                </div>
                <Suspense
                  fallback={
                    <div className={styles.empty}>
                      <Spin />
                    </div>
                  }
                >
                  <FlowsChart funds={query.data.funds} metric={metric} />
                </Suspense>
                <p className={shared.note}>
                  缺失保留空值。发行商估算与供应商已报告数据按当前来源展示，不拼成同一条不明口径的序列。
                </p>
              </section>
              {query.data.funds.map((fund) => (
                <section className={shared.panel} key={fund.symbol}>
                  <div className={shared.toolbar}>
                    <h2>{fund.symbol} · 原始依据与结果</h2>
                    <Tag color={fund.meta.is_estimated ? "gold" : "cyan"}>
                      {fund.meta.is_estimated ? "ESTIMATED" : "VENDOR REPORTED"}
                    </Tag>
                    <AttachResearchContextButton
                      ticker={validAlternativeSymbol(researchTicker) ?? ""}
                      body={{
                        kind: "etf_flows",
                        symbol: fund.symbol,
                        provider,
                        ...(connection ? { connection_id: connection.id } : {}),
                        lookback_days: lookback,
                      }}
                      label={`将 ${fund.symbol} 作为研究背景`}
                    />
                  </div>
                  <div className={shared.table}>
                    <Table<ETFFlowPoint>
                      rowKey={(point) => point.date + ":" + (point.as_of_date ?? "unknown")}
                      size="small"
                      dataSource={[...fund.points].reverse()}
                      pagination={{ pageSize: 12, showSizeChanger: false }}
                      scroll={{ x: 850 }}
                      columns={[
                        { title: "文件日", dataIndex: "date" },
                        {
                          title: "有效日",
                          dataIndex: "as_of_date",
                          render: (value) => value ?? "未知",
                        },
                        {
                          title: "NAV $/份",
                          dataIndex: "nav",
                          align: "right",
                          render: (value) => decimal(value, 4),
                        },
                        {
                          title: "流通份额",
                          dataIndex: "shares_outstanding",
                          align: "right",
                          render: (value) => decimal(value, 0),
                        },
                        {
                          title: fund.meta.is_estimated ? "估算流量 USD" : "已报告净流量 USD",
                          dataIndex: "net_flow_usd",
                          align: "right",
                          render: (value) => (
                            <span className={direction(value)}>{amount(value, true)}</span>
                          ),
                        },
                        {
                          title: "质量",
                          dataIndex: "quality",
                          render: (value) => <Quality value={value} />,
                        },
                        {
                          title: "缺失/核对原因",
                          dataIndex: "reason",
                          render: (value) => value ?? "—",
                        },
                      ]}
                      expandable={{
                        expandedRowRender: (point) => (
                          <p className={shared.muted}>
                            {point.method} · {point.flow_kind}
                          </p>
                        ),
                      }}
                    />
                  </div>
                  <Metadata meta={fund.meta} />
                </section>
              ))}
            </>
          )}
        </>
      )}
    </>
  );
}

function OptionsView({ scope, connections, connectionsPending }: SharedProps) {
  const [params, setParams] = useSearchParams();
  const raw = params.get("provider");
  const provider: GetMarketOptionsProvider = raw === "massive" || raw === "alpaca" ? raw : "yahoo";
  const symbol = validAlternativeSymbol(params.get("symbol")) ?? "";
  const expiration = params.get("expiration") || undefined;
  const connection = chosenConnection(connections, provider, params.get("connection"));
  const [kind, setKind] = useState<"both" | "call" | "put">("both");
  const [metric, setMetric] = useState<"implied_volatility" | "open_interest">(
    "implied_volatility",
  );
  const change = (values: Record<string, string | undefined>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
  const query = useQuery({
    queryKey: queryKeys.resource(scope, "options", {
      symbol,
      provider,
      connection_id: connection?.id,
      connection_revision: connection?.revision,
      expiration,
      max_expirations: 1,
    }),
    queryFn: ({ signal }) =>
      getMarketOptions(
        {
          symbol,
          provider,
          max_expirations: 1,
          ...(expiration ? { expiration } : {}),
          ...(provider !== "yahoo" && connection ? { connection_id: connection.id } : {}),
        },
        { signal },
      ),
    enabled: !!symbol && (provider === "yahoo" || !!connection),
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    retry: shouldRetryQuery,
  });
  const contracts = (query.data?.contracts ?? [])
    .filter((item) => kind === "both" || item.contract_type === kind)
    .sort((a, b) => a.strike - b.strike || a.contract_type.localeCompare(b.contract_type));
  return (
    <>
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <h2>OPTIONS CHAIN · {symbol || "尚未选择标的"}</h2>
          <Button
            icon={<ReloadOutlined />}
            loading={query.isFetching}
            disabled={!symbol || (provider !== "yahoo" && !connection)}
            onClick={() => {
              void query.refetch();
            }}
          >
            读取最新缓存
          </Button>
        </div>
        <div className={shared.body}>
          <div className={styles.controls}>
            <Form
              key={symbol}
              initialValues={{ symbol }}
              layout="inline"
              onFinish={(values: { symbol: string }) =>
                change({ symbol: values.symbol.trim().toUpperCase(), expiration: undefined })
              }
            >
              <Form.Item
                name="symbol"
                label="美股 / ETF"
                rules={[{ required: true, pattern: /^[A-Za-z][A-Za-z0-9.-]{0,31}$/ }]}
              >
                <Input aria-label="期权标的代码" />
              </Form.Item>
              <Form.Item>
                <Button htmlType="submit">查询</Button>
              </Form.Item>
            </Form>
            <label className={styles.field} htmlFor="options-provider">
              来源
              <Select
                id="options-provider"
                aria-label="期权数据来源"
                value={provider}
                onChange={(value) =>
                  change({ provider: value, connection: undefined, expiration: undefined })
                }
                options={[
                  { value: "yahoo", label: "Yahoo公开期权 · 免费" },
                  { value: "alpaca", label: "Alpaca · 已有账户" },
                  { value: "massive", label: "Massive · 已有期权权限" },
                ]}
              />
            </label>
            {provider !== "yahoo" && (
              <Accounts
                provider={provider}
                connections={connections}
                selected={connection?.id}
                onChange={(value) => change({ connection: value })}
              />
            )}
            <label className={styles.field} htmlFor="options-expiry">
              到期日
              <Select
                id="options-expiry"
                aria-label="期权到期日"
                value={expiration ?? "nearest"}
                onChange={(value) =>
                  change({ expiration: value === "nearest" ? undefined : value })
                }
                options={[
                  { value: "nearest", label: "最近可用到期日" },
                  ...(query.data?.available_expirations ?? []).map((value) => ({
                    value,
                    label: value,
                  })),
                ]}
              />
            </label>
            <label className={styles.field} htmlFor="options-expiry-specific">
              指定到期日
              <input
                id="options-expiry-specific"
                aria-label="指定期权到期日"
                type="date"
                value={expiration ?? ""}
                onChange={(event) => change({ expiration: event.target.value || undefined })}
              />
            </label>
          </div>
        </div>
        <p className={shared.note}>
          Yahoo可直接试用；报价时刻、OI日期和Greeks缺失时保留未知。Alpaca
          indicative为派生指示性数据，OPRA需已有权限。当前为研究快照。
        </p>
        <div className={shared.body}>
          <AttachResearchContextButton
            ticker={symbol}
            disabled={provider !== "yahoo" && !connection}
            body={{
              kind: "options",
              symbol,
              provider,
              ...(connection ? { connection_id: connection.id } : {}),
              ...(expiration ? { expiration } : {}),
            }}
          />
          <p className={shared.muted}>
            显式点击才保存当前来源的补充快照，并进入参数确认页；不启动模型。所选标的须确认为公司股票。
          </p>
        </div>
      </section>
      {!symbol && (
        <Alert
          showIcon
          type="info"
          title={
            params.get("symbol")
              ? "该证券代码不在当前美股期权查询格式范围内"
              : "请选择需要查询的美股或ETF"
          }
          description="不会自动使用示例股票。输入明确标的后才会读取期权快照；是否存在期权及来源覆盖以实际响应为准。"
        />
      )}
      {provider !== "yahoo" && !connection ? (
        <NeedConnection
          provider={provider === "alpaca" ? "Alpaca" : "Massive Options"}
          pending={connectionsPending}
        />
      ) : (
        <>
          {query.isError && (
            <Failure
              error={query.error}
              retry={() => {
                void query.refetch();
              }}
            />
          )}
          {query.isFetching && !query.data && (
            <div className={styles.empty}>
              <Spin />
              正在读取所选到期日的期权链
            </div>
          )}
          {query.data && (
            <>
              <div className={styles.metrics}>
                <div className={styles.metric}>
                  <small>本次返回合约</small>
                  <strong>{query.data.contracts.length}</strong>
                  <p>到期日 {query.data.selected_expirations.join(", ") || "暂无"}</p>
                </div>
                <div className={styles.metric}>
                  <small>样本 Put / Call · 成交量</small>
                  <strong>{decimal(query.data.put_call_volume_ratio, 3)}</strong>
                  <p>数量缺失或分母为0不计算</p>
                </div>
                <div className={styles.metric}>
                  <small>样本 Put / Call · OI</small>
                  <strong>{decimal(query.data.put_call_oi_ratio, 3)}</strong>
                  <p>非全市场口径，也不表示买卖方向</p>
                </div>
              </div>
              <section className={shared.panel}>
                <div className={shared.toolbar}>
                  <h2>{symbol} · 合约分布</h2>
                  <div className={shared.actions}>
                    <Select
                      aria-label="期权类型筛选"
                      value={kind}
                      onChange={setKind}
                      options={[
                        { value: "both", label: "CALL + PUT" },
                        { value: "call", label: "CALL" },
                        { value: "put", label: "PUT" },
                      ]}
                    />
                    <Select
                      aria-label="期权图表指标"
                      value={metric}
                      onChange={setMetric}
                      options={[
                        { value: "implied_volatility", label: "隐含波动率 IV" },
                        { value: "open_interest", label: "持仓量 OI" },
                      ]}
                    />
                  </div>
                </div>
                <Suspense
                  fallback={
                    <div className={styles.empty}>
                      <Spin />
                    </div>
                  }
                >
                  <OptionsChart contracts={contracts} metric={metric} />
                </Suspense>
                <p className={shared.note}>
                  图表与表格按CALL/PUT筛选。上方比率仍基于全部本次返回合约。缺失值不补零；同一行权价存在多个调整合约时图表留空，原始合约保留在表格。
                </p>
                <div className={shared.table}>
                  <Table<OptionContract>
                    rowKey="symbol"
                    size="small"
                    dataSource={contracts}
                    pagination={{ pageSize: 15, showSizeChanger: false }}
                    scroll={{ x: 1320 }}
                    columns={[
                      { title: "到期日", dataIndex: "expiration" },
                      {
                        title: "类型",
                        dataIndex: "contract_type",
                        render: (value) => (
                          <Tag color={value === "call" ? "cyan" : "gold"}>
                            {String(value).toUpperCase()}
                          </Tag>
                        ),
                      },
                      {
                        title: "行权价 $",
                        dataIndex: "strike",
                        align: "right",
                        render: (value) => decimal(value, 3),
                      },
                      {
                        title: "Bid $",
                        dataIndex: "bid",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Ask $",
                        dataIndex: "ask",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Last $",
                        dataIndex: "last_price",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Volume",
                        dataIndex: "volume",
                        align: "right",
                        render: (value) => amount(value),
                      },
                      {
                        title: "OI",
                        dataIndex: "open_interest",
                        align: "right",
                        render: (value) => amount(value),
                      },
                      {
                        title: "IV",
                        dataIndex: "implied_volatility",
                        align: "right",
                        render: volatility,
                      },
                      {
                        title: "Delta",
                        dataIndex: "delta",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Gamma",
                        dataIndex: "gamma",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Theta",
                        dataIndex: "theta",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                      {
                        title: "Vega",
                        dataIndex: "vega",
                        align: "right",
                        render: (value) => decimal(value, 4),
                      },
                    ]}
                    expandable={{
                      expandedRowRender: (contract) => (
                        <div className={styles.metadata}>
                          <span>{contract.symbol}</span>
                          <span>报价 {time(contract.quote_as_of)}</span>
                          <span>最后交易 {time(contract.last_trade_at)}</span>
                          <span>OI有效日 {contract.open_interest_as_of ?? "未知"}</span>
                          <span>合约乘数 {contract.multiplier ?? "未知"}</span>
                        </div>
                      ),
                    }}
                  />
                </div>
                <Metadata meta={query.data.meta} />
              </section>
            </>
          )}
        </>
      )}
    </>
  );
}

function NewsView({ scope, connections, connectionsPending }: SharedProps) {
  const [params, setParams] = useSearchParams();
  const connection = chosenConnection(connections, "massive", params.get("connection"));
  const rawSymbol = params.get("symbol");
  const symbol = validAlternativeSymbol(rawSymbol);
  const invalidSymbol = !!rawSymbol && !symbol;
  const hours = [6, 24, 72, 168].includes(Number(params.get("hours")))
    ? Number(params.get("hours"))
    : 24;
  const change = (values: Record<string, string | undefined>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
  const query = useQuery({
    queryKey: queryKeys.resource(scope, "news-sentiment", {
      provider: "massive",
      connection_id: connection?.id,
      connection_revision: connection?.revision,
      symbol,
      lookback_hours: hours,
      limit: 100,
    }),
    queryFn: ({ signal }) =>
      getMarketNewsSentiment(
        {
          provider: "massive",
          ...(connection ? { connection_id: connection.id } : {}),
          ...(symbol ? { symbol } : {}),
          lookback_hours: hours,
          limit: 100,
        },
        { signal },
      ),
    enabled: !!connection && !invalidSymbol,
    staleTime: 1_800_000,
    refetchOnWindowFocus: false,
    retry: shouldRetryQuery,
  });
  return (
    <>
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <h2>NEWS SENTIMENT · 供应商新闻样本</h2>
          <Button
            icon={<ReloadOutlined />}
            loading={query.isFetching}
            disabled={!connection || invalidSymbol}
            onClick={() => {
              void query.refetch();
            }}
          >
            读取最新缓存
          </Button>
        </div>
        <div className={shared.body}>
          <div className={styles.controls}>
            <Accounts
              provider="massive"
              connections={connections}
              selected={connection?.id}
              onChange={(value) => change({ connection: value })}
            />
            <Form
              key={symbol ?? "all"}
              initialValues={{ symbol: symbol ?? rawSymbol ?? "" }}
              layout="inline"
              onFinish={(values: { symbol: string }) =>
                change({ symbol: values.symbol.trim().toUpperCase() || undefined })
              }
            >
              <Form.Item name="symbol" label="标的筛选" rules={[{ pattern: /^[A-Za-z0-9.-]*$/ }]}>
                <Input aria-label="新闻标的筛选" placeholder="留空查看供应商广泛样本" allowClear />
              </Form.Item>
              <Form.Item>
                <Button htmlType="submit">查询</Button>
              </Form.Item>
            </Form>
            <label className={styles.field} htmlFor="news-window">
              发布时间窗
              <Select
                id="news-window"
                aria-label="新闻时间窗"
                value={hours}
                onChange={(value) => change({ hours: String(value) })}
                options={[6, 24, 72, 168].map((value) => ({ value, label: `最近${value}小时` }))}
              />
            </label>
          </div>
        </div>
        <p className={shared.note}>
          Massive Stocks Basic新闻免费，但需要自己的API
          Key。未指定标的也只是供应商新闻样本；不代表所有美股媒体、上市公司或投资者。新闻情绪不等于资金流。
        </p>
        <div className={shared.body}>
          <AttachResearchContextButton
            ticker={symbol ?? ""}
            disabled={!connection || invalidSymbol}
            body={{
              kind: "news",
              symbol: symbol ?? null,
              provider: "massive",
              ...(connection ? { connection_id: connection.id } : {}),
              lookback_hours: hours,
            }}
          />
          <p className={shared.muted}>
            先按目标公司股票筛选，再显式附加当前时间窗的新闻样本；不会自动启动研究。
          </p>
        </div>
      </section>
      {invalidSymbol && (
        <Alert
          showIcon
          type="warning"
          title="证券代码无效，未改为查询全市场"
          description="请修改标的代码，或清空后明确查询供应商广泛新闻样本。"
        />
      )}
      {!connection ? (
        <NeedConnection provider="Massive Stocks / News" pending={connectionsPending} />
      ) : (
        <>
          {query.isError && (
            <Failure
              error={query.error}
              retry={() => {
                void query.refetch();
              }}
            />
          )}
          {query.isFetching && !query.data && (
            <div className={styles.empty}>
              <Spin />
              正在读取供应商新闻样本
            </div>
          )}
          {query.data && (
            <>
              <div className={styles.metrics}>
                <div className={styles.metric}>
                  <small>已分类情绪平衡</small>
                  <strong className={direction(query.data.sentiment_balance)}>
                    {query.data.sentiment_balance == null
                      ? "—"
                      : `${(query.data.sentiment_balance * 100).toFixed(1)}%`}
                  </strong>
                  <p>(正面−负面) / 已分类ticker观察数</p>
                </div>
                <div className={styles.metric}>
                  <small>去重文章</small>
                  <strong>{query.data.article_count}</strong>
                  <p>
                    {query.data.publisher_count}家样本媒体 · {query.data.ticker_count}个已评分ticker
                  </p>
                </div>
                <div className={styles.metric}>
                  <small>未分类文章</small>
                  <strong>{query.data.unclassified_articles}</strong>
                  <p>缺失情绪不计作中性</p>
                </div>
                <div className={styles.metric}>
                  <small>正面 / 中性 / 负面观察</small>
                  <strong>
                    {query.data.positive} / {query.data.neutral} / {query.data.negative}
                  </strong>
                  <p>按文章×ticker去重统计</p>
                </div>
              </div>
              <section className={shared.panel}>
                <div className={shared.toolbar}>
                  <h2>样本情绪分布</h2>
                  <Quality value={query.data.meta.quality} />
                </div>
                <Suspense
                  fallback={
                    <div className={styles.empty}>
                      <Spin />
                    </div>
                  }
                >
                  <NewsChart news={query.data} />
                </Suspense>
                <Metadata meta={query.data.meta} />
              </section>
              <section className={shared.panel}>
                <div className={shared.toolbar}>
                  <h2>新闻证据与原文</h2>
                  <span className={shared.muted}>显示已返回文章，是否截断见覆盖信息</span>
                </div>
                {query.data.articles.length === 0 && (
                  <div className={styles.empty}>此时间窗没有返回可用新闻；未以模拟文章替代</div>
                )}
                {query.data.articles.map((article) => {
                  const url = externalArticle(article.url);
                  return (
                    <article className={styles.news} key={article.id}>
                      <div className={styles.newsMeta}>
                        <span>{article.publisher}</span>
                        <time dateTime={article.published_at}>{time(article.published_at)}</time>
                        <span>原文发布时间</span>
                      </div>
                      <h3>
                        {url ? (
                          <a href={url} target="_blank" rel="noreferrer">
                            {article.title} ↗
                          </a>
                        ) : (
                          article.title
                        )}
                      </h3>
                      <div className={shared.actions}>
                        {article.tickers.map((ticker) => (
                          <Tag key={ticker}>{ticker}</Tag>
                        ))}
                        <span className={shared.muted}>
                          {article.missing_sentiment
                            ? "情绪未分类"
                            : `正面 ${article.positive_observations} · 中性 ${article.neutral_observations} · 负面 ${article.negative_observations}`}
                        </span>
                      </div>
                    </article>
                  );
                })}
              </section>
            </>
          )}
        </>
      )}
    </>
  );
}

export function AlternativeDataPage({
  bootstrap,
  view,
}: {
  bootstrap: BootstrapResponse;
  view: View;
}) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [contextParams] = useSearchParams();
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "data-connections"),
    queryFn: ({ signal }) => listDataConnections({ signal }),
    retry: shouldRetryQuery,
  });
  const props = {
    scope,
    connections: connections.data?.connections ?? [],
    connectionsPending: connections.isPending,
  };
  return (
    <div className={shared.stack}>
      <div className={shared.heading}>
        <small>MARKET INTELLIGENCE · SOURCE AWARE</small>
        <h1>
          {view === "flows"
            ? "ETF申赎与份额流"
            : view === "options"
              ? "期权链与波动率"
              : "市场新闻与情绪"}
        </h1>
        <p>
          从原始数据、明确方法和覆盖范围判断市场关注方向；每项结果均可查看来源、时刻、缺失和质量。
        </p>
      </div>
      <nav className={styles.tabs} aria-label="专业数据视图">
        {(
          [
            ["flows", "ETF申赎"],
            ["options", "期权分析"],
            ["news", "新闻情绪"],
          ] as const
        ).map(([key, label]) => (
          <Link
            key={key}
            className={view === key ? styles.active : undefined}
            to={alternativeViewUrl(key, contextParams)}
          >
            {label}
          </Link>
        ))}
        <Link to="/settings/sources">数据源设置</Link>
      </nav>
      {connections.isError && <Alert type="warning" showIcon title={message(connections.error)} />}
      {view === "flows" ? (
        <FlowsView {...props} />
      ) : view === "options" ? (
        <OptionsView {...props} />
      ) : (
        <NewsView {...props} />
      )}
    </div>
  );
}
