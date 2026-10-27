import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Form,
  InputNumber,
  Popconfirm,
  Spin,
  Table,
  Tag,
  message,
} from "antd";
import { DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  applyDataSettings,
  clearMarketCache,
  getDataSettings,
  getDataStatus,
  updateDataSettings,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  DataCacheClearResponse,
  DataRuntimeSettings,
  DataSettingsResponse,
  LegacyDataSourceStatus,
  MarketCacheStat,
} from "../../shared/api/generated/models";
import {
  ApplyDataSettingsResponse,
  ClearMarketCacheResponse,
  GetDataSettingsResponse,
  GetDataStatusResponse,
  UpdateDataSettingsBody,
  UpdateDataSettingsResponse,
} from "../../shared/api/generated/validators";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { compactText, marketFailure, timestampText } from "../market/market-utils";
import styles from "../../shared/ui/workbench.module.css";

const activationLabels = {
  active: "已生效",
  saved_pending: "已保存，等待应用",
  restart_required: "需重新应用或重启服务",
};
const numericFields: {
  name: keyof DataRuntimeSettings;
  label: string;
  min: number;
  max: number;
  detail: string;
}[] = [
  {
    name: "minute_cache_ttl_seconds",
    label: "分钟行情缓存时效（秒）",
    min: 5,
    max: 300,
    detail: "影响分钟与小时 K 线复用时长，不代表来源实时延迟。",
  },
  {
    name: "daily_cache_ttl_seconds",
    label: "日级行情缓存时效（秒）",
    min: 30,
    max: 3600,
    detail: "影响日、周、月 K 线复用时长；历史数据仍受来源覆盖约束。",
  },
  {
    name: "aggregation_max_workers",
    label: "市场聚合最大并发",
    min: 1,
    max: 8,
    detail: "提高并发可能增加供应商请求压力；个股长研究使用独立执行通道。",
  },
  {
    name: "aggregation_timeout_seconds",
    label: "市场聚合单次超时（秒）",
    min: 5,
    max: 60,
    detail: "超时来源按缺失或部分数据展示，不以零代替。",
  },
  {
    name: "aggregation_refresh_seconds",
    label: "市场快照缓存时效（秒）",
    min: 30,
    max: 3600,
    detail: "控制日级聚合快照复用，不会把已完成交易日数据变成实时行情。",
  },
];
const cacheLabels: Record<MarketCacheStat["id"], string> = {
  market_bars: "可重建行情 K 线",
  frozen_market_snapshots: "冻结市场快照与证据",
};
const retainedLabels: Record<DataCacheClearResponse["retained"][number], string> = {
  frozen_market_snapshots: "冻结市场快照与证据",
  research_artifacts: "研究报告与任务记录",
  credentials: "凭据",
  provider_session: "供应商会话",
};

