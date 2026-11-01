import { useRef, useState } from "react";
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Switch, Table, Tag } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearResearchDataConnection,
  createResearchDataConnection,
  listResearchDataConnections,
  testResearchDataConnection,
  updateResearchDataConnection,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  ResearchDataConnectionCreate,
  ResearchDataConnectionView,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import styles from "../../shared/ui/workbench.module.css";
import {
  hasSavedResearchCredential,
  researchConnectionPayload,
} from "./research-connection-fields";
import type { ResearchConnectionFields } from "./research-connection-fields";

type Provider = ResearchDataConnectionCreate["provider"];
type Fields = ResearchConnectionFields;
const providers: {
  id: Provider;
  name: string;
  purpose: string;
  help: string;
  url: string;
}[] = [
  {
    id: "fred",
    name: "FRED",
    purpose: "宏观经济时间序列",
    help: "填写自己的 FRED API Key。用于研究工具的宏观数据，不改变行情图的 Yahoo 来源。",
    url: "https://fred.stlouisfed.org/docs/api/api_key.html",
  },
  {
    id: "alpha_vantage",
    name: "Alpha Vantage",
    purpose: "研究行情、财务与新闻工具",
    help: "不同端点权限取决于已有账号套餐；保存 Key 或基础检测通过不代表所有新闻、行情端点都有权限。",
    url: "https://www.alphavantage.co/documentation/",
  },
  {
    id: "typesafe_jev",
    name: "TypeSafe Jev",
    purpose: "社交帖子相关性与观点筛选",
    help: "这是额外的模型筛选服务，调用可能消耗账号额度。它不提供原始股价，也不代替研究主模型。",
    url: "https://docs.typesafe.ai/",
  },
  {
    id: "sec_edgar",
    name: "SEC EDGAR",
    purpose: "公司披露与财务报表",
    help: "SEC 公共披露无需 API Key；请填写真实应用名称与联系邮箱作为 User-Agent，不使用代码中的示例联系人。",
    url: "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
  },
];
function fail(error: unknown) {
  return error instanceof Error || error instanceof ApiError
    ? error.message
    : "研究数据连接操作失败";
}

