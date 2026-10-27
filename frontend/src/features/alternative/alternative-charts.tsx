import { useEffect, useRef } from "react";
import { init, use as registerCharts } from "echarts/core";
import type { ComposeOption } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import type { BarSeriesOption, LineSeriesOption } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import type {
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type {
  ETFFlowSeries,
  NewsSentimentResponse,
  OptionContract,
} from "../../shared/api/generated/models";
import { amount, finite, optionCurve } from "./alternative-utils";
import styles from "./alternative.module.css";

registerCharts([
  BarChart,
  LineChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);
type ChartOption = ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
>;

function Figure({ option, label }: { option: ChartOption; label: string }) {
  const container = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = init(container.current, undefined, { renderer: "canvas" });
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);
  return <figure ref={container} aria-label={label} className={styles.chart} />;
}

const base: ChartOption = {
  animation: false,
  aria: { enabled: true },
  color: ["#41b79c", "#d29f5e", "#7b9bd1", "#d47589"],
  grid: { left: 78, right: 30, top: 55, bottom: 46 },
  legend: { top: 10, textStyle: { color: "#98adbf" } },
  tooltip: {
    trigger: "axis",
    renderMode: "richText",
    backgroundColor: "#192a37",
    borderColor: "#3b5264",
    textStyle: { color: "#d0dce5" },
  },
  yAxis: {
    type: "value",
    scale: true,
    axisLabel: { color: "#8ca2b4", formatter: (value: number) => amount(value) },
    splitLine: { lineStyle: { color: "#233746" } },
  },
};

export function FlowsChart({ funds, metric }: { funds: ETFFlowSeries[]; metric: "flow" | "nav" }) {
  const days = [
    ...new Set(funds.flatMap((fund) => fund.points.map((point) => point.as_of_date ?? point.date))),
  ].sort();
  const option: ChartOption = {
    ...base,
    aria: {
      enabled: true,
      description:
        metric === "flow"
          ? "日级ETF资金流，估算和已报告值请查看来源；缺失保留断点。"
          : "发行商或供应商日级NAV，美元/份。",
    },
    xAxis: {
      type: "category",
      data: days,
      axisLabel: { color: "#8ba1b6", formatter: (value: string) => value.slice(5) },
      axisTick: { show: false },
    },
    series: funds.map((fund) => {
      const values = new Map(
        fund.points.map((point) => [
          point.as_of_date ?? point.date,
          metric === "flow" ? point.net_flow_usd : point.nav,
        ]),
      );
      return {
        name: `${fund.symbol} · ${metric === "nav" ? "NAV" : fund.meta.is_estimated ? "估算" : "供应商已报告"}`,
        type: metric === "flow" ? "bar" : "line",
        data: days.map((day) => values.get(day) ?? null),
        connectNulls: false,
        ...(metric === "flow"
          ? { barMaxWidth: 16 }
          : { showSymbol: false, lineStyle: { width: 2 } }),
      };
    }),
  };
  const hasValues = funds.some((fund) =>
    fund.points.some((point) => finite(metric === "flow" ? point.net_flow_usd : point.nav)),
  );
  return days.length && hasValues ? (
    <Figure
      option={option}
      label={metric === "flow" ? "ETF日级流量（美元）；方法见表格" : "ETF日级NAV（美元/份）"}
    />
  ) : (
    <div className={styles.empty}>所选指标没有有效数据；缺失或待核对的观察未补成0</div>
  );
}

export function OptionsChart({
  contracts,
  metric,
}: {
  contracts: OptionContract[];
  metric: "implied_volatility" | "open_interest";
}) {
  const curve = optionCurve(contracts, metric);
  const option: ChartOption = {
    ...base,
    aria: {
      enabled: true,
      description:
        metric === "implied_volatility"
          ? "所选到期日期权IV曲线；缺失保留断点，不计算Greeks。"
          : "所选到期日合约持仓量；不代表买卖方向。",
    },
    xAxis: {
      type: "category",
      name: "行权价 $",
      nameLocation: "middle",
      nameGap: 30,
      nameTextStyle: { color: "#8299ae" },
      data: curve.strikes.map(String),
      axisLabel: { color: "#8ba1b6" },
      axisTick: { show: false },
    },
    ...(metric === "implied_volatility"
      ? {
          yAxis: {
            type: "value",
            scale: true,
            axisLabel: {
              color: "#8ca2b4",
              formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
            },
            splitLine: { lineStyle: { color: "#233746" } },
          },
        }
      : {}),
    series: [
      {
        type: metric === "implied_volatility" ? "line" : "bar",
        name: "CALL",
        data: curve.calls,
        connectNulls: false,
        showSymbol: false,
      },
      {
        type: metric === "implied_volatility" ? "line" : "bar",
        name: "PUT",
        data: curve.puts,
        connectNulls: false,
        showSymbol: false,
      },
    ],
  };
  return [...curve.calls, ...curve.puts].some(finite) ? (
    <Figure option={option} label="按行权价排列的所选合约；重复行权价的调整合约不合并" />
  ) : (
    <div className={styles.empty}>此范围没有该指标的有效字段；原始合约与缺失情况见下表</div>
  );
}

export function NewsChart({ news }: { news: NewsSentimentResponse }) {
  const option: ChartOption = {
    ...base,
    grid: { left: 64, right: 30, top: 30, bottom: 45 },
    legend: { show: false },
    aria: {
      enabled: true,
      description:
        "供应商新闻样本中，按文章和ticker去重的正面、中性、负面观察数量。不是全市场投资者情绪。",
    },
    xAxis: {
      type: "category",
      data: ["正面ticker观察", "中性ticker观察", "负面ticker观察"],
      axisLabel: { color: "#90a6b8" },
      axisTick: { show: false },
    },
    series: [
      {
        name: "已分类ticker观察",
        type: "bar",
        barMaxWidth: 52,
        data: [
          { value: news.positive, itemStyle: { color: "#41b79c" } },
          { value: news.neutral, itemStyle: { color: "#7a91a5" } },
          { value: news.negative, itemStyle: { color: "#d47589" } },
        ],
      },
    ],
  };
  return <Figure option={option} label="供应商新闻样本的ticker情绪观察分布" />;
}
