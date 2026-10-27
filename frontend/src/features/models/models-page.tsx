import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { MinusCircleOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createModelConnection,
  updateModelConnection,
  deleteModelConnection,
  discoverConnectionModels,
  listModelConnections,
  listModelProfiles,
  listModelProviders,
  createModelProfile,
  updateModelProfile,
  testModelProfile,
  getModelSettings,
  saveModelSettings,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  ConnectionCreate,
  ConnectionView,
  LocalAuthorizationView,
  ProfileView,
  TemplateWrite,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import styles from "../../shared/ui/workbench.module.css";
import type { ConnectionEditorFields } from "./connection-fields";
import { connectionFields, protocolDescriptions, protocolLabels } from "./connection-fields";
import { ResearchWorkflow } from "../research/research-workflow";
import { modelsFromProfiles } from "../research/workflow-utils";
import { modelReadiness } from "./model-readiness";

type ProfileFields = { connection_id: string; model_id: string; parameters_text: string };
const statusLabels: Record<string, string> = {
  not_tested: "未检测",
  untested: "未检测",
  passed: "聊天通过",
  failed: "检测失败",
  stale: "需重新检测",
};
function safeError(error: unknown) {
  return error instanceof ApiError ? error.message : "操作未完成，请检查连接后重试";
}

function LocalAuthorizationNotice({ state }: { state: LocalAuthorizationView }) {
  return (
    <Alert
      type={state.status === "authorized" ? "info" : "warning"}
      showIcon
      title={
        state.status === "authorized"
          ? "ChatGPT本机订阅授权已保存 · 模型能力单独检测"
          : "ChatGPT本机订阅授权未就绪"
      }
      description={
        <div>
          <p>{state.message}</p>
          <p>
            在项目根目录的终端执行登录命令，在自己的浏览器授权，完成后刷新配置。此页面不接收Token，也不会自动登录或调用模型。
          </p>
          <Typography.Paragraph copyable={{ text: state.login_command }}>
            <code>{state.login_command}</code>
          </Typography.Paragraph>
          <p>退出本机授权（影响此机器所有ChatGPT连接）：</p>
          <Typography.Paragraph copyable={{ text: state.logout_command }}>
            <code>{state.logout_command}</code>
          </Typography.Paragraph>
          <p>
            连接使用本机当前授权；更换账号后必须重新检测。模型与工具检测通过前，保存授权状态不代表研究已可运行。
          </p>
        </div>
      }
    />
  );
}

export function ModelsPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const cache = useQueryClient();
  const [notices, noticeContext] = message.useMessage();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [editor, setEditor] = useState<ConnectionView | null | undefined>();
  const [profileEditor, setProfileEditor] = useState<ProfileView | null | undefined>();
  const [providerId, setProviderId] = useState<ConnectionCreate["provider"]>("openai");
  const [connectionForm] = Form.useForm<ConnectionEditorFields>();
  const [profileForm] = Form.useForm<ProfileFields>();
  const [settingsForm] = Form.useForm<TemplateWrite>();
  const [workflowOpen, setWorkflowOpen] = useState(false);
  const workflowAnalysts: TemplateWrite["analysts"] | undefined = Form.useWatch(
    "analysts",
    settingsForm,
  );
  const workflowDepth: TemplateWrite["depth"] | undefined = Form.useWatch("depth", settingsForm);
  const workflowDefaultProfile: string | undefined = Form.useWatch(
    "default_profile_id",
    settingsForm,
  );
  const workflowRoleBindings: TemplateWrite["role_bindings"] | undefined = Form.useWatch(
    "role_bindings",
    settingsForm,
  );
  const [discovery, setDiscovery] = useState<{ models: { id: string }[]; message: string }>();
  const providers = useQuery({
    queryKey: queryKeys.resource(scope, "model-providers"),
    queryFn: ({ signal }) => listModelProviders({ signal }),
    retry: shouldRetryQuery,
  });
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "model-connections"),
    queryFn: ({ signal }) => listModelConnections({ signal }),
    retry: shouldRetryQuery,
  });
  const profiles = useQuery({
    queryKey: queryKeys.resource(scope, "model-profiles"),
    queryFn: ({ signal }) => listModelProfiles({ signal }),
    retry: shouldRetryQuery,
  });
  const settings = useQuery({
    queryKey: queryKeys.resource(scope, "model-settings"),
    queryFn: ({ signal }) => getModelSettings({ signal }),
    retry: shouldRetryQuery,
  });
  const registry = providers.data?.providers ?? [];
  const provider = registry.find((item) => item.id === providerId);
  const localAuthorization = registry.find((item) => item.id === "chatgpt")?.local_authorization;
  const profileConnectionId: string | undefined = Form.useWatch("connection_id", profileForm);
  const profileUsesSubscription = connections.data?.connections.some(
    (item) => item.id === profileConnectionId && item.provider === "chatgpt",
  );
  const modelOptions = (profiles.data?.profiles ?? []).map((item) => ({
    value: item.id,
    label: `${item.connection_name} / ${item.model_id} · ${statusLabels[item.test_status] ?? item.test_status}`,
  }));
  const readiness = modelReadiness({
    defaultId: workflowDefaultProfile,
    bindings: workflowRoleBindings ?? {},
    analysts: workflowAnalysts ?? [],
    roles: settings.data?.roles ?? [],
    profiles: profiles.data?.profiles ?? [],
    connections: connections.data?.connections ?? [],
  });

  useEffect(() => {
    const template = settings.data?.default_template;
    settingsForm.resetFields();
    settingsForm.setFieldsValue(
      template
        ? { ...template, expected_version: template.version }
        : {
            expected_version: 0,
            name: "默认研究配置",
            analysts: ["market", "social", "news", "fundamentals"],
            depth: 1,
            language: "Chinese",
            role_bindings: {},
          },
    );
  }, [settings.data, settingsForm]);

  async function refresh() {
    await cache.invalidateQueries({ queryKey: queryKeys.scope(scope) });
  }

  async function act(label: string, action: () => Promise<unknown>, after?: () => void) {
    setBusy(label);
    setError(undefined);
    try {
      await action();
      after?.();
      await refresh();
      void notices.success(label);
    } catch (failure) {
      setError(safeError(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        setEditor(undefined);
        setProfileEditor(undefined);
        connectionForm.resetFields();
      }
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  function openConnection(value?: ConnectionView) {
    connectionForm.resetFields();
    const selected = registry.find((item) => item.id === value?.provider) ?? registry[0];
    setProviderId(selected?.id ?? "openai");
    connectionForm.setFieldsValue(
      value
        ? {
            name: value.name,
            endpoint: value.endpoint,
            enabled: value.enabled,
            provider: selected?.id ?? "openai",
            auth_type:
              value.auth_type === "oauth_local"
                ? "oauth_local"
                : value.auth_type === "headers"
                  ? "headers"
                  : value.auth_type === "none"
                    ? "none"
                    : "api_key",
            vendor_name: value.vendor_name,
            api_protocol: value.api_protocol,
            header_rows: [],
            clear_extra_headers: false,
          }
        : {
            provider: selected?.id ?? "openai",
            auth_type: selected?.auth_types[0] ?? "api_key",
            endpoint: selected?.default_endpoint,
            enabled: true,
            scope: "private",
            api_protocol: selected?.default_protocol ?? "auto",
            header_rows: [],
          },
    );
    setEditor(value ?? null);
  }

  function submitConnection(values: ConnectionEditorFields) {
    // Secret-bearing requests bypass MutationCache; the password stays only in this form/request.
    void act(
      editor ? "连接已更新" : "连接已保存",
      async () => {
        try {
          const fields = connectionFields(values, editor);
          if (editor) {
            await updateModelConnection(editor.id, {
              ...fields,
              revision: editor.revision,
              clear_extra_headers: values.clear_extra_headers ?? false,
            });
          } else
            await createModelConnection({
              ...fields,
              provider: providerId,
              scope: "private",
            });
        } finally {
          connectionForm.setFieldValue("api_key", undefined);
          connectionForm.setFieldValue("header_rows", []);
        }
      },
      () => {
        setEditor(undefined);
        connectionForm.resetFields();
      },
    );
  }

  function openProfile(value?: ProfileView, connectionId?: string) {
    profileForm.resetFields();
    setDiscovery(undefined);
    profileForm.setFieldsValue(
      value
        ? {
            connection_id: value.connection_id,
            model_id: value.model_id,
            parameters_text: JSON.stringify(value.parameters, null, 2),
          }
        : {
            connection_id:
              connectionId ?? connections.data?.connections.find((item) => item.enabled)?.id,
            parameters_text: "{}",
          },
    );
    setProfileEditor(value ?? null);
  }

  function submitProfile(values: ProfileFields) {
    void act(
      profileEditor ? "模型配置已更新" : "模型配置已保存",
      async () => {
        let parameters: unknown;
        try {
          parameters = JSON.parse(values.parameters_text || "{}");
        } catch {
          throw new ApiError({
            status: 0,
            code: "invalid_parameters",
            message: "高级参数必须是合法 JSON 对象",
          });
        }
        if (parameters === null || typeof parameters !== "object" || Array.isArray(parameters))
          throw new ApiError({
            status: 0,
            code: "invalid_parameters",
            message: "高级参数必须是 JSON 对象",
          });
        const body = {
          model_id: values.model_id.trim(),
          parameters: parameters as Record<string, unknown>,
        };
        if (profileEditor)
          await updateModelProfile(profileEditor.id, { ...body, revision: profileEditor.revision });
        else await createModelProfile({ ...body, connection_id: values.connection_id });
      },
      () => setProfileEditor(undefined),
    );
  }

  const failedQuery = [providers, connections, profiles, settings].find((query) => query.isError);
  return (
    <div className={styles.stack}>
      {noticeContext}
      <div className={styles.heading}>
        <small>MODEL CONNECTIONS</small>
        <h1>模型连接与角色配置</h1>
        <p>
          选择服务预设或自定义供应商，明确 API 协议，再填写地址和模型
          ID。聊天与工具能力分别检测；API
          Key与请求头保存在系统密钥库，ChatGPT订阅复用本机OAuth授权。
        </p>
      </div>
      {(error || failedQuery) && (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setError(undefined)}
          title={error ?? safeError(failedQuery?.error)}
          action={
            <Button
              onClick={() => {
                void refresh();
              }}
            >
              刷新配置
            </Button>
          }
        />
      )}
      <Alert
        showIcon
        type="info"
        title="基础配置 · 一个模型即可覆盖整个研究团队"
        description={
          <div>
            <p>
              ① 保存供应商连接 → ② 新增模型并主动检测聊天 / 工具 → ③ 选择默认模型、保存研究配置。
            </p>
            <div className={styles.actions}>
              <Tag>
                {connections.data?.connections.some((item) => item.enabled)
                  ? "已有启用连接"
                  : "待创建连接"}
              </Tag>
              <Tag>
                {profiles.data?.profiles.some((item) => item.test_status === "passed")
                  ? "已有聊天检测通过模型"
                  : "待新增 / 检测模型"}
              </Tag>
              <Tag>
                {settings.data?.default_template
                  ? `默认配置已保存 v${settings.data.default_template.version}`
                  : "待保存默认研究配置"}
              </Tag>
            </div>
            <p>
              需要混用不同模型时，再展开高级角色覆盖。下方检查只读取已有检测结果，不会自动调用模型；数据源账号在设置中心单独管理。
            </p>
          </div>
        }
      />
      {localAuthorization && <LocalAuthorizationNotice state={localAuthorization} />}
      {settings.isSuccess && !settings.data.default_template && (
        <Alert
          type="info"
          showIcon
          title="尚未绑定默认研究模板"
          description="研究当前可沿用本机CLI/环境的兼容配置；这不会自动创建或验证本页的模型连接。保存并检测连接、配置角色模板后，研究才采用显式连接配置。"
        />
      )}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>供应商连接</h2>
          <div className={styles.actions}>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                void refresh();
              }}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={!!busy || !registry.length}
              onClick={() => openConnection()}
            >
              新增连接
            </Button>
          </div>
        </div>
        {!connections.data?.connections.length && (
          <div className={styles.body}>
            <p className={styles.muted}>
              尚未保存供应商连接。支持自定义名称、接口地址、模型 ID
              和请求头鉴权，也可使用无需认证的服务。
            </p>
          </div>
        )}
        {connections.data?.connections.map((connection) => (
          <div className={styles.connection} key={connection.id}>
            <div className={styles.row}>
              <h3>{connection.name}</h3>
              <Tag>{connection.vendor_name}</Tag>
              <Tag>
                {connection.provider === "chatgpt"
                  ? "ChatGPT订阅客户端"
                  : (protocolLabels[connection.resolved_protocol] ?? connection.resolved_protocol)}
              </Tag>
              <Tag color={connection.enabled ? "green" : "default"}>
                {connection.enabled ? "启用" : "停用"}
              </Tag>
              <Tag>
                {connection.auth_type === "oauth_local"
                  ? connection.local_authorization?.status === "authorized"
                    ? "本机授权已保存"
                    : "本机未授权"
                  : connection.credential_configured || connection.headers_configured
                    ? "凭据已保存"
                    : connection.auth_type === "none"
                      ? "无需凭据"
                      : "缺少凭据"}
              </Tag>
            </div>
            <div className={styles.muted}>
              <span className={styles.mono}>
                {connection.provider === "chatgpt"
                  ? "本机ChatGPT客户端管理订阅接口"
                  : connection.endpoint || "供应商默认地址"}
              </span>{" "}
              ·{" "}
              {connection.auth_type === "oauth_local"
                ? "OAuth令牌由本机授权组件管理，连接不保存Token"
                : (connection.credential_mask ?? "无密钥")}
              {connection.headers_configured && (
                <span> · 请求头已保存：{connection.header_names.join(" / ")}（值不回显）</span>
              )}
            </div>
            <div className={styles.actions}>
              <Button size="small" disabled={!!busy} onClick={() => openConnection(connection)}>
                编辑连接
              </Button>
              <Button
                size="small"
                disabled={!!busy || !connection.enabled}
                onClick={() => openProfile(undefined, connection.id)}
              >
                添加模型
              </Button>
              <Button
                size="small"
                disabled={!!busy}
                onClick={() => {
                  void act(connection.enabled ? "连接已停用" : "连接已启用", () =>
                    updateModelConnection(connection.id, {
                      revision: connection.revision,
                      enabled: !connection.enabled,
                    }),
                  );
                }}
              >
                {connection.enabled ? "停用" : "启用"}
              </Button>
              <Popconfirm
                title="删除这条连接？"
                description="已有模板引用时需先调整绑定。历史报告会保留。"
                onConfirm={() => {
                  void act("连接已删除", () =>
                    deleteModelConnection(connection.id, { revision: connection.revision }),
                  );
                }}
              >
                <Button size="small" danger disabled={!!busy}>
                  删除
                </Button>
              </Popconfirm>
            </div>
          </div>
        ))}
        <p className={styles.note}>
          现有环境配置：快速 {bootstrap.model.quick_model ?? "未设置"} / 深度{" "}
          {bootstrap.model.deep_model ?? "未设置"}
          。这是只读的本机配置，不等于上方已保存连接，也没有自动迁移密钥。
        </p>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>模型配置</h2>
          <Button
            icon={<PlusOutlined />}
            disabled={!!busy || !connections.data?.connections.some((item) => item.enabled)}
            onClick={() => openProfile()}
          >
            新增模型
          </Button>
        </div>
        <div className={styles.table}>
          <Table<ProfileView>
            rowKey="id"
            pagination={false}
            size="small"
            loading={profiles.isPending}
            dataSource={profiles.data?.profiles ?? []}
            locale={{ emptyText: "先创建连接，再填写具体模型 ID" }}
            columns={[
              {
                title: "连接 / 模型",
                key: "model",
                render: (_, profile) => (
                  <>
                    <strong>{profile.connection_name}</strong>
                    <div className={styles.mono}>{profile.model_id}</div>
                  </>
                ),
              },
              {
                title: "检测",
                key: "test",
                render: (_, profile) => (
                  <>
                    <Tag
                      color={
                        profile.test_status === "passed"
                          ? "green"
                          : profile.test_status === "failed"
                            ? "red"
                            : "default"
                      }
                    >
                      {statusLabels[profile.test_status] ?? profile.test_status}
                    </Tag>
                    <Tag
                      color={
                        profile.capabilities.tools === true ||
                        profile.capabilities.tools === "supported"
                          ? "green"
                          : profile.capabilities.tools === "unsupported"
                            ? "warning"
                            : "default"
                      }
                    >
                      {profile.capabilities.tools === true ||
                      profile.capabilities.tools === "supported"
                        ? "工具通过"
                        : profile.capabilities.tools === "unsupported" ||
                            profile.capabilities.tools === false
                          ? "工具未通过"
                          : "工具未检测"}
                    </Tag>
                  </>
                ),
              },
              {
                title: "操作",
                key: "actions",
                render: (_, profile) => (
                  <div className={styles.actions}>
                    <Button size="small" disabled={!!busy} onClick={() => openProfile(profile)}>
                      编辑
                    </Button>
                    <Button
                      size="small"
                      loading={busy === `检测 ${profile.model_id}`}
                      disabled={!!busy}
                      onClick={() => {
                        void act(`检测 ${profile.model_id}`, async () => {
                          const result = await testModelProfile(profile.id, {
                            revision: profile.revision,
                          });
                          if (!result.success)
                            throw new ApiError({
                              status: 0,
                              code: "model_test_failed",
                              message: result.message,
                            });
                        });
                      }}
                    >
                      检测模型
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </div>
        <p className={styles.note}>
          检测会向供应商发送请求，可能产生少量模型用量。已保存与检测通过分开记录；更换模型或凭据后需要重新检测。
        </p>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>基础研究配置</h2>
          <div className={styles.actions}>
            <Button onClick={() => setWorkflowOpen(true)}>查看 12 角色决策路径</Button>
            <Tag>
              {settings.data?.default_template
                ? `配置 v${settings.data.default_template.version}`
                : "尚未保存模板"}
            </Tag>
          </div>
        </div>
        <div className={styles.body}>
          <Form
            name="model-template"
            form={settingsForm}
            layout="vertical"
            onFinish={(values) => {
              void act("研究配置已保存", () =>
                saveModelSettings({
                  ...values,
                  expected_version: settings.data?.default_template?.version ?? 0,
                  role_bindings: Object.fromEntries(
                    Object.entries(values.role_bindings ?? {}).filter(
                      ([, value]) => typeof value === "string" && !!value,
                    ),
                  ),
                }),
              );
            }}
          >
            <div className={styles.columns}>
              <Form.Item name="name" label="配置名称" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item
                name="default_profile_id"
                label="默认模型"
                rules={[{ required: true, message: "请选择默认模型" }]}
              >
                <Select options={modelOptions} placeholder="全部角色默认使用此模型" />
              </Form.Item>
            </div>
            <div className={styles.columns}>
              <Form.Item name="depth" label="研究深度">
                <Select
                  options={[
                    { value: 1, label: "快速 · 1 轮" },
                    { value: 2, label: "标准 · 2 轮" },
                    { value: 3, label: "深入 · 3 轮" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="language" label="报告语言">
                <Select
                  options={[
                    { value: "Chinese", label: "简体中文" },
                    { value: "English", label: "English" },
                  ]}
                />
              </Form.Item>
            </div>
            <Form.Item
              name="analysts"
              label="分析师"
              rules={[{ required: true, message: "至少选择一类分析师" }]}
            >
              <Checkbox.Group
                options={[
                  { value: "market", label: "技术" },
                  { value: "fundamentals", label: "基本面" },
                  { value: "news", label: "新闻" },
                  { value: "social", label: "情绪" },
                ]}
              />
            </Form.Item>
            <Collapse
              items={[
                {
                  key: "roles",
                  label: `高级 · ${settings.data?.roles.length ?? 12} 角色模型覆盖（${Object.values(workflowRoleBindings ?? {}).filter(Boolean).length} 项独立绑定）`,
                  forceRender: true,
                  children: (
                    <>
                      {readiness.effective.map(({ role, profile, inherited, problems }) => (
                        <div className={styles.role} key={role.id}>
                          <div>
                            <span>{role.label}</span>
                            <div className={styles.muted}>
                              {inherited ? "继承默认：" : "独立覆盖："}
                              {profile
                                ? `${profile.connection_name} / ${profile.model_id}`
                                : "待选择模型"}
                            </div>
                            {problems.length > 0 && <Tag color="warning">需要检查</Tag>}
                          </div>
                          <Form.Item name={["role_bindings", role.id]}>
                            <Select allowClear placeholder="继承默认模型" options={modelOptions} />
                          </Form.Item>
                        </div>
                      ))}
                    </>
                  ),
                },
              ]}
            />
            <Alert
              showIcon
              type={readiness.ready && !failedQuery ? "info" : "warning"}
              title={
                readiness.ready && !failedQuery
                  ? "当前草稿的模型检测记录满足基础检查"
                  : "当前草稿仍有配置待确认"
              }
              description={
                <div>
                  {failedQuery ? (
                    <p>配置查询失败，请刷新后重新核对。</p>
                  ) : readiness.issues.length ? (
                    <>
                      <ul>
                        {readiness.issues.slice(0, 6).map((issue) => (
                          <li key={issue}>{issue}</li>
                        ))}
                      </ul>
                      {readiness.issues.length > 6 && (
                        <p>另有 {readiness.issues.length - 6} 项，请展开角色覆盖查看。</p>
                      )}
                    </>
                  ) : (
                    <p>默认模型、启用连接、已有聊天检测及所选分析师的工具检测均已核对。</p>
                  )}
                  <p>
                    这是未保存草稿的只读模型检查，不包含数据源覆盖或实际推理权限保证。保存后，新研究才使用这份模板；启动时后端仍会重新验证。
                  </p>
                </div>
              }
            />
            <p className={styles.muted}>
              允许先保存未检测配置；正式研究会检查所选模型是否通过检测。每次保存创建新模板版本。
            </p>
            <Button
              type="primary"
              htmlType="submit"
              loading={busy === "研究配置已保存"}
              disabled={!!busy || !modelOptions.length}
            >
              保存研究配置
            </Button>
          </Form>
        </div>
      </section>
      <Modal
        title="研究决策路径 · 当前配置草稿"
        open={workflowOpen}
        onCancel={() => setWorkflowOpen(false)}
        width={1200}
        centered
        footer={null}
        destroyOnHidden
        styles={{
          container: {
            display: "flex",
            flexDirection: "column",
            height: "min(860px, calc(100dvh - 48px))",
            overflow: "hidden",
            boxSizing: "border-box",
          },
          header: { flex: "0 0 auto" },
          body: { flex: 1, minHeight: 0, overflowY: "auto" },
        }}
      >
        <ResearchWorkflow
          bootstrap={bootstrap}
          mode="draft"
          analysts={
            workflowAnalysts ??
            settings.data?.default_template?.analysts ??
            bootstrap.defaults.analysts
          }
          depth={
            workflowDepth ?? settings.data?.default_template?.depth ?? bootstrap.defaults.depth
          }
          models={modelsFromProfiles(
            {
              default_profile_id: workflowDefaultProfile,
              role_bindings: workflowRoleBindings ?? {},
            },
            profiles.data?.profiles ?? [],
            "draft",
          )}
        />
      </Modal>
      <Modal
        title={editor ? "编辑供应商连接" : "新增供应商连接"}
        width={760}
        styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }}
        open={editor !== undefined}
        destroyOnHidden
        confirmLoading={!!busy}
        onCancel={() => {
          if (!busy) {
            setEditor(undefined);
            connectionForm.resetFields();
          }
        }}
        onOk={() => connectionForm.submit()}
      >
        <Form
          name="model-connection"
          form={connectionForm}
          layout="vertical"
          onFinish={submitConnection}
          preserve={false}
        >
          <Form.Item
            label="连接名称"
            name="name"
            rules={[{ required: true, whitespace: true, message: "请输入连接名称" }]}
          >
            <Input autoComplete="off" maxLength={120} />
          </Form.Item>
          <Form.Item label="服务预设" name="provider" rules={[{ required: true }]}>
            <Select
              disabled={!!editor}
              options={registry.map((item) => ({ value: item.id, label: item.name }))}
              onChange={(value: ConnectionCreate["provider"]) => {
                setProviderId(value);
                const next = registry.find((item) => item.id === value);
                connectionForm.setFieldsValue({
                  auth_type: next?.auth_types[0] ?? "api_key",
                  endpoint: next?.default_endpoint,
                  api_key: undefined,
                  api_protocol: next?.default_protocol ?? "auto",
                  vendor_name: next?.id === "custom" ? undefined : next?.name,
                  header_rows: [],
                  clear_extra_headers: false,
                });
              }}
            />
          </Form.Item>
          <Form.Item
            label="供应商显示名称"
            name="vendor_name"
            extra="可以填写你的自定义厂商或网关名称；连接名称用于区分账号或用途。"
          >
            <Input autoComplete="off" maxLength={120} placeholder="如：我的企业模型网关" />
          </Form.Item>
          <Form.Item label="API 协议" name="api_protocol" rules={[{ required: true }]}>
            <Select
              disabled={providerId === "chatgpt"}
              options={(provider?.supported_protocols ?? ["auto"]).map((value) => ({
                value,
                label:
                  providerId === "chatgpt"
                    ? "ChatGPT订阅客户端（固定）"
                    : (protocolLabels[value] ?? value),
              }))}
            />
          </Form.Item>
          {providerId === "chatgpt" ? (
            <p className={styles.muted}>
              本机ChatGPT客户端管理订阅接口，无需填写OpenAI API Key或接口地址。
            </p>
          ) : (
            <Form.Item
              noStyle
              shouldUpdate={(before: ConnectionEditorFields, after: ConnectionEditorFields) =>
                before.api_protocol !== after.api_protocol
              }
            >
              {({ getFieldValue }) => {
                const selectedProtocol: unknown = getFieldValue("api_protocol");
                return (
                  <p className={styles.muted}>
                    {
                      protocolDescriptions[
                        typeof selectedProtocol === "string" ? selectedProtocol : "auto"
                      ]
                    }
                  </p>
                );
              }}
            </Form.Item>
          )}
          <Form.Item label="认证方式" name="auth_type">
            <Select
              disabled={providerId === "chatgpt"}
              options={(provider?.auth_types ?? ["api_key"]).map((value) => ({
                value,
                label:
                  value === "oauth_local"
                    ? "本机ChatGPT OAuth订阅授权"
                    : value === "api_key"
                      ? "API Key"
                      : value === "headers"
                        ? "自定义请求头鉴权"
                        : "无需认证",
              }))}
              onChange={(value: ConnectionCreate["auth_type"]) => {
                connectionForm.setFieldValue("api_key", undefined);
                if (value === "none")
                  connectionForm.setFieldsValue({ header_rows: [], clear_extra_headers: false });
              }}
            />
          </Form.Item>
          {providerId === "chatgpt" && provider?.local_authorization && (
            <LocalAuthorizationNotice state={provider.local_authorization} />
          )}
          <Form.Item
            noStyle
            shouldUpdate={(before: ConnectionEditorFields, after: ConnectionEditorFields) =>
              before.auth_type !== after.auth_type
            }
          >
            {({ getFieldValue }) =>
              getFieldValue("auth_type") === "api_key" ? (
                <Form.Item
                  label={editor ? "替换 API Key（留空保留原值）" : "API Key"}
                  name="api_key"
                  rules={
                    editor?.credential_configured
                      ? []
                      : [{ required: true, whitespace: true, message: "请输入 API Key" }]
                  }
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item
            hidden={providerId === "chatgpt"}
            label="接口地址"
            name="endpoint"
            extra="填写 API 基础地址，不填写单次生成端点。自定义服务必须提供地址；协议和工具支持以检测结果为准。"
          >
            <Input
              disabled={providerId === "chatgpt"}
              autoComplete="off"
              placeholder={provider?.default_endpoint ?? "https://your-gateway.example/v1"}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(before: ConnectionEditorFields, after: ConnectionEditorFields) =>
              before.auth_type !== after.auth_type
            }
          >
            {({ getFieldValue }) =>
              ["api_key", "headers"].includes(String(getFieldValue("auth_type"))) ? (
                <>
                  <h3>自定义请求头</h3>
                  <p className={styles.muted}>
                    值通过系统密钥库保存，不回显。
                    {editor?.headers_configured
                      ? `现有请求头：${editor.header_names.join(" / ")}。留空保留；填写后整组替换，请提供需要保留的全部值。`
                      : "可用于企业网关令牌或服务要求的附加请求头。"}
                  </p>
                  <Form.List name="header_rows">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map((field) => (
                          <div className={styles.row} key={field.key}>
                            <Form.Item
                              name={[field.name, "name"]}
                              style={{ flex: 1, minWidth: 150 }}
                            >
                              <Input
                                aria-label={`请求头 ${field.name + 1} 名称`}
                                placeholder="X-API-Key"
                                autoComplete="off"
                              />
                            </Form.Item>
                            <Form.Item
                              name={[field.name, "value"]}
                              style={{ flex: 2, minWidth: 200 }}
                            >
                              <Input.Password
                                aria-label={`请求头 ${field.name + 1} 值`}
                                placeholder="凭据值（不回显）"
                                autoComplete="new-password"
                              />
                            </Form.Item>
                            <Button
                              aria-label={`移除请求头 ${field.name + 1}`}
                              icon={<MinusCircleOutlined />}
                              onClick={() => remove(field.name)}
                            />
                          </div>
                        ))}
                        <Button
                          size="small"
                          icon={<PlusOutlined />}
                          disabled={fields.length >= 16}
                          onClick={() => {
                            connectionForm.setFieldValue("clear_extra_headers", false);
                            add({ name: "", value: "" });
                          }}
                        >
                          添加请求头
                        </Button>
                      </>
                    )}
                  </Form.List>
                  {editor?.headers_configured && (
                    <Form.Item
                      name="clear_extra_headers"
                      valuePropName="checked"
                      style={{ marginTop: 12 }}
                    >
                      <Checkbox
                        onChange={(event) => {
                          if (event.target.checked) connectionForm.setFieldValue("header_rows", []);
                        }}
                      >
                        清除当前连接的自定义请求头
                      </Checkbox>
                    </Form.Item>
                  )}
                </>
              ) : getFieldValue("auth_type") === "none" ? (
                <p className={styles.muted}>
                  无需认证不会读取其他连接或环境中的密钥。仅用于你明确选择的无需凭据服务。
                </p>
              ) : null
            }
          </Form.Item>
          <p className={styles.muted}>
            凭据通过本机 API 保存；不写浏览器存储，不加入 Query/Mutation 缓存。
          </p>
        </Form>
      </Modal>
      <Modal
        title={profileEditor ? "编辑模型配置" : "新增模型配置"}
        open={profileEditor !== undefined}
        destroyOnHidden
        confirmLoading={!!busy}
        onCancel={() => {
          if (!busy) setProfileEditor(undefined);
        }}
        onOk={() => profileForm.submit()}
      >
        <Form
          name="model-profile"
          form={profileForm}
          layout="vertical"
          onFinish={submitProfile}
          preserve={false}
        >
          <Form.Item name="connection_id" label="供应商连接" rules={[{ required: true }]}>
            <Select
              disabled={!!profileEditor}
              options={connections.data?.connections
                .filter((item) => item.enabled)
                .map((item) => ({ value: item.id, label: `${item.name} / ${item.provider}` }))}
              onChange={() => setDiscovery(undefined)}
            />
          </Form.Item>
          <div className={styles.actions}>
            <Button
              size="small"
              disabled={!!busy}
              onClick={() => {
                const connectionId: unknown = profileForm.getFieldValue("connection_id");
                if (typeof connectionId !== "string" || !connectionId) return;
                void act("模型列表已读取", async () => {
                  const result = await discoverConnectionModels(connectionId);
                  setDiscovery({ models: result.models ?? [], message: result.message });
                });
              }}
            >
              读取可用模型列表
            </Button>
          </div>
          {discovery && <p className={styles.muted}>{discovery.message}</p>}
          {!!discovery?.models.length && (
            <Select
              style={{ width: "100%", marginBottom: 15 }}
              placeholder="从列表选择，或在下方手填"
              options={discovery.models.map((item) => ({ value: item.id, label: item.id }))}
              onChange={(value: string) => profileForm.setFieldValue("model_id", value)}
              showSearch
            />
          )}
          <Form.Item
            label="模型 ID"
            name="model_id"
            rules={[{ required: true, whitespace: true, message: "请输入供应商的准确模型 ID" }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="高级参数（JSON）"
            name="parameters_text"
            extra={
              profileUsesSubscription
                ? "ChatGPT订阅仅支持timeout、max_retries和reasoning_effort；不能配置temperature、top_p、max_tokens、seed、stop或extra_body。"
                : "支持 temperature、max_tokens、timeout、max_retries、top_p，以及协议允许的 seed / stop / 思考参数。OpenAI兼容协议可用extra_body传递有界厂商扩展；不能放密钥、消息、工具或模型覆盖。"
            }
          >
            <Input.TextArea rows={4} spellCheck={false} />
          </Form.Item>
          <p className={styles.muted}>
            例如：
            {JSON.stringify(
              profileUsesSubscription
                ? { reasoning_effort: "low", timeout: 30 }
                : {
                    temperature: 0.2,
                    max_tokens: 4096,
                    extra_body: { thinking: { type: "enabled" } },
                  },
            )}
            {profileUsesSubscription
              ? "。保存后需单独检测该订阅模型的聊天与工具能力。"
              : "。扩展参数是否被服务接受，需要实际检测；Responses 不接受 seed / stop。"}
          </p>
        </Form>
      </Modal>
    </div>
  );
}
