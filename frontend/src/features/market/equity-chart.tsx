import { useEffect, useRef } from "react";
import { Button } from "antd";
import type { ChartInput } from "./equity-chart-controller";
import { EquityChartController } from "./equity-chart-controller";
import { isIntraday } from "./market-utils";
import styles from "./market.module.css";

export function EquityChart(input: ChartInput) {
  const { data, settings } = input;
  const container = useRef<HTMLElement>(null),
    legend = useRef<HTMLDivElement>(null);
  const controller = useRef<EquityChartController | null>(null);
  const extraPanels =
    settings.indicators.filter((value) => ["rsi", "macd", "atr"].includes(value)).length +
    (settings.compareSpy ? 1 : 0);
  useEffect(() => {
    if (!container.current) return;
    const runtime = new EquityChartController(container.current, legend.current);
    controller.current = runtime;
    return () => {
      runtime.dispose();
      controller.current = null;
    };
  }, []);
  useEffect(() => {
    controller.current?.synchronize(input);
  }, [input]);
  return (
    <>
      <div className={styles.chartLegend} ref={legend}>
        移动十字光标查看 OHLC / 成交量 · 滚轮缩放 · 拖拽平移
      </div>
      <figure
        ref={container}
        aria-label={`${data.symbol} ${data.interval}价格及成交量图，时区${settings.zone}`}
        style={{ height: 490 + extraPanels * 120 }}
        className={styles.chart}
      />
      <div className={styles.chartCaption}>
        <span>
          {data.symbol} · {data.adjustment_label} ·{" "}
          {isIntraday(data.interval)
            ? `横轴 ${settings.zone === "UTC" ? "UTC" : "纽约时间 ET（自动夏令时）"}`
            : "横轴为交易日"}{" "}
          · 涨绿跌红
        </span>
        <Button size="small" onClick={() => controller.current?.fitContent()}>
          适应已加载范围
        </Button>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts™
        </a>
      </div>
    </>
  );
}
