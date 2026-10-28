import { useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Checkbox,
  Collapse,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { BootstrapResponse } from "../../shared/api/generated/models";
import { listModelConnections, listModelProfiles } from "../../shared/api/generated/client";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { useAuth } from "../auth/auth-context";
import {
  deleteRuntimeConnection,
  listRuntimeConnections,
  listRuntimeTypes,
  saveRuntimeConnection,
  testRuntimeConnection,
} from "./runtime-api";
import { compatibleRuntimeProfiles, runtimeReady, runtimeReadinessLabels } from "./runtime-state";
import type { RuntimeConnection, RuntimeEditorFields } from "./runtime-state";
import styles from "../../shared/ui/workbench.module.css";

const failureMessage = (error: unknown) =>
  error instanceof ApiError ? error.message : "运行环境操作未完成，请稍后重试。";
const timeLabel = (value: string | null) => (value ? new Date(value).toLocaleString() : "尚未检测");

export function RuntimesPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const auth = useAuth();
  const owner = auth.session?.user?.is_admin === true;
  const scope = scopeForWorkspace(bootstrap.workspace);
  const cache = useQueryClient();
  const [notices, noticeContext] = message.useMessage();
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [editor, setEditor] = useState<RuntimeConnection | null | undefined>();
  const [form] = Form.useForm<RuntimeEditorFields>();
  const authMode: RuntimeEditorFields["auth_mode"] | undefined = Form.useWatch("auth_mode", form);
  const types = useQuery({
    queryKey: queryKeys.resource(scope, "runtime-types"),
    queryFn: ({ signal }) => listRuntimeTypes(signal),
    retry: shouldRetryQuery,
  });
  const runtimes = useQuery({
    queryKey: queryKeys.resource(scope, "runtime-connections"),
    queryFn: ({ signal }) => listRuntimeConnections(signal),
    retry: shouldRetryQuery,
    enabled: owner,
  });
  const models = useQuery({
    queryKey: queryKeys.resource(scope, "model-profiles"),
    queryFn: ({ signal }) => listModelProfiles({ signal }),
    retry: shouldRetryQuery,
    enabled: owner,
  });
  const connections = useQuery({
    queryKey: queryKeys.resource(scope, "model-connections"),
    queryFn: ({ signal }) => listModelConnections({ signal }),
    retry: shouldRetryQuery,
    enabled: owner,
  });
  const profiles = compatibleRuntimeProfiles(
    models.data?.profiles ?? [],
    connections.data?.connections ?? [],
  );
  const rows: RuntimeConnection[] = runtimes.data?.connections ?? [];

  function edit(current: RuntimeConnection | null) {
    setError(undefined);
    form.resetFields();
    form.setFieldsValue(
      current
        ? { ...current }
        : { name: "本机 Codex", model_id: "", auth_mode: "runtime_login", enabled: false },
    );
    setEditor(current);
  }
  async function refresh() {
    await cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "runtime-connections") });
  }
  async function save(fields: RuntimeEditorFields) {
    if (busy) return;
    setBusy("save");
    setError(undefined);
    try {
      await saveRuntimeConnection(fields, editor);
      setEditor(undefined);
      await refresh();
      void notices.success("运行环境已保存，请检测后再用于研究。");
    } catch (failure) {
      setError(failureMessage(failure));
    } finally {
      setBusy(undefined);
    }
  }
  async function runAction(current: RuntimeConnection, action: "test" | "delete") {
    if (busy) return;
    setBusy(current.id);
    setError(undefined);
    try {
      if (action === "test") {
        const result = await testRuntimeConnection(current);
        if (result.success) void notices.success(result.message);
        else setError(result.message);
      } else await deleteRuntimeConnection(current);
      await refresh();
    } catch (failure) {
      setError(failureMessage(failure));
    } finally {
      setBusy(undefined);
    }
  }
  return (
    <div className={styles.stack}>
      {noticeContext}
      <div className={styles.heading}>
        <small>LOCAL AGENT RUNTIME</small>
        <h1>电脑与运行环境</h1>
        <p>连接实际执行研究的 Agent 工具，分别检查程序、账号、模型和金融工具能力。</p>
      </div>
      <Alert
        showIcon
        type="info"
        title="执行位置：服务器本机"
        description="这里的本机指运行后端和研究 worker 的电脑。Codex 程序由 worker 按需启动；浏览器关闭不停止研究。模型和数据服务仍可能通过网络调用。"
      />
      {!owner && (
        <Alert
          showIcon
          type="warning"
          title="当前账号无法使用主人的本机 Runtime"
          description="当前本机执行仅对原工作区主人开放。你仍可配置自己的模型并使用内置多角色研究；个人电脑连接器尚未开放。"
        />
      )}
      {(error || types.isError || (owner && runtimes.isError)) && (
        <Alert
          showIcon
          type="error"
          title={error ?? failureMessage(types.error ?? runtimes.error)}
        />
      )}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>Runtime 类型</h2>
          <Tag>能力按实际版本检测</Tag>
        </div>
        <div className={styles.body}>
          {types.isPending ? (
            <Spin />
          ) : (
            <div className={styles.row}>
              {types.data?.types.map((item) => (
                <Tag key={item.id} color={item.implemented ? "cyan" : "default"}>
                  {item.name} · {item.implemented ? "已提供适配" : "待接入"}
                </Tag>
              ))}
            </div>
          )}
          <p className={styles.muted}>
            当前提供 Codex 的受控 stdio 执行。Claude Code 和 OpenCode
            的类型保留为后续接入，不会显示虚假的已连接状态。
          </p>
        </div>
      </section>
      {owner && (
        <section className={styles.panel}>
          <div className={styles.toolbar}>
            <h2>我的运行环境</h2>
            <div className={styles.actions}>
              <Button
                icon={<ReloadOutlined />}
                disabled={!!busy}
                onClick={() => {
                  void refresh();
                }}
              >
                刷新
              </Button>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!!busy}
                onClick={() => edit(null)}
              >
                添加运行环境
              </Button>
            </div>
          </div>
          {runtimes.isPending ? (
            <div className={styles.body}>
              <Spin />
            </div>
          ) : rows.length ? (
            rows.map((current) => (
              <div className={styles.connection} key={current.id}>
                <div className={styles.row}>
                  <h3>{current.name}</h3>
                  <Tag color={runtimeReady(current) ? "green" : "default"}>
                    {runtimeReadinessLabels[current.readiness]}
                  </Tag>
                  <Tag>{current.enabled ? "已启用" : "未启用"}</Tag>
                </div>
                <p className={styles.muted}>
                  Codex · 服务器本机 · stdio · 程序版本 {current.program_version ?? "未知"} · 检测于{" "}
                  {timeLabel(current.checked_at)}
                </p>
                <p>
                  模型：<span className={styles.mono}>{current.model_id}</span>
                  {current.reasoning_effort
                    ? ` · 思考档位 ${current.reasoning_effort}`
                    : " · 跟随模型默认思考档位"}
                </p>
                <p className={styles.muted}>
                  认证：
                  {current.auth_mode === "runtime_login"
                    ? "本连接专属 Codex 登录"
                    : "当前账号的模型连接"}{" "}
                  ·{" "}
                  {current.auth_status === "authorized"
                    ? "授权可用"
                    : current.auth_status === "not_authorized"
                      ? "尚未授权"
                      : "授权状态未知"}
                </p>
                <p className={styles.muted}>{current.message}</p>
                {current.auth_mode === "runtime_login" &&
                  current.login_command &&
                  current.auth_status !== "authorized" && (
                    <Alert
                      showIcon
                      type="warning"
                      title="完成这个连接的 Runtime 授权"
                      description={
                        <div>
                          <p>
                            在自己的终端运行下方命令并完成登录，再回到此处重新检测。此登录使用本应用的独立配置目录，不读取已有
                            Codex 或 ChatGPT 模型连接的授权。
                          </p>
                          <Typography.Paragraph copyable={{ text: current.login_command }}>
                            <code>{current.login_command}</code>
                          </Typography.Paragraph>
                        </div>
                      }
                    />
                  )}
                <div className={styles.row}>
                  <Tag>金融工具 {current.capabilities?.dynamic_tools ? "可用" : "待确认"}</Tag>
                  <Tag>取消 {current.capabilities?.cancel ? "支持" : "待确认"}</Tag>
                  <Tag>会话恢复 {current.capabilities?.resume ? "支持" : "暂不开放"}</Tag>
                </div>
                <div className={styles.actions}>
                  <Button disabled={!!busy} onClick={() => edit(current)}>
                    编辑
                  </Button>
                  <Button
                    loading={busy === current.id}
                    disabled={!!busy}
                    onClick={() => {
                      void runAction(current, "test");
                    }}
                  >
                    检测环境 · 不调用模型
                  </Button>
                  <Link
                    to={`/research/new?${new URLSearchParams({ execution_mode: "local_agent", runtime_connection_id: current.id })}`}
                  >
                    发起研究
                  </Link>
                  <Popconfirm
                    title="删除这个运行环境配置？"
                    description="保留历史报告。已排队任务引用失效后会停止，不会自动换用其他配置。"
                    onConfirm={() => {
                      void runAction(current, "delete");
                    }}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button danger disabled={!!busy}>
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            ))
          ) : (
            <div className={styles.body}>
              <p>还没有运行环境，添加 Codex 连接并完成检测后即可使用。</p>
            </div>
          )}
          <p className={styles.note}>
            环境检测只读取版本、协议、登录状态与模型清单，不启动研究或推理。模型实际可调用性会在研究执行时确认。
          </p>
        </section>
      )}
      <Collapse
        items={[
          {
            key: "scope",
            label: "运行权限、模型兼容和研究边界",
            children: (
              <div className={styles.muted}>
                <p>
                  每个连接使用本应用的独立 Codex
                  配置目录。保存后按连接显示的登录命令，由本人在终端完成授权，再回到这里检测；页面不接收或展示
                  OAuth Token，也不会继承其他 Codex 或 ChatGPT 连接的登录。
                </p>
                <p>
                  自定义模型通过“模型服务”保存凭据。这里只能绑定当前账号已通过聊天检测、已启用且使用
                  Responses 协议的模型；Chat Completions 接口不能直接作为 Codex 上游。
                </p>
                <p>
                  研究使用本系统的金融工具和冻结资料。Codex
                  内部模型请求次数可能无法完整观测，未知用量会明确显示。当前独立 Agent
                  模式不宣称执行了内置 12 角色，也不支持将重新发送提示词视为会话恢复。
                </p>
                <p>
                  <Link to="/settings/models">管理模型服务</Link> ·{" "}
                  <Link to="/settings/sources">管理数据源账号</Link>
                </p>
              </div>
            ),
          },
        ]}
      />
      <Modal
        title={editor ? "编辑运行环境" : "添加运行环境"}
        open={editor !== undefined}
        onCancel={() => {
          if (!busy) setEditor(undefined);
        }}
        footer={null}
        forceRender
        width={680}
        styles={{ body: { maxHeight: "min(68vh, 620px)", overflowY: "auto" } }}
        maskClosable={false}
      >
        <Form<RuntimeEditorFields>
          form={form}
          layout="vertical"
          onFinish={(fields) => {
            void save(fields);
          }}
          disabled={!!busy}
        >
          {error && <Alert showIcon type="error" title={error} />}
          <Form.Item
            name="name"
            label="连接名称"
            rules={[{ required: true, whitespace: true, max: 120 }]}
          >
            <Input maxLength={120} placeholder="例如：本机半导体研究 Codex" />
          </Form.Item>
          <p className={styles.muted}>
            Runtime：Codex · 执行位置：服务器本机 · 连接方式：受控 stdio
          </p>
          <Form.Item
            name="executable_path"
            label="Codex 可执行程序路径（可选）"
            extra="留空由后端发现。只接受实际程序路径，不接受命令参数、脚本或环境变量。"
          >
            <Input placeholder="自动发现本机 Codex" autoComplete="off" />
          </Form.Item>
          <Form.Item name="auth_mode" label="模型与认证来源" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "runtime_login", label: "本连接专属 Codex 登录" },
                { value: "model_connection", label: "使用我配置的 Responses 模型连接" },
              ]}
            />
          </Form.Item>
          {authMode === "model_connection" && (
            <Form.Item
              name="model_profile_id"
              label="Responses 模型配置"
              rules={[{ required: true, message: "请选择已检测的 Responses 模型配置" }]}
              extra={
                <span>
                  此入口复用地址、认证和模型ID；选择无额外推理参数的配置，思考档位在下方设置。模型凭据保留在系统密钥库。
                  <Link to="/settings/models">配置模型</Link>
                </span>
              }
            >
              <Select
                loading={models.isPending || connections.isPending}
                options={profiles.map((item) => ({
                  value: item.id,
                  label: `${item.connection_name} / ${item.model_id}`,
                }))}
                placeholder="选择已启用并通过聊天及工具检测的模型"
                onChange={(value: string) => {
                  const profile = profiles.find((item) => item.id === value);
                  if (profile) form.setFieldValue("model_id", profile.model_id);
                }}
              />
            </Form.Item>
          )}
          {(models.isError || connections.isError) && authMode === "model_connection" && (
            <Alert showIcon type="warning" title="模型配置暂时无法读取，请关闭弹窗并刷新后重试。" />
          )}
          <Form.Item
            name="model_id"
            label="精确模型 ID"
            rules={[
              { required: true, whitespace: true, max: 240 },
              { pattern: /^\S+$/, message: "模型 ID 不能含空白字符" },
            ]}
            extra="手填模型仍需通过环境检测；启动后核对 Runtime 实际生效的模型。"
          >
            <AutoComplete
              options={(editor?.models ?? []).map((item) => ({ value: item.id }))}
              disabled={authMode === "model_connection"}
              placeholder="填写当前 Runtime 或连接实际支持的模型 ID"
            />
          </Form.Item>
          <Form.Item
            name="reasoning_effort"
            label="思考档位（可选）"
            extra="只在模型支持时生效；不支持的档位会返回明确错误。"
          >
            <Select
              allowClear
              placeholder="使用模型默认值"
              options={["low", "medium", "high", "xhigh", "max"].map((value) => ({
                value,
                label: value,
              }))}
            />
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked">
            <Checkbox>启用此运行环境，检测通过后可用于研究</Checkbox>
          </Form.Item>
          <p className={styles.muted}>
            保存不调用模型。更改程序、模型、认证或思考档位后，需要重新检测；已有研究保留原引用。
          </p>
          <div className={styles.actions}>
            <Button onClick={() => setEditor(undefined)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={busy === "save"}>
              保存运行环境
            </Button>
          </div>
        </Form>
      </Modal>
    </div>
  );
}
