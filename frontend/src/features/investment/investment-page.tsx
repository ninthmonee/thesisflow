import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Input,
  Popconfirm,
  Select,
  Spin,
  Tabs,
  Tag,
  message,
} from "antd";
import { EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  createInvestmentPlan,
  getInvestmentPolicy,
  listInvestmentPlans,
  updateInvestmentPlan,
  updateInvestmentPolicy,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  InvestmentPlanView,
  InvestmentPolicyView,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { PolicyEditor, PlanEditor } from "./investment-editors";
import { InvestmentPrinciples } from "./investment-principles";
import { InvestmentEvaluationView } from "./investment-evaluation";
import {
  actionRuleLabels,
  filterInvestmentPlans,
  formatInvestmentNumber,
  investmentPlanUpdate,
  investmentPlanWrite,
  investmentEvaluationLabels,
  investmentPolicyUpdate,
  investmentStatusLabels,
  newInvestmentPlan,
  riskLimitLabels,
} from "./investment-fields";
import type { PlanFields } from "./investment-fields";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./investment.module.css";

const failureMessage = (error: unknown) =>
  error instanceof ApiError || error instanceof Error ? error.message : "操作未完成，请稍后重试。";
const savedTime = (value: string | undefined | null) =>
  value ? new Date(value).toLocaleString() : "尚未保存";
const styleLabels = {
  long_term: "长期持有",
  left_side: "左侧分批",
  trend: "趋势确认",
  swing: "波段观察",
};

interface PlanEditorState {
  current: InvestmentPlanView | null;
  initial: PlanFields;
  review: boolean;
}

function PlanCard({
  plan,
  busy,
  onEdit,
  onStatus,
}: {
  plan: InvestmentPlanView;
  busy: boolean;
  onEdit: (plan: InvestmentPlanView, review?: boolean) => void;
  onStatus: (plan: InvestmentPlanView, status: "archived" | "watching") => Promise<void>;
}) {
  return (
    <article className={styles.planRow}>
      <div className={styles.planHeading}>
        <div>
          <h3>
            {plan.ticker} {plan.title && <span>· {plan.title}</span>}
          </h3>
          <div className={shared.row}>
            <Tag>{investmentStatusLabels[plan.status ?? "draft"]}</Tag>
            {plan.theme && <Tag>{plan.theme}</Tag>}
            <Tag color={plan.evaluation.status === "breach" ? "warning" : "default"}>
              {investmentEvaluationLabels[plan.evaluation.status]}
            </Tag>
            {plan.review_due && <Tag color="warning">已到手动复核日期</Tag>}
          </div>
        </div>
        <div className={shared.actions}>
          <Button size="small" disabled={busy} onClick={() => onEdit(plan)}>
            编辑
          </Button>
          <Button
            size="small"
            disabled={busy || plan.status === "archived"}
            onClick={() => onEdit(plan, true)}
          >
            复盘
          </Button>
          {plan.status !== "archived" && (
            <Link
              to={`/research/new?ticker=${encodeURIComponent(plan.ticker)}&investment_plan_id=${encodeURIComponent(plan.id)}`}
            >
              按体系研究
            </Link>
          )}
          {plan.run_id && <Link to={`/runs/${encodeURIComponent(plan.run_id)}`}>关联研究</Link>}
          {plan.status === "archived" ? (
            <Button
              size="small"
              disabled={busy}
              onClick={() => {
                void onStatus(plan, "watching");
              }}
            >
              恢复为观察
            </Button>
          ) : (
            <Popconfirm
              title="归档这份决策计划？"
              description="归档后仍可在归档筛选中恢复，不删除历史研究。"
              onConfirm={() => {
                void onStatus(plan, "archived");
              }}
            >
              <Button size="small" disabled={busy}>
                归档
              </Button>
            </Popconfirm>
          )}
        </div>
      </div>
      <p className={shared.muted}>
        分析日 {plan.analysis_date} · 体系 v{plan.policy_snapshot.revision} · 计划 v{plan.revision}{" "}
        · 更新 {savedTime(plan.updated_at)}
      </p>
      <p className={styles.planExcerpt}>
        {plan.business || "先记录公司如何赚钱、行业趋势和护城河；缺少证据的判断保持待补充。"}
      </p>
      <div className={styles.metrics}>
        <div className={styles.metric}>
          <small>安全边际 · 本人估值假设</small>
          <strong>{formatInvestmentNumber(plan.evaluation.margin_of_safety_pct, "%")}</strong>
        </div>
        <div className={styles.metric}>
          <small>计划单股仓位</small>
          <strong>{formatInvestmentNumber(plan.risk_inputs?.target_position_pct, "%")}</strong>
        </div>
        <div className={styles.metric}>
          <small>下次手动复核</small>
          <strong style={{ fontSize: 13 }}>{plan.review_on || "未设置"}</strong>
        </div>
        <div className={styles.metric}>
          <small>六问记录进度</small>
          <strong>
            {plan.evaluation.questions.filter((item) => item.status === "recorded").length} /{" "}
            {plan.evaluation.questions.length}
          </strong>
        </div>
      </div>
      <Collapse
        items={[
          {
            key: "evaluation",
            label: "六问自检、情景与风险边界",
            children: <InvestmentEvaluationView evaluation={plan.evaluation} />,
          },
          {
            key: "review",
            label: "执行纪律、失效条件与复盘记录",
            children: (
              <div className={shared.stack}>
                {actionRuleLabels.map(([key, label]) => (
                  <section key={key}>
                    <h4>{label}</h4>
                    <pre className={shared.reportText}>
                      {plan.action_rules?.[key] || "尚未记录"}
                    </pre>
                  </section>
                ))}
                <section>
                  <h4>核心假设失效条件</h4>
                  <pre className={shared.reportText}>{plan.invalidation || "尚未记录"}</pre>
                </section>
                <section>
                  <h4>复盘记录</h4>
                  <pre className={shared.reportText}>{plan.review_notes || "尚未记录"}</pre>
                  <p className={shared.muted}>最近本人复盘：{savedTime(plan.reviewed_at)}</p>
                </section>
                <p className={shared.muted}>{plan.scope_notice}</p>
              </div>
            ),
          },
        ]}
      />
    </article>
  );
}

export function InvestmentPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const cache = useQueryClient();
  const [params] = useSearchParams();
  const requestedTicker = params.get("ticker") ?? "";
  const requestedRun = params.get("run_id");
  const requestedDate = params.get("analysis_date");
  const prefillKey = `${requestedTicker}:${requestedRun ?? ""}:${requestedDate ?? ""}`;
  const [prefilled, setPrefilled] = useState<string>();
  const [tab, setTab] = useState(requestedTicker ? "plans" : "policy");
  const [policyEditor, setPolicyEditor] = useState<InvestmentPolicyView>();
  const [planEditor, setPlanEditor] = useState<PlanEditorState>();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [dueOnly, setDueOnly] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [editorError, setEditorError] = useState<string>();
  const [notices, noticeContext] = message.useMessage();
  const policy = useQuery({
    queryKey: queryKeys.resource(scope, "investment-policy"),
    queryFn: ({ signal }) => getInvestmentPolicy({ signal }),
    retry: shouldRetryQuery,
  });
  const plans = useQuery({
    queryKey: queryKeys.resource(scope, "investment-plans", { includeArchived: true }),
    queryFn: ({ signal }) => listInvestmentPlans({ include_archived: true }, { signal }),
    retry: shouldRetryQuery,
  });
  const [today] = useState(
    () =>
      bootstrap.today ??
      new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date()),
  );
  const urlEditor = useMemo<PlanEditorState | undefined>(() => {
    if (requestedTicker && policy.data && prefilled !== prefillKey) {
      return {
        current: null,
        initial: newInvestmentPlan(
          policy.data,
          requestedDate || today,
          requestedTicker,
          requestedRun,
        ),
        review: false,
      };
    }
  }, [policy.data, prefilled, prefillKey, requestedTicker, requestedRun, requestedDate, today]);
  const activeEditor = planEditor ?? urlEditor;

  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "investment-policy") }),
      cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "investment-plans") }),
    ]);
  }
  function editPlan(current: InvestmentPlanView | null, review = false) {
    if (!policy.data) return;
    setPrefilled(prefillKey);
    setEditorError(undefined);
    setPlanEditor({
      current,
      initial: current
        ? { ...investmentPlanWrite(current), rebase_policy: false, mark_reviewed: review }
        : newInvestmentPlan(policy.data, today),
      review,
    });
  }
  async function savePolicy(fields: InvestmentPolicyView) {
    if (!policyEditor || busy) return;
    setBusy("policy");
    setEditorError(undefined);
    try {
      await updateInvestmentPolicy(investmentPolicyUpdate(fields, policyEditor.revision));
      setPolicyEditor(undefined);
      await refresh();
      void notices.success("投资体系新版本已保存，原有计划快照保持不变。");
    } catch (failure) {
      setEditorError(failureMessage(failure));
    } finally {
      setBusy(undefined);
    }
  }
  async function savePlan(fields: PlanFields) {
    if (!activeEditor || busy) return;
    setBusy("plan");
    setEditorError(undefined);
    try {
      if (activeEditor.current)
        await updateInvestmentPlan(
          activeEditor.current.id,
          investmentPlanUpdate(fields, activeEditor.current.revision),
        );
      else await createInvestmentPlan(investmentPlanWrite(fields));
      setPlanEditor(undefined);
      setPrefilled(prefillKey);
      await refresh();
      void notices.success(
        fields.mark_reviewed
          ? "已保存本人的复盘记录。"
          : "决策计划已保存，检查结果按本人假设计算。",
      );
    } catch (failure) {
      setEditorError(failureMessage(failure));
    } finally {
      setBusy(undefined);
    }
  }
  async function setPlanStatus(plan: InvestmentPlanView, nextStatus: "archived" | "watching") {
    if (busy) return;
    setBusy(plan.id);
    setError(undefined);
    try {
      await updateInvestmentPlan(
        plan.id,
        investmentPlanUpdate({ ...investmentPlanWrite(plan), status: nextStatus }, plan.revision),
      );
      await refresh();
      void notices.success(
        nextStatus === "archived" ? "已归档，可从归档筛选恢复。" : "已恢复为观察状态。",
      );
    } catch (failure) {
      setError(failureMessage(failure));
    } finally {
      setBusy(undefined);
    }
  }
  const rows = plans.data?.items ?? [];
  const visible = filterInvestmentPlans(rows, {
    search,
    status,
    dueOnly: tab === "reviews" ? true : dueOnly,
    includeArchived,
  });
  const dueCount = rows.filter((item) => item.review_due && item.status !== "archived").length;

  function planList(review = false) {
    return (
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <div>
            <h2>{review ? "到期待复盘" : "我的个股决策计划"}</h2>
            <span className={shared.muted}>先记录假设，再研究证据，最后由本人决定。</span>
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!!busy || !policy.data}
            onClick={() => editPlan(null)}
          >
            新增决策计划
          </Button>
        </div>
        <div className={shared.body}>
          <div className={shared.row}>
            <Input
              allowClear
              aria-label="搜索决策计划"
              placeholder="搜索代码、主题或计划名称"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              style={{ width: 280 }}
            />
            <Select
              aria-label="筛选计划状态"
              value={status}
              onChange={setStatus}
              style={{ width: 150 }}
              options={[
                { value: "all", label: "全部状态" },
                ...Object.entries(investmentStatusLabels).map(([value, label]) => ({
                  value,
                  label,
                })),
              ]}
            />
            {!review && (
              <Checkbox checked={dueOnly} onChange={(event) => setDueOnly(event.target.checked)}>
                只看到期复核
              </Checkbox>
            )}
            {!review && (
              <Checkbox
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
              >
                显示已归档
              </Checkbox>
            )}
          </div>
          {review && (
            <p className={shared.muted}>
              这里依据你填写的复核日期筛选；没有后台行情监控或自动提醒。其他已复盘记录可在“个股决策”中按状态查看。
            </p>
          )}
          <p className={shared.muted}>
            列表最多显示最近 200 份计划；当前搜索与数量基于已读取的记录。
          </p>
        </div>
        {plans.isPending ? (
          <div className={shared.body}>
            <Spin />
          </div>
        ) : plans.isError ? (
          <div className={shared.body}>
            <Alert
              showIcon
              type="warning"
              title={failureMessage(plans.error)}
              action={
                <Button
                  onClick={() => {
                    void plans.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : visible.length ? (
          visible.map((item) => (
            <PlanCard
              key={item.id}
              plan={item}
              busy={!!busy}
              onEdit={editPlan}
              onStatus={setPlanStatus}
            />
          ))
        ) : (
          <div className={styles.empty}>
            {review
              ? "当前筛选下没有到期待复盘的计划。"
              : "还没有符合条件的计划。先添加关注的股票或 ETF，从草稿开始补齐六个问题。"}
          </div>
        )}
      </section>
    );
  }
  return (
    <div className={shared.stack}>
      {noticeContext}
      <header className={styles.hero}>
        <div>
          <small>PERSONAL INVESTMENT SYSTEM</small>
          <h1>我的投资体系</h1>
          <p>
            把长期目标落实到每一次研究：先看周期，再选公司；分别判断质量、估值与安全边际，检查风险后再作决定。
          </p>
        </div>
        <div className={styles.creed}>
          <div>
            先守本金，再追收益<span>本金 × 收益 × 时间</span>
          </div>
          <div>
            用纪律代替情绪，用概率代替预测<span>记录假设 · 检查边界 · 手动复盘</span>
          </div>
        </div>
      </header>
      {error && <Alert showIcon type="warning" title={error} />}
      <Tabs
        activeKey={tab}
        onChange={setTab}
        tabBarExtraContent={
          <Button
            icon={<ReloadOutlined />}
            disabled={!!busy}
            onClick={() => {
              void refresh();
            }}
          >
            刷新
          </Button>
        }
        items={[
          {
            key: "policy",
            label: "体系与个人纪律",
            children: (
              <div className={shared.stack}>
                <section className={shared.panel}>
                  <div className={shared.toolbar}>
                    <div>
                      <h2>{policy.data?.name ?? "我的投资体系"}</h2>
                      <span className={shared.muted}>
                        {policy.data?.saved
                          ? `个人版本 v${policy.data.revision} · ${savedTime(policy.data.updated_at)}`
                          : "原则已备好，具体风险阈值由本人填写"}
                      </span>
                    </div>
                    <Button
                      type="primary"
                      icon={<EditOutlined />}
                      disabled={!!busy || !policy.data}
                      onClick={() => {
                        setEditorError(undefined);
                        setPolicyEditor(policy.data);
                      }}
                    >
                      编辑体系与风险边界
                    </Button>
                  </div>
                  <div className={shared.body}>
                    {policy.isPending ? (
                      <Spin />
                    ) : policy.isError ? (
                      <Alert
                        showIcon
                        type="warning"
                        title={failureMessage(policy.error)}
                        action={
                          <Button
                            onClick={() => {
                              void policy.refetch();
                            }}
                          >
                            重试
                          </Button>
                        }
                      />
                    ) : (
                      <>
                        <div className={shared.row}>
                          <Tag>{styleLabels[policy.data?.style ?? "long_term"]}</Tag>
                          <Tag color={policy.data?.enabled ? "cyan" : "default"}>
                            {policy.data?.enabled ? "可用于新研究" : "新研究已停用"}
                          </Tag>
                          <Tag>
                            复核间隔{" "}
                            {policy.data?.review_interval_days
                              ? `${policy.data.review_interval_days} 天`
                              : "由你设置"}
                          </Tag>
                        </div>
                        <p className={shared.muted}>{policy.data?.objective}</p>
                        <InvestmentPrinciples values={policy.data?.principles} />
                      </>
                    )}
                  </div>
                </section>
                <section className={shared.panel}>
                  <div className={shared.toolbar}>
                    <h2>个人风险边界</h2>
                    <span className={shared.muted}>留空保持未知，不视为通过</span>
                  </div>
                  <div className={`${shared.body} ${styles.metrics}`}>
                    {riskLimitLabels.map(([key, label, unit]) => (
                      <div key={key} className={styles.metric}>
                        <small>{label}</small>
                        <strong>
                          {formatInvestmentNumber(policy.data?.risk_limits?.[key], unit)}
                        </strong>
                      </div>
                    ))}
                  </div>
                </section>
                <Alert
                  showIcon
                  type="info"
                  title="体系贯穿研究与复盘"
                  description="新研究可明确附加当前体系，并冻结所选决策计划。模型按六个问题整理依据与未知；资金纪律由确定性计算检查，不把概率、公允价值或仓位假设包装成已验证结论。"
                />
              </div>
            ),
          },
          {
            key: "plans",
            label: `个股决策 · ${rows.filter((item) => item.status !== "archived").length}`,
            children: planList(),
          },
          { key: "reviews", label: `到期复盘 · ${dueCount}`, children: planList(true) },
        ]}
      />
      {policyEditor && (
        <PolicyEditor
          policy={policyEditor}
          busy={busy === "policy"}
          error={editorError}
          onCancel={() => {
            if (!busy) setPolicyEditor(undefined);
          }}
          onSave={savePolicy}
        />
      )}
      {activeEditor && policy.data && (
        <PlanEditor
          key={`${activeEditor.current?.id ?? "new"}:${activeEditor.review}`}
          initial={activeEditor.initial}
          current={activeEditor.current}
          policy={policy.data}
          review={activeEditor.review}
          busy={busy === "plan"}
          error={editorError}
          onCancel={() => {
            if (!busy) {
              setPlanEditor(undefined);
              setPrefilled(prefillKey);
            }
          }}
          onSave={savePlan}
        />
      )}
    </div>
  );
}
