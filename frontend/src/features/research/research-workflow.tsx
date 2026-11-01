import { useState } from "react";
import { Alert, Button, Modal, Spin, Tabs, Tag } from "antd";
import {
  ArrowDownOutlined,
  ArrowRightOutlined,
  AuditOutlined,
  BankOutlined,
  DatabaseOutlined,
  FallOutlined,
  FileTextOutlined,
  HistoryOutlined,
  LineChartOutlined,
  MessageOutlined,
  ReadOutlined,
  ReloadOutlined,
  RiseOutlined,
  RobotOutlined,
  SafetyOutlined,
  SwapOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { getResearchWorkflow } from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  ResearchWorkflowNode,
  RunSnapshot,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { shouldRetryQuery } from "../../shared/api/transport";
import { workflowDepth, workflowNodeStatus } from "./workflow-utils";
import type { WorkflowModelAssignment, WorkflowModelBindings } from "./workflow-utils";
import styles from "./research-workflow.module.css";

export interface ResearchWorkflowProps {
  bootstrap: BootstrapResponse;
  analysts?: readonly string[];
  depth?: number;
  mode?: "preview" | "draft";
  models?: WorkflowModelBindings;
  run?: RunSnapshot;
}

const nodeIcons: Record<string, React.ReactNode> = {
  market: <LineChartOutlined />,
  social: <MessageOutlined />,
  news: <ReadOutlined />,
  fundamentals: <BankOutlined />,
  bull: <RiseOutlined />,
  bear: <FallOutlined />,
  research_manager: <AuditOutlined />,
  trader: <FileTextOutlined />,
  risk_aggressive: <ThunderboltOutlined />,
  risk_conservative: <SafetyOutlined />,
  risk_neutral: <SwapOutlined />,
  portfolio_manager: <AuditOutlined />,
  memory_log: <HistoryOutlined />,
  rating_resolution: <ReadOutlined />,
  archive: <DatabaseOutlined />,
};
const runStatusLabels: Record<string, string> = {
  pending: "等待执行",
  queued: "排队中",
  running: "研究中",
  cancelling: "取消中",
  cancelled: "已取消",
  completed: "任务已完成",
  failed: "任务失败",
  interrupted: "已中断",
};
const modelSourceLabels: Record<string, string> = {
  draft: "未保存的角色绑定",
  template: "所选模板绑定",
  manifest: "本次研究冻结模型绑定",
  legacy: "历史本机运行配置",
  bootstrap: "当前本机配置预览（尚未冻结）",
};

function Arrow({ label, down = false }: { label?: string; down?: boolean }) {
  return (
    <div className={down ? styles.downArrow : styles.arrow} aria-hidden="true">
      {down ? <ArrowDownOutlined /> : <ArrowRightOutlined />}
      {label && <span>{label}</span>}
    </div>
  );
}

function nodeModel(
  node: ResearchWorkflowNode,
  models?: WorkflowModelBindings,
): WorkflowModelAssignment | undefined {
  if (node.llm_usage === "none") return undefined;
  const exact = models?.roles[node.id];
  if (exact) return exact;
  if (node.id === "memory_log") return models?.roles.quick;
  return models?.fallbackByTier && node.tier ? models.roles[node.tier] : undefined;
}

function roleReports(node: ResearchWorkflowNode, run?: RunSnapshot) {
  return (node.report_keys ?? []).flatMap((key) => {
    const text = run?.reports?.[key];
    return typeof text === "string" && text.trim() ? [{ key, text }] : [];
  });
}

export function ResearchWorkflow({
  bootstrap,
  analysts,
  depth,
  mode = "preview",
  models,
  run,
}: ResearchWorkflowProps) {
  const scope = scopeForWorkspace(bootstrap.workspace);
  const definition = useQuery({
    queryKey: queryKeys.resource(scope, "research-workflow"),
    queryFn: ({ signal }) => getResearchWorkflow({ signal }),
    retry: shouldRetryQuery,
    staleTime: 300_000,
    refetchOnWindowFocus: false,
  });
  const [selectedId, setSelectedId] = useState<string>();
  const selectedAnalysts = run?.analysts ?? analysts ?? bootstrap.defaults.analysts ?? [];
  const rounds = workflowDepth(run?.depth ?? depth ?? bootstrap.defaults.depth);
  const graph = definition.data;
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId);
  const nodeMap = new Map((graph?.nodes ?? []).map((node) => [node.id, node]));
  const stageMap = new Map((graph?.stages ?? []).map((stage) => [stage.id, stage]));
  const analystNodes =
    graph?.nodes.filter((node) => node.kind === "role" && !!node.selection_key) ?? [];
  const enabledCount = analystNodes.filter(
    (node) => node.selection_key && selectedAnalysts.includes(node.selection_key),
  ).length;
  const chosenModel = selectedNode ? nodeModel(selectedNode, models) : undefined;
  const reports = selectedNode ? roleReports(selectedNode, run) : [];

  function stateFor(node: ResearchWorkflowNode) {
    return workflowNodeStatus({
      roleId: node.id,
      stage: node.stage_id,
      reportKeys: node.report_keys ?? [],
      selectedAnalysts,
      run,
    });
  }
  function turnsFor(node: ResearchWorkflowNode) {
    if (node.selection_key && !selectedAnalysts.includes(node.selection_key)) return "本次跳过";
    if (node.kind === "auxiliary")
      return node.llm_usage === "conditional"
        ? "按条件调用模型 · 不计12角色"
        : "确定性辅助 · 不计12角色";
    if (node.tool_loop) return "产出研究报告 · 含工具往返";
    const loop = graph?.loops.find((item) => item.node_ids.includes(node.id));
    return loop ? `正常路径计划发言 ${rounds} 次` : "正常路径处理 1 次";
  }
  function renderNode(node: ResearchWorkflowNode) {
    const status = stateFor(node);
    const assignment = nodeModel(node, models);
    const diagnostic = node.id === "memory_log" && status.kind === "output";
    const modelText =
      node.llm_usage === "none" ? "无需模型" : (assignment?.model ?? "模型绑定未知");
    return (
      <button
        type="button"
        key={node.id}
        className={[
          styles.node,
          styles[status.kind],
          node.kind === "auxiliary" ? styles.auxiliary : undefined,
          diagnostic ? styles.diagnostic : undefined,
          selectedId === node.id ? styles.selected : undefined,
        ]
          .filter(Boolean)
          .join(" ")}
        onClick={() => setSelectedId(node.id)}
        aria-haspopup="dialog"
        aria-pressed={selectedId === node.id}
        aria-label={`${node.label} · ${status.label}，查看职责、模型与产出`}
      >
        <span className={styles.nodeTop}>
          <span className={styles.icon}>{nodeIcons[node.id] ?? <RobotOutlined />}</span>
          <strong>{node.label}</strong>
          <span className={styles.tier} title="默认模型层级；角色可以独立覆盖模型">
            {node.tier ? `默认${node.tier}` : "辅助"}
          </span>
        </span>
        <span className={styles.description}>{node.responsibility}</span>
        <span className={styles.model} title={modelText}>
          {modelText}
        </span>
        <span className={styles.cardFooter}>
          <span className={styles.statusDot} />
          {status.label}
          <small>
            {node.tool_loop ? "工具循环" : node.kind === "role" ? "模型角色" : "辅助节点"}
          </small>
        </span>
        <span className={styles.turns}>{turnsFor(node)}</span>
      </button>
    );
  }
  function renderStage(id: string) {
    const stage = stageMap.get(id);
    if (!stage) return null;
    const nodes = stage.node_ids.flatMap((nodeId) => {
      const node = nodeMap.get(nodeId);
      return node ? [node] : [];
    });
    const roles = nodes.filter((node) => node.kind === "role"),
      auxiliaries = nodes.filter((node) => node.kind === "auxiliary");
    const loop = graph?.loops.find((item) => item.stage_id === id);
    const phaseActive = nodes.some((node) => stateFor(node).kind === "current");
    return (
      <section
        className={[styles.stage, phaseActive ? styles.stageCurrent : undefined, styles[id]]
          .filter(Boolean)
          .join(" ")}
        aria-label={stage.label}
      >
        <header className={styles.stageHeader}>
          <div>
            <span className={styles.stageNumber}>
              {String((graph?.stages.indexOf(stage) ?? 0) + 1).padStart(2, "0")}
            </span>
            <h3>{stage.label}</h3>
          </div>
          {phaseActive && <span className={styles.phaseLabel}>任务上报阶段范围</span>}
          {loop && (
            <span className={styles.loopCount}>
              计划{rounds}轮 · {rounds * loop.turns_per_round}次发言
            </span>
          )}
        </header>
        <p className={styles.stageDescription}>{stage.description}</p>
        <div
          className={[
            styles.roleGrid,
            loop ? styles.loopGrid : undefined,
            stage.mode === "parallel" ? styles.parallelGrid : undefined,
            stage.mode === "cycle" ? styles.riskGrid : undefined,
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {roles.map((node, index) => (
            <div className={styles.nodeStep} key={node.id}>
              {renderNode(node)}
              {loop && index < roles.length - 1 && (
                <span className={styles.turnArrow} aria-hidden="true">
                  {stage.mode === "alternating" ? "↔" : "→"}
                </span>
              )}
            </div>
          ))}
        </div>
        {loop && (
          <div className={styles.loopBack}>
            <ReloadOutlined />
            <span>
              {stage.mode === "alternating"
                ? `双方各计划发言${rounds}次后，由研究经理汇总`
                : `三方各计划发言${rounds}次后，进入组合经理`}
            </span>
          </div>
        )}
        {!!auxiliaries.length && (
          <div className={stage.mode === "parallel" ? styles.parallelAux : styles.postGrid}>
            {auxiliaries.map(renderNode)}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className={styles.workflow} aria-label="ThesisFlow 研究决策路径">
      <header className={styles.header}>
        <div>
          <small>RESEARCH DECISION FLOW</small>
          <h2>12角色研究决策路径</h2>
          <p>
            {run
              ? "当前引擎路径 + 当前任务阶段 / 已保存报告"
              : mode === "draft"
                ? "未保存配置的路径预览"
                : "研究配置预览 · 点击角色查看职责与模型"}
          </p>
        </div>
        <div className={styles.headerTags}>
          <Tag>
            {run
              ? (runStatusLabels[run.status] ?? run.status)
              : mode === "draft"
                ? "草稿预览"
                : "尚未运行"}
          </Tag>
          <Tag>
            分析师 {enabledCount}/{analystNodes.length || "—"}
          </Tag>
          <Tag>depth {rounds}</Tag>
        </div>
      </header>
      {definition.isPending ? (
        <div className={styles.empty}>
          <Spin />
          <p>正在读取服务端研究路径定义…</p>
        </div>
      ) : definition.isError || !graph ? (
        <div className={styles.empty}>
          <Alert
            type="warning"
            title="研究路径定义暂时无法读取"
            description="此处不使用模拟流程替代；已有研究入口与报告仍保留。"
            action={
              <Button
                onClick={() => {
                  void definition.refetch();
                }}
              >
                重试
              </Button>
            }
          />
        </div>
      ) : (
        <>
          <div className={styles.legend}>
            <span>
              <i className={styles.plannedDot} />
              计划路径
            </span>
            <span>
              <i className={styles.currentDot} />
              任务阶段范围
            </span>
            <span>
              <i className={styles.outputDot} />
              已有产出
            </span>
            <span>
              <i className={styles.skippedDot} />
              未选 / 跳过
            </span>
            <small>
              {graph.role_count}个模型角色 +{" "}
              {graph.nodes.filter((node) => node.kind === "auxiliary").length}个辅助节点
            </small>
          </div>
          {enabledCount === 0 && !run && (
            <div className={styles.notice}>
              <Alert
                type="warning"
                title="草稿尚未选择分析师；至少选择一个后才能形成可运行研究配置。"
              />
            </div>
          )}
          <div className={styles.canvas}>
            {renderStage("analysts")}
            <div className={styles.join}>
              <span />
              <div>已选分析师 + 已注入的历史经验分支返回后汇合</div>
              <span />
            </div>
            <Arrow down label="进入研究辩论" />
            <div className={styles.middleChain}>
              {renderStage("debate")}
              <Arrow label="轮次结束" />
              {renderStage("research_plan")}
              <Arrow label="研究计划" />
              {renderStage("proposal")}
            </div>
            <Arrow down label="提交风险审议" />
            <div className={styles.riskChain}>
              {renderStage("risk_debate")}
              <Arrow label="轮次结束" />
              {renderStage("final_decision")}
            </div>
            <div className={styles.memoryLink}>
              <HistoryOutlined />
              <span>历史经验 ┄┄→ 仅组合经理消费</span>
              <small>
                记忆辅助可能按条件调用quick反思；当前Web按运行/尝试隔离日志，不表示跨任务共享记忆。
              </small>
            </div>
            <div className={styles.endMarker}>END · 主图结束</div>
            <Arrow down label="由运行入口进行图外处理" />
            {renderStage("postprocess")}
          </div>
          <footer className={styles.footer}>
            <p>
              <strong>{models?.label ?? "模型绑定尚未解析"}</strong> · {graph.version}
            </p>
            <p>
              卡片发言次数是正常路径计划，工具往返、重试或结构化输出回退可能增加模型请求；不代表计费次数。
            </p>
            <p>
              {run
                ? "任务只上报粗粒度阶段与已保存报告；同阶段高亮不代表角色同时运行。首版多空或风控报告存在，不代表所有轮次已完成。当前路径定义也不等于旧任务当时的精确冻结拓扑。"
                : "预览不调用模型、不执行研究；未选分析师显示跳过，多空和风险循环随depth改变。"}
            </p>
          </footer>
        </>
      )}
      <Modal
        title={
          selectedNode
            ? `${selectedNode.label} · ${selectedNode.kind === "role" ? "模型角色" : "辅助节点"}`
            : "角色详情"
        }
        open={!!selectedNode}
        centered
        width={860}
        footer={null}
        onCancel={() => setSelectedId(undefined)}
        destroyOnHidden
        classNames={{
          container: styles.detailContainer,
          body: styles.detailBody,
          header: styles.detailHeader,
        }}
      >
        {selectedNode && (
          <Tabs
            key={selectedNode.id}
            items={[
              {
                key: "role",
                label: "职责与真实输入输出",
                children: (
                  <div className={styles.details}>
                    <p className={styles.responsibility}>{selectedNode.responsibility}</p>
                    <div className={styles.detailFacts}>
                      <Tag>{selectedNode.kind === "role" ? "计入12角色" : "不计入12角色"}</Tag>
                      <Tag>
                        {selectedNode.llm_usage === "conditional"
                          ? "条件触发模型调用"
                          : selectedNode.llm_usage === "required"
                            ? "需要模型"
                            : "无需额外模型"}
                      </Tag>
                      <Tag>{turnsFor(selectedNode)}</Tag>
                    </div>
                    <div className={styles.modelBox}>
                      <strong>模型绑定</strong>
                      <p>
                        {selectedNode.llm_usage === "none" ? (
                          "此确定性辅助步骤不绑定模型。"
                        ) : chosenModel?.model ? (
                          <>
                            <code>{chosenModel.model}</code> · {chosenModel.provider ?? "厂商未知"}
                            {chosenModel.connectionName ? ` · ${chosenModel.connectionName}` : ""}
                          </>
                        ) : (
                          "未解析到该节点的模型绑定；不以当前其他模型代替未知历史配置。"
                        )}
                      </p>
                      {chosenModel && (
                        <small>
                          {modelSourceLabels[chosenModel.source] ?? chosenModel.source}
                          {chosenModel.testStatus
                            ? ` · 模型配置检测状态 ${chosenModel.testStatus}`
                            : ""}
                        </small>
                      )}
                      <small>{models?.label ?? "尚无绑定上下文"}</small>
                    </div>
                    <div className={styles.ioColumns}>
                      <div>
                        <h4>读取的状态字段</h4>
                        {selectedNode.input_keys.length ? (
                          <div className={styles.keyList}>
                            {selectedNode.input_keys.map((key) => (
                              <code key={key}>{key}</code>
                            ))}
                          </div>
                        ) : (
                          <p>不读取AgentState字段。</p>
                        )}
                      </div>
                      <div>
                        <h4>写入的状态字段</h4>
                        {selectedNode.output_keys.length ? (
                          <div className={styles.keyList}>
                            {selectedNode.output_keys.map((key) => (
                              <code key={key}>{key}</code>
                            ))}
                          </div>
                        ) : (
                          <p>不向AgentState新增字段；持久产物见下方说明。</p>
                        )}
                      </div>
                    </div>
                    {selectedNode.tool_loop && (
                      <div className={styles.toolLoop}>
                        <strong>私有工具循环</strong>
                        <div>{selectedNode.tool_loop.node_ids.join(" ⇄ ")}</div>
                        <p>{selectedNode.tool_loop.description}</p>
                        <small>
                          循环上限参数：{selectedNode.tool_loop.rounds_parameter}
                          ；不据此推断当前执行次数。
                        </small>
                      </div>
                    )}
                    <div className={styles.nodeNotes}>
                      {selectedNode.notes.map((note) => (
                        <p key={note}>{note}</p>
                      ))}
                    </div>
                    {graph && (
                      <div className={styles.edgeNotes}>
                        <h4>路径条件</h4>
                        {graph.edges
                          .filter(
                            (edge) =>
                              edge.target_id === selectedNode.id ||
                              edge.source_ids.includes(selectedNode.id),
                          )
                          .map((edge) => (
                            <p key={edge.id}>
                              <code>
                                {edge.source_ids
                                  .map((id) => nodeMap.get(id)?.label ?? id)
                                  .join(" + ")}{" "}
                                → {nodeMap.get(edge.target_id)?.label ?? edge.target_id}
                              </code>
                              <span>{edge.condition || "顺序衔接"}</span>
                            </p>
                          ))}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: "report",
                label: `${selectedNode.id === "memory_log" ? "已保存诊断" : "已保存产出"} (${reports.length})`,
                children: (
                  <div className={styles.details}>
                    {!run ? (
                      <Alert type="info" title="这是路径预览，没有真实运行报告。" />
                    ) : !reports.length ? (
                      <Alert
                        type="info"
                        title="当前任务尚无该节点对应的已保存文本。"
                        description="缺少文本不等于节点失败，也不能据此断言未执行。"
                      />
                    ) : (
                      <>
                        <Alert
                          type="info"
                          title={
                            selectedNode.id === "memory_log"
                              ? "诊断记录可能说明跳过或失败，不代表历史结算/反思成功。"
                              : "报告可用不代表该角色已完成全部轮次，也不代表整个研究已完成。"
                          }
                        />
                        {reports.map((report) => (
                          <div key={report.key}>
                            <h4>{report.key}</h4>
                            <pre className={styles.report}>{report.text}</pre>
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
      </Modal>
    </section>
  );
}
