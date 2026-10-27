import { Alert, Button, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getRuntimeReadiness } from "../../shared/api/generated/client";
import type { ResearchQualitySummary } from "../../shared/api/generated/models";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import styles from "../../shared/ui/workbench.module.css";

export function RuntimeReadinessNotice({ scope }: { scope: QueryScope }) {
  const readiness = useQuery({
    queryKey: queryKeys.resource(scope, "runtime-readiness"),
    queryFn: ({ signal }) => getRuntimeReadiness({ signal }),
    retry: shouldRetryQuery,
    refetchInterval: 10_000,
  });
  if (readiness.isPending)
    return (
      <p className={styles.muted}>
        <Spin size="small" /> 正在读取研究执行通道状态…
      </p>
    );
  if (!readiness.data || readiness.isError)
    return (
      <Alert
        showIcon
        type="warning"
        title="研究执行通道状态暂未确认"
        description="API可以响应不代表worker已经在线。提交后可能排队，请先刷新确认。"
        action={
          <Button
            onClick={() => {
              void readiness.refetch();
            }}
          >
            刷新状态
          </Button>
        }
      />
    );
  const { worker, queue, checked_at } = readiness.data;
  return (
    <Alert
      showIcon
      type={worker.state === "ready" ? "info" : "warning"}
      title={
        worker.state === "offline"
          ? "研究worker离线 · 新任务只能排队"
          : worker.state === "busy"
            ? "研究worker忙碌 · 新任务将排队"
            : "研究worker在线"
      }
      description={
        <div>
          <p>
            空闲 {worker.idle_workers} / 在线 {worker.active_workers} · 排队 {queue.queued_count} ·
            执行 {queue.running_count} · 待恢复 {queue.stalled_count}。
          </p>
          <p>
            {worker.state === "offline"
              ? "API仍可保存排队任务，但worker恢复前不会开始研究；排队时间不等于模型执行时间。"
              : "模型和数据账号仍按本次研究配置检查；worker在线不代表供应商权限或数据质量已通过。"}
          </p>
          <small className={styles.muted}>
            检查 {checked_at} · 最近心跳 {worker.last_heartbeat_at ?? "尚未收到"}
          </small>
        </div>
      }
    />
  );
}

const qualityLabels = {
  usable: "证据可用",
  degraded: "部分证据 / 降级",
  insufficient: "证据不足",
  unverified: "尚未核验",
};

export function ResearchQualityNotice({ quality }: { quality?: ResearchQualitySummary }) {
  if (!quality)
    return (
      <Alert
        showIcon
        type="warning"
        title="数据资格尚未确认"
        description="历史报告原文保留；缺少资格记录时，不把原始模型评级、目标价或仓位当作已核验结论。"
      />
    );
  return (
    <Alert
      showIcon
      type={quality.status === "usable" ? "info" : "warning"}
      title={`数据资格 · ${qualityLabels[quality.status]}`}
      description={
        <div>
          <div className={styles.actions}>
            <Tag>证据 {quality.evidence_count}</Tag>
            <Tag>价格 {quality.validated_price ? "已验证" : "未验证"}</Tag>
            <Tag>评级 {quality.eligible_for_rating ? "具备数据资格" : "不发布为有效结论"}</Tag>
            <Tag>价位 {quality.eligible_for_levels ? "具备数据资格" : "不具备资格"}</Tag>
            <Tag>仓位 {quality.eligible_for_sizing ? "具备数据资格" : "不具备资格"}</Tag>
          </div>
          <p>
            可用类别：{quality.available_categories.join("、") || "暂无"}；缺失：
            {quality.missing_categories.join("、") || "未列出缺失类别"}。
          </p>
          {quality.issues.map((issue) => (
            <p key={`${issue.code}:${issue.message}`}>{issue.message}</p>
          ))}
          <p className={styles.muted}>
            {quality.scope_notice ?? "资格只反映捕获证据检查，不证明模型判断正确或适合个人。"}
          </p>
        </div>
      }
    />
  );
}
