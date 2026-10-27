import { useEffect, useRef } from "react";
import { init, use as registerCharts } from "echarts/core";
import { BarChart, HeatmapChart } from "echarts/charts";
import type { BarSeriesOption, HeatmapSeriesOption } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import type {
  GridComponentOption,
  TooltipComponentOption,
  VisualMapComponentOption,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ComposeOption } from "echarts/core";
import type { SectorMetric } from "../../shared/api/generated/models";
import { finite, sectorHeatData, signedText } from "./market-utils";
import styles from "./market.module.css";

registerCharts([
  BarChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  AriaComponent,
  CanvasRenderer,
]);
type SectorChartOption = ComposeOption<
  | BarSeriesOption
  | HeatmapSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | VisualMapComponentOption
>;

export function SectorCharts({
  sectors,
  mode,
}: {
  sectors: SectorMetric[];
  mode: "heatmap" | "relative";
}) {
  const container = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = init(container.current, undefined, { renderer: "canvas" });
    const relative = sectors
      .filter((item) => finite(item.relative_return_pp))
      .sort((a, b) => (a.relative_return_pp ?? 0) - (b.relative_return_pp ?? 0));
    const heat = sectorHeatData(sectors);
    const maximum = Math.max(1, ...heat.map((item) => Math.abs(item.value[0] ?? 0)));
    const option: SectorChartOption =
      mode === "heatmap"
        ? {
            animation: false,
            aria: {
              enabled: true,
              description: "行业ETF价格收益热力图，等面积色块仅表达收益，不代表市值或资金净流入。",
            },
            grid: { left: 8, right: 8, top: 8, bottom: 54 },
            xAxis: {
              type: "category",
              data: ["1", "2", "3", "4"],
              show: false,
              splitArea: { show: true },
            },
            yAxis: { type: "category", data: ["1", "2", "3"], show: false, inverse: true },
            tooltip: {
              trigger: "item",
              renderMode: "richText",
              backgroundColor: "#182330",
              borderColor: "#354654",
              textStyle: { color: "#c3d0db" },
              formatter: (params) => {
                const item = Array.isArray(params) ? params[0] : params;
                const sector = heat[item?.dataIndex ?? -1]?.sector;
                return sector
                  ? `${sector.name} · ${sector.symbol}\n价格收益 ${signedText(sector.return_percent)}\n相对SPY ${signedText(sector.relative_return_pp, " pp")}\n截至 ${sector.as_of ?? "—"}`
                  : "数据不可用";
              },
            },
            visualMap: {
              min: -maximum,
              max: maximum,
              calculable: false,
              orient: "horizontal",
              left: "center",
              bottom: 6,
              precision: 2,
              text: ["强", "弱"],
              textStyle: { color: "#8196aa" },
              inRange: { color: ["#9b3f54", "#263746", "#218776"] },
            },
            series: [
              {
                type: "heatmap",
                data: heat.map((item, index) => ({
                  name: item.name,
                  value: [index % 4, Math.floor(index / 4), item.value[0] ?? 0],
                })),
                label: {
                  show: true,
                  color: "#e3edf4",
                  fontSize: 12,
                  lineHeight: 21,
                  formatter: (params) => {
                    const sector = heat[params.dataIndex]?.sector;
                    return sector
                      ? `${sector.symbol} · ${sector.name}\n${signedText(sector.return_percent)}`
                      : "—";
                  },
                },
                itemStyle: { borderColor: "#111923", borderWidth: 4, borderRadius: 5 },
                emphasis: { itemStyle: { borderColor: "#8194a6", borderWidth: 2 } },
              },
            ],
          }
        : {
            animation: false,
            aria: { enabled: true, description: "行业ETF相对SPY价格收益差，单位为百分点。" },
            grid: { left: 58, right: 72, top: 18, bottom: 38 },
            xAxis: {
              type: "value",
              axisLabel: { color: "#7e92a7", formatter: "{value} pp" },
              splitLine: { lineStyle: { color: "#243340" } },
            },
            yAxis: {
              type: "category",
              data: relative.map((sector) => sector.symbol),
              axisLabel: { color: "#a9b9c8" },
              axisLine: { show: false },
              axisTick: { show: false },
            },
            tooltip: {
              trigger: "axis",
              renderMode: "richText",
              backgroundColor: "#182330",
              borderColor: "#354654",
              textStyle: { color: "#c3d0db" },
              formatter: (params) => {
                const item = Array.isArray(params) ? params[0] : params;
                const sector = relative[item?.dataIndex ?? -1];
                return sector
                  ? `${sector.name} · ${sector.symbol}\n相对SPY ${signedText(sector.relative_return_pp, " pp")}\n同一来源 / feed / 共同截止日`
                  : "数据不可用";
              },
            },
            series: [
              {
                type: "bar",
                barMaxWidth: 18,
                label: {
                  show: true,
                  position: "right",
                  color: "#abbcc9",
                  formatter: (params) =>
                    signedText(relative[params.dataIndex]?.relative_return_pp, " pp"),
                },
                data: relative.map((sector) => ({
                  value: sector.relative_return_pp,
                  itemStyle: {
                    color: (sector.relative_return_pp ?? 0) >= 0 ? "#2a9d87" : "#c95b71",
                    borderRadius: 2,
                  },
                })),
              },
            ],
          };
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [sectors, mode]);
  const count = sectors.filter((sector) =>
    finite(mode === "heatmap" ? sector.return_percent : sector.relative_return_pp),
  ).length;
  return count ? (
    <figure
      ref={container}
      aria-label={mode === "heatmap" ? "行业ETF收益热力图" : "行业ETF相对SPY收益差图"}
      className={styles.sectorChart}
    />
  ) : (
    <div className={styles.chartLoading}>来源未返回可用于此图的板块数据</div>
  );
}
