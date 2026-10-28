import { Alert, Tag } from "antd";
import type { ManifestResponse, RunSnapshot } from "../../shared/api/generated/models";
import type { ResearchEvent } from "../../shared/api/research-events";
import { runtimeManifestSummary } from "./runtime-state";
import styles from "../../shared/ui/workbench.module.css";

function eventLabel(event: ResearchEvent) {
  if (event.type === "progress" || event.type === "runtime")
    return `${event.payload.label}${event.type === "runtime" && event.payload.tool ? ` · ${event.payload.tool}` : ""}`;
  if (event.type === "report") return `研究产物已保存：${event.payload.section}`;
  if (event.type === "error") return event.payload.message;
  if (event.type === "state") return `执行状态：${event.payload.status}`;
  if (event.type === "resync_required") return event.payload.reason;
  return "已报告用量更新";
}

export function RuntimeExecutionPanel({
  run,
  manifest,
  events,
}: {
  run: RunSnapshot;
  manifest: ManifestResponse;
  events: readonly ResearchEvent[];
}) {
  const runtime = runtimeManifestSummary(manifest);
  return (
    <div className={styles.stack}>
      <Alert
        showIcon
        type="info"
        title="本地 Agent 独立研究"
        description="此运行由所选 Runtime 自主使用受控金融工具。下方展示实际保存的事件；不将这次研究描述为已执行内置 12 角色。"
      />
      <div className={styles.row}>
        <Tag>{runtime.runtime ?? "Runtime 类型未知"}</Tag>
        <Tag>服务器本机</Tag>
        <Tag>{runtime.transport ?? "连接方式未知"}</Tag>
      </div>
      <div className={styles.summary}>
        <div>
          <small>运行环境</small>
          <strong>{runtime.name ?? "历史未记录"}</strong>
        </div>
        <div>
          <small>请求模型</small>
          <strong className={styles.mono}>{runtime.model ?? "未知"}</strong>
        </div>
        <div>
          <small>确认生效模型</small>
          <strong className={styles.mono}>
            {run.runtime_execution?.effective_model ?? "尚未确认"}
          </strong>
        </div>
        <div>
          <small>确认生效服务</small>
          <strong>{run.runtime_execution?.effective_provider ?? "尚未确认"}</strong>
        </div>
        <div>
          <small>程序版本</small>
          <strong>{runtime.version ?? "未知"}</strong>
        </div>
        <div>
          <small>认证来源</small>
          <strong>
            {runtime.authMode === "runtime_login"
              ? "本连接专属 Codex 登录"
              : runtime.authMode === "model_connection"
                ? "账号模型连接"
                : "未知"}
          </strong>
        </div>
      </div>
      <p className={styles.muted}>
        以上配置读取自本次冻结清单，不能单独证明 Runtime
        实际使用了该模型。执行确认、工具调用和产物结果以下方已记录事件及证据为准；未上报的内部步骤和用量保持未知。
      </p>
      <div className={styles.eventLog}>
        {events.length ? (
          events.map((event) => (
            <div key={event.event_id}>
              <time>{new Date(event.timestamp).toLocaleString()}</time>
              <span>{eventLabel(event)}</span>
            </div>
          ))
        ) : run.events?.length ? (
          run.events.map((event) => (
            <div key={event.id}>
              <time>{new Date(event.time).toLocaleString()}</time>
              <span>{event.message}</span>
            </div>
          ))
        ) : (
          <p className={styles.muted}>尚无已保存的执行事件，等待 worker 接取任务。</p>
        )}
      </div>
      <p className={styles.muted}>
        工具原始来源与数据资格在“工具证据与资格”中查看，报告保留已有产出。当前不提供 Runtime
        会话恢复；重新研究会新建任务并可能产生新用量。
      </p>
    </div>
  );
}
