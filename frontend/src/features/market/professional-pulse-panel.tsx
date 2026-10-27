import { useState } from "react";
import { Alert, Button, Checkbox, Collapse, Form, Input, Select, Spin, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getProfessionalMarketPulse, listDataConnections } from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  GetProfessionalMarketPulseParams,
  ProfessionalPulseMeta,
} from "../../shared/api/generated/models";
import { GetProfessionalMarketPulseResponse } from "../../shared/api/generated/validators";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import { professionalPulseParameters } from "./professional-pulse-params";
import type { ProfessionalPulseSelection } from "./professional-pulse-params";
import { compactText, marketFailure, numberText, signedText, timestampText } from "./market-utils";
import shared from "../../shared/ui/workbench.module.css";

const availability: Record<string, string> = {
  disabled: "未启用",
  available: "已有样本",
  configuration_required: "需要配置账号",
  unavailable: "数据不可用",
  error: "读取失败",
};
const deltaLabels: Record<string, string> = {
  put_call_volume_ratio: "成交量比",
  put_call_oi_ratio: "持仓比",
  contract_count: "合约样本数",
  latest_flow_usd: "最新日流量",
  available_points_sum_usd: "窗口有效样本合计",
  sentiment_balance: "新闻正负余额",
  article_count: "文章数",
  positive: "正面观察数",
  negative: "负面观察数",
  neutral: "中性观察数",
};

function PulseMetadata({ meta }: { meta: ProfessionalPulseMeta }) {
  return (
    <Collapse
      size="small"
      ghost
      items={[
        {
          key: "meta",
          label: `${availability[meta.availability]} · ${meta.source} / ${meta.feed ?? "feed未知"} · ${meta.as_of ?? "数据时点未知"}`,
          children: (
            <>
              <p>
                数据质量 {meta.quality} · 来源接收{" "}
                {timestampText(meta.received_at, "America/New_York")}
              </p>
              <p>{meta.scope}</p>
              <p>{meta.method}</p>
              <p>
                覆盖：
                {Object.entries(meta.coverage ?? {})
                  .map(([key, value]) => `${key}=${value ?? "未知"}`)
                  .join(" · ") || "未知"}
              </p>
              {meta.warnings?.map((warning) => (
                <p key={warning} className={shared.muted}>
                  {warning}
                </p>
              ))}
              <p>
                本机基线：
                {meta.baseline.status === "available"
                  ? `已找到不同数据时点 ${meta.baseline.as_of}`
                  : "未知"}
                。{meta.baseline.reason}
              </p>
              {meta.baseline.status === "available" && (
                <p>
                  {Object.entries(meta.baseline.deltas ?? {})
                    .map(([key, value]) => `${deltaLabels[key] ?? key}差 ${signedText(value, "")}`)
                    .join(" · ")}
                </p>
              )}
            </>
          ),
        },
      ]}
    />
  );
}