export function DataSettingsPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    cache = useQueryClient();
  const [form] = Form.useForm<DataRuntimeSettings>();
  const [notices, noticeContext] = message.useMessage();
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [clearResult, setClearResult] = useState<DataCacheClearResponse>();
  const settings = useQuery({
    queryKey: queryKeys.resource(scope, "data-settings"),
    queryFn: async ({ signal }) => GetDataSettingsResponse.parse(await getDataSettings({ signal })),
    retry: shouldRetryQuery,
  });
  const status = useQuery({
    queryKey: queryKeys.resource(scope, "data-status"),
    queryFn: async ({ signal }) => GetDataStatusResponse.parse(await getDataStatus({ signal })),
    retry: shouldRetryQuery,
    refetchInterval: (query) => (query.state.data?.activation === "active" ? false : 5000),
  });
  const activation = status.data?.activation ?? settings.data?.activation;
  const provider = status.data?.market_provider;

  useEffect(() => {
    if (!settings.data) return;
    form.resetFields();
    form.setFieldsValue(settings.data.settings);
  }, [form, settings.data]);

  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "data-settings") }),
      cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "data-status") }),
    ]);
  }
  async function refreshMarket() {
    await Promise.all(
      [
        "market-capabilities",
        "market-bars",
        "market-quotes",
        "market-indicators",
        "market-pulse",
      ].map((resource) =>
        cache.invalidateQueries({ queryKey: queryKeys.resource(scope, resource) }),
      ),
    );
  }
  async function act(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError(undefined);
    try {
      await action();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "配置已被其他页面修改，已刷新最新版本；请检查新值后重新保存。"
          : marketFailure(failure),
      );
      if (failure instanceof ApiError && failure.status === 409) await refresh();
    } finally {
      setBusy(undefined);
    }
  }
  async function accept(response: DataSettingsResponse) {
    cache.setQueryData(queryKeys.resource(scope, "data-settings"), response);
    await refresh();
    if (response.activation === "active") await refreshMarket();
    void notices.success(
      response.activation === "active" ? "数据设置已保存并生效" : "数据设置已保存，请检查应用状态",
    );
  }
  function save(values: DataRuntimeSettings) {
    if (!settings.data) return;
    const revision = settings.data.revision;
    void act("save", async () => {
      const body = UpdateDataSettingsBody.parse({ revision, settings: values });
      await accept(UpdateDataSettingsResponse.parse(await updateDataSettings(body)));
    });
  }

  return (
    <div className={styles.stack}>
      {noticeContext}
      <div className={styles.heading}>
        <small>DATA CENTER · SOURCE & QUALITY</small>
        <h1>数据中心</h1>
        <p>管理本机行情缓存与市场聚合参数，查看实际来源能力、环境配置存在性和缓存统计。</p>
      </div>
      {(error || settings.isError || status.isError) && (
        <Alert
          showIcon
          type="error"
          title={error ?? marketFailure(settings.error ?? status.error)}
          action={
            <Button
              onClick={() => {
                setError(undefined);
                void refresh();
              }}
            >
              重新读取
            </Button>
          }
        />
      )}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>运行参数</h2>
          <div className={styles.actions}>
            <Tag color={activation === "active" ? "success" : "warning"}>
              {activation ? activationLabels[activation] : "状态待确认"}
            </Tag>
            <Button
              icon={<ReloadOutlined />}
              disabled={!!busy}
              onClick={() => {
                void refresh();
              }}
            >
              刷新设置
            </Button>
          </div>
        </div>
        <div className={styles.body}>
          {settings.isPending && <Spin />}
          {settings.data && (
            <>
              <p className={styles.muted}>
                作用域：本机工作区 · 行情来源 Yahoo · 设置版本 {settings.data.revision} · 更新{" "}
                {timestampText(settings.data.updated_at, "America/New_York")}
              </p>
              {activation && activation !== "active" && (
                <Alert
                  showIcon
                  type="warning"
                  title="保存的设置尚未确认生效"
                  description="可重新应用已保存版本。若仍无法应用，请检查服务日志与运行依赖，必要时重启本机服务。"
                  action={
                    <Button
                      loading={busy === "apply"}
                      disabled={!!busy}
                      onClick={() => {
                        void act("apply", async () =>
                          accept(ApplyDataSettingsResponse.parse(await applyDataSettings())),
                        );
                      }}
                    >
                      应用已保存设置
                    </Button>
                  }
                />
              )}
              <Form
                name="data-settings"
                form={form}
                layout="vertical"
                onFinish={save}
                disabled={!!busy}
                style={{ marginTop: 20 }}
              >
                <div className={styles.columns}>
                  {numericFields.map((field) => (
                    <Form.Item
                      key={field.name}
                      name={field.name}
                      label={field.label}
                      extra={`${field.min}–${field.max}，整数。${field.detail}`}
                      rules={[
                        {
                          required: true,
                          type: "integer",
                          min: field.min,
                          max: field.max,
                          message: `请输入 ${field.min}–${field.max} 的整数`,
                        },
                      ]}
                    >
                      <InputNumber
                        min={field.min}
                        max={field.max}
                        precision={0}
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  ))}
                </div>
                <Button type="primary" htmlType="submit" loading={busy === "save"}>
                  保存并应用数据设置
                </Button>
              </Form>
            </>
          )}
        </div>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>当前行情来源与能力</h2>
          <Tag color={status.data?.market_provider_available ? "success" : "warning"}>
            {status.data?.market_provider_available ? "供应商适配器已初始化" : "适配器未就绪"}
          </Tag>
        </div>
        <div className={styles.body}>
          {status.isPending && <Spin />}
          {provider ? (
            <>
              <div className={styles.summary}>
                <div>
                  <small>来源 / Feed</small>
                  <strong>
                    {provider.source} / {provider.feed}
                  </strong>
                </div>
                <div>
                  <small>价格性质</small>
                  <strong>K 线收盘字段</strong>
                </div>
                <div>
                  <small>实时 / NBBO</small>
                  <strong>未提供保证 / 未提供</strong>
                </div>
              </div>
              <p className={styles.muted}>
                {provider.warning}。适配器初始化不代表每个证券或每次网络请求都已验证。
              </p>
              <Collapse
                ghost
                size="small"
                items={[
                  {
                    key: "capability",
                    label: "查看周期、时段与调整口径",
                    children: (
                      <div className={styles.stack}>
                        <p className={styles.muted}>
                          周期：{provider.intervals.join(" · ")}
                          <br />
                          时段：{provider.sessions.join(" · ")}
                          <br />
                          调整口径：
                          {Object.entries(provider.adjustments)
                            .map(([key, value]) => `${key}：${value}`)
                            .join("；")}
                        </p>
                        <Table<{ interval: string; periods: string[] }>
                          rowKey="interval"
                          size="small"
                          pagination={false}
                          dataSource={Object.entries(provider.interval_periods).map(
                            ([interval, periods]) => ({ interval, periods }),
                          )}
                          columns={[
                            { title: "K 线周期", dataIndex: "interval" },
                            {
                              title: "可用历史窗口",
                              key: "periods",
                              render: (_, row) => row.periods.join(" · "),
                            },
                          ]}
                        />
                      </div>
                    ),
                  },
                ]}
              />
            </>
          ) : (
            !status.isPending && (
              <p className={styles.muted}>
                行情供应商尚未初始化。配置仍可保存，运行状态会显示是否需要重新应用。
              </p>
            )
          )}
          {status.data && (
            <p className={styles.muted}>
              状态读取 {timestampText(status.data.received_at, "America/New_York")} · 设置版本{" "}
              {status.data.settings_revision}
            </p>
          )}
        </div>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>兼容环境中的研究数据配置</h2>
          <Tag>环境存在性 · 未验证连接</Tag>
          <Link to="/settings/sources">配置研究数据连接</Link>
        </div>
        <div className={styles.table}>
          <Table<LegacyDataSourceStatus>
            rowKey="id"
            size="small"
            pagination={false}
            loading={status.isPending}
            dataSource={status.data?.legacy_sources ?? []}
            columns={[
              { title: "数据来源", dataIndex: "name" },
              {
                title: "环境凭据",
                key: "configured",
                render: (_, item) => <Tag>{item.environment_configured ? "已配置" : "未配置"}</Tag>,
              },
              { title: "用途", dataIndex: "purpose" },
              {
                title: "接入范围",
                key: "integration",
                render: () => "原有研究工具；非当前行情 feed",
              },
            ]}
          />
        </div>
        <p className={styles.note}>
          此表仅表示兼容环境配置的存在性，不能代替“数据源账号”中的连接状态。 FRED / Alpha Vantage /
          TypeSafe Jev / SEC EDGAR 可在数据源账号中配置并单独检测。 当前行情图仍使用 Yahoo 来源。
        </p>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>应用拥有的缓存</h2>
          <Popconfirm
            title="清理可重建行情 K 线缓存？"
            description="将保留冻结快照与证据、研究报告、凭据和供应商会话。下次读取行情会重新请求来源。"
            onConfirm={() => {
              void act("clear", async () => {
                const response = ClearMarketCacheResponse.parse(await clearMarketCache());
                setClearResult(response);
                await refresh();
                await refreshMarket();
              });
            }}
          >
            <Button danger icon={<DeleteOutlined />} disabled={!!busy} loading={busy === "clear"}>
              清理行情缓存
            </Button>
          </Popconfirm>
        </div>
        <div className={styles.table}>
          <Table<MarketCacheStat>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={status.data?.cache ?? []}
            columns={[
              { title: "缓存类型", key: "name", render: (_, item) => cacheLabels[item.id] },
              {
                title: "文件数",
                key: "count",
                render: (_, item) => item.file_count.toLocaleString("en-US"),
              },
              { title: "大小", key: "bytes", render: (_, item) => `${compactText(item.bytes)} B` },
              {
                title: "清理范围",
                key: "clear",
                render: (_, item) =>
                  `${item.clearable ? "可重建，允许清理" : "冻结证据，保留"}${item.truncated ? " · 扫描已达上限，统计可能不完整" : ""}`,
              },
            ]}
          />
        </div>
        {clearResult && (
          <div className={styles.body}>
            <Alert
              type={clearResult.failed_file_count ? "warning" : "success"}
              showIcon
              title={`已删除 ${clearResult.removed_file_count} 个行情缓存文件（${compactText(clearResult.removed_bytes)} B），失败 ${clearResult.failed_file_count} 个`}
              description={`保留：${clearResult.retained.map((item) => retainedLabels[item]).join("、")}。${clearResult.in_memory_invalidated ? "内存行情缓存已失效。" : "内存失效状态未确认。"}清理时间 ${timestampText(clearResult.cleared_at, "America/New_York")}`}
            />
          </div>
        )}
      </section>
      {!!status.data?.notices.length && (
        <Alert
          type="info"
          showIcon
          title="数据中心说明"
          description={
            <ul>
              {status.data.notices.map((notice) => (
                <li key={notice}>{notice}</li>
              ))}
            </ul>
          }
        />
      )}
    </div>
  );
}
