import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
} from "antd";
import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelResearchRun,
  createResearchRun,
  getModelSettings,
  getResearchManifest,
  getResearchRun,
  listResearchRuns,
  listResearchTemplates,
  listModelProfiles,
  resumeResearchRun,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  CreateResearchRequest,
  ListResearchRunsStatus,
  RunSnapshot,
} from "../../shared/api/generated/models";
import { GetResearchRunResponse } from "../../shared/api/generated/validators";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { terminalStatuses, useResearchEvents } from "./use-research-events";
import { historyParameters } from "./history-query";
import { ResearchWorkflow } from "./research-workflow";
import { modelsFromBootstrap, modelsFromManifest, modelsFromProfiles } from "./workflow-utils";
import type { WorkflowModelBindings } from "./workflow-utils";
import {
  CURRENT_MODEL_CONFIGURATION,
  modelBindingSummary,
  qualifiedResearchRating,
  researchDefaultTab,
  researchContextIds,
  researchPrefill,
  researchPrefillUrl,
  researchRequest,
  sourceTemplateId,
} from "./research-setup";
import { ResearchQualityNotice, RuntimeReadinessNotice } from "./research-status";
import { ResearchEvidencePanel } from "./research-evidence";
import { ResearchContextPicker } from "./research-context";
import { ResearchViewpoints } from "./research-viewpoints";
import { ResearchComparisonPanel } from "./research-comparison";
import styles from "../../shared/ui/workbench.module.css";

const analystChoices = [
  { value: "market", label: "技术分析" },
  { value: "fundamentals", label: "基本面" },
  { value: "news", label: "新闻" },
  { value: "social", label: "情绪" },
];
const objectiveChoices = [
  { value: "general", label: "综合研究" },
  { value: "technical", label: "技术形态" },
  { value: "earnings", label: "财报与基本面" },
  { value: "thematic", label: "主题与产业链" },
  { value: "risk_review", label: "风险复核" },
];
const horizonChoices = [
  { value: "short", label: "短期 · 数天至数周" },
  { value: "medium", label: "中期 · 1–6个月" },
  { value: "long", label: "长期 · 超过6个月" },
];
const statusLabels: Record<string, string> = {
  pending: "等待执行",
  queued: "排队中",
  running: "研究中",
  cancelling: "正在取消",
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
};
const stageLabels: Record<string, string> = {
  initializing: "初始化",
  queued: "等待执行",
  analysts: "分析师研究",
  research: "多空辩论",
  trading: "交易提案",
  risk: "风险讨论",
  portfolio: "最终决策",
  completed: "研究完成",
  interrupted: "已中断",
  cancelled: "已取消",
};
const reportLabels: Record<string, string> = {
  market_report: "技术分析",
  fundamentals_report: "基本面分析",
  news_report: "新闻分析",
  sentiment_report: "情绪分析",
  bull_history: "多头观点",
  bear_history: "空头观点",
  investment_plan: "研究经理汇总",
  trader_investment_plan: "交易提案",
  aggressive_history: "激进风险观点",
  neutral_history: "中立风险观点",
  conservative_history: "保守风险观点",
  final_trade_decision: "最终决策",
  memory_note: "历史经验状态",
};
function failureMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "操作未完成，请稍后重试";
}
function safeDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString();
}
function formattedNumber(value?: number) {
  return value === undefined ? "—" : value.toLocaleString();
}

export function ResearchPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const [search] = useSearchParams();
  return (
    <ResearchCreateForm
      key={`${search.get("from_run") ?? "new"}:${search.get("prefill") ?? "current"}:${search.getAll("context").join(",")}`}
      bootstrap={bootstrap}
    />
  );
}

