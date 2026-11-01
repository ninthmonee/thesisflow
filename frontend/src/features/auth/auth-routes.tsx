import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Alert, Button, Form, Input, Spin } from "antd";
import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from "@ant-design/icons";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { ApiError } from "../../shared/api/transport";
import { useAuth } from "./auth-context";
import {
  authPageUrl,
  normalizedUsername,
  safeReturnPath,
  validNewPassword,
  validUsername,
} from "./auth-forms";
import styles from "./auth.module.css";

interface AuthFields {
  username: string;
  password: string;
  confirm_password?: string;
  display_name?: string;
  setup_token?: string;
}

function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <main className={styles.screen}>
      <section className={styles.introduction}>
        <div className={styles.brand}>
          <span>TF</span>
          <div>
            研衡 · ThesisFlow<small>个人智能投研与决策工作台</small>
          </div>
        </div>
        <p className={styles.kicker}>YOUR RESEARCH · YOUR WORKSPACE</p>
        <h1>
          让研究积累在
          <br />
          自己的工作区
        </h1>
        <p>行情观察、主题与产业链、多角色研究和手动复核，统一在你的投研工作台。</p>
        <div className={styles.privacy}>
          <SafetyCertificateOutlined />
          <span>账号之间独立保存自选股、模型连接、研究记录与观点。</span>
        </div>
      </section>
      <section className={styles.card}>{children}</section>
      <footer className={styles.footer}>研衡 · ThesisFlow · 个人智能投研与决策工作台</footer>
    </main>
  );
}