export function ProfessionalPulsePanel({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [form] = Form.useForm<ProfessionalPulseSelection>();
  const [selection, setSelection] = useState<GetProfessionalMarketPulseParams>();
  const [error, setError] = useState<string>();
  const optionsProvider = Form.useWatch("optionsProvider", form) ?? "yahoo";
  const flowsProvider = Form.useWatch("flowsProvider", form) ?? "ishares";
  const includeNews = Form.useWatch("includeNews", form) ?? false;
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "data-connections"),
    queryFn: ({ signal }) => listDataConnections({ signal }),
    retry: shouldRetryQuery,
  });
  const pulse = useQuery({
    queryKey: queryKeys.resource(scope, "professional-market-pulse", selection ?? {}),
    queryFn: async ({ signal }) =>
      GetProfessionalMarketPulseResponse.parse(
        await getProfessionalMarketPulse(selection, { signal }),
      ),
    enabled: selection !== undefined,
    retry: shouldRetryQuery,
    staleTime: 30_000,
  });
  const data = pulse.data;
  function accounts(provider: string) {
    return (connections.data?.connections ?? [])
      .filter((row) => row.provider === provider)
      .map((row) => ({
        value: row.id,
        label: `${row.name}${row.enabled ? "" : "（停用）"}`,
        disabled: !row.enabled,
      }));
  }
  function load(values: ProfessionalPulseSelection) {
    try {
      const parameters = professionalPulseParameters(values);
      setError(undefined);
      setSelection(parameters);
      if (JSON.stringify(parameters) === JSON.stringify(selection)) void pulse.refetch();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "样本选择无效");
    }
  }
  return (
    <section className={shared.panel}>
      <div className={shared.toolbar}>
        <h2>专业维度 · 显式观察样本</h2>
        <Link to="/settings/sources">配置数据账号</Link>
      </div>
      <div className={shared.body}>
        <p className={shared.muted}>
          期权、ETF份额流和新闻样本独立展示。不会默认选择股票，也不会因为浏览页面读取账号数据；填写后点击读取。已有账号的额度及费用由来源套餐决定。
        </p>
        <Form
          form={form}
          layout="vertical"
          onFinish={load}
          initialValues={{
            options: "",
            etfs: "",
            news: "",
            includeNews: false,
            optionsProvider: "yahoo",
            flowsProvider: "ishares",
            lookbackDays: 30,
            lookbackHours: 24,
          }}
        >
          <div className={shared.row}>
            <Form.Item name="options" label="期权样本（最多5个）">
              <Input placeholder="填写美股代码，以英文逗号分隔" />
            </Form.Item>
            <Form.Item name="optionsProvider" label="期权来源">
              <Select
                style={{ width: 170 }}
                onChange={() => form.setFieldValue("optionsConnection", undefined)}
                options={[
                  { value: "yahoo", label: "Yahoo公开快照" },
                  { value: "massive", label: "Massive已有账号" },
                  { value: "alpaca", label: "Alpaca已有账号" },
                ]}
              />
            </Form.Item>
            {optionsProvider !== "yahoo" && (
              <Form.Item name="optionsConnection" label="期权账号">
                <Select
                  allowClear
                  placeholder="最近已启用连接"
                  style={{ width: 180 }}
                  options={accounts(optionsProvider)}
                />
              </Form.Item>
            )}
          </div>
          <div className={shared.row}>
            <Form.Item name="etfs" label="ETF份额流样本（最多10个）">
              <Input placeholder="明确填写已适配ETF代码" />
            </Form.Item>
            <Form.Item name="flowsProvider" label="份额流来源">
              <Select
                style={{ width: 170 }}
                onChange={() => form.setFieldValue("flowsConnection", undefined)}
                options={[
                  { value: "ishares", label: "iShares份额估算" },
                  { value: "intrinio", label: "Intrinio已有权限" },
                ]}
              />
            </Form.Item>
            {flowsProvider !== "ishares" && (
              <Form.Item name="flowsConnection" label="份额流账号">
                <Select
                  allowClear
                  placeholder="最近已启用连接"
                  style={{ width: 180 }}
                  options={accounts(flowsProvider)}
                />
              </Form.Item>
            )}
            <Form.Item name="lookbackDays" label="份额流滚动窗口">
              <Select
                style={{ width: 120 }}
                options={[7, 30, 90].map((value) => ({ value, label: `${value}日` }))}
              />
            </Form.Item>
          </div>
          <div className={shared.row}>
            <Form.Item name="includeNews" valuePropName="checked">
              <Checkbox>显式启用Massive新闻样本</Checkbox>
            </Form.Item>
            {includeNews && (
              <>
                <Form.Item name="news" label="新闻股票（留空为供应商广泛样本）">
                  <Input placeholder="留空不代表全媒体/全市场覆盖" />
                </Form.Item>
                <Form.Item name="newsConnection" label="新闻账号">
                  <Select
                    allowClear
                    placeholder="最近已启用连接"
                    style={{ width: 180 }}
                    options={accounts("massive")}
                  />
                </Form.Item>
                <Form.Item name="lookbackHours" label="新闻滚动窗口">
                  <Select
                    style={{ width: 120 }}
                    options={[6, 24, 72, 168].map((value) => ({ value, label: `${value}小时` }))}
                  />
                </Form.Item>
              </>
            )}
          </div>
          <div className={shared.actions}>
            <Button type="primary" htmlType="submit" loading={pulse.isFetching}>
              读取所选样本（不调用模型）
            </Button>
            <Button
              disabled={!selection}
              loading={pulse.isFetching}
              onClick={() => {
                if (selection) setSelection({ ...selection, refresh: true });
                if (selection?.refresh) void pulse.refetch();
              }}
            >
              刷新已提交样本
            </Button>
          </div>
        </Form>
        {error && <Alert type="warning" title={error} />}
        {connections.isError && (
          <Alert
            type="warning"
            title="账号列表暂不可读，已有账号选择维持未知；可重试数据连接设置。"
          />
        )}
        {pulse.isError && <Alert type="error" title={marketFailure(pulse.error)} />}
        {pulse.isFetching && <Spin size="small" />}
        {!selection && (
          <p className={shared.muted}>三个维度均未启用，尚未请求期权、份额流或新闻来源。</p>
        )}
        {data && (
          <>
            <p className={shared.muted}>
              以下为上次提交样本；表单修改后须再次读取。聚合接收{" "}
              {timestampText(data.received_at, "America/New_York")}
            </p>
            <h3>
              期权结构 <Tag>{availability[data.options_availability]}</Tag>
            </h3>
            {!!data.options.length && (
              <div className={shared.table}>
                <Table
                  size="small"
                  pagination={false}
                  rowKey="symbol"
                  dataSource={data.options}
                  columns={[
                    { title: "标的", dataIndex: "symbol" },
                    { title: "返回合约", render: (_, row) => numberText(row.contract_count, 0) },
                    { title: "到期日数", render: (_, row) => numberText(row.expiration_count, 0) },
                    {
                      title: "Put/Call 成交量比",
                      render: (_, row) => numberText(row.put_call_volume_ratio),
                    },
                    {
                      title: "Put/Call 持仓比",
                      render: (_, row) => numberText(row.put_call_oi_ratio),
                    },
                  ]}
                />
              </div>
            )}
            {data.options.map((row) => (
              <PulseMetadata key={row.symbol} meta={row.meta} />
            ))}
            <h3>
              ETF份额流 <Tag>{availability[data.flows_availability]}</Tag>
            </h3>
            {!!data.etf_flows.length && (
              <div className={shared.table}>
                <Table
                  size="small"
                  pagination={false}
                  rowKey="symbol"
                  dataSource={data.etf_flows}
                  columns={[
                    { title: "ETF", dataIndex: "symbol" },
                    {
                      title: "口径",
                      render: (_, row) =>
                        row.flow_kind === "estimated"
                          ? "份额变化估算"
                          : row.flow_kind === "vendor_reported"
                            ? "供应商报告值"
                            : "未知/混合",
                    },
                    {
                      title: "最新日（USD）",
                      render: (_, row) => compactText(row.latest_flow_usd),
                    },
                    {
                      title: "有效点合计（USD）",
                      render: (_, row) => compactText(row.available_points_sum_usd),
                    },
                    {
                      title: "有效/返回点",
                      render: (_, row) =>
                        `${numberText(row.valid_point_count, 0)}/${numberText(row.point_count, 0)}`,
                    },
                  ]}
                />
              </div>
            )}
            {data.etf_flows.map((row) => (
              <PulseMetadata key={row.symbol} meta={row.meta} />
            ))}
            <h3>
              新闻样本 <Tag>{availability[data.news_availability]}</Tag>
            </h3>
            {data.news && (
              <>
                <p>
                  {data.news.symbol ?? "供应商广泛样本"} · 文章{" "}
                  {numberText(data.news.article_count, 0)} · 发布方{" "}
                  {numberText(data.news.publisher_count, 0)} · 有评分ticker{" "}
                  {numberText(data.news.ticker_count, 0)}
                </p>
                <p>
                  正面 {numberText(data.news.positive, 0)} / 负面{" "}
                  {numberText(data.news.negative, 0)} / 中性 {numberText(data.news.neutral, 0)} ·
                  未分类文章 {numberText(data.news.unclassified_articles, 0)} · 正负余额{" "}
                  {numberText(data.news.sentiment_balance)}
                </p>
                <PulseMetadata meta={data.news.meta} />
              </>
            )}
            <p className={shared.muted}>{data.scope_notice}</p>
            <p className={shared.muted}>
              当前市场简报只解释上方价量快照；此处样本不会自动混入简报或个股研究。单标的完整数据及显式附加入口：
              <Link to="/insights/options">期权</Link> · <Link to="/insights/flows">ETF份额流</Link>{" "}
              · <Link to="/insights/news">新闻</Link>。
            </p>
          </>
        )}
      </div>
    </section>
  );
}
