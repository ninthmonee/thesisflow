import { useState } from "react";
import { Alert, Collapse, Select, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { compareResearchRuns } from "../../shared/api/generated/client";
import type { RunSnapshot } from "../../shared/api/generated/models";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { qualifiedResearchRating } from "./research-setup";
import styles from "../../shared/ui/workbench.module.css";

const fieldLabels: Record<string, string> = {
  date: "研究日期",
  analysts: "分析师选择",
  depth: "轮数",
  language: "语言",
  template_version_id: "模板版本",
  objective: "目标",
  horizon: "期限",
  holding_state: "当前证券持有状态",
  context_ids: "附加资料",
  max_llm_calls: "模型请求预算",
  max_runtime_seconds: "时长预算",
  max_tokens_total: "Token预算",
  checkpoint: "检查点设置",
};

export function ResearchComparisonPanel({
  scope,
  run,
  candidates,
  reportLabels,
}: {
  scope: QueryScope;
  run: RunSnapshot;
  candidates: readonly RunSnapshot[];
  reportLabels: Readonly<Record<string, string>>;
}) {
  const [otherId, setOtherId] = useState<string>();
  const options = candidates.filter((item) => item.id !== run.id && item.ticker === run.ticker);
  const comparison = useQuery({
    queryKey: queryKeys.resource(scope, "research-comparison", { runId: run.id, otherId }),
    queryFn: ({ signal }) =>
      compareResearchRuns(run.id, { other_run_id: otherId ?? "" }, { signal }),
    enabled: !!otherId,
    retry: shouldRetryQuery,
  });
  return (
    <div className={styles.stack}>
      <Alert
        showIcon
        type="info"
        title="同股研究文本与配置对照"
        description="比较的是保存的输入字段、模型标识、已捕获证据和报告文本，不是投资业绩，也不能把不同来源、日期或模型的差异解释为模型优劣。"
      />
      <Select
        allowClear
        placeholder="选择另一条近期同股研究"
        value={otherId}
        onChange={setOtherId}
        options={options.map((item) => ({
          value: item.id,
          label: `${item.date} · ${qualifiedResearchRating(item) ?? "无有效评级 / 未核验"} · ${item.created_at}`,
        }))}
        notFoundContent="暂无其他已完成的同股研究"
      />
      {otherId &&
        (comparison.isPending ? (
          <Spin />
        ) : comparison.isError ? (
          <Alert
            type="warning"
            showIcon
            title={comparison.error instanceof ApiError ? comparison.error.message : "比较暂不可读"}
          />
        ) : comparison.data ? (
          <>
            <div className={styles.actions}>
              <Tag>模型 {comparison.data.model_changed ? "存在变化" : "未检测到变化"}</Tag>
              <Tag>
                捕获证据{" "}
                {comparison.data.same_captured_evidence == null
                  ? "无法确认一致性"
                  : comparison.data.same_captured_evidence
                    ? "捕获集合一致"
                    : "不同"}
              </Tag>
              <Link target="_blank" rel="noreferrer" to={`/runs/${otherId}`}>
                另开对照报告
              </Link>
            </div>
            <p>
              有变化的输入字段：
              {comparison.data.request_changes
                .map((field) => fieldLabels[field] ?? field)
                .join("、") || "未检测到字段变化"}
              。
            </p>
            <p className={styles.muted}>
              {comparison.data.scope_notice}。捕获集合一致也不表示全部外部输入或模型采样完全一致。
            </p>
            <Collapse
              items={comparison.data.changed_sections.map((section) => ({
                key: section,
                label: reportLabels[section] ?? section,
                children: (
                  <pre className={styles.reportText}>
                    {comparison.data?.section_diffs[section] ?? "此章节没有可展示的文本差异。"}
                  </pre>
                ),
              }))}
            />
            {!comparison.data.changed_sections.length && (
              <p className={styles.muted}>已保存的章节文本没有差异。</p>
            )}
          </>
        ) : null)}
    </div>
  );
}
