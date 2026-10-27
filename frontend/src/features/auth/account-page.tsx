import { useRef, useState } from "react";
import { Alert, Button, Descriptions, Form, Input, Popconfirm, Select, Tag } from "antd";
import { LockOutlined, LogoutOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getRegistrationSettings,
  updateRegistrationSettings,
} from "../../shared/api/generated/client";
import {
  GetRegistrationSettingsResponse,
  UpdateRegistrationSettingsResponse,
} from "../../shared/api/generated/validators";
import type {
  AuthPasswordChange,
  AuthRegistrationSettings,
} from "../../shared/api/generated/models";
import { queryKeys } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { useAuth } from "./auth-context";
import { validNewPassword } from "./auth-forms";
import styles from "../../shared/ui/workbench.module.css";
import accountStyles from "./auth.module.css";

type PasswordFields = AuthPasswordChange & { confirm_password: string };

function failureMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function RegistrationPolicy() {
  const auth = useAuth();
  const user = auth.session?.user;
  const cache = useQueryClient();
  const scope = { userId: user?.id ?? "", workspaceId: user?.workspace.id ?? "" };
  const key = queryKeys.resource(scope, "registration-settings");
  const settings = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) =>
      GetRegistrationSettingsResponse.parse(await getRegistrationSettings({ signal })),
    enabled: user?.is_admin === true,
    retry: shouldRetryQuery,
    staleTime: 0,
  });
  const [draft, setDraft] = useState<AuthRegistrationSettings["registration_mode"]>();
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);
  const mode = draft ?? settings.data?.registration_mode;
  async function save() {
    if (!mode || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      const next = UpdateRegistrationSettingsResponse.parse(
        await updateRegistrationSettings({ registration_mode: mode }),
      );
      cache.setQueryData(key, next);
      setDraft(undefined);
      setSaved(true);
      await auth.refresh();
    } catch (failure) {
      if (!(failure instanceof Error && failure.name === "AbortError"))
        setError(failureMessage(failure, "无法更新注册设置，请重试"));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <section className={styles.panel}>
      <div className={styles.toolbar}>
        <h2>注册设置</h2>
        <Tag>管理员</Tag>
      </div>
      <div className={`${styles.body} ${styles.stack}`}>
        <p className={styles.muted}>
          开放注册允许朋友自行创建账号和独立工作区。关闭后，已有账号仍可登录。
        </p>
        {(error || settings.isError) && (
          <Alert
            type="error"
            showIcon
            title={error ?? failureMessage(settings.error, "无法读取注册设置")}
          />
        )}
        {saved && <Alert type="success" showIcon title="注册设置已保存" />}
        <div className={styles.row}>
          <Select
            aria-label="账号注册方式"
            value={mode}
            loading={settings.isPending}
            disabled={!settings.data || busy}
            onChange={(value: AuthRegistrationSettings["registration_mode"]) => {
              setDraft(value);
              setSaved(false);
            }}
            options={[
              { value: "open", label: "开放注册" },
              { value: "closed", label: "关闭注册" },
            ]}
            style={{ minWidth: 160 }}
          />
          <Button
            type="primary"
            loading={busy}
            disabled={!settings.data || mode === settings.data.registration_mode}
            onClick={() => {
              void save();
            }}
          >
            保存注册设置
          </Button>
          <Button
            icon={<ReloadOutlined />}
            disabled={busy}
            onClick={() => {
              void settings.refetch();
            }}
          >
            刷新设置
          </Button>
        </div>
      </div>
    </section>
  );
}

export function AccountPage() {
  const auth = useAuth();
  const user = auth.session?.user;
  const [form] = Form.useForm<PasswordFields>();
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState<string>();
  if (!user) return null;

  async function changePassword(values: PasswordFields) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    try {
      await auth.changePassword({
        current_password: values.current_password,
        new_password: values.new_password,
      });
      form.resetFields();
    } catch (failure) {
      if (!(failure instanceof Error && failure.name === "AbortError"))
        setError(failureMessage(failure, "无法更新密码，请重试"));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <div className={styles.stack}>
      <div className={styles.heading}>
        <small>ACCOUNT & SECURITY</small>
        <h1>账号与安全</h1>
        <p>管理你的登录密码、会话和独立工作区。</p>
      </div>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>当前账号</h2>
          <Tag color={user.is_admin ? "cyan" : undefined}>
            {user.is_admin ? "管理员" : "个人账号"}
          </Tag>
        </div>
        <div className={styles.body}>
          <Descriptions
            column={{ xs: 1, md: 2 }}
            size="small"
            items={[
              { key: "username", label: "用户名", children: user.username },
              { key: "name", label: "显示名称", children: user.display_name },
              { key: "workspace", label: "工作区", children: user.workspace.name },
              { key: "scope", label: "资料范围", children: "当前账号独立保存" },
            ]}
          />
        </div>
      </section>
      {error && <Alert type="error" showIcon title={error} />}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>修改密码</h2>
          <LockOutlined />
        </div>
        <div className={styles.body}>
          <p className={styles.muted}>
            密码更新后，其他设备的旧会话将退出。当前浏览器会建立新的登录会话。
          </p>
          <Form<PasswordFields>
            form={form}
            layout="vertical"
            disabled={busy}
            className={accountStyles.passwordForm}
            onFinish={(values) => {
              void changePassword(values);
            }}
          >
            <Form.Item
              name="current_password"
              label="当前密码"
              rules={[{ required: true, message: "请输入当前密码" }]}
            >
              <Input.Password autoComplete="current-password" maxLength={256} />
            </Form.Item>
            <Form.Item
              name="new_password"
              label="新密码"
              rules={[
                { required: true, message: "请输入新密码" },
                {
                  validator: (_, value: unknown) =>
                    typeof value === "string" && validNewPassword(value)
                      ? Promise.resolve()
                      : Promise.reject(new Error("密码须为12–128个字符，且不能全为空白")),
                },
              ]}
              extra="可包含空格和中文，建议使用独立的长密码。"
            >
              <Input.Password autoComplete="new-password" maxLength={256} />
            </Form.Item>
            <Form.Item
              name="confirm_password"
              label="确认新密码"
              dependencies={["new_password"]}
              rules={[
                { required: true, message: "请再次输入新密码" },
                {
                  validator: (_, value: unknown) =>
                    value === form.getFieldValue("new_password")
                      ? Promise.resolve()
                      : Promise.reject(new Error("两次密码不一致")),
                },
              ]}
            >
              <Input.Password autoComplete="new-password" maxLength={256} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={busy}>
              更新密码
            </Button>
          </Form>
        </div>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>登录会话</h2>
        </div>
        <div className={`${styles.body} ${styles.stack}`}>
          <p className={styles.muted}>
            退出所有会话后，此账号在当前浏览器和其他设备上都需要重新登录。
          </p>
          <div className={styles.row}>
            <Popconfirm
              title="退出此账号的所有会话？"
              description="包括当前浏览器。你的工作区资料会保留。"
              okText="退出所有会话"
              cancelText="取消"
              onConfirm={() => {
                void auth.logoutAll().catch(() => {});
              }}
            >
              <Button danger icon={<LogoutOutlined />} disabled={busy}>
                退出所有会话
              </Button>
            </Popconfirm>
          </div>
        </div>
      </section>
      {user.is_admin && <RegistrationPolicy />}
    </div>
  );
}
