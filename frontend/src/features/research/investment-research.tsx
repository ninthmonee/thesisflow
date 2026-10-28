import { useEffect } from "react";
import { Alert, Checkbox, Form, Select, Spin, Tag } from "antd";
import type { FormInstance } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getInvestmentPolicy, listInvestmentPlans } from "../../shared/api/generated/client";
import type {
  CreateResearchRequest,
  ManifestResponse,
  RunSnapshot,
} from "../../shared/api/generated/models";
import type { QueryScope } from "../../shared/api/query-keys";
import { queryKeys } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import styles from "../../shared/ui/workbench.module.css";

export function InvestmentResearchFields({
  form,
  scope,
  ticker,
  analysisDate,
  onReady,
}: {
  form: FormInstance<CreateResearchRequest>;
  scope: QueryScope;
  ticker: string;
  analysisDate: string;
  onReady: (ready: boolean) => void;
}) {
  const applied: boolean | undefined = Form.useWatch("apply_investment_system", form);
  const planId: string | null | undefined = Form.useWatch("investment_plan_id", form);
  const enabled = applied !== false;
  const symbol = ticker.trim().toUpperCase();
  const policy = useQuery({
    queryKey: queryKeys.resource(scope, "investment-policy"),
    queryFn: ({ signal }) => getInvestmentPolicy({ signal }),
    enabled,
    retry: shouldRetryQuery,
  });
  const plans = useQuery({
    queryKey: queryKeys.resource(scope, "investment-plans", { ticker: symbol }),
    queryFn: ({ signal }) =>
      listInvestmentPlans({ ticker: symbol, include_archived: false }, { signal }),
    enabled: enabled && !!symbol,
    retry: shouldRetryQuery,
  });
  const selected = plans.data?.items.find((item) => item.id === planId);
  const planValid =
    !planId || (!!selected && selected.ticker === symbol && selected.analysis_date <= analysisDate);
  const ready =
    !enabled ||
    (policy.isSuccess && policy.data.enabled === true && (!planId || plans.isSuccess) && planValid);
  useEffect(() => onReady(ready), [ready, onReady]);
  return (
    <section className={styles.stack}>
      <Form.Item name="apply_investment_system" valuePropName="checked">
        <Checkbox>应用我的投资体系 · 先守本金、先看周期、用纪律决策</Checkbox>
      </Form.Item>
      {enabled && (
        <>
          {policy.isPending ? (
            <Spin size="small" />
          ) : policy.isError ? (
            <Alert
              showIcon
              type="error"
              title="投资体系暂时无法读取"
              description="可在投资体系页重试，或取消本次应用；尚未启动研究。"
            />
          ) : (
            <Alert
              showIcon
              type={policy.data.enabled ? "info" : "warning"}
              title={`${policy.data.name} · ${policy.data.saved ? `规则版本 ${policy.data.revision}` : "初始原则，数值待配置"}`}
              description={
                policy.data.enabled
                  ? "按宏观、行业与估值周期→公司质量→价格与安全边际→风险与仓位→操作与复盘研究。保存本次规则版本；个人资产金额、现金和生活税务备忘不作为模型输入。"
                  : "体系已停用。请在体系页启用，或取消本次应用。"
              }
            />
          )}
          <Form.Item
            name="investment_plan_id"
            label="附加同股决策计划（可选）"
            extra="不附加也可研究。草稿观点和手填价位仍需取证；历史研究不能附加日期更晚的计划。"
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              loading={plans.isFetching}
              placeholder="仅应用体系规则，不附加决策草稿"
              options={(plans.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.ticker} · ${item.title || "决策计划"} · ${item.analysis_date} · v${item.revision}`,
                disabled: item.analysis_date > analysisDate,
              }))}
            />
          </Form.Item>
          {!planValid && (
            <Alert showIcon type="warning" title="请重新选择本次证券和日期可用的决策计划" />
          )}
          {plans.isError && (
            <Alert
              showIcon
              type="warning"
              title="决策计划暂时无法读取"
              description="不选择计划仍可仅应用体系规则。"
            />
          )}
          <p className={styles.muted}>
            <Link to="/investment">编辑体系、设置阈值与决策计划</Link>
            。未填写的概率、估值和仓位不补成确定答案；新研究使用启动时的当前规则，历史规则保留在原任务中。
          </p>
        </>
      )}
    </section>
  );
}

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function InvestmentRunPanel({
  manifest,
  run,
}: {
  manifest: ManifestResponse | undefined;
  run: RunSnapshot;
}) {
  if (!manifest) return <Spin tip="正在读取本次投资体系输入…" />;
  const packet = object(manifest?.selection.investment_system);
  const snapshot = object(packet?.snapshot);
  const policy = object(snapshot?.policy);
  const plan = object(snapshot?.plan);
  const applied = manifest?.request.apply_investment_system === true && !!snapshot;
  const createUrl = `/investment?${new URLSearchParams({
    ticker: run.ticker,
    analysis_date: run.date,
    ...(run.status === "completed" ? { run_id: run.id } : {}),
  })}`;
  return (
    <div className={styles.stack}>
      <Alert
        showIcon
        type="info"
        title={applied ? "本次研究已冻结投资体系输入" : "本次研究未应用投资体系"}
        description={
          applied
            ? "这里只说明采用了哪些研究规则，不自动证明模型逐项遵守，也不将检查表变成买卖许可。"
            : "旧报告继续保留。可以据此建立自己的决策计划，再明确选择体系发起新的研究。"
        }
      />
      {applied && (
        <>
          <div className={styles.row}>
            <Tag>{typeof policy?.name === "string" ? policy.name : "投资体系"}</Tag>
            <Tag>规则版本 {typeof policy?.revision === "number" ? policy.revision : "未知"}</Tag>
            <Tag>{plan ? "含冻结决策草稿" : "仅规则与研究问题"}</Tag>
          </div>
          <p className={styles.muted}>
            修改当前体系不改写这份历史输入。复盘时分别检验证据、判断、执行纪律与结果。
          </p>
          {run.reports?.investment_system_checklist ? (
            <pre className={styles.reportText}>{run.reports.investment_system_checklist}</pre>
          ) : (
            <p>
              体系已作为研究要求保存。执行器尚未归档核对章节；不能据此认为六个问题已经得到验证。
            </p>
          )}
          {typeof packet?.prompt === "string" && (
            <details>
              <summary>查看本次冻结的研究要求</summary>
              <pre className={styles.reportText}>{packet.prompt}</pre>
            </details>
          )}
        </>
      )}
      <p>
        <Link to={createUrl}>基于这份研究建立决策计划或复盘</Link> ·{" "}
        <Link to="/investment">我的投资体系</Link>
      </p>
    </div>
  );
}