function AuthScreen({ mode }: { mode: "login" | "register" | "setup" }) {
  const auth = useAuth();
  const [params] = useSearchParams();
  const next = safeReturnPath(params.get("next"));
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [failure, setFailure] = useState<string>();
  const [form] = Form.useForm<AuthFields>();
  const creating = mode !== "login";
  const title =
    mode === "setup" ? "创建管理员账号" : mode === "register" ? "创建研究账号" : "登录研究工作台";
  async function submit(values: AuthFields) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setFailure(undefined);
    try {
      const username = normalizedUsername(values.username);
      if (mode === "login") {
        await auth.login({ username, password: values.password });
      } else {
        const body = {
          username,
          password: values.password,
          display_name: values.display_name?.trim() || null,
        };
        if (mode === "setup") await auth.setup({ ...body, setup_token: values.setup_token ?? "" });
        else await auth.register(body);
      }
      form.resetFields();
    } catch (error) {
      if (!(error instanceof Error && error.name === "AbortError"))
        setFailure(error instanceof ApiError ? error.message : "暂时无法完成请求，请稍后重试。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <AuthFrame>
      <div className={styles.cardHeading}>
        <p className={styles.kicker}>
          {mode === "setup"
            ? "INITIAL SETUP"
            : mode === "register"
              ? "CREATE ACCOUNT"
              : "WELCOME BACK"}
        </p>
        <h2>{title}</h2>
        <p>
          {mode === "setup"
            ? "首次设置需要启动终端提供的初始化凭证，现有本机工作区会保留在这个管理员账号下。"
            : mode === "register"
              ? "开放注册，无需邀请码。注册后进入独立工作区，模型账号由你自行配置。"
              : "使用你的账号继续研究。"}
        </p>
      </div>
      {auth.notice && <Alert type="info" showIcon title={auth.notice} />}
      {(failure || auth.error) && <Alert type="error" showIcon title={failure ?? auth.error} />}
      <Form<AuthFields>
        form={form}
        name={`auth-${mode}`}
        layout="vertical"
        disabled={busy}
        onFinish={(values) => {
          void submit(values);
        }}
        autoComplete="on"
        className={styles.form}
      >
        {mode === "setup" && (
          <Form.Item
            name="setup_token"
            label="初始化凭证"
            rules={[
              { required: true, message: "请输入启动终端提供的初始化凭证" },
              { min: 32, max: 128, message: "初始化凭证格式不正确" },
            ]}
            extra="在运行服务的终端取得凭证后输入。此项仅首次设置使用。"
          >
            <Input.Password autoComplete="off" maxLength={128} />
          </Form.Item>
        )}
        <Form.Item
          name="username"
          label="用户名"
          rules={[
            { required: true, message: "请输入用户名" },
            {
              validator: (_, value: unknown) =>
                typeof value === "string" && validUsername(value)
                  ? Promise.resolve()
                  : Promise.reject(
                      new Error("请输入3–32位字母、数字、点、下划线或短横线，首位为字母或数字"),
                    ),
            },
          ]}
          extra={creating ? "用户名不区分大小写，保存为小写。" : undefined}
        >
          <Input
            prefix={<UserOutlined />}
            autoComplete="username"
            maxLength={32}
            autoCapitalize="none"
            spellCheck={false}
          />
        </Form.Item>
        {creating && (
          <Form.Item
            name="display_name"
            label="显示名称（可选）"
            rules={[{ max: 80, message: "显示名称最多80个字符" }]}
          >
            <Input autoComplete="nickname" maxLength={80} />
          </Form.Item>
        )}
        <Form.Item
          name="password"
          label="密码"
          rules={[
            { required: true, message: "请输入密码" },
            ...(creating
              ? [
                  {
                    validator: (_: unknown, value: unknown) =>
                      typeof value === "string" && validNewPassword(value)
                        ? Promise.resolve()
                        : Promise.reject(new Error("密码须为12–128个字符，且不能全为空白")),
                  },
                ]
              : []),
          ]}
          extra={creating ? "建议使用较长的独立密码，可包含空格和中文。" : undefined}
        >
          <Input.Password
            prefix={<LockOutlined />}
            autoComplete={creating ? "new-password" : "current-password"}
            maxLength={256}
          />
        </Form.Item>
        {creating && (
          <Form.Item
            name="confirm_password"
            label="确认密码"
            dependencies={["password"]}
            rules={[
              { required: true, message: "请再次输入密码" },
              {
                validator: (_, value: unknown) =>
                  value === form.getFieldValue("password")
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次密码不一致")),
              },
            ]}
          >
            <Input.Password autoComplete="new-password" maxLength={256} />
          </Form.Item>
        )}
        <Button type="primary" htmlType="submit" block loading={busy} size="large">
          {mode === "setup"
            ? "创建管理员并保留原工作区"
            : mode === "register"
              ? "注册并进入工作区"
              : "登录"}
        </Button>
      </Form>
      {mode === "login" && auth.session?.registration_mode === "open" && (
        <p className={styles.switch}>
          还没有账号？ <Link to={authPageUrl("register", next)}>立即注册</Link>
        </p>
      )}
      {mode === "login" && auth.session?.registration_mode === "closed" && (
        <p className={styles.switch}>当前暂未开放新账号注册。</p>
      )}
      {mode === "register" && (
        <p className={styles.switch}>
          已有账号？ <Link to={authPageUrl("login", next)}>返回登录</Link>
        </p>
      )}
    </AuthFrame>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();
  const [params] = useSearchParams();
  const publicPage = ["/login", "/register", "/setup"].includes(location.pathname);
  if (auth.phase === "checking" || auth.phase === "transition")
    return (
      <AuthFrame>
        <div className={styles.loading}>
          <Spin size="large" />
          <p>{auth.phase === "transition" ? "正在切换登录状态…" : "正在确认登录状态…"}</p>
        </div>
      </AuthFrame>
    );
  if (auth.phase === "error" || !auth.session)
    return (
      <AuthFrame>
        <Alert type="error" showIcon title="无法确认登录状态" description={auth.error} />
        <Button
          onClick={() => {
            void auth.refresh();
          }}
        >
          重新连接
        </Button>
      </AuthFrame>
    );
  if (auth.session.authenticated && auth.session.user && auth.session.csrf_token) {
    if (publicPage) return <Navigate to={safeReturnPath(params.get("next"))} replace />;
    return children;
  }
  const next = auth.manualLogout
    ? undefined
    : publicPage
      ? params.get("next")
      : `${location.pathname}${location.search}${location.hash}`;
  if (auth.session.setup_required) {
    if (location.pathname !== "/setup") return <Navigate to={authPageUrl("setup", next)} replace />;
    return <AuthScreen key="setup" mode="setup" />;
  }
  if (location.pathname === "/register" && auth.session.registration_mode === "open")
    return <AuthScreen key="register" mode="register" />;
  if (location.pathname !== "/login") return <Navigate to={authPageUrl("login", next)} replace />;
  return <AuthScreen key="login" mode="login" />;
}
