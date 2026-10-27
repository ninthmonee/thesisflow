import { useState } from "react";
import { Alert, Button, Checkbox, Collapse, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getResearchEvidence } from "../../shared/api/generated/client";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { ResearchQualityNotice } from "./research-status";
import styles from "../../shared/ui/workbench.module.css";

const statusLabels: Record<string, string> = {
  validated: "已验证",
  available: "有返回内容",
  unknown: "结构 / 时点未知",
  unavailable: "不可用",
  error: "读取失败",
};

export function ResearchEvidencePanel({
  scope,
  runId,
  active,
}: {
  scope: QueryScope;
  runId: string;
  active: boolean;
}) {
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [includeBody, setIncludeBody] = useState(false);
  const cursor = cursors.at(-1);
  const evidence = useQuery({
    queryKey: queryKeys.resource(scope, "research-evidence", { runId, cursor, includeBody }),
    queryFn: ({ signal }) =>
      getResearchEvidence(runId, { limit: 25, cursor, include_body: includeBody }, { signal }),
    retry: shouldRetryQuery,
    refetchInterval: active ? 5000 : false,
  });
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        本页只列实际捕获的工具证据。公开请求、数据时点与接收时间分别展示；有返回内容不代表通过验证，未捕获的数据也不等于零。
      </p>
      <div className={styles.actions}>
        <Checkbox checked={includeBody} onChange={(event) => setIncludeBody(event.target.checked)}>
          读取有界白名单正文
        </Checkbox>
        <Button
          onClick={() => {
            void evidence.refetch();
          }}
          loading={evidence.isFetching}
        >
          刷新证据
        </Button>
      </div>
      {evidence.isPending ? (
        <Spin />
      ) : evidence.isError ? (
        <Alert
          type="warning"
          showIcon
          title={evidence.error instanceof ApiError ? evidence.error.message : "证据暂不可读"}
        />
      ) : evidence.data ? (
        <>
          <ResearchQualityNotice quality={evidence.data.quality} />
          {!evidence.data.capture_started && (
            <Alert
              showIcon
              type="info"
              title="这条研究尚无证据捕获记录"
              description="可能是历史研究或尚未执行工具；不会从报告文本反推或补造原始响应。"
            />
          )}
          <p className={styles.muted}>{evidence.data.scope_notice}</p>
          <Collapse
            items={evidence.data.items.map((item) => ({
              key: item.id,
              label: (
                <span>
                  {item.category} · {item.provider} · {item.method}{" "}
                  <Tag>{statusLabels[item.status] ?? item.status}</Tag>
                </span>
              ),
              children: (
                <div>
                  <p>
                    请求截止：{item.requested_as_of ?? "未知"}；数据时点：
                    {item.data_as_of ?? "未知"}
                  </p>
                  <p>
                    公开时间：{item.published_at ?? "未知"}；接收：{item.received_at}
                  </p>
                  <p>
                    尝试：{item.attempt_id ?? "历史 / 未知"}；快照：
                    {item.snapshot_id ?? "未保存正文快照"}
                  </p>
                  <p>
                    保留 {item.stored_bytes} 字节 {item.truncated ? "· 内容已截断" : ""}；响应摘要{" "}
                    {item.response_hash ?? "未保存"}
                  </p>
                  {(item.issues ?? []).map((issue) => (
                    <p key={issue.code}>{issue.message}</p>
                  ))}
                  <h4>公开请求</h4>
                  <pre className={styles.reportText}>{JSON.stringify(item.request, null, 2)}</pre>
                  {includeBody && (
                    <>
                      <h4>白名单摘录</h4>
                      <pre className={styles.reportText}>
                        {item.body ?? "该记录未保留可展示正文。"}
                      </pre>
                    </>
                  )}
                </div>
              ),
            }))}
          />
          {!evidence.data.items.length && <p className={styles.muted}>此页没有捕获记录。</p>}
          <div className={styles.actions}>
            <span>第 {cursors.length} 页</span>
            <Button
              disabled={cursors.length === 1 || evidence.isFetching}
              onClick={() => setCursors((items) => items.slice(0, -1))}
            >
              上一页
            </Button>
            <Button
              disabled={!evidence.data.next_cursor || evidence.isFetching}
              onClick={() => {
                const next = evidence.data.next_cursor;
                if (next) setCursors((items) => [...items, next]);
              }}
            >
              下一页
            </Button>
          </div>
        </>
      ) : null}
    </div>
  );
}
