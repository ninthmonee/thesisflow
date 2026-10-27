import { useEffect, useState } from "react";
import { Alert, Button, Spin, Tag } from "antd";
import { useQueries } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  createResearchContext,
  getResearchContext,
  searchInstruments,
} from "../../shared/api/generated/client";
import type { ResearchContextCreate } from "../../shared/api/generated/models";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import styles from "../../shared/ui/workbench.module.css";

const kindLabels: Record<string, string> = {
  market: "市场背景",
  theme: "主题与产业链",
  options: "期权样本",
  news: "新闻样本",
  etf_flows: "ETF份额 / 申赎背景",
};

export function ResearchContextPicker({
  scope,
  ids,
  ticker,
  onChange,
  onValidityChange,
}: {
  scope: QueryScope;
  ids: readonly string[];
  ticker: string;
  onChange?: (ids: string[]) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: queryKeys.resource(scope, "research-context", { id }),
      queryFn: ({ signal }: { signal: AbortSignal }) => getResearchContext(id, { signal }),
      retry: shouldRetryQuery,
    })),
  });
  const mismatches = queries.map(
    (query) =>
      !!query.data &&
      ["options", "news"].includes(query.data.kind) &&
      !!query.data.symbol &&
      !!ticker.trim() &&
      query.data.symbol !== ticker.trim().toUpperCase(),
  );
  const valid = queries.every(
    (query, index) => !!query.data && !query.isError && !mismatches[index],
  );
  useEffect(() => onValidityChange?.(valid), [valid, onValidityChange]);
  return (
    <section className={styles.stack}>
      <h3>附加研究资料 · {ids.length} / 5</h3>
      <p className={styles.muted}>
        这些来源快照是提供给研究角色的补充资料，不替代分析师取证。
        {onChange
          ? "移除只影响本次未提交的选择，不删除历史快照。"
          : "以下为本次任务引用的已保存快照，不会替换成当前来源的新数据。"}
      </p>
      {!ids.length && (
        <p className={styles.muted}>
          尚未附加资料。可从市场、主题、期权或新闻视图显式附加；不会因为浏览过页面就自动纳入研究。
        </p>
      )}
      {ids.map((id, index) => {
        const query = queries[index],
          context = query?.data;
        return (
          <div key={id}>
            <div className={styles.actions}>
              <Tag>{context ? (kindLabels[context.kind] ?? context.kind) : "资料待确认"}</Tag>
              {onChange && (
                <Button size="small" onClick={() => onChange(ids.filter((value) => value !== id))}>
                  从本次研究移除
                </Button>
              )}
            </div>
            {query?.isPending ? (
              <Spin size="small" />
            ) : query?.isError ? (
              <Alert
                showIcon
                type="warning"
                title={onChange ? "该资料无法读取，请重试或移除后启动" : "该历史资料目前无法读取"}
                action={
                  <Button
                    onClick={() => {
                      void query.refetch();
                    }}
                  >
                    重试
                  </Button>
                }
              />
            ) : context ? (
              <>
                <p>{context.summary}</p>
                <small className={styles.muted}>
                  来源 {context.source} · 数据时点 {context.as_of ?? "未知"} · 捕获{" "}
                  {context.received_at} · 摘要 {context.content_hash}
                </small>
                <p className={styles.muted}>
                  {context.scope_notice}。这是已保存快照，选择或恢复引用不会重新拉取最新数据。
                </p>
                {mismatches[index] && (
                  <Alert
                    type="warning"
                    showIcon
                    title={`资料证券 ${context.symbol} 与本次研究 ${ticker.toUpperCase()} 不一致，${onChange ? "请移除或修改研究股票" : "请核查原运行资料"}。`}
                  />
                )}
              </>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

export function AttachResearchContextButton({
  body,
  ticker,
  disabled = false,
  label = "附加到个股研究",
}: {
  body: ResearchContextCreate;
  ticker: string;
  disabled?: boolean;
  label?: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>();
  async function attach() {
    const target = ticker.trim().toUpperCase();
    if (!target) {
      setError("请先明确选择要研究的公司股票。");
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const directory = await searchInstruments({ q: target });
      const instrument = directory.items.find((item) => item.symbol === target);
      if (!instrument || instrument.asset_type !== "stock" || target.startsWith("^")) {
        setError("核心研究只支持已确认的公司股票；ETF可作为背景资料，请选择目标公司股票。");
        return;
      }
      const context = await createResearchContext(body);
      void navigate(
        `/research/new?${new URLSearchParams({ ticker: target, context: context.id })}`,
      );
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "附加资料失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <Button
        disabled={disabled || !ticker.trim()}
        loading={busy}
        onClick={() => {
          void attach();
        }}
      >
        {label}
      </Button>
      {error && <p className={styles.muted}>{error}</p>}
    </div>
  );
}
