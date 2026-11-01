import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Form,
  Input,
  InputNumber,
  Select,
  Spin,
  Tag,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  getMarketBars,
  getMarketCalendar,
  getMarketCapabilities,
  getMarketIndicators,
  getMarketQuotes,
  listResearchRuns,
  searchInstruments,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  GetMarketBarsParams,
  InstrumentResponse,
  MarketMeta,
} from "../../shared/api/generated/models";
import {
  GetMarketBarsResponse,
  GetMarketCalendarResponse,
  GetMarketCapabilitiesResponse,
  GetMarketIndicatorsResponse,
  GetMarketQuotesResponse,
  GetMarketIndicatorsQueryParams,
} from "../../shared/api/generated/validators";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import { EquityChart } from "./equity-chart";
import { constrainChartSettings, readChartSettings, writeChartSettings } from "./chart-settings";
import type {
  ChartSettings,
  IndicatorKind,
  IndicatorParameters,
  PriceView,
} from "./chart-settings";
import { buildComparison } from "./comparison-data";
import {
  availablePeriods,
  compactText,
  datedWindow,
  finite,
  intervalChoices,
  isIntraday,
  marketFailure,
  marketAsOfText,
  numberText,
  periodChoices,
  qualityLabels,
  signedText,
  timestampText,
} from "./market-utils";
import type { ChartZone, Interval, Period } from "./market-utils";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./market.module.css";

const indicatorChoices: { value: IndicatorKind; label: string }[] = [
  { value: "sma", label: "SMA" },
  { value: "ema", label: "EMA" },
  { value: "boll", label: "BOLL" },
  { value: "rsi", label: "RSI" },
  { value: "macd", label: "MACD" },
  { value: "atr", label: "ATR" },
];

function IndicatorParametersForm({
  parameters,
  onApply,
}: {
  parameters: IndicatorParameters;
  onApply: (parameters: IndicatorParameters) => void;
}) {
  const [form] = Form.useForm<IndicatorParameters>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    form.setFieldsValue(parameters);
  }, [form, parameters]);
  const fields: {
    key: keyof IndicatorParameters;
    label: string;
    min: number;
    max: number;
    fraction?: boolean;
  }[] = [
    { key: "sma_period", label: "SMA 周期", min: 2, max: 200 },
    { key: "ema_period", label: "EMA 周期", min: 2, max: 200 },
    { key: "rsi_period", label: "RSI 周期", min: 2, max: 200 },
    { key: "atr_period", label: "ATR 周期", min: 2, max: 200 },
    { key: "macd_fast", label: "MACD 快线", min: 2, max: 100 },
    { key: "macd_slow", label: "MACD 慢线", min: 3, max: 200 },
    { key: "macd_signal", label: "MACD 信号线", min: 2, max: 100 },
    { key: "boll_period", label: "BOLL 周期", min: 2, max: 200 },
    { key: "boll_deviation", label: "BOLL 标准差倍数", min: 0.1, max: 5, fraction: true },
  ];
  return (
    <Form
      name="chart-indicators"
      form={form}
      layout="vertical"
      initialValues={parameters}
      onFinish={(values) => {
        const checked = GetMarketIndicatorsQueryParams.safeParse({ symbol: "AAPL", ...values });
        if (!checked.success || values.macd_fast >= values.macd_slow) {
          setError("请检查参数范围；MACD 快线周期须小于慢线周期。");
          return;
        }
        setError(undefined);
        onApply(values);
      }}
    >
      {error && <Alert type="warning" title={error} />}
      <div className={styles.parameterGrid}>
        {fields.map((field) => (
          <Form.Item
            key={field.key}
            name={field.key}
            label={field.label}
            rules={[
              {
                required: true,
                type: field.fraction ? "number" : "integer",
                min: field.min,
                max: field.max,
                message: `范围 ${field.min}–${field.max}${field.fraction ? "" : "，整数"}`,
              },
            ]}
          >
            <InputNumber
              min={field.min}
              max={field.max}
              precision={field.fraction ? 2 : 0}
              step={field.fraction ? 0.1 : 1}
              style={{ width: "100%" }}
            />
          </Form.Item>
        ))}
      </div>
      <Button htmlType="submit">应用指标参数</Button>
      <p className={shared.muted}>
        应用后重新请求数值指标并写入当前链接；预热不足显示缺失。研究工具的默认算法可能不同。
      </p>
    </Form>
  );
}

