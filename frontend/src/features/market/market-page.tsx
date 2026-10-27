import { useState } from "react";
import { Alert, Button, Collapse, Input, Popover, Select, Spin, Table, Tag, Tooltip } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { generateMarketBrief, getMarketPulse } from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  BriefClaim,
  GetMarketPulseWindow,
  MarketBrief,
  MarketSnapshot,
  MetricEvidence,
  SectorMetric,
} from "../../shared/api/generated/models";
import {
  GenerateMarketBriefResponse,
  GetMarketPulseResponse,
} from "../../shared/api/generated/validators";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import { SectorCharts } from "./sector-charts";
import { ProfessionalPulsePanel } from "./professional-pulse-panel";
import { AttachResearchContextButton } from "../research/research-context";
import {
  compactText,
  finite,
  marketFailure,
  numberText,
  qualityLabels,
  signedText,
  timestampText,
} from "./market-utils";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./market.module.css";

function valueColor(value: number | null | undefined) {
  return finite(value) ? (value >= 0 ? styles.positive : styles.negative) : styles.neutral;
}

function EvidenceLinks({ ids, evidence }: { ids: string[]; evidence: MetricEvidence[] }) {
  return (
    <span>
      {ids.map((id) => {
        const item = evidence.find((entry) => entry.id === id);
        return (
          <Popover
            key={id}
            title={item?.label ?? "证据暂不可用"}
            trigger="click"
            content={
              item ? (
                <div style={{ maxWidth: 380 }}>
                  <p>
                    数值：{numberText(item.value)} {item.unit}
                    <br />
                    截至：{item.as_of ?? "—"}
                    <br />
                    来源：{item.source ?? "—"} / {item.feed ?? "—"}
                    <br />
                    质量：{qualityLabels[item.quality]}
                  </p>
                  <p>{item.method}</p>
                </div>
              ) : (
                "当前快照中没有此项证据。"
              )
            }
          >
            <button type="button" className={styles.evidenceTag}>
              {item?.label ?? "证据暂不可用"}
            </button>
          </Popover>
        );
      })}
    </span>
  );
}

function Claims({ claims, snapshot }: { claims: BriefClaim[]; snapshot: MarketSnapshot }) {
  return (
    <>
      {claims.length ? (
        claims.map((claim, index) => (
          <p key={`${index}:${claim.evidence_ids.join(",")}`}>
            {claim.text}
            <EvidenceLinks ids={claim.evidence_ids} evidence={snapshot.evidence} />
          </p>
        ))
      ) : (
        <p className={shared.muted}>没有足够证据形成此项观察。</p>
      )}
    </>
  );
}

