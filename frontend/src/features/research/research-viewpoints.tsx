import { useState } from "react";
import { Alert, Button, Checkbox, Form, Input, Modal, Select, Spin, Tag } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createViewpoint,
  listViewpoints,
  updateViewpoint,
} from "../../shared/api/generated/client";
import type { RunSnapshot, ViewpointView } from "../../shared/api/generated/models";
import { queryKeys } from "../../shared/api/query-keys";
import type { QueryScope } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { confirmedViewpoint, viewpointFields, viewpointWrite } from "./viewpoint-fields";
import type { ViewpointFields } from "./viewpoint-fields";
import styles from "../../shared/ui/workbench.module.css";
import controls from "./research-controls.module.css";

const stanceLabels = { watch: "继续观察", positive: "偏积极", neutral: "中性", negative: "偏谨慎" };
const statusLabels = { active: "跟踪中", invalidated: "已判定失效", archived: "已归档" };

export function ResearchViewpoints({ scope, run }: { scope: QueryScope; run: RunSnapshot }) {
  const cache = useQueryClient(),
    [form] = Form.useForm<ViewpointFields>();
  const [open, setOpen] = useState(false),
    [editor, setEditor] = useState<ViewpointView>(),
    [conflict, setConflict] = useState<ViewpointView>(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string>();
  const viewpoints = useQuery({
    queryKey: queryKeys.resource(scope, "viewpoints", { runId: run.id }),
    queryFn: ({ signal }) => listViewpoints({ run_id: run.id, include_archived: true }, { signal }),
    retry: shouldRetryQuery,
  });
  const existing = viewpoints.data?.items.find((item) => item.run_id === run.id);
  function edit(view?: ViewpointView) {
    setEditor(view);
    setConflict(undefined);
    setError(undefined);
    form.resetFields();
    form.setFieldsValue(viewpointFields(view));
    setOpen(true);
  }
  async function save(values: ViewpointFields) {
    setBusy(true);
    setError(undefined);
    try {
      if (editor)
        await updateViewpoint(editor.id, { ...viewpointWrite(values), revision: editor.revision });
      else await createViewpoint(confirmedViewpoint(run.id, values));
      await cache.invalidateQueries({ queryKey: queryKeys.scope(scope) });
      setOpen(false);
    } catch (failure) {
      setError(
        failure instanceof ApiError || failure instanceof Error
          ? failure.message
          : "观点保存失败。",
      );
      if (failure instanceof ApiError && failure.status === 409) {
        try {
          const latest = await listViewpoints({ run_id: run.id, include_archived: true });
          setConflict(latest.items.find((item) => item.run_id === run.id));
        } catch {
          setError("观点版本冲突且最新记录暂不可读；草稿已保留，请稍后重试。");
        }
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={styles.stack}>
      <Alert
        showIcon
        type="info"
        title="人工研究观点 · 手动复核"
        description="只有你明确确认的论据和失效条件才会保存。到期状态由复核日期判断，不会自动监控行情、调用模型、验证论据或交易；人工确认也不会提升原始数据资格。"
      />
      {viewpoints.isPending ? (
        <Spin />
      ) : viewpoints.isError ? (
        <Alert
          type="warning"
          showIcon
          title="观点记录暂不可读"
          action={
            <Button
              onClick={() => {
                void viewpoints.refetch();
              }}
            >
              重试
            </Button>
          }
        />
      ) : existing ? (
        <section>
          <div className={styles.actions}>
            <Tag>{stanceLabels[existing.stance ?? "watch"]}</Tag>
            <Tag>{statusLabels[existing.status]}</Tag>
            {existing.review_due && <Tag color="warning">已到手动复核日期</Tag>}
            <Tag>源资格 {existing.source_quality}</Tag>
            <Button onClick={() => edit(existing)}>编辑 / 复核 / 归档</Button>
          </div>
          <h3>我认可的依据</h3>
          <pre className={styles.reportText}>{existing.thesis}</pre>
          <h3>失效条件</h3>
          <pre className={styles.reportText}>{existing.invalidation}</pre>
          <p>
            计划复核 {existing.review_on ?? "未设置"} · 最近复核{" "}
            {existing.reviewed_at ?? "尚未记录"} · 版本 {existing.revision}
          </p>
          {existing.review_notes && (
            <>
              <h3>复核记录</h3>
              <pre className={styles.reportText}>{existing.review_notes}</pre>
            </>
          )}
          <p className={styles.muted}>{existing.scope_notice}</p>
        </section>
      ) : (
        <>
          <p className={styles.muted}>
            {run.status === "completed"
              ? "此研究尚未保存人工观点。表单将从空白开始，不自动采纳模型判断。"
              : "研究完成后才可手动建立观点；现有阶段报告仍可阅读。"}
          </p>
          <Button disabled={run.status !== "completed"} onClick={() => edit()}>
            手动建立研究观点
          </Button>
        </>
      )}
      <Modal
        title={editor ? "编辑我的研究观点" : "手动确认研究观点"}
        open={open}
        onCancel={() => {
          if (!busy) setOpen(false);
        }}
        footer={null}
        centered
        maskClosable={!busy}
        closable={!busy}
        keyboard={!busy}
        classNames={{ container: controls.modalContainer, body: controls.modalBody }}
      >
        {error && <Alert showIcon type="warning" title={error} />}
        {conflict && (
          <Alert
            showIcon
            type="warning"
            title={`已有更新版本 v${conflict.revision}；当前草稿已保留`}
            description={
              <div>
                <p>最新观点：{conflict.thesis}</p>
                <p>最新失效条件：{conflict.invalidation}</p>
                <Button
                  onClick={() => {
                    setEditor(conflict);
                    setConflict(undefined);
                    setError(undefined);
                  }}
                >
                  采用最新版本号，保留当前草稿继续编辑
                </Button>
              </div>
            }
          />
        )}
        <Form
          form={form}
          layout="vertical"
          disabled={busy}
          onFinish={(values) => {
            void save(values);
          }}
        >
          <Form.Item name="stance" label="我的研究立场">
            <Select
              options={Object.entries(stanceLabels).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item
            name="thesis"
            label="我认可的论据"
            rules={[{ required: true, whitespace: true, max: 3000 }]}
          >
            <Input.TextArea
              rows={4}
              maxLength={3000}
              showCount
              placeholder="手动整理你认可的事实、推论与仍需验证之处"
            />
          </Form.Item>
          <Form.Item
            name="invalidation"
            label="哪些情况会使观点失效"
            rules={[{ required: true, whitespace: true, max: 2000 }]}
          >
            <Input.TextArea rows={3} maxLength={2000} showCount />
          </Form.Item>
          <Form.Item name="review_on" label="计划手动复核日期（可选）">
            <Input type="date" />
          </Form.Item>
          <Form.Item name="review_notes" label="复核记录" rules={[{ max: 3000 }]}>
            <Input.TextArea rows={3} maxLength={3000} />
          </Form.Item>
          {editor ? (
            <>
              <Form.Item name="status" label="观点状态">
                <Select
                  options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
                />
              </Form.Item>
              <Form.Item name="mark_reviewed" valuePropName="checked">
                <Checkbox>保存时记录本次已手动复核</Checkbox>
              </Form.Item>
            </>
          ) : (
            <Form.Item
              name="confirmed"
              valuePropName="checked"
              rules={[
                {
                  validator: (_, value) =>
                    value === true
                      ? Promise.resolve()
                      : Promise.reject(new Error("请明确确认你的研究观点")),
                },
              ]}
            >
              <Checkbox>这是我本人整理并认可的观点，已检查其依据与失效条件</Checkbox>
            </Form.Item>
          )}
          <Button type="primary" htmlType="submit" loading={busy} disabled={!!conflict}>
            {editor ? "保存我的修订" : "确认并保存观点"}
          </Button>
        </Form>
      </Modal>
    </div>
  );
}
