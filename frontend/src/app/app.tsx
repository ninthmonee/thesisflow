import { Component, lazy, Suspense } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Alert, Button, ConfigProvider, Spin, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  BarChartOutlined,
  ApartmentOutlined,
  ControlOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
  LogoutOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useBootstrap } from "../shared/api/bootstrap";
import type { BootstrapResponse } from "../shared/api/generated/models";
import type { AuthUser } from "../shared/api/generated/models";
import { ApiError } from "../shared/api/transport";
import { AuthProvider } from "../features/auth/auth-provider";
import { AuthGate } from "../features/auth/auth-routes";
import { useAuth } from "../features/auth/auth-context";
import { AccountPage } from "../features/auth/account-page";
import styles from "./app.module.css";

const queryClient = new QueryClient();
const NewResearchPage = lazy(() =>
  import("../features/research/research-page").then((module) => ({ default: module.ResearchPage })),
);
const RunDetailPage = lazy(() =>
  import("../features/research/research-page").then((module) => ({
    default: module.RunDetailPage,
  })),
);
const ResearchHistoryPage = lazy(() =>
  import("../features/research/research-page").then((module) => ({
    default: module.ResearchHistoryPage,
  })),
);

const legacyWorkspace = "/";
const ThemesPage = lazy(() =>
  import("../features/themes/themes-page").then((module) => ({ default: module.ThemesPage })),
);
const AlternativeDataPage = lazy(() =>
  import("../features/alternative/alternative-page").then((module) => ({
    default: module.AlternativeDataPage,
  })),
);
const DataConnectionsPage = lazy(() =>
  import("../features/settings/data-connections-page").then((module) => ({
    default: module.DataConnectionsPage,
  })),
);
const ModelsPage = lazy(() =>
  import("../features/models/models-page").then((module) => ({ default: module.ModelsPage })),
);
const RuntimesPage = lazy(() =>
  import("../features/runtimes/runtimes-page").then((module) => ({ default: module.RuntimesPage })),
);
const WatchlistsPage = lazy(() =>
  import("../features/watchlists/watchlists-page").then((module) => ({
    default: module.WatchlistsPage,
  })),
);
const MarketPage = lazy(() =>
  import("../features/market/market-page").then((module) => ({ default: module.MarketPage })),
);
const DataSettingsPage = lazy(() =>
  import("../features/settings/data-settings-page").then((module) => ({
    default: module.DataSettingsPage,
  })),
);

const navigation = [
  { path: "/research/new", label: "个股研究", subtitle: "RESEARCH", icon: <FileSearchOutlined /> },
  { path: "/stocks", label: "行情工作台", subtitle: "MARKET VIEW", icon: <LineChartOutlined /> },
  {
    path: "/market",
    label: "市场脉搏",
    subtitle: "MARKET PULSE",
    icon: <FundProjectionScreenOutlined />,
  },
  { path: "/sectors", label: "一级行业", subtitle: "SECTOR RADAR", icon: <BarChartOutlined /> },
  {
    path: "/themes",
    label: "主题与产业链",
    subtitle: "THEMATIC RADAR",
    icon: <ApartmentOutlined />,
  },
  {
    path: "/insights",
    label: "资金·期权·新闻",
    subtitle: "DATA INTELLIGENCE",
    icon: <SwapOutlined />,
  },
  { path: "/reports", label: "研究记录", subtitle: "RESEARCH LIBRARY", icon: <DatabaseOutlined /> },
  {
    path: "/settings",
    label: "设置中心",
    subtitle: "CONFIGURATION",
    icon: <ControlOutlined />,
  },
];

class AppBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Raw client errors are deliberately not copied to service logs or reports.
  }

  override render() {
    if (this.state.failed) {
      return (
        <div className={styles.fatal}>
          <Alert
            type="error"
            showIcon
            title="工作台暂时无法显示"
            description="请重新加载页面。原有个股研究入口仍可单独打开。"
          />
          <Button onClick={() => window.location.reload()}>重新加载</Button>
          <Button href={legacyWorkspace}>打开原有研究</Button>
        </div>
      );
    }
    return this.props.children;
  }
}

function StatusPill({ children, good = false }: { children: ReactNode; good?: boolean }) {
  return <span className={`${styles.pill} ${good ? styles.pillGood : ""}`}>{children}</span>;
}

function SettingsLayout() {
  return (
    <>
      <nav className={styles.settingsNav} aria-label="设置分类">
        <NavLink to="/settings/models">模型与研究配置</NavLink>
        <NavLink to="/settings/runtimes">电脑与运行环境</NavLink>
        <NavLink to="/settings/data">数据中心</NavLink>
        <NavLink to="/settings/sources">数据源账号</NavLink>
      </nav>
      <Outlet />
    </>
  );
}