export function MarketMetadata({ meta, zone }: { meta: MarketMeta; zone: ChartZone }) {
  const asOfKind =
    meta.as_of_kind === "trading_date"
      ? "交易日"
      : meta.as_of_kind === "bar_end"
        ? "K线结束"
        : "K线起始";
  const asOf = marketAsOfText(meta, zone);
  return (
    <div className={styles.metadata}>
      <span>
        来源 {meta.source} / {meta.feed}
      </span>
      <span>
        数据截至 {asOf}（{asOfKind}）
      </span>
      <span>接收 {timestampText(meta.received_at, zone)}</span>
      <span>{meta.delay_label ?? "来源未承诺实时延迟"}</span>
      <Tag color={meta.quality === "complete" ? "default" : "warning"}>
        {qualityLabels[meta.quality]} ·{" "}
        {meta.cache === "hit" ? "缓存" : meta.cache === "stale" ? "过期缓存" : "新获取"}
      </Tag>
    </div>
  );
}

export function EquityPanel({
  bootstrap,
  instrument,
  symbol,
  showSymbolSearch = true,
}: {
  bootstrap: BootstrapResponse;
  instrument?: InstrumentResponse;
  symbol: string;
  showSymbolSearch?: boolean;
}) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [symbolError, setSymbolError] = useState<string>();
  const [advanced, setAdvanced] = useState<string[]>([]);
  const capabilities = useQuery({
    queryKey: queryKeys.resource(scope, "market-capabilities"),
    queryFn: async ({ signal }) =>
      GetMarketCapabilitiesResponse.parse(await getMarketCapabilities({ signal })),
    retry: shouldRetryQuery,
    staleTime: 300_000,
  });
  const source = capabilities.data?.source,
    feed = capabilities.data?.feed;
  const rawSearch = searchParams.toString();
  const requested = useMemo(() => readChartSettings(new URLSearchParams(rawSearch)), [rawSearch]);
  const settings = useMemo(
    () => (capabilities.data ? constrainChartSettings(requested, capabilities.data) : requested),
    [requested, capabilities.data],
  );
  const { zone, view, indicators, parameters } = settings;
  const selection = {
    interval: settings.interval,
    period: settings.period,
    session: settings.session,
    adjustment: settings.adjustment,
  };
  const supported =
    !!capabilities.data?.interval_periods[settings.interval]?.includes(settings.period) &&
    capabilities.data.sessions.includes(settings.session) &&
    Object.hasOwn(capabilities.data.adjustments, settings.adjustment);
  useEffect(() => {
    if (!capabilities.data) return;
    const canonical = writeChartSettings(new URLSearchParams(rawSearch), settings);
    canonical.set("symbol", symbol);
    if (canonical.toString() !== rawSearch) setSearchParams(canonical, { replace: true });
  }, [capabilities.data, rawSearch, settings, setSearchParams, symbol]);
  function changeSettings(update: Partial<ChartSettings>) {
    const next = { ...settings, ...update };
    const constrained = capabilities.data ? constrainChartSettings(next, capabilities.data) : next;
    const search = writeChartSettings(searchParams, constrained);
    search.set("symbol", symbol);
    setSearchParams(search, { replace: true });
  }
  const params = { symbol, ...selection };
  const bars = useQuery({
    queryKey: queryKeys.resource(scope, "market-bars", { source, feed, ...params }),
    queryFn: async ({ signal }) =>
      GetMarketBarsResponse.parse(await getMarketBars(params, { signal })),
    enabled: capabilities.isSuccess && supported,
    placeholderData: (previous) =>
      previous?.symbol === symbol &&
      previous.interval === selection.interval &&
      previous.session === selection.session &&
      previous.adjustment === selection.adjustment &&
      previous.meta.source === source &&
      previous.meta.feed === feed
        ? previous
        : undefined,
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const quote = useQuery({
    queryKey: queryKeys.resource(scope, "market-quotes", { source, feed, symbol }),
    queryFn: async ({ signal }) =>
      GetMarketQuotesResponse.parse(await getMarketQuotes({ symbols: symbol }, { signal })),
    enabled: capabilities.isSuccess && supported,
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const technical = useQuery({
    queryKey: queryKeys.resource(scope, "market-indicators", {
      source,
      feed,
      ...params,
      ...parameters,
    }),
    queryFn: async ({ signal }) =>
      GetMarketIndicatorsResponse.parse(
        await getMarketIndicators({ ...params, ...parameters }, { signal }),
      ),
    enabled: capabilities.isSuccess && supported && indicators.length > 0,
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const spy = useQuery({
    queryKey: queryKeys.resource(scope, "market-bars", { source, feed, ...params, symbol: "SPY" }),
    queryFn: async ({ signal }) =>
      GetMarketBarsResponse.parse(await getMarketBars({ ...params, symbol: "SPY" }, { signal })),
    enabled: capabilities.isSuccess && supported && settings.compareSpy,
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const comparison = useMemo(
    () =>
      bars.data && spy.data && settings.compareSpy
        ? buildComparison(bars.data, spy.data)
        : undefined,
    [bars.data, spy.data, settings.compareSpy],
  );
  const recent = useQuery({
    queryKey: queryKeys.resource(scope, "recent-stock-research", {
      ticker: symbol,
      status: "completed",
      limit: 5,
    }),
    queryFn: ({ signal }) =>
      listResearchRuns({ ticker: symbol, status: "completed", limit: 5 }, { signal }),
    retry: shouldRetryQuery,
    staleTime: 30_000,
  });
  const calendarWindow = datedWindow(bootstrap.today);
  const calendar = useQuery({
    queryKey: queryKeys.resource(scope, "market-calendar", calendarWindow),
    queryFn: async ({ signal }) => {
      if (!calendarWindow) throw new Error("日期未就绪");
      return GetMarketCalendarResponse.parse(await getMarketCalendar(calendarWindow, { signal }));
    },
    enabled: !!calendarWindow,
    retry: shouldRetryQuery,
    staleTime: 60_000,
  });
  const lookup = useQuery({
    queryKey: queryKeys.resource(scope, "instrument-search", { q: symbol }),
    queryFn: ({ signal }) => searchInstruments({ q: symbol }, { signal }),
    enabled: symbol !== instrument?.symbol && !symbol.startsWith("^"),
    retry: shouldRetryQuery,
    staleTime: 300_000,
  });
  const selectedInstrument =
    instrument?.symbol === symbol
      ? instrument
      : lookup.data?.items.find((item) => item.symbol === symbol);
  const companyStock = selectedInstrument?.asset_type === "stock" && !symbol.startsWith("^");
  const currentQuote = quote.data?.quotes.find((item) => item.symbol === symbol);
  const latest = bars.data?.bars.at(-1);
  const allowed = capabilities.data ? availablePeriods(selection.interval, capabilities.data) : [];
  const marketState: Record<string, string> = {
    pre: "盘前",
    regular: "常规交易中",
    post: "盘后",
    closed: "休市",
  };

  function chooseInterval(interval: Interval) {
    changeSettings({ interval });
  }
  function refresh() {
    void capabilities.refetch();
    void bars.refetch();
    void quote.refetch();
    if (indicators.length) void technical.refetch();
    if (calendarWindow) void calendar.refetch();
    if (settings.compareSpy) void spy.refetch();
  }

  return (
    <section className={shared.panel}>
      <div className={styles.quoteHeader}>
        <div>
          <div className={styles.eyebrow}>US EQUITY · PRICE & VOLUME</div>
          <h2>
            <span className={shared.mono}>{symbol}</span>{" "}
            <small>{selectedInstrument?.name ?? "美股 / ETF / 指数"}</small>
          </h2>
          <div className={styles.quoteValue}>
            {numberText(currentQuote?.price)}
            <span
              className={
                finite(currentQuote?.change_percent)
                  ? currentQuote.change_percent >= 0
                    ? styles.positive
                    : styles.negative
                  : styles.neutral
              }
            >
              {signedText(currentQuote?.change, "")} ({signedText(currentQuote?.change_percent)})
            </span>
          </div>
          <p className={shared.muted}>
            参考价格：来源 K 线收盘字段 · 原生口径 · 非逐笔或 NBBO 报价
          </p>
        </div>
        <div className={styles.quoteControls}>
          {showSymbolSearch && (
            <Input.Search
              aria-label="查看美股行情代码"
              placeholder="代码，如 AAPL / SPY / ^VIX"
              enterButton="查看行情"
              onSearch={(value) => {
                const next = value.trim().toUpperCase();
                if (!/^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$/.test(next)) {
                  setSymbolError("请输入有效的美股、ETF 或指数代码");
                  return;
                }
                setSymbolError(undefined);
                const nextSearch = writeChartSettings(searchParams, settings);
                nextSearch.set("symbol", next);
                void navigate({ pathname: "/stocks", search: nextSearch.toString() });
              }}
            />
          )}
          <div className={shared.actions}>
            <Tag>
              {calendar.data
                ? `美股 · ${marketState[calendar.data.current_state]}`
                : "交易时段待确认"}
            </Tag>
            <Button icon={<ReloadOutlined />} onClick={refresh} loading={bars.isFetching}>
              刷新数据
            </Button>
            {companyStock && (
              <Link to={`/research/new?ticker=${encodeURIComponent(symbol)}`}>
                <Button type="primary">ThesisFlow 个股研究</Button>
              </Link>
            )}
          </div>
        </div>
      </div>
      {!companyStock && (
        <p className={shared.note}>
          当前核心个股研究面向公司股票；ETF /
          指数可查看行情与市场分析。股票身份未确认时，研究入口等待证券信息。
        </p>
      )}
      {symbolError && <Alert type="warning" title={symbolError} />}
      {capabilities.isError && (
        <Alert
          type="error"
          title={marketFailure(capabilities.error)}
          action={
            <Button
              onClick={() => {
                void capabilities.refetch();
              }}
            >
              重试数据能力
            </Button>
          }
        />
      )}
      <div className={styles.chartToolbar}>
        <Select<Interval>
          aria-label="K线周期"
          value={selection.interval}
          onChange={chooseInterval}
          options={intervalChoices.map((item) => ({
            ...item,
            disabled: !capabilities.data?.intervals.includes(item.value),
          }))}
          style={{ width: 100 }}
        />
        <Select<Period>
          aria-label="历史范围"
          value={selection.period}
          onChange={(period) => changeSettings({ period })}
          options={periodChoices.map((item) => ({
            ...item,
            disabled: !allowed.includes(item.value),
          }))}
          style={{ width: 116 }}
        />
        <Select<PriceView>
          aria-label="价格图样式"
          value={view}
          options={[
            { value: "candles", label: "蜡烛图" },
            { value: "line", label: "分时收盘线" },
          ]}
          onChange={(view) => changeSettings({ view })}
          style={{ width: 132 }}
        />
        <Select<GetMarketBarsParams["session"]>
          aria-label="交易时段"
          value={selection.session}
          disabled={!isIntraday(selection.interval)}
          options={[
            { value: "regular", label: "常规时段" },
            { value: "extended", label: "含盘前盘后" },
          ]}
          onChange={(session) => {
            if (session) changeSettings({ session });
          }}
          style={{ width: 132 }}
        />
        <Select<GetMarketBarsParams["adjustment"]>
          aria-label="价格调整口径"
          value={selection.adjustment}
          options={[
            {
              value: "provider_native",
              label: capabilities.data?.adjustments.provider_native ?? "来源原生口径",
            },
            {
              value: "dividend_adjusted",
              label: capabilities.data?.adjustments.dividend_adjusted ?? "来源股息调整",
            },
          ]}
          onChange={(adjustment) => {
            if (adjustment) changeSettings({ adjustment });
          }}
          style={{ minWidth: 180, maxWidth: 280 }}
        />
        <Select<ChartZone>
          aria-label="图表显示时区"
          value={zone}
          options={[
            { value: "America/New_York", label: "纽约 ET" },
            { value: "UTC", label: "UTC" },
          ]}
          onChange={(zone) => changeSettings({ zone })}
          style={{ width: 110 }}
        />
        <Select<IndicatorKind[]>
          aria-label="技术指标"
          mode="multiple"
          value={indicators}
          options={indicatorChoices}
          onChange={(indicators) => changeSettings({ indicators })}
          placeholder="选择指标"
          maxTagCount="responsive"
          style={{ minWidth: 220, flex: 1 }}
        />
        <Checkbox
          checked={settings.compareSpy}
          onChange={(event) => changeSettings({ compareSpy: event.target.checked })}
        >
          与 SPY 共同起点比较
        </Checkbox>
        <Button
          size="small"
          onClick={() => {
            setAdvanced(["parameters"]);
            document
              .getElementById("chart-parameters")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        >
          指标参数
        </Button>
      </div>
      {bars.isPlaceholderData && (
        <p className={shared.note}>正在加载所选历史范围；当前图仍保留此前已加载区间和缩放位置。</p>
      )}
      {((bars.isPending && supported) || capabilities.isPending) && (
        <div className={styles.chartLoading}>
          <Spin />
          <p>正在读取来源行情…</p>
        </div>
      )}
      {bars.isError && (
        <div className={shared.body}>
          <Alert
            type="error"
            showIcon
            title={marketFailure(bars.error)}
            description="未用模拟行情替代。可切换代码、缩短分钟范围或稍后刷新。"
            action={
              <Button
                onClick={() => {
                  void bars.refetch();
                }}
              >
                重试行情
              </Button>
            }
          />
        </div>
      )}
      {capabilities.isSuccess && !supported && (
        <Alert
          type="warning"
          title="当前来源没有匹配的行情周期、时段或调整口径"
          description="请刷新来源能力，或选择来源支持的组合。不会发送超出来源能力的请求。"
        />
      )}
      {quote.isError && (
        <p className={shared.note}>参考报价读取失败：{marketFailure(quote.error)}</p>
      )}
      {bars.data && (
        <>
          <div className={styles.ohlcRow}>
            <span>
              最新 K 线{" "}
              {latest
                ? isIntraday(selection.interval)
                  ? timestampText(latest.timestamp, zone)
                  : latest.date
                : "—"}
            </span>
            <span>
              O <strong>{numberText(latest?.open)}</strong>
            </span>
            <span>
              H <strong>{numberText(latest?.high)}</strong>
            </span>
            <span>
              L <strong>{numberText(latest?.low)}</strong>
            </span>
            <span>
              C <strong>{numberText(latest?.close)}</strong>
            </span>
            <span>
              VOL <strong>{compactText(latest?.volume)}</strong>
            </span>
            {latest && !latest.is_complete && <Tag color="warning">未完成 K 线</Tag>}
          </div>
          {bars.data.bars.length ? (
            <EquityChart
              data={bars.data}
              indicatorData={technical.data}
              settings={settings}
              comparison={comparison}
            />
          ) : (
            <div className={styles.chartLoading}>来源未返回此范围的数据</div>
          )}
          <MarketMetadata meta={bars.data.meta} zone={zone} />
          {bars.data.meta.warning && <p className={shared.note}>{bars.data.meta.warning}</p>}
        </>
      )}
      {settings.compareSpy && (
        <div className={shared.note}>
          {spy.isPending
            ? "正在读取同口径 SPY 数据…"
            : spy.isError
              ? `SPY 比较不可用：${marketFailure(spy.error)}`
              : (comparison?.reason ??
                (comparison
                  ? `比较覆盖 ${comparison.start} → ${comparison.end}；共同完整 K 线 ${comparison.commonCount} 根（${symbol} 有效 ${comparison.primaryCount}，SPY 有效 ${comparison.benchmarkCount}）。`
                  : "等待双方行情覆盖。"))}
          <br />
          共同起点为
          0%，独立百分比窗格比较价格变化；来源、feed、周期、时段、调整口径一致。仅共同完整 K
          线参与，缺失保留空白，不补值或计算总回报。
        </div>
      )}
      <p className={shared.note}>
        设置已写入当前链接，刷新与分享可恢复。“历史范围”仅扩展来源支持的已加载区间，最多 5,000 根 K
        线；可点击“适应已加载范围”查看全部，不提供无限左滑分页。
      </p>
      <div id="chart-parameters">
        <Collapse
          ghost
          size="small"
          activeKey={advanced}
          onChange={(keys) => setAdvanced(typeof keys === "string" ? [keys] : keys)}
          items={[
            {
              key: "parameters",
              label: `高级指标参数 · SMA ${parameters.sma_period} / EMA ${parameters.ema_period} / RSI ${parameters.rsi_period} / MACD ${parameters.macd_fast},${parameters.macd_slow},${parameters.macd_signal} / BOLL ${parameters.boll_period},${parameters.boll_deviation} / ATR ${parameters.atr_period}`,
              children: (
                <IndicatorParametersForm
                  parameters={parameters}
                  onApply={(parameters) => changeSettings({ parameters })}
                />
              ),
            },
          ]}
        />
      </div>
      {indicators.length > 0 && technical.isError && (
        <p className={shared.note}>
          指标暂不可用：{marketFailure(technical.error)}。价格图仍独立显示。
        </p>
      )}
      {technical.data && indicators.length > 0 && (
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: "methods",
              label: `技术指标计算口径 · 截至 ${marketAsOfText(technical.data.meta, zone)} · 预热不足保留缺失`,
              children: (
                <pre className={styles.methodText}>
                  {JSON.stringify(
                    { parameters: technical.data.parameters, method: technical.data.method },
                    null,
                    2,
                  )}
                </pre>
              ),
            },
          ]}
        />
      )}
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: "data",
            label: "数据来源与交易日历说明",
            children: (
              <div className={shared.stack}>
                <p className={shared.muted}>
                  {capabilities.data?.warning ?? "数据能力尚未确认"}
                  。来源原生调整口径不等同于未复权，价格差异需按调整口径比较。缺失点保留为空，未补零或插值。
                </p>
                {currentQuote && <MarketMetadata meta={currentQuote.meta} zone={zone} />}
                {calendar.isError && (
                  <p className={shared.muted}>交易日历暂不可用：{marketFailure(calendar.error)}</p>
                )}
                {calendar.data && (
                  <>
                    <p className={shared.muted}>
                      {calendar.data.source} · {calendar.data.calendar} · 状态时间{" "}
                      {timestampText(calendar.data.received_at, zone)}；NYSE
                      日历作为交易日参考，不构成具体证券交易权限。
                    </p>
                    <div className={styles.calendarRows}>
                      {calendar.data.sessions.map((day) => (
                        <div key={day.date}>
                          <span>
                            {day.date}
                            {day.is_early_close ? " · 提前收市" : ""}
                          </span>
                          <span>
                            {timestampText(day.open, zone)} → {timestampText(day.close, zone)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ),
          },
        ]}
      />
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: "recent",
            label: `${symbol} 最近已完成研究（${recent.data?.items.length ?? 0}）`,
            children: (
              <div className={shared.stack}>
                {recent.isPending && <Spin size="small" />}
                {recent.isError && (
                  <Alert
                    type="warning"
                    title={`研究记录暂不可用：${marketFailure(recent.error)}`}
                    action={
                      <Button
                        onClick={() => {
                          void recent.refetch();
                        }}
                      >
                        重试
                      </Button>
                    }
                  />
                )}
                {recent.data && !recent.data.items.length && (
                  <p className={shared.muted}>尚无已完成研究。此列表只读取历史，不会启动模型。</p>
                )}
                {recent.data?.items.map((run) => (
                  <div key={run.id} className={styles.recentResearch}>
                    <Link to={`/runs/${run.id}`}>
                      {run.ticker} · {run.date} · 查看已完成报告
                    </Link>
                    <span className={shared.muted}>
                      完成 {timestampText(run.finished_at, zone)}
                      {run.rating ? ` · ${run.rating}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}