function ResearchCreateForm({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    navigate = useNavigate();
  const [search] = useSearchParams(),
    [form] = Form.useForm<CreateResearchRequest>();
  const fromRun = search.get("from_run");
  const prefillMode = search.get("prefill") === "original" ? "original" : "current";
  const contextSignature = search.getAll("context").join(",");
  const urlContexts = useMemo(
    () => researchContextIds(contextSignature.split(",")),
    [contextSignature],
  );
  const [contextIds, setContextIds] = useState(urlContexts.ids),
    [contextReady, setContextReady] = useState(!urlContexts.ids.length),
    [contextWarning, setContextWarning] = useState(urlContexts.warning);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>();
  const idempotency = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const presetApplied = useRef(false);
  const workflowAnchor = useRef<HTMLDivElement>(null);
  const workflowAnalysts: CreateResearchRequest["analysts"] | undefined = Form.useWatch(
    "analysts",
    form,
  );
  const workflowDepth: CreateResearchRequest["depth"] | undefined = Form.useWatch("depth", form);
  const selectedDate: string | undefined = Form.useWatch("date", form);
  const selectedTicker: string | undefined = Form.useWatch("ticker", form);
  const workflowTemplateId: CreateResearchRequest["template_version_id"] = Form.useWatch(
    "template_version_id",
    form,
  );
  const templates = useQuery({
    queryKey: queryKeys.resource(scope, "research-templates"),
    queryFn: ({ signal }) => listResearchTemplates({ signal }),
    retry: shouldRetryQuery,
  });
  const settings = useQuery({
    queryKey: queryKeys.resource(scope, "model-settings"),
    queryFn: ({ signal }) => getModelSettings({ signal }),
    retry: shouldRetryQuery,
  });
  const profiles = useQuery({
    queryKey: queryKeys.resource(scope, "model-profiles"),
    queryFn: ({ signal }) => listModelProfiles({ signal }),
    retry: shouldRetryQuery,
  });
  const sourceRun = useQuery({
    queryKey: queryKeys.resource(scope, "research-run", { runId: fromRun }),
    queryFn: ({ signal }) => getResearchRun(fromRun ?? "", { signal }),
    enabled: !!fromRun,
    retry: shouldRetryQuery,
  });
  const sourceManifest = useQuery({
    queryKey: queryKeys.resource(scope, "research-manifest", { runId: fromRun }),
    queryFn: ({ signal }) => getResearchManifest(fromRun ?? "", { signal }),
    enabled: !!fromRun,
    retry: shouldRetryQuery,
  });
  const sourceLoading =
    !!fromRun &&
    (sourceRun.isPending || sourceManifest.isPending || settings.isPending || templates.isPending);
  const sourceError =
    !!fromRun &&
    (sourceRun.isError || sourceManifest.isError || settings.isError || templates.isError);
  const historicalTemplate = sourceManifest.data
    ? templates.data?.templates.find((item) => item.id === sourceTemplateId(sourceManifest.data))
    : undefined;
  const workflowTemplate =
    !workflowTemplateId && fromRun
      ? undefined
      : workflowTemplateId && workflowTemplateId !== CURRENT_MODEL_CONFIGURATION
        ? templates.data?.templates.find((item) => item.id === workflowTemplateId)
        : settings.data?.default_template;
  const workflowModels: WorkflowModelBindings = workflowTemplate
    ? modelsFromProfiles(workflowTemplate, profiles.data?.profiles ?? [], "template")
    : (workflowTemplateId && workflowTemplateId !== CURRENT_MODEL_CONFIGURATION) ||
        settings.isPending ||
        templates.isPending ||
        settings.isError ||
        (!!fromRun && !workflowTemplateId)
      ? { label: "模板模型信息尚未确认", fallbackByTier: false, roles: {} }
      : modelsFromBootstrap(bootstrap.model);
  const queryTicker = search.get("ticker");
  useEffect(() => {
    if (!settings.data || presetApplied.current || fromRun) return;
    presetApplied.current = true;
    const preset = settings.data.default_template;
    if (
      preset &&
      !form.isFieldsTouched(["template_version_id", "analysts", "depth", "language"], false)
    ) {
      form.setFieldsValue({
        template_version_id: preset.id,
        analysts: preset.analysts,
        depth: preset.depth,
        language: preset.language,
      });
    }
  }, [settings.data, form, fromRun]);
  useEffect(() => {
    if (
      !fromRun ||
      !sourceManifest.data ||
      !sourceRun.data ||
      !settings.data ||
      !templates.data ||
      presetApplied.current
    )
      return;
    presetApplied.current = true;
    const values = researchPrefill(
      sourceManifest.data,
      prefillMode,
      bootstrap.today ?? "",
      templates.data.templates,
      settings.data.default_template,
    );
    form.setFieldsValue(values);
    const contexts = researchContextIds([...urlContexts.ids, ...(values.context_ids ?? [])]);
    setContextIds(contexts.ids);
    setContextReady(!contexts.ids.length);
    setContextWarning(urlContexts.warning ?? contexts.warning);
  }, [
    fromRun,
    sourceManifest.data,
    sourceRun.data,
    settings.data,
    templates.data,
    prefillMode,
    bootstrap.today,
    form,
    urlContexts,
  ]);
  useEffect(() => {
    if (queryTicker && !fromRun) form.setFieldValue("ticker", queryTicker.toUpperCase());
  }, [queryTicker, fromRun, form]);
  async function submit(values: CreateResearchRequest) {
    if (sourceLoading || sourceError || !contextReady) return;
    const body = researchRequest({ ...values, context_ids: contextIds });
    const fingerprint = JSON.stringify([scope, body]);
    if (idempotency.current?.fingerprint !== fingerprint)
      idempotency.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true);
    setError(undefined);
    try {
      const result = await createResearchRun(body, {
        headers: { "Idempotency-Key": idempotency.current.key },
      });
      idempotency.current = undefined;
      void navigate(`/runs/${result.id}`);
    } catch (failure) {
      setError(failureMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={styles.stack}>
      <div className={styles.heading}>
        <small>TRADINGAGENTS EQUITY RESEARCH</small>
        <h1>启动个股研究</h1>
        <p>四类分析师、多空辩论、交易提案、风险讨论与最终决策完整保留。研究将在服务端继续执行。</p>
      </div>
      {error && <Alert type="error" title={error} showIcon />}
      <RuntimeReadinessNotice scope={scope} />
      {fromRun && (
        <Alert
          showIcon
          type={sourceError ? "error" : "info"}
          title={
            prefillMode === "current"
              ? "重新研究预填 · 更新到今天，使用当前配置"
              : "重新研究预填 · 原日期与可读取的原模板"
          }
          description={
            sourceLoading ? (
              "正在读取原任务及公开配置；尚未创建新研究。"
            ) : sourceError ? (
              "原研究或配置无法读取，暂不能启动。请返回原任务重试。"
            ) : sourceManifest.data ? (
              <div>
                <p>
                  来自 <Link to={`/runs/${fromRun}`}>{sourceRun.data?.ticker} · 原研究</Link>
                  。预填不会启动模型，请核对后点击启动。
                </p>
                <p>
                  日期：{sourceManifest.data.request.date} → {selectedDate || "待选择"}；模板：
                  {historicalTemplate
                    ? `${historicalTemplate.name} v${historicalTemplate.version}`
                    : "历史本机配置 / 原模板不可读取"}{" "}
                  →{" "}
                  {workflowTemplate
                    ? `${workflowTemplate.name} v${workflowTemplate.version}`
                    : workflowTemplateId === CURRENT_MODEL_CONFIGURATION
                      ? "显式使用当前本机配置"
                      : "待明确选择"}
                </p>
                <p>原模型：{modelBindingSummary(modelsFromManifest(sourceManifest.data))}</p>
                <p>
                  本次模型：{modelBindingSummary(workflowModels)}
                  。模板引用会在启动时重新检查，原始工具输入未全部冻结，不保证相同结果。过去日期仅用于日级回顾研究，不代表已验证当时可得的全部数据。
                </p>
                {prefillMode === "original" && !workflowTemplateId && (
                  <p>
                    原模板不可读取或属于历史本机配置；请选择一个模板，或明确选择当前配置，才可启动。
                  </p>
                )}
              </div>
            ) : null
          }
        />
      )}
      <div className={styles.columns}>
        <section className={styles.panel}>
          <div className={styles.toolbar}>
            <h2>研究参数</h2>
            <Tag>真实模型调用</Tag>
          </div>
          <div className={styles.body}>
            <Form
              name="research-create"
              form={form}
              layout="vertical"
              initialValues={{
                ticker: queryTicker ?? "",
                date: bootstrap.today ?? "",
                analysts: bootstrap.defaults.analysts,
                depth: bootstrap.defaults.depth,
                language: bootstrap.defaults.language,
                checkpoint: true,
                template_version_id: fromRun ? undefined : CURRENT_MODEL_CONFIGURATION,
                objective: "general",
                horizon: "medium",
                holding_state: "unknown",
                max_llm_calls: 80,
                max_runtime_seconds: 1800,
              }}
              onFinish={(values) => {
                void submit(values);
              }}
            >
              <Form.Item
                label="股票代码"
                name="ticker"
                rules={[
                  { required: true, whitespace: true, message: "请输入股票代码" },
                  { pattern: /^[A-Za-z0-9^][A-Za-z0-9._^=-]*$/, message: "请输入有效代码" },
                ]}
              >
                <Input placeholder="输入美股代码" maxLength={32} autoComplete="off" />
              </Form.Item>
              <div className={styles.columns}>
                <Form.Item name="objective" label="研究目标">
                  <Select options={objectiveChoices} />
                </Form.Item>
                <Form.Item name="horizon" label="观察期限">
                  <Select options={horizonChoices} />
                </Form.Item>
              </div>
              <Form.Item
                label="分析日期（美东）"
                name="date"
                rules={[{ required: true }]}
                extra="按America/New_York日期选择；采用日级回顾边界，不能据此保证所有历史资料在当时已可得。"
              >
                <Input type="date" min="1970-01-01" max={bootstrap.today ?? undefined} />
              </Form.Item>
              <Form.Item
                label="模型与研究模板"
                name="template_version_id"
                rules={
                  fromRun
                    ? [{ required: true, message: "请明确选择本次使用的模板或当前配置" }]
                    : undefined
                }
              >
                <Select
                  loading={templates.isPending}
                  options={[
                    {
                      value: CURRENT_MODEL_CONFIGURATION,
                      label: settings.data?.default_template
                        ? `明确使用当前默认配置 v${settings.data.default_template.version}`
                        : "明确使用当前本机 quick / deep 配置（兼容来源）",
                    },
                    ...(templates.data?.templates ?? []).map((item) => ({
                      value: item.id,
                      label: `${item.name} · v${item.version}`,
                    })),
                  ]}
                  onChange={(value: string) => {
                    const template = templates.data?.templates.find((item) => item.id === value);
                    if (template)
                      form.setFieldsValue({
                        analysts: template.analysts,
                        depth: template.depth,
                        language: template.language,
                      });
                  }}
                />
              </Form.Item>
              <p className={styles.muted}>
                {settings.data?.default_template
                  ? `已保存默认配置 v${settings.data.default_template.version}，可选择对应模板使用角色绑定。`
                  : "未保存模板时可沿用本机模型；新模型需先检测。"}{" "}
                <Link to="/settings/models">管理模型</Link>
              </p>
              <Form.Item
                label="分析师"
                name="analysts"
                rules={[{ required: true, type: "array", min: 1, message: "至少选择一类分析师" }]}
              >
                <Checkbox.Group options={analystChoices} />
              </Form.Item>
              <div className={styles.columns}>
                <Form.Item label="研究深度" name="depth">
                  <Select
                    options={[
                      { value: 1, label: "快速 · 1 轮" },
                      { value: 2, label: "标准 · 2 轮" },
                      { value: 3, label: "深入 · 3 轮" },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="报告语言" name="language">
                  <Select
                    options={[
                      { value: "Chinese", label: "简体中文" },
                      { value: "English", label: "English" },
                    ]}
                  />
                </Form.Item>
              </div>
              <Form.Item name="checkpoint" valuePropName="checked">
                <Checkbox>保存检查点，允许兼容状态恢复</Checkbox>
              </Form.Item>
              <Collapse
                items={[
                  {
                    key: "budget",
                    label: "进阶 · 持有状态与执行预算",
                    forceRender: true,
                    children: (
                      <>
                        <Form.Item
                          name="holding_state"
                          label="当前证券持有状态"
                          extra="仅描述本次研究证券，不读取金融账户，也不假设你的全部组合。"
                        >
                          <Select
                            options={[
                              { value: "unknown", label: "未说明" },
                              { value: "not_held", label: "未持有" },
                              { value: "held", label: "已持有" },
                            ]}
                          />
                        </Form.Item>
                        <div className={styles.columns}>
                          <Form.Item
                            name="max_llm_calls"
                            label="模型请求次数上限"
                            rules={[{ required: true }]}
                          >
                            <InputNumber min={1} max={500} precision={0} />
                          </Form.Item>
                          <Form.Item
                            name="max_runtime_seconds"
                            label="执行时长上限（秒）"
                            rules={[{ required: true }]}
                          >
                            <InputNumber min={60} max={14400} precision={0} />
                          </Form.Item>
                        </div>
                        <Form.Item
                          name="max_tokens_total"
                          label="已报告Token总量上限（可选）"
                          extra="上游可能不返回用量，未知用量不能当作零；请求进行中也可能超过边界。预算不等同实际费用。"
                        >
                          <InputNumber
                            min={1}
                            max={10000000}
                            precision={0}
                            placeholder="不设置额外Token上限"
                          />
                        </Form.Item>
                        <p className={styles.muted}>
                          预算在服务端调用边界检查，不保证撤回已发请求。恢复原任务沿用预算；新建研究使用本次确认的值。
                        </p>
                      </>
                    ),
                  },
                ]}
              />
              {contextWarning && <Alert showIcon type="warning" title={contextWarning} />}
              <ResearchContextPicker
                scope={scope}
                ids={contextIds}
                ticker={selectedTicker ?? ""}
                onChange={(ids) => {
                  setContextReady(!ids.length);
                  setContextIds(ids);
                }}
                onValidityChange={setContextReady}
              />
              <p className={styles.muted}>
                启动会调用模型与数据服务。同输入失败重试保留幂等键，避免重复创建任务。
              </p>
              <Button
                type="primary"
                htmlType="submit"
                loading={busy}
                disabled={sourceLoading || sourceError || !contextReady}
              >
                启动 TradingAgents 分析
              </Button>
            </Form>
          </div>
        </section>
        <section className={styles.panel}>
          <div className={styles.toolbar}>
            <h2>完整研究链路</h2>
            <div className={styles.actions}>
              <Tag>12 个角色</Tag>
              <Button
                size="small"
                onClick={() =>
                  workflowAnchor.current?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                查看可视化路径
              </Button>
            </div>
          </div>
          <div className={styles.body}>
            {[
              ["分析师研究", "技术、基本面、新闻与情绪独立取证。"],
              ["多空辩论", "双方依据交锋，研究经理形成汇总。"],
              ["交易提案", "交易员提出条件与计划。"],
              ["风险讨论", "激进、中立与保守观点分别审视风险。"],
              ["最终决策", "保存判断、依据、分歧与完整报告。"],
            ].map(([title, detail]) => (
              <div className={styles.connection} key={title}>
                <h3>{title}</h3>
                <p className={styles.muted}>{detail}</p>
              </div>
            ))}
            <p className={styles.muted}>点击下方路径中的角色，查看其职责、输入、输出与模型分配。</p>
            <p className={styles.muted}>
              切换页面或关闭浏览器不取消服务端任务。<Link to="/reports">查看研究记录</Link>
            </p>
          </div>
        </section>
      </div>
      <div ref={workflowAnchor} id="research-workflow">
        <ResearchWorkflow
          bootstrap={bootstrap}
          analysts={workflowAnalysts ?? bootstrap.defaults.analysts}
          depth={workflowDepth ?? bootstrap.defaults.depth}
          models={workflowModels}
        />
      </div>
    </div>
  );
}

export function RunDetailPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const { runId } = useParams();
  return <RunDetailContent key={runId} bootstrap={bootstrap} runId={runId} />;
}

function RunDetailContent({
  bootstrap,
  runId,
}: {
  bootstrap: BootstrapResponse;
  runId: string | undefined;
}) {
  const navigate = useNavigate(),
    cache = useQueryClient();
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [error, setError] = useState<string>(),
    [busy, setBusy] = useState(false);
  const [selectedTab, setSelectedTab] = useState<string>();
  const run = useQuery({
    queryKey: queryKeys.resource(scope, "research-run", { runId }),
    queryFn: async ({ signal }) =>
      GetResearchRunResponse.parse(await getResearchRun(runId ?? "", { signal })),
    enabled: !!runId,
    retry: shouldRetryQuery,
    refetchInterval: (query) =>
      query.state.data && terminalStatuses.has(query.state.data.status) ? false : 2500,
  });
  const events = useResearchEvents(
    scope,
    run.data?.id,
    !!run.data && !terminalStatuses.has(run.data.status),
  );
  const manifest = useQuery({
    queryKey: queryKeys.resource(scope, "research-manifest", { runId }),
    queryFn: ({ signal }) => getResearchManifest(runId ?? "", { signal }),
    enabled: !!runId,
    retry: shouldRetryQuery,
  });
  const recent = useQuery({
    queryKey: queryKeys.resource(scope, "recent-stock-research", {
      ticker: run.data?.ticker,
      status: "completed",
      limit: 6,
    }),
    queryFn: ({ signal }) =>
      listResearchRuns(
        { ticker: run.data?.ticker ?? "", status: "completed", limit: 6 },
        { signal },
      ),
    enabled: !!run.data?.ticker,
    retry: shouldRetryQuery,
  });
  async function action(kind: "cancel" | "resume") {
    if (!runId) return;
    setBusy(true);
    setError(undefined);
    try {
      if (kind === "cancel") await cancelResearchRun(runId);
      else await resumeResearchRun(runId);
      await cache.invalidateQueries({ queryKey: queryKeys.scope(scope) });
    } catch (failure) {
      setError(failureMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  if (run.isPending)
    return (
      <div className={styles.body}>
        <Spin />
        <p>正在读取研究任务…</p>
      </div>
    );
  if (run.isError || !run.data)
    return (
      <Alert
        type="error"
        showIcon
        title={failureMessage(run.error)}
        action={
          <Button
            onClick={() => {
              void run.refetch();
            }}
          >
            重新加载
          </Button>
        }
      />
    );
  const data = run.data,
    reports = data.reports ?? {},
    keys = [
      ...Object.keys(reportLabels).filter((key) => key in reports),
      ...Object.keys(reports).filter((key) => !(key in reportLabels)),
    ];
  const complete = data.status === "completed",
    active = !terminalStatuses.has(data.status);
  return (
    <div className={styles.stack}>
      <div className={styles.heading}>
        <small>RESEARCH EXECUTION</small>
        <h1>
          {data.ticker} 个股研究{" "}
          <Tag color={complete ? "green" : data.status === "failed" ? "red" : "default"}>
            {statusLabels[data.status] ?? data.status}
          </Tag>
        </h1>
        <p>
          {data.date} · {data.depth} 轮研究 · {safeDate(data.created_at)}
        </p>
      </div>
      {(error || data.error) && (
        <Alert
          showIcon
          type={data.status === "failed" ? "error" : "warning"}
          title={error ?? data.error}
        />
      )}
      {active && <RuntimeReadinessNotice scope={scope} />}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>{stageLabels[data.stage] ?? data.stage}</h2>
          <div className={styles.actions}>
            <Tag>
              {events.connection === "live" ? "实时事件已连接" : active ? "轮询同步中" : "历史任务"}
            </Tag>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                void run.refetch();
              }}
            >
              刷新
            </Button>
            {active && (
              <Button
                danger
                disabled={busy || data.status === "cancelling"}
                onClick={() => {
                  void action("cancel");
                }}
              >
                取消任务
              </Button>
            )}
            {["interrupted", "failed", "cancelled"].includes(data.status) && (
              <Button
                disabled={busy || !data.checkpoint}
                onClick={() => {
                  void action("resume");
                }}
              >
                继续兼容检查点
              </Button>
            )}
            <Dropdown
              menu={{
                items: [
                  { key: "current", label: "更新到今天，使用当前配置…" },
                  { key: "original", label: "按原日期 / 原模板预填…" },
                ],
                onClick: ({ key }) => {
                  void navigate(
                    researchPrefillUrl(data.id, key === "original" ? "original" : "current"),
                  );
                },
              }}
            >
              <Button disabled={busy}>重新研究 · 先核对参数</Button>
            </Dropdown>
            <Link to="/reports">研究记录</Link>
          </div>
        </div>
        <div className={`${styles.body} ${styles.summary}`}>
          {[
            [
              "累计执行耗时",
              data.stats?.elapsed_seconds === undefined
                ? "—"
                : `${Math.floor(data.stats.elapsed_seconds)} 秒`,
            ],
            ["累计模型请求", formattedNumber(data.stats?.llm_calls)],
            ["累计数据工具", formattedNumber(data.stats?.tool_calls)],
            [
              "已报告输入 Tokens",
              `${formattedNumber(data.stats?.tokens_in)}${data.stats?.usage_quality === "complete" ? "" : "（总量未知）"}`,
            ],
            [
              "已报告输出 Tokens",
              `${formattedNumber(data.stats?.tokens_out)}${data.stats?.usage_quality === "complete" ? "" : "（总量未知）"}`,
            ],
            ["有效判断", qualifiedResearchRating(data) ?? "尚无具备资格的判断"],
          ].map(([label, value]) => (
            <div key={label}>
              <small>{label}</small>
              <strong className={styles.mono}>{value}</strong>
            </div>
          ))}
        </div>
        <p className={styles.note}>
          取消在可中止边界生效。恢复原研究与重新研究分开操作，检查点不兼容时会说明原因。
        </p>
        <p className={styles.note}>
          累计用量：{data.stats?.attempt_count ?? "未知"} 次尝试 ·{" "}
          {data.stats?.usage_quality === "complete"
            ? "已报告用量完整"
            : data.stats?.usage_quality === "partial"
              ? "上游用量仅部分返回"
              : "上游用量完整性未知"}{" "}
          · 不确定外部请求 {data.stats?.uncertain_calls ?? "未知"}
          。未知用量不等于零，不据此推算费用。
        </p>
        {data.stats?.current_attempt && (
          <p className={styles.note}>
            当前尝试 #{data.stats.current_attempt.attempt_no ?? "未知"}：模型请求{" "}
            {data.stats.current_attempt.llm_calls} · 工具 {data.stats.current_attempt.tool_calls} ·
            已报告输入/输出Token {data.stats.current_attempt.tokens_in} /{" "}
            {data.stats.current_attempt.tokens_out} · 耗时{" "}
            {Math.floor(data.stats.current_attempt.elapsed_seconds)} 秒；上方数字为跨尝试累计。
          </p>
        )}
        <p className={styles.note}>
          本次目标：
          {objectiveChoices.find((item) => item.value === data.objective)?.label ??
            "历史未记录"} ·{" "}
          {horizonChoices.find((item) => item.value === data.horizon)?.label ?? "期限未记录"} ·
          模型请求上限 {data.max_llm_calls ?? "历史未记录"} / 执行时长{" "}
          {data.max_runtime_seconds ?? "历史未记录"} 秒。
        </p>
      </section>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>报告与执行依据</h2>
          <div className={styles.actions}>
            {complete && data.markdown_url && (
              <Button
                href={`/api/v1/runs/${encodeURIComponent(data.id)}/report.md`}
                icon={<DownloadOutlined />}
              >
                下载 Markdown
              </Button>
            )}
            {complete && data.report_url && (
              <Button
                href={`/api/v1/runs/${encodeURIComponent(data.id)}/report`}
                target="_blank"
                rel="noopener noreferrer"
              >
                打开完整报告
              </Button>
            )}
          </div>
        </div>
        <div className={styles.body}>
          <Tabs
            activeKey={researchDefaultTab(data.status, selectedTab)}
            onChange={setSelectedTab}
            items={[
              {
                key: "conclusion",
                label: "结论与依据",
                children: (
                  <div className={styles.stack}>
                    <Alert
                      showIcon
                      type={complete ? "info" : "warning"}
                      title={`有效判断：${qualifiedResearchRating(data) ?? "尚无具备资格的判断"}`}
                      description={`研究日 ${data.date} · ${statusLabels[data.status] ?? data.status} · 已保存 ${keys.length} 个章节。${complete ? "任务完成不等于所有数据来源完整。" : "此处保留已有产出，不视作完整研究结论。"}`}
                    />
                    <ResearchQualityNotice quality={data.quality} />
                    {!!manifest.data?.request.context_ids?.length && (
                      <ResearchContextPicker
                        scope={scope}
                        ids={manifest.data.request.context_ids}
                        ticker={data.ticker}
                      />
                    )}
                    {!qualifiedResearchRating(data) && (data.raw_rating || data.rating) && (
                      <p className={styles.muted}>
                        原始模型判断：{data.raw_rating ?? data.rating}（未作为有效评级发布）。
                      </p>
                    )}
                    <section>
                      <h3>已保存的最终决策 · 模型原文</h3>
                      <p className={styles.muted}>
                        原文中的评级、价位和仓位需结合上方独立数据资格判断；保留文本不表示其已通过验证。
                      </p>
                      {reports.final_trade_decision ? (
                        <pre className={styles.reportText}>{reports.final_trade_decision}</pre>
                      ) : (
                        <p className={styles.muted}>
                          没有已保存的最终决策。可在阶段报告查看已有依据。
                        </p>
                      )}
                    </section>
                    <section>
                      <h3>模型与配置来源</h3>
                      <p>{modelBindingSummary(modelsFromManifest(manifest.data))}</p>
                      <p className={styles.muted}>
                        历史配置只读自本次运行清单；缺失信息保持未知。数据覆盖与原始证据尚未汇总时，请结合阶段报告和运行配置核查。
                      </p>
                    </section>
                    <section>
                      <h3>近期同股研究 · 手动对照</h3>
                      {recent.isPending ? (
                        <Spin />
                      ) : recent.isError ? (
                        <p className={styles.muted}>近期记录暂不可读。</p>
                      ) : recent.data?.items.filter((item) => item.id !== data.id).length ? (
                        recent.data.items
                          .filter((item) => item.id !== data.id)
                          .slice(0, 3)
                          .map((item) => (
                            <p key={item.id}>
                              <Link to={`/runs/${item.id}`}>
                                {item.ticker} · {item.date} ·{" "}
                                {qualifiedResearchRating(item) ?? "无有效评级 / 未核验"}
                              </Link>{" "}
                              · 创建 {safeDate(item.created_at)}
                            </p>
                          ))
                      ) : (
                        <p className={styles.muted}>暂无其他已完成的同股研究。</p>
                      )}
                      <p className={styles.muted}>
                        报告日期、模型和来源可能不同；这里不自动推断结论变化的原因。
                      </p>
                    </section>
                  </div>
                ),
              },
              {
                key: "workflow",
                label: "研究决策路径",
                children: (
                  <ResearchWorkflow
                    bootstrap={bootstrap}
                    run={data}
                    models={modelsFromManifest(manifest.data)}
                  />
                ),
              },
              {
                key: "evidence",
                label: "工具证据与资格",
                children: (
                  <ResearchEvidencePanel
                    scope={scope}
                    runId={data.id}
                    active={active && researchDefaultTab(data.status, selectedTab) === "evidence"}
                  />
                ),
              },
              {
                key: "viewpoints",
                label: "我的观点与复核",
                children: <ResearchViewpoints scope={scope} run={data} />,
              },
              {
                key: "comparison",
                label: "同股报告对照",
                children: (
                  <ResearchComparisonPanel
                    scope={scope}
                    run={data}
                    candidates={recent.data?.items ?? []}
                    reportLabels={reportLabels}
                  />
                ),
              },
              {
                key: "reports",
                label: `阶段报告 (${keys.length})`,
                children: keys.length ? (
                  <Collapse
                    items={keys.map((key) => ({
                      key,
                      label: reportLabels[key] ?? key,
                      children: <pre className={styles.reportText}>{reports[key]}</pre>,
                    }))}
                    defaultActiveKey={["final_trade_decision", "investment_plan"]}
                  />
                ) : (
                  <p className={styles.muted}>等待分析师提交报告，完成的章节会逐步出现。</p>
                ),
              },
              {
                key: "html",
                label: "完整排版报告",
                disabled: !complete || !data.report_url,
                children:
                  complete && data.report_url ? (
                    <iframe
                      className={styles.reportFrame}
                      src={`/api/v1/runs/${encodeURIComponent(data.id)}/report`}
                      sandbox=""
                      title={`${data.ticker} 完整研究报告`}
                    />
                  ) : null,
              },
              {
                key: "events",
                label: "执行动态",
                children: (
                  <div className={styles.eventLog}>
                    {events.events.length
                      ? events.events.map((event) => (
                          <div key={event.event_id}>
                            <time>{safeDate(event.timestamp)}</time>
                            <span>
                              {event.type === "progress"
                                ? event.payload.label
                                : event.type === "report"
                                  ? `${reportLabels[event.payload.section] ?? event.payload.section}已更新`
                                  : event.type === "state"
                                    ? statusLabels[event.payload.status]
                                    : event.type === "error"
                                      ? event.payload.message
                                      : event.type === "resync_required"
                                        ? event.payload.reason
                                        : "用量统计已更新"}
                            </span>
                          </div>
                        ))
                      : data.events?.map((event) => (
                          <div key={event.id}>
                            <time>{safeDate(event.time)}</time>
                            <span>{event.message}</span>
                          </div>
                        ))}
                  </div>
                ),
              },
              {
                key: "manifest",
                label: "运行配置",
                children: manifest.isError ? (
                  <p className={styles.muted}>运行配置暂不可读取。</p>
                ) : (
                  <pre className={styles.reportText}>
                    {manifest.data ? JSON.stringify(manifest.data, null, 2) : "读取中…"}
                  </pre>
                ),
              },
            ]}
          />
        </div>
      </section>
    </div>
  );
}

export function ResearchHistoryPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const [ticker, setTicker] = useState("");
  const [status, setStatus] = useState<ListResearchRunsStatus>();
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors[cursors.length - 1];
  const history = useQuery({
    queryKey: queryKeys.resource(scope, "research-history", { ticker, status, cursor }),
    queryFn: ({ signal }) =>
      listResearchRuns(historyParameters(ticker, status, cursor), { signal }),
    retry: shouldRetryQuery,
  });
  return (
    <div className={styles.stack}>
      <div className={styles.heading}>
        <small>RESEARCH LIBRARY</small>
        <h1>研究记录</h1>
        <p>分页查看持久化研究，保留报告与当次配置边界。</p>
      </div>
      {history.isError && (
        <Alert
          type="error"
          title={failureMessage(history.error)}
          action={
            <Button
              onClick={() => {
                void history.refetch();
              }}
            >
              重试
            </Button>
          }
        />
      )}
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <h2>历史研究</h2>
          <div className={styles.actions}>
            <Input.Search
              aria-label="按股票代码搜索报告"
              placeholder="股票代码"
              onSearch={(value) => {
                setTicker(value.trim().toUpperCase());
                setCursors([undefined]);
              }}
              allowClear
              style={{ width: 180 }}
            />
            <Select
              aria-label="按任务状态筛选"
              value={status}
              allowClear
              placeholder="全部状态"
              style={{ width: 140 }}
              options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
              onChange={(value: ListResearchRunsStatus | undefined) => {
                setStatus(value);
                setCursors([undefined]);
              }}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                void history.refetch();
              }}
            >
              刷新
            </Button>
          </div>
        </div>
        <div className={styles.table}>
          <Table<RunSnapshot>
            rowKey="id"
            size="small"
            loading={history.isPending}
            dataSource={history.data?.items ?? []}
            pagination={false}
            locale={{ emptyText: "没有匹配的研究记录" }}
            columns={[
              {
                title: "股票 / 日期",
                key: "ticker",
                render: (_, item) => (
                  <>
                    <Link to={`/runs/${item.id}`} className={styles.mono}>
                      {item.ticker}
                    </Link>
                    <div className={styles.muted}>{item.date}</div>
                  </>
                ),
              },
              {
                title: "状态",
                key: "status",
                render: (_, item) => (
                  <Tag color={item.status === "completed" ? "green" : "default"}>
                    {statusLabels[item.status] ?? item.status}
                  </Tag>
                ),
              },
              {
                title: "判断",
                key: "rating",
                render: (_, item) =>
                  qualifiedResearchRating(item) ??
                  (item.raw_rating || item.rating ? "原始判断未核验" : "—"),
              },
              { title: "创建时间", key: "time", render: (_, item) => safeDate(item.created_at) },
              {
                title: "报告",
                key: "report",
                render: (_, item) => <Link to={`/runs/${item.id}`}>打开详情</Link>,
              },
            ]}
          />
        </div>
        <div className={styles.toolbar}>
          <span className={styles.muted}>第 {cursors.length} 页</span>
          <div className={styles.actions}>
            <Button
              disabled={cursors.length === 1 || history.isFetching}
              onClick={() => setCursors((old) => old.slice(0, -1))}
            >
              上一页
            </Button>
            <Button
              disabled={!history.data?.next_cursor || history.isFetching}
              onClick={() => {
                if (history.data?.next_cursor)
                  setCursors((old) => [...old, history.data.next_cursor ?? undefined]);
              }}
            >
              下一页
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