function ConnectedPages({ data }: { data: BootstrapResponse }) {
  return (
    <Suspense
      fallback={
        <div className={styles.loading}>
          <Spin />
          <p>正在加载工作区…</p>
        </div>
      }
    >
      <Routes>
        <Route path="/" element={<Navigate to="/research/new" replace />} />
        <Route path="/research/new" element={<NewResearchPage bootstrap={data} />} />
        <Route path="/runs/:runId" element={<RunDetailPage bootstrap={data} />} />
        <Route path="/stocks" element={<WatchlistsPage bootstrap={data} />} />
        <Route path="/stocks/:instrumentId" element={<WatchlistsPage bootstrap={data} />} />
        <Route path="/reports" element={<ResearchHistoryPage bootstrap={data} />} />
        <Route path="/themes" element={<ThemesPage bootstrap={data} />} />
        <Route path="/insights" element={<Navigate to="/insights/flows" replace />} />
        <Route
          path="/insights/flows"
          element={<AlternativeDataPage bootstrap={data} view="flows" />}
        />
        <Route
          path="/insights/options"
          element={<AlternativeDataPage bootstrap={data} view="options" />}
        />
        <Route
          path="/insights/news"
          element={<AlternativeDataPage bootstrap={data} view="news" />}
        />
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<Navigate to="models" replace />} />
          <Route path="models" element={<ModelsPage bootstrap={data} />} />
          <Route path="runtimes" element={<RuntimesPage bootstrap={data} />} />
          <Route path="data" element={<DataSettingsPage bootstrap={data} />} />
          <Route path="sources" element={<DataConnectionsPage bootstrap={data} />} />
          <Route path="*" element={<Navigate to="/settings/models" replace />} />
        </Route>
        <Route path="/market" element={<MarketPage bootstrap={data} />} />
        <Route path="/sectors" element={<MarketPage bootstrap={data} view="sectors" />} />
        <Route path="*" element={<Navigate to="/research/new" replace />} />
      </Routes>
    </Suspense>
  );
}

function Terminal({ user }: { user: AuthUser }) {
  const auth = useAuth();
  const bootstrap = useBootstrap({ userId: user.id, workspaceId: user.workspace.id });
  const location = useLocation();
  const current = navigation.find((item) =>
    location.pathname.startsWith(item.path.split("/").slice(0, 2).join("/")),
  );
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <NavLink className={styles.brand} to="/research/new">
          <span>TA</span>
          <div>
            TradingAgents<small>RESEARCH TERMINAL</small>
          </div>
        </NavLink>
        <div className={styles.navLabel}>WORKSPACE</div>
        <nav aria-label="主导航">
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `${styles.navItem} ${isActive ? styles.navActive : ""}`}
            >
              {item.icon}
              <span>
                {item.label}
                <small>{item.subtitle}</small>
              </span>
            </NavLink>
          ))}
        </nav>
        <div className={styles.sidebarBottom}>
          <SafetyCertificateOutlined />
          <div>
            独立个人工作区<small>ACCOUNT · PRIVATE</small>
          </div>
        </div>
      </aside>
      <div className={styles.mainColumn}>
        <header className={styles.topbar}>
          <div>
            <span>WORKBENCH</span>
            <strong>
              {location.pathname === "/account" ? "账号与安全" : (current?.label ?? "个股研究")}
            </strong>
          </div>
          <div className={styles.topbarActions}>
            <StatusPill good={bootstrap.isSuccess}>
              {bootstrap.isSuccess
                ? "本机 API 已连接"
                : bootstrap.isPending
                  ? "连接本机服务"
                  : "连接失败"}
            </StatusPill>
            <NavLink
              to="/account"
              className={styles.accountName}
              title={`账号与安全：${user.username}`}
            >
              <UserOutlined /> {user.display_name}
            </NavLink>
            <Button
              icon={<LogoutOutlined />}
              aria-label={`退出登录：${user.username}`}
              onClick={() => {
                void auth.logout().catch(() => {});
              }}
            >
              退出
            </Button>
            <Button
              aria-label="刷新工作区状态"
              icon={<ReloadOutlined />}
              onClick={() => {
                void bootstrap.refetch();
              }}
            />
          </div>
        </header>
        <div className={styles.buildNotice}>
          <span>DATA</span>行情、主题观察样本与新闻情绪均显示来源、时间和覆盖范围。
        </div>
        <main className={styles.content}>
          {auth.error && <Alert type="warning" showIcon title={auth.error} />}
          {auth.notice && <Alert type="info" showIcon title={auth.notice} />}
          {location.pathname === "/account" ? (
            <AccountPage />
          ) : bootstrap.isPending ? (
            <div className={styles.loading}>
              <Spin size="large" />
              <p>正在读取本机工作区…</p>
            </div>
          ) : bootstrap.isError ? (
            <div className={styles.connectionError}>
              <Alert
                type="error"
                showIcon
                title="无法读取工作区"
                description={
                  bootstrap.error instanceof ApiError
                    ? bootstrap.error.message
                    : "服务返回的数据格式不符合接口约定，请检查服务版本。"
                }
              />
              <Button
                onClick={() => {
                  void bootstrap.refetch();
                }}
                icon={<ReloadOutlined />}
              >
                重新连接
              </Button>
              <Button href={legacyWorkspace}>打开原有研究</Button>
            </div>
          ) : (
            <ConnectedPages data={bootstrap.data} />
          )}
        </main>
        <footer className={styles.footer}>
          <span>TRADINGAGENTS · {bootstrap.data?.application.version ?? "LOCAL"}</span>
          <span>来源状态与配置均由本机 API 返回</span>
        </footer>
      </div>
    </div>
  );
}

function AuthenticatedTerminal() {
  const auth = useAuth();
  const user = auth.session?.user;
  return user ? <Terminal key={`${user.id}:${user.workspace.id}`} user={user} /> : null;
}

export function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: "#59c6b3",
          colorBgBase: "#0b1017",
          colorBgContainer: "#111923",
          colorBorder: "#283440",
          borderRadius: 5,
          fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          fontSize: 13,
        },
      }}
    >
      <AppBoundary>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
            <AuthProvider>
              <AuthGate>
                <AuthenticatedTerminal />
              </AuthGate>
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </AppBoundary>
    </ConfigProvider>
  );
}