export function ResearchDataConnectionsPanel({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    cache = useQueryClient();
  const [editor, setEditor] = useState<{
    provider: Provider;
    original?: ResearchDataConnectionView;
  }>();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>(),
    [notice, setNotice] = useState<string>();
  const inFlight = useRef(false);
  const [form] = Form.useForm<Fields>();
  const credentialSource = Form.useWatch("credential_source", form);
  const enabled = Form.useWatch("enabled", form) ?? true;
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "research-data-connections"),
    queryFn: ({ signal }) => listResearchDataConnections({ signal }),
    retry: shouldRetryQuery,
  });
  const details = providers.find((provider) => provider.id === editor?.provider);
  async function refresh() {
    await cache.invalidateQueries({
      queryKey: queryKeys.resource(scope, "research-data-connections"),
    });
  }
  function clearFields() {
    form.setFieldsValue({ api_key: undefined, user_agent: undefined });
  }
  function open(provider: Provider, original?: ResearchDataConnectionView) {
    form.resetFields();
    form.setFieldsValue({
      provider,
      name: original?.name ?? providers.find((item) => item.id === provider)?.name ?? "",
      enabled: original?.enabled ?? true,
      credential_source: original?.credential_source ?? "keychain",
      ...(provider === "typesafe_jev"
        ? {
            model: original?.model ?? "jev-latest",
            base_url: original?.base_url ?? "https://api.typesafe.ai",
          }
        : {}),
    });
    setError(undefined);
    setEditor({ provider, original });
  }
  async function save(values: Fields) {
    if (!editor || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const fields = researchConnectionPayload(values, editor.provider);
      if (editor.original) {
        await updateResearchDataConnection(editor.original.id, {
          ...fields,
          revision: editor.original.revision,
        });
      } else {
        await createResearchDataConnection({ ...fields, provider: editor.provider });
      }
      setEditor(undefined);
      setNotice("研究数据连接已保存。新研究将使用该配置；能力检测需单独执行。");
      await refresh();
    } catch (failure) {
      setError(fail(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        setEditor(undefined);
        await refresh();
      }
    } finally {
      clearFields();
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function test(connection: ResearchDataConnectionView) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const result = await testResearchDataConnection(connection.id, {
        revision: connection.revision,
      });
      if (result.success) setNotice(result.message);
      else setError(result.message);
      await refresh();
    } catch (failure) {
      setError(fail(failure));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function clear(connection: ResearchDataConnectionView) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await clearResearchDataConnection(connection.id, { revision: connection.revision });
      setNotice("凭据已清除且连接已停用；不会自动改用环境变量中的账号。");
      await refresh();
    } catch (failure) {
      setError(fail(failure));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>ThesisFlow 研究数据连接</h2>
          <Tag>凭据通过配置接入</Tag>
        </div>
        <div className={styles.body}>
          <p className={styles.muted}>
            模型连接在“模型与研究配置”管理；下方配置用于个股研究的数据与筛选工具。
            页面保存的凭据进入系统密钥库，接口只返回状态。缺失、停用或清除的连接不会用示例 Key
            替代。
          </p>
          {(error || connections.isError) && (
            <Alert type="error" showIcon title={error ?? fail(connections.error)} />
          )}
          {notice && <Alert type="success" showIcon title={notice} />}
        </div>
        <div className={styles.table}>
          <Table
            rowKey="id"
            pagination={false}
            size="small"
            loading={connections.isPending}
            dataSource={providers.map((provider) => ({
              ...provider,
              connection: connections.data?.connections.find(
                (connection) => connection.provider === provider.id,
              ),
            }))}
            columns={[
              { title: "来源", dataIndex: "name" },
              { title: "用途", dataIndex: "purpose" },
              {
                title: "配置与凭据",
                key: "configuration",
                render: (_, item) => {
                  const connection = item.connection;
                  if (!connection) return <Tag>尚未保存连接</Tag>;
                  return (
                    <>
                      <Tag>{connection.enabled ? "启用" : "停用"}</Tag>
                      <div className={styles.muted}>
                        {connection.credential_source === "legacy_environment"
                          ? "已从本机环境导入密钥库"
                          : "系统密钥库"}
                        {" · "}
                        {(
                          item.id === "sec_edgar"
                            ? connection.user_agent_configured
                            : connection.credential_configured
                        )
                          ? "凭据已配置"
                          : "凭据缺失"}
                      </div>
                    </>
                  );
                },
              },
              {
                title: "检测",
                key: "test",
                render: (_, item) => (
                  <Tag>
                    {item.connection?.test_status === "passed"
                      ? "所测能力通过"
                      : item.connection?.test_status === "failed"
                        ? "检测失败"
                        : "尚未检测"}
                  </Tag>
                ),
              },
              {
                title: "操作",
                key: "actions",
                render: (_, item) => (
                  <div className={styles.actions}>
                    <Button
                      size="small"
                      disabled={busy || connections.isError}
                      onClick={() => open(item.id, item.connection)}
                    >
                      {item.connection ? "编辑连接" : "配置连接"}
                    </Button>
                    {item.connection && (
                      <>
                        <Button
                          size="small"
                          disabled={busy || !item.connection.enabled}
                          onClick={() => {
                            if (item.connection) void test(item.connection);
                          }}
                        >
                          检测
                        </Button>
                        <Popconfirm
                          title="清除该研究数据连接的凭据并停用？"
                          description="现有研究报告保留。重新使用时需要配置凭据或再次从环境导入。"
                          onConfirm={() => {
                            if (item.connection) void clear(item.connection);
                          }}
                        >
                          <Button size="small" danger disabled={busy}>
                            清除凭据
                          </Button>
                        </Popconfirm>
                      </>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
        <p className={styles.note}>
          检测会向对应供应商发出实际请求，可能消耗账号额度；保存配置本身不验证权限。
          研究会记录连接版本，凭据或配置变化后旧排队任务需重新创建。
        </p>
      </section>
      <Modal
        title={"配置研究数据连接 · " + (details?.name ?? "")}
        open={!!editor}
        footer={null}
        destroyOnHidden
        closable={!busy}
        maskClosable={!busy}
        onCancel={() => {
          if (!busy) {
            clearFields();
            setEditor(undefined);
          }
        }}
      >
        {error && <Alert type="error" showIcon title={error} />}
        <p>{details?.help}</p>
        {details && (
          <a href={details.url} target="_blank" rel="noreferrer">
            供应商官方说明
          </a>
        )}
        <Form
          name="research-data-connection-editor"
          form={form}
          layout="vertical"
          disabled={busy}
          onFinish={(values) => {
            void save(values);
          }}
        >
          <Form.Item name="name" label="连接名称" rules={[{ required: true, max: 120 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="credential_source" label="凭据来源">
            <Select
              options={[
                { value: "keychain", label: "系统密钥库 · 在页面填写" },
                { value: "legacy_environment", label: "从本机环境导入一次 · 存入密钥库" },
              ]}
              onChange={clearFields}
            />
          </Form.Item>
          {credentialSource === "legacy_environment" ? (
            <Alert
              type="info"
              title="启用并保存时从本机环境导入凭据到系统密钥库，不回显原值。"
              description="以后环境变化不会自动替换研究凭据。选择此来源再次保存会重新导入；Jev 的模型与接口设置也随本次导入保存。"
            />
          ) : editor?.provider === "sec_edgar" ? (
            <Form.Item
              name="user_agent"
              label="SEC 联系身份（应用名称与邮箱）"
              preserve={false}
              extra={
                hasSavedResearchCredential(editor.original, credentialSource, editor.provider)
                  ? "已配置，留空保留；填写新值将替换原身份。"
                  : "填写实际联系人，不使用示例邮箱。"
              }
              rules={[
                {
                  required:
                    enabled &&
                    !hasSavedResearchCredential(editor.original, credentialSource, editor.provider),
                },
              ]}
            >
              <Input.Password autoComplete="off" />
            </Form.Item>
          ) : (
            <Form.Item
              name="api_key"
              label="API Key"
              preserve={false}
              extra={
                editor &&
                hasSavedResearchCredential(editor.original, credentialSource, editor.provider)
                  ? "已保存，留空保留；填写新 Key 将替换原凭据。"
                  : "仅写入系统密钥库，保存后不回显。"
              }
              rules={[
                {
                  required:
                    enabled &&
                    (!editor ||
                      !hasSavedResearchCredential(
                        editor.original,
                        credentialSource,
                        editor.provider,
                      )),
                },
              ]}
            >
              <Input.Password autoComplete="off" />
            </Form.Item>
          )}
          {editor?.provider === "typesafe_jev" && credentialSource !== "legacy_environment" && (
            <>
              <Form.Item name="base_url" label="TypeSafe 接口">
                <Select
                  options={[
                    { value: "https://api.typesafe.ai", label: "TypeSafe 官方" },
                    { value: "https://openrouter.ai/api", label: "OpenRouter System One 兼容接口" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="model" label="筛选模型 ID" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
            </>
          )}
          <Form.Item name="enabled" label="启用连接" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={busy}>
            保存研究数据连接
          </Button>
        </Form>
      </Modal>
    </>
  );
}
