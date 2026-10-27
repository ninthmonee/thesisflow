import { useState } from "react";
import { Alert, Button, Form, Input, Modal, Select, Switch, Table, Tag } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  createDataConnection,
  getAlternativeCapabilities,
  listDataConnections,
  testDataConnection,
  updateDataConnection,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  DataConnectionCreate,
  DataConnectionView,
  DataConnectionTestRequest,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { publicLink } from "../themes/theme-utils";
import styles from "../../shared/ui/workbench.module.css";
import { ResearchDataConnectionsPanel } from "./research-data-connections-panel";

type ConnectionFields = Omit<DataConnectionCreate, "api_key"> & { api_key?: string };
const names = {
  massive: "Massive",
  intrinio: "Intrinio / ETF Global",
  alpaca: "Alpaca Market Data",
};
function failure(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "数据连接操作失败";
}

export function DataConnectionsPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    cache = useQueryClient();
  const [editor, setEditor] = useState<{ original?: DataConnectionView }>();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>(),
    [notice, setNotice] = useState<string>();
  const [form] = Form.useForm<ConnectionFields>();
  const selectedProvider = Form.useWatch("provider", form);
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "data-connections"),
    queryFn: ({ signal }) => listDataConnections({ signal }),
    retry: shouldRetryQuery,
  });
  const capabilities = useQuery({
    queryKey: queryKeys.resource(scope, "alternative-capabilities"),
    queryFn: ({ signal }) => getAlternativeCapabilities({ signal }),
    retry: shouldRetryQuery,
  });
  async function refresh() {
    await cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "data-connections") });
    await cache.invalidateQueries({
      queryKey: queryKeys.resource(scope, "alternative-capabilities"),
    });
  }
  function openEditor(original?: DataConnectionView) {
    setError(undefined);
    form.resetFields();
    form.setFieldsValue({
      name: original?.name ?? "",
      provider: original?.provider ?? "massive",
      feed: original?.feed === "opra" ? "opra" : "indicative",
      enabled: original?.enabled ?? true,
    });
    setEditor({ original });
  }
  async function save(values: ConnectionFields) {
    if (!editor) return;
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      if (editor.original) {
        await updateDataConnection(editor.original.id, {
          name: values.name,
          enabled: values.enabled,
          feed: values.feed,
          revision: editor.original.revision,
          ...(values.api_key?.trim() ? { api_key: values.api_key.trim() } : {}),
          ...(values.api_secret?.trim() ? { api_secret: values.api_secret.trim() } : {}),
        });
      } else await createDataConnection({ ...values, api_key: values.api_key?.trim() ?? "" });
      setEditor(undefined);
      setNotice("连接已保存；各数据能力需要分别检测。");
      await refresh();
    } catch (problem) {
      setError(failure(problem));
      if (problem instanceof ApiError && problem.status === 409) {
        setEditor(undefined);
        await refresh();
      }
    } finally {
      form.setFieldsValue({ api_key: undefined, api_secret: undefined });
      setBusy(false);
    }
  }
  async function test(
    connection: DataConnectionView,
    capability: DataConnectionTestRequest["capability"],
  ) {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const result = await testDataConnection(connection.id, {
        revision: connection.revision,
        capability,
        symbol: capability === "etf_flows" ? "SOXX" : "NVDA",
      });
      if (result.success) setNotice(result.message);
      else setError(result.message);
      await refresh();
    } catch (problem) {
      setError(failure(problem));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={styles.stack}>
      <div className={styles.heading}>
        <small>DATA CONNECTIONS · FREE FIRST</small>
        <h1>数据源与账号</h1>
        <p>
          先使用免费公开源，或接入你已有的数据账号。保存连接不订阅付费方案，也不扩大账号的数据权限。
        </p>
      </div>
      {(error || connections.isError) && (
        <Alert type="error" showIcon title={error ?? failure(connections.error)} />
      )}
      {notice && <Alert type="success" showIcon title={notice} />}
      <ResearchDataConnectionsPanel bootstrap={bootstrap} />
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>当前来源能力</h2>
          <Link to="/insights/flows">打开专业数据</Link>
        </div>
        <div className={styles.table}>
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            loading={capabilities.isPending}
            dataSource={capabilities.data?.sources ?? []}
            columns={[
              { title: "来源", dataIndex: "name" },
              {
                title: "数据",
                key: "capabilities",
                render: (_, item) =>
                  item.capabilities.map((value) => (
                    <Tag key={value}>
                      {value === "options" ? "期权" : value === "news" ? "新闻情绪" : "ETF申赎"}
                    </Tag>
                  )),
              },
              {
                title: "账号",
                key: "account",
                render: (_, item) => (
                  <>
                    <Tag color={item.configured ? "green" : "gold"}>
                      {item.configured ? "可用配置" : "需要配置"}
                    </Tag>
                    <div className={styles.muted}>{item.authentication}</div>
                  </>
                ),
              },
              { title: "覆盖与边界", dataIndex: "scope_notice" },
              {
                title: "文档",
                key: "docs",
                render: (_, item) => {
                  const url = publicLink(item.documentation_url);
                  return url ? (
                    <a href={url} target="_blank" rel="noreferrer">
                      官方说明
                    </a>
                  ) : (
                    "—"
                  );
                },
              },
            ]}
          />
        </div>
        <p className={styles.note}>
          iShares公开NAV/份额用于标注方法的估算；Yahoo期权不承诺实时OPRA。Massive免费股票账号的新闻与付费期权权限分别检测。Intrinio
          ETF Global仅在已有相应权限时读取。
        </p>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>已有账号连接</h2>
          <div className={styles.actions}>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                void refresh();
              }}
            >
              刷新
            </Button>
            <Button icon={<PlusOutlined />} onClick={() => openEditor()}>
              新增数据连接
            </Button>
          </div>
        </div>
        <div className={styles.table}>
          <Table<DataConnectionView>
            rowKey="id"
            size="small"
            loading={connections.isPending}
            dataSource={connections.data?.connections ?? []}
            pagination={false}
            locale={{ emptyText: "公开来源无需账号；有自己的API Key时在此添加连接。" }}
            columns={[
              {
                title: "连接 / 来源",
                key: "connection",
                render: (_, item) => (
                  <>
                    {item.name}
                    <div className={styles.muted}>{names[item.provider]}</div>
                  </>
                ),
              },
              {
                title: "凭据",
                key: "credential",
                render: (_, item) => (
                  <>
                    {item.credential_configured ? "••••••••" : "未配置"}
                    {item.provider === "alpaca" ? (
                      <div className={styles.muted}>
                        Secret {item.secondary_credential_configured ? "已保存" : "未配置"} ·{" "}
                        {item.feed}
                      </div>
                    ) : null}
                  </>
                ),
              },
              {
                title: "能力检测",
                key: "tested",
                render: (_, item) => (
                  <>
                    {Object.entries(item.test_results).map(([key, value]) => (
                      <Tag key={key}>
                        {key}: {value}
                      </Tag>
                    ))}
                    <div className={styles.muted}>
                      {item.tested_at ? new Date(item.tested_at).toLocaleString() : "尚未检测"}
                    </div>
                  </>
                ),
              },
              {
                title: "状态",
                key: "enabled",
                render: (_, item) => <Tag>{item.enabled ? "启用" : "停用"}</Tag>,
              },
              {
                title: "操作",
                key: "actions",
                render: (_, item) => (
                  <div className={styles.actions}>
                    <Button size="small" disabled={busy} onClick={() => openEditor(item)}>
                      编辑
                    </Button>
                    {item.provider === "massive" ? (
                      <>
                        <Button
                          size="small"
                          disabled={busy || !item.enabled}
                          onClick={() => {
                            void test(item, "news");
                          }}
                        >
                          检测新闻
                        </Button>
                        <Button
                          size="small"
                          disabled={busy || !item.enabled}
                          onClick={() => {
                            void test(item, "options");
                          }}
                        >
                          检测期权
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="small"
                        disabled={busy || !item.enabled}
                        onClick={() => {
                          void test(item, item.provider === "intrinio" ? "etf_flows" : "options");
                        }}
                      >
                        检测能力
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
        <p className={styles.note}>
          检测会向对应数据商发送只读请求并占用该账号额度；不会下单。Key仅写入后端系统密钥存储，不回显、不进入浏览器持久存储或Query/Mutation缓存。
        </p>
      </section>
      <Modal
        title={editor?.original ? "编辑数据连接" : "新增数据连接"}
        open={!!editor}
        onCancel={() => {
          if (!busy) {
            form.resetFields();
            setEditor(undefined);
          }
        }}
        footer={null}
        destroyOnHidden
      >
        <Form
          name="data-connection-editor"
          form={form}
          layout="vertical"
          onFinish={(values) => {
            void save(values);
          }}
        >
          {error && <Alert type="error" title={error} />}
          <Form.Item name="name" label="连接名称" rules={[{ required: true, max: 120 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="provider" label="数据供应商" rules={[{ required: true }]}>
            <Select
              disabled={!!editor?.original}
              onChange={() =>
                form.setFieldsValue({
                  api_key: undefined,
                  api_secret: undefined,
                  feed: "indicative",
                })
              }
              options={Object.entries(names).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: !editor?.original }]}>
            <Input.Password
              autoComplete="off"
              placeholder={
                editor?.original?.credential_configured
                  ? "已保存，留空保持；输入新值会轮换"
                  : "输入自己的API Key"
              }
            />
          </Form.Item>
          {selectedProvider === "alpaca" && (
            <>
              <Form.Item
                name="api_secret"
                preserve={false}
                label="API Secret"
                rules={[{ required: !editor?.original }]}
              >
                <Input.Password autoComplete="off" placeholder="Alpaca Secret Key" />
              </Form.Item>
              <Form.Item name="feed" label="期权Feed">
                <Select
                  options={[
                    { value: "indicative", label: "Indicative · 非OPRA指示性报价" },
                    { value: "opra", label: "OPRA · 需要已有账号权限" },
                  ]}
                />
              </Form.Item>
            </>
          )}
          <Form.Item name="enabled" label="启用连接" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>
            保存连接
          </Button>
        </Form>
      </Modal>
    </div>
  );
}