function MarketBriefPanel({ snapshot }: { snapshot: MarketSnapshot }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    snapshotId: string;
    brief?: MarketBrief;
    error?: string;
  }>();
  const brief = result?.snapshotId === snapshot.id ? result.brief : undefined;
  const error = result?.snapshotId === snapshot.id ? result.error : undefined;

  async function generate() {
    setBusy(true);
    try {
      const response = GenerateMarketBriefResponse.parse(
        await generateMarketBrief({ snapshot_id: snapshot.id, language: "Chinese" }),
      );
      setResult({ snapshotId: snapshot.id, brief: response });
    } catch (failure) {
      setResult({ snapshotId: snapshot.id, error: marketFailure(failure) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={shared.panel}>
      <div className={shared.toolbar}>
        <h2>市场情绪与方向解读</h2>
        <Button
          loading={busy}
          disabled={!snapshot.as_of || snapshot.quality === "unavailable"}
          onClick={() => {
            void generate();
          }}
        >
          生成市场简报（调用模型）
        </Button>
      </div>
      <div className={shared.body}>
        <p className={shared.muted}>
          读取页面只汇总数值，不调用模型。点击后使用已配置模型解释当前快照，可能产生模型费用。简报不提供没有来源的情绪分数。
          专业维度需单独显式加载，不会自动混入这份价量简报。
        </p>
        {error && <Alert type="error" title={error} />}
        {brief ? (
          <>
            <p className={shared.muted}>
              数据截至 {brief.as_of} · 生成 {timestampText(brief.generated_at, "America/New_York")}{" "}
              · {brief.provider ?? "未知供应商"} / {brief.model ?? "未知模型"}
            </p>
            <div className={styles.briefGrid}>
              <div>
                <h3>市场状态</h3>
                <Claims claims={[brief.content.state]} snapshot={snapshot} />
              </div>
              <div>
                <h3>主要变化</h3>
                <Claims claims={brief.content.changes ?? []} snapshot={snapshot} />
              </div>
              <div>
                <h3>成交关注方向</h3>
                <Claims claims={brief.content.attention ?? []} snapshot={snapshot} />
              </div>
              <div>
                <h3>分歧与风险</h3>
                <Claims
                  claims={[...(brief.content.divergences ?? []), ...(brief.content.risks ?? [])]}
                  snapshot={snapshot}
                />
              </div>
            </div>
            <p className={shared.muted}>{brief.scope_notice}</p>
            {!!brief.data_gaps.length && (
              <Alert
                type="warning"
                title="简报的数据缺口"
                description={brief.data_gaps.join("；")}
              />
            )}
          </>
        ) : (
          <p className={shared.muted}>
            可先检查下方波动率、观察样本广度、行业相对强弱和数据缺口，再按需生成解读。
          </p>
        )}
      </div>
    </section>
  );
}

function AttachMarketSnapshot({ snapshot }: { snapshot: MarketSnapshot }) {
  const [ticker, setTicker] = useState("");
  return (
    <section className={shared.panel}>
      <div className={shared.toolbar}>
        <h2>把当前市场快照附加到个股研究</h2>
      </div>
      <div className={shared.body}>
        <p className={shared.muted}>
          先明确目标公司股票，点击后会校验证券类型并保存这份市场背景，只进入研究创建页供你预览；不会启动模型研究。ETF或指数可作为背景，不能当作公司研究对象。
        </p>
        <div className={shared.row}>
          <Input
            value={ticker}
            onChange={(event) => setTicker(event.target.value)}
            placeholder="目标公司股票代码"
            aria-label="市场背景目标公司股票"
            maxLength={32}
            style={{ width: 250 }}
          />
          <AttachResearchContextButton
            body={{ kind: "market", market_snapshot_id: snapshot.id }}
            ticker={ticker}
            disabled={!snapshot.as_of || snapshot.quality === "unavailable"}
            label="保存当前快照并进入研究预览"
          />
        </div>
        <p className={shared.muted}>
          附加内容：{snapshot.id} · 数据截至 {snapshot.as_of ?? "未知"} · 质量{" "}
          {qualityLabels[snapshot.quality]}。只包含此价量快照，不包含下方另行读取的专业维度。
        </p>
      </div>
    </section>
  );
}

function SnapshotEvidence({ snapshot }: { snapshot: MarketSnapshot }) {
  return (
    <Collapse
      ghost
      items={[
        {
          key: "evidence",
          label: `核对数值证据与计算口径（${snapshot.evidence.length} 项）`,
          children: (
            <div className={shared.table}>
              <Table<MetricEvidence>
                size="small"
                rowKey="id"
                pagination={{ pageSize: 12, showSizeChanger: false }}
                dataSource={snapshot.evidence}
                columns={[
                  {
                    title: "证据 / 指标",
                    key: "label",
                    render: (_, item) => (
                      <>
                        <span className={shared.mono}>{item.id}</span>
                        <div>{item.label}</div>
                      </>
                    ),
                  },
                  {
                    title: "数值",
                    key: "value",
                    render: (_, item) => `${numberText(item.value)} ${item.unit}`,
                  },
                  {
                    title: "来源 / 截止",
                    key: "source",
                    render: (_, item) => (
                      <>
                        {item.source ?? "—"} / {item.feed ?? "—"}
                        <div className={shared.muted}>
                          {item.as_of ?? "—"} · {qualityLabels[item.quality]}
                        </div>
                      </>
                    ),
                  },
                  { title: "方法", dataIndex: "method" },
                ]}
              />
            </div>
          ),
        },
        {
          key: "optional",
          label: "独立专业数据与本快照边界",
          children: (
            <div className={styles.availability}>
              {snapshot.optional_data.map((item) => (
                <div key={item.id}>
                  <strong>{item.name} · 未纳入此快照</strong>
                  <span>{item.reason}</span>
                  <Link
                    to={
                      item.id === "etf_net_flows"
                        ? "/insights/flows"
                        : item.id === "options_put_call"
                          ? "/insights/options"
                          : "/insights/news"
                    }
                  >
                    打开对应专业数据
                  </Link>
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}

export function MarketPage({
  bootstrap,
  view = "pulse",
}: {
  bootstrap: BootstrapResponse;
  view?: "pulse" | "sectors";
}) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [window, setWindow] = useState<GetMarketPulseWindow>("1D");
  const snapshot = useQuery({
    queryKey: queryKeys.resource(scope, "market-pulse", { window }),
    queryFn: async ({ signal }) =>
      GetMarketPulseResponse.parse(await getMarketPulse({ window }, { signal })),
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const data = snapshot.data;

  return (
    <div className={shared.stack}>
      <div className={shared.heading}>
        <small>
          {view === "pulse" ? "MARKET PULSE · EVIDENCE FIRST" : "SECTOR RADAR · PARTICIPATION"}
        </small>
        <h1>{view === "pulse" ? "市场脉搏" : "板块与成交关注"}</h1>
        <p>
          {view === "pulse"
            ? "从指数 ETF 代理、波动预期和固定观察样本，了解美股最近的市场状态。"
            : "通过行业 ETF 价格强弱与相对成交量观察关注方向；成交活跃度与资金净流入分别定义。"}
        </p>
      </div>
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <div className={shared.actions}>
            <h2>已完成交易日快照</h2>
            <Select<GetMarketPulseWindow>
              aria-label="市场比较窗口"
              value={window}
              onChange={setWindow}
              options={[
                { value: "1D", label: "最近 1 交易日" },
                { value: "5D", label: "最近 5 交易日" },
                { value: "1M", label: "最近 1 个月" },
              ]}
              style={{ width: 180 }}
            />
          </div>
          <Button
            icon={<ReloadOutlined />}
            loading={snapshot.isFetching}
            onClick={() => {
              void snapshot.refetch();
            }}
          >
            重新读取快照
          </Button>
        </div>
        <div className={shared.body}>
          {data ? (
            <>
              <div className={shared.row}>
                <Tag color={data.quality === "complete" ? "default" : "warning"}>
                  {qualityLabels[data.quality]}
                </Tag>
                <span className={shared.muted}>
                  共同截止日 {data.as_of ?? "暂无共同截止日"} · 比较 {data.window_start ?? "—"} →{" "}
                  {data.window_end ?? "—"}
                </span>
              </div>
              <p className={shared.muted}>
                来源 {data.source ?? "—"} / {data.feed ?? "—"} · 调整 {data.adjustment} · 接收{" "}
                {timestampText(data.received_at, "America/New_York")}
              </p>
              <p className={shared.muted}>{data.basis}</p>
            </>
          ) : (
            <p className={shared.muted}>日级数据以最近完整交易日为依据，不显示为“今日实时”。</p>
          )}
        </div>
      </section>
      {snapshot.isPending && (
        <div className={styles.chartLoading}>
          <Spin />
          <p>正在汇总市场快照，首次读取可能需要等待来源返回…</p>
        </div>
      )}
      {snapshot.isError && (
        <Alert
          type="error"
          showIcon
          title={marketFailure(snapshot.error)}
          description="未使用模拟指数或虚构情绪分数。请稍后重试。"
          action={
            <Button
              onClick={() => {
                void snapshot.refetch();
              }}
            >
              重试
            </Button>
          }
        />
      )}
      {data && (
        <>
          {!!data.warnings.length && (
            <Alert
              type="warning"
              showIcon
              title="覆盖范围与数据质量提示"
              description={
                <ul>
                  {data.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          )}
          <div className={styles.benchmarkGrid}>
            {data.benchmarks.map((benchmark) => (
              <section className={styles.benchmark} key={benchmark.symbol}>
                <h3>
                  <Link to={`/stocks?symbol=${encodeURIComponent(benchmark.symbol)}`}>
                    {benchmark.symbol}
                  </Link>{" "}
                  <small>{benchmark.name}</small>
                </h3>
                <div className={styles.benchmarkValue}>
                  {numberText(benchmark.price)}
                  <span className={valueColor(benchmark.return_percent)}>
                    {signedText(benchmark.return_percent)}
                  </span>
                </div>
                <small>
                  {benchmark.kind === "volatility_index"
                    ? "波动预期指数 · 不代表涨跌方向"
                    : "指数 ETF 代理 · 非指数本身"}
                </small>
                <small>
                  截至 {benchmark.as_of ?? "—"} · {qualityLabels[benchmark.quality]}
                </small>
                {benchmark.reason && <small>{benchmark.reason}</small>}
              </section>
            ))}
          </div>
          {view === "pulse" && (
            <section className={shared.panel}>
              <div className={shared.toolbar}>
                <h2>观察样本广度</h2>
                <Tag>
                  {data.breadth.available_count} / {data.breadth.requested_count} 有效
                </Tag>
              </div>
              <div className={shared.body}>
                <div className={styles.breadthGrid}>
                  <div>
                    <span>上涨样本</span>
                    <strong className={styles.positive}>
                      {numberText(data.breadth.advancers, 0)}
                    </strong>
                  </div>
                  <div>
                    <span>下跌样本</span>
                    <strong className={styles.negative}>
                      {numberText(data.breadth.decliners, 0)}
                    </strong>
                  </div>
                  <div>
                    <span>持平样本</span>
                    <strong>{numberText(data.breadth.unchanged, 0)}</strong>
                  </div>
                  <div>
                    <span>高于 SMA20</span>
                    <strong>
                      {finite(data.breadth.above_sma20_percent)
                        ? `${numberText(data.breadth.above_sma20_percent)}%`
                        : "—"}
                    </strong>
                  </div>
                </div>
                <p className={shared.muted}>
                  {data.breadth.universe_name} / {data.breadth.universe_version} ·{" "}
                  {data.breadth.scope_notice} · SMA20 有效样本 {data.breadth.sma20_sample_count}
                </p>
                <Collapse
                  ghost
                  size="small"
                  items={[
                    {
                      key: "sample",
                      label: "查看固定样本名单",
                      children: (
                        <p className={shared.mono}>{data.breadth.universe_symbols.join(" · ")}</p>
                      ),
                    },
                  ]}
                />
              </div>
            </section>
          )}
          <div className={shared.columns}>
            <section className={shared.panel}>
              <div className={shared.toolbar}>
                <h2>行业 ETF 价格收益</h2>
                <Tag>{window}</Tag>
              </div>
              <SectorCharts sectors={data.sectors} mode="heatmap" />
              <p className={shared.note}>
                色块等面积，仅表示价格收益；缺失板块不绘制中性色。ETF 代理不等同于行业内全部公司。
              </p>
            </section>
            <section className={shared.panel}>
              <div className={shared.toolbar}>
                <h2>相对 SPY 强弱</h2>
                <Tag>百分点 pp</Tag>
              </div>
              <SectorCharts sectors={data.sectors} mode="relative" />
              <p className={shared.note}>
                行业 ETF 收益减 SPY 收益；同一来源、feed 与共同截止日。价格收益不承诺总回报。
              </p>
            </section>
          </div>
          <section className={shared.panel}>
            <div className={shared.toolbar}>
              <h2>板块关注度明细</h2>
              <span className={shared.muted}>可按相对收益、日 RVOL 或成交额估算排序</span>
            </div>
            <div className={shared.table}>
              <Table<SectorMetric>
                rowKey="symbol"
                size="small"
                pagination={false}
                dataSource={data.sectors}
                scroll={{ x: 920 }}
                columns={[
                  {
                    title: "行业 ETF",
                    key: "sector",
                    fixed: "left",
                    render: (_, item) => (
                      <>
                        <Link
                          to={`/stocks?symbol=${encodeURIComponent(item.symbol)}`}
                          className={shared.mono}
                        >
                          {item.symbol}
                        </Link>
                        <div className={shared.muted}>{item.name}</div>
                      </>
                    ),
                  },
                  {
                    title: "价格收益",
                    key: "return",
                    sorter: (a, b) =>
                      (a.return_percent ?? -Infinity) - (b.return_percent ?? -Infinity),
                    render: (_, item) => (
                      <span className={valueColor(item.return_percent)}>
                        {signedText(item.return_percent)}
                      </span>
                    ),
                  },
                  {
                    title: "相对 SPY",
                    key: "relative",
                    sorter: (a, b) =>
                      (a.relative_return_pp ?? -Infinity) - (b.relative_return_pp ?? -Infinity),
                    render: (_, item) => (
                      <span className={valueColor(item.relative_return_pp)}>
                        {signedText(item.relative_return_pp, " pp")}
                      </span>
                    ),
                  },
                  {
                    title: "日 RVOL",
                    key: "rvol",
                    sorter: (a, b) => (a.daily_rvol ?? -Infinity) - (b.daily_rvol ?? -Infinity),
                    render: (_, item) => (
                      <Tooltip title={item.rvol_method}>
                        <span>
                          {finite(item.daily_rvol) ? `${numberText(item.daily_rvol)}×` : "—"}
                          <div className={shared.muted}>基准 {item.rvol_sample_count} 日</div>
                        </span>
                      </Tooltip>
                    ),
                  },
                  {
                    title: "完整日成交量",
                    key: "volume",
                    render: (_, item) => compactText(item.volume),
                  },
                  {
                    title: "成交额估算 USD",
                    key: "turnover",
                    sorter: (a, b) =>
                      (a.turnover_estimate_usd ?? -Infinity) -
                      (b.turnover_estimate_usd ?? -Infinity),
                    render: (_, item) => (
                      <Tooltip title={item.turnover_method}>
                        <span>{compactText(item.turnover_estimate_usd)}</span>
                      </Tooltip>
                    ),
                  },
                  {
                    title: "覆盖 / 截止",
                    key: "quality",
                    render: (_, item) => (
                      <>
                        {qualityLabels[item.quality]}
                        <div className={shared.muted}>
                          {item.as_of ?? "—"}
                          {item.reason ? ` · ${item.reason}` : ""}
                        </div>
                      </>
                    ),
                  },
                ]}
              />
            </div>
            <p className={shared.note}>
              日 RVOL = 最近完整交易日成交量 ÷ 此前 20 个完整交易日均量（同一
              feed，预热不足不计算），不随上方收益窗口改为分钟 RVOL。成交额 = 完整日收盘价 ×
              成交量的估算值，不能解释为净买入或资金净流入。
            </p>
          </section>
          {!!data.observed_divergences.length && (
            <section className={shared.panel}>
              <div className={shared.toolbar}>
                <h2>数值分歧观察</h2>
                <Tag>规则证据</Tag>
              </div>
              <div className={shared.body}>
                {data.observed_divergences.map((observation, index) => (
                  <p
                    key={`${index}:${observation.evidence_ids.join(",")}`}
                    className={shared.muted}
                  >
                    {observation.text}
                    <EvidenceLinks ids={observation.evidence_ids} evidence={data.evidence} />
                  </p>
                ))}
              </div>
            </section>
          )}
          <MarketBriefPanel key={data.id} snapshot={data} />
          <AttachMarketSnapshot snapshot={data} />
          <section className={shared.panel}>
            <SnapshotEvidence snapshot={data} />
          </section>
        </>
      )}
      {view === "pulse" && <ProfessionalPulsePanel bootstrap={bootstrap} />}
    </div>
  );
}
