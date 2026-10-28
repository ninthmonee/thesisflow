import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Select, Tabs } from "antd";
import type { InvestmentPlanView, InvestmentPolicyView } from "../../shared/api/generated/models";
import {
  actionRuleLabels,
  investmentStatusLabels,
  policyPrincipleLabels,
  riskLimitLabels,
} from "./investment-fields";
import type { PlanFields } from "./investment-fields";
import styles from "./investment.module.css";

const area = { autoSize: { minRows: 2, maxRows: 5 }, maxLength: 4000, showCount: true };
const styleOptions = [
  { value: "long_term", label: "长期持有优秀企业" },
  { value: "left_side", label: "左侧分批布局" },
  { value: "trend", label: "趋势确认后参与" },
  { value: "swing", label: "波段观察" },
];

export function PolicyEditor({
  policy,
  busy,
  error,
  onCancel,
  onSave,
}: {
  policy: InvestmentPolicyView;
  busy: boolean;
  error?: string;
  onCancel: () => void;
  onSave: (fields: InvestmentPolicyView) => Promise<void>;
}) {
  const [form] = Form.useForm<InvestmentPolicyView>();
  useEffect(() => {
    form.resetFields();
    form.setFieldsValue(policy);
  }, [form, policy]);
  return (
    <Modal
      open
      title={`编辑投资体系 · ${policy.saved ? `v${policy.revision}` : "尚未保存"}`}
      width={860}
      centered
      footer={null}
      onCancel={onCancel}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      classNames={{ container: styles.modalContainer, body: styles.modalBody }}
    >
      {error && <Alert showIcon type="warning" title={error} />}
      <Form
        form={form}
        layout="vertical"
        onFinish={() => {
          void onSave(form.getFieldsValue(true));
        }}
        disabled={busy}
      >
        <Alert
          showIcon
          type="info"
          title="先写下自己的边界，再检查每一次判断"
          description="比例留空表示尚未设置；系统不会替你设定仓位、回撤或收益目标。修改体系生成新版本，原有计划与研究保留当时快照。"
          style={{ marginBottom: 18 }}
        />
        <Tabs
          items={[
            {
              key: "principles",
              label: "目标与八项原则",
              children: (
                <>
                  <div className={styles.fieldGrid}>
                    <Form.Item name="name" label="体系名称" rules={[{ required: true }]}>
                      <Input maxLength={120} />
                    </Form.Item>
                    <Form.Item name="style" label="主要操作方式">
                      <Select options={styleOptions} />
                    </Form.Item>
                    <Form.Item name="objective" label="长期目标" className={styles.wide}>
                      <Input.TextArea {...area} maxLength={2000} />
                    </Form.Item>
                  </div>
                  {policyPrincipleLabels.map(([key, label]) => (
                    <Form.Item key={key} name={["principles", key]} label={label}>
                      <Input.TextArea {...area} />
                    </Form.Item>
                  ))}
                </>
              ),
            },
            {
              key: "limits",
              label: "风险边界",
              children: (
                <>
                  <p>按资产组合百分比填写个人纪律，未知时留空。盈亏比的单位为倍。</p>
                  <div className={styles.fieldGrid}>
                    {riskLimitLabels.map(([key, label, unit]) => (
                      <Form.Item
                        key={key}
                        name={["risk_limits", key]}
                        label={`${label}（${unit}）`}
                      >
                        <InputNumber
                          min={key === "min_reward_risk" ? 0.000001 : 0}
                          max={key === "min_reward_risk" ? 1000 : 100}
                          precision={key === "min_reward_risk" ? 6 : 2}
                          placeholder="由你设定，留空不检查通过"
                        />
                      </Form.Item>
                    ))}
                    <Form.Item name="review_interval_days" label="默认复核间隔（天，可留空）">
                      <InputNumber min={1} max={3650} precision={0} placeholder="未设置" />
                    </Form.Item>
                  </div>
                  <Alert
                    showIcon
                    type="info"
                    title="检查个人纪律，不替你作投资判断"
                    description="止损参考价不保证成交或限制最大损失；情景、概率和公允价值均需由本人提供并持续复核。"
                  />
                </>
              ),
            },
            {
              key: "actions",
              label: "操作与生活边界",
              children: (
                <>
                  {actionRuleLabels.map(([key, label]) => (
                    <Form.Item key={key} name={["action_rules", key]} label={label}>
                      <Input.TextArea {...area} />
                    </Form.Item>
                  ))}
                  <Form.Item name="life_notes" label="生活支出与流动性备忘（仅个人记录）">
                    <Input.TextArea
                      {...area}
                      placeholder="确定要用的钱、家庭现金安排、分散要求等"
                    />
                  </Form.Item>
                  <Form.Item name="tax_notes" label="税务备忘（仅个人记录）">
                    <Input.TextArea
                      {...area}
                      placeholder="待核实事项、成本与记录要求；不会自动判断税务"
                    />
                  </Form.Item>
                  <p>生活与税务备忘不会随研究发送给模型。</p>
                </>
              ),
            },
          ]}
        />
        <Form.Item name="enabled" valuePropName="checked">
          <Checkbox>允许新研究使用本人的投资体系</Checkbox>
        </Form.Item>
        <div className={styles.formFooter}>
          <Button disabled={busy} onClick={onCancel}>
            取消
          </Button>
          <Button type="primary" loading={busy} onClick={() => form.submit()}>
            保存新版本
          </Button>
        </div>
      </Form>
    </Modal>
  );
}

export function PlanEditor({
  initial,
  current,
  policy,
  busy,
  error,
  review = false,
  onCancel,
  onSave,
}: {
  initial: PlanFields;
  current: InvestmentPlanView | null;
  policy: InvestmentPolicyView;
  busy: boolean;
  error?: string;
  review?: boolean;
  onCancel: () => void;
  onSave: (fields: PlanFields) => Promise<void>;
}) {
  const [form] = Form.useForm<PlanFields>();
  const [tab, setTab] = useState(review ? "review" : "thesis");
  useEffect(() => {
    form.resetFields();
    form.setFieldsValue(initial);
  }, [form, initial]);
  return (
    <Modal
      open
      title={current ? `${current.ticker} · 编辑决策计划 v${current.revision}` : "新增个股决策计划"}
      width={980}
      centered
      footer={null}
      onCancel={onCancel}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      classNames={{ container: styles.modalContainer, body: styles.modalBody }}
    >
      {error && <Alert showIcon type="warning" title={error} />}
      <Alert
        showIcon
        type="info"
        title="先存草稿，逐步补齐六个核心问题"
        description="可以留空。计划仅记录本人假设与纪律，不会添加自选、修改真实持仓或下单；不填写的数值保持未知。"
        style={{ marginBottom: 16 }}
      />
      <Form
        form={form}
        layout="vertical"
        disabled={busy}
        onFinish={() => {
          void onSave(form.getFieldsValue(true));
        }}
      >
        <div className={styles.fieldGrid}>
          <Form.Item
            name="ticker"
            label="股票或 ETF 代码"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={32} placeholder="例如 NVDA、SOXX" disabled={!!current} />
          </Form.Item>
          <Form.Item name="analysis_date" label="计划分析日期（美东）" rules={[{ required: true }]}>
            <Input type="date" min="1970-01-01" />
          </Form.Item>
          <Form.Item name="title" label="计划名称（可选）">
            <Input maxLength={160} />
          </Form.Item>
          <Form.Item name="theme" label="主题 / 产业链（可选）">
            <Input maxLength={120} placeholder="按本人关注方向填写" />
          </Form.Item>
        </div>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: "thesis",
              label: "① 周期与公司",
              children: (
                <>
                  <h3>行业趋势怎么样？</h3>
                  <Form.Item name="macro_cycle" label="宏观周期与证据">
                    <Input.TextArea
                      {...area}
                      placeholder="经济、通胀、利率、流动性；证据时点与相反信号"
                    />
                  </Form.Item>
                  <Form.Item name="industry_cycle" label="行业周期与证据">
                    <Input.TextArea
                      {...area}
                      placeholder="萌芽、成长、爆发、成熟或衰退；需求和供给的实际依据"
                    />
                  </Form.Item>
                  <Form.Item name="valuation_cycle" label="估值周期与证据">
                    <Input.TextArea
                      {...area}
                      placeholder="市场对增长的定价、历史区间、可比公司及其局限"
                    />
                  </Form.Item>
                  <h3>公司靠什么赚钱？护城河是什么？</h3>
                  <Form.Item name="business" label="商业模式与收入来源">
                    <Input.TextArea {...area} />
                  </Form.Item>
                  <Form.Item name="moat" label="护城河、定价权与竞争地位">
                    <Input.TextArea {...area} />
                  </Form.Item>
                  <Form.Item name="quality" label="公司质量与管理层">
                    <Input.TextArea
                      {...area}
                      placeholder="营收利润持续性、自由现金流、资本配置、管理层及脆弱点"
                    />
                  </Form.Item>
                  <Form.Item name="evidence_notes" label="证据来源、日期与待核实事项">
                    <Input.TextArea
                      {...area}
                      placeholder="注明财报 / 数据时点与来源，区分事实和推断；ETF 按持仓与资产类型研究"
                    />
                  </Form.Item>
                </>
              ),
            },
            {
              key: "valuation",
              label: "② 价格与情景",
              children: (
                <>
                  <h3>当前估值贵不贵？</h3>
                  <Form.Item name="valuation" label="估值方法、假设与局限">
                    <Input.TextArea
                      {...area}
                      placeholder="分别记录质量、价格与安全边际；不同资产采用适合的方法"
                    />
                  </Form.Item>
                  <div className={styles.fieldGrid}>
                    <Form.Item name="entry_price" label="计划入场参考价（USD / 股）">
                      <InputNumber
                        min={0.000001}
                        max={1e12}
                        precision={6}
                        placeholder="本人填写，不从行情自动采纳"
                      />
                    </Form.Item>
                    <Form.Item name="fair_value" label="本人估计的公允价值（USD / 股）">
                      <InputNumber
                        min={0.000001}
                        max={1e12}
                        precision={6}
                        placeholder="手填估值，并非客观真值"
                      />
                    </Form.Item>
                    <Form.Item name="estimated_cost_pct" label="估计总成本（占投入资金 %）">
                      <InputNumber
                        min={0}
                        max={100}
                        precision={3}
                        placeholder="未知时留空，只展示毛期望值"
                      />
                    </Form.Item>
                    <Form.Item name="stop_loss_price" label="多头保护参考价（USD / 股）">
                      <InputNumber
                        min={0.000001}
                        max={1e12}
                        precision={6}
                        placeholder="低于入场价；不保证成交"
                      />
                    </Form.Item>
                  </div>
                  <p>三种情景的价格从低到高填写；全部概率提供时须合计 100%，未估计的概率请留空。</p>
                  <div className={styles.scenarios}>
                    {(
                      [
                        ["bear", "悲观情景"],
                        ["base", "基准情景"],
                        ["bull", "乐观情景"],
                      ] as const
                    ).map(([key, label]) => (
                      <section className={styles.scenario} key={key}>
                        <h4>{label}</h4>
                        <Form.Item
                          name={["scenarios", key, "target_price"]}
                          label="假设价格（USD / 股）"
                        >
                          <InputNumber
                            min={0}
                            max={1e12}
                            precision={6}
                            placeholder="可填 0 表示归零"
                          />
                        </Form.Item>
                        <Form.Item
                          name={["scenarios", key, "probability_pct"]}
                          label="本人估计概率（%）"
                        >
                          <InputNumber
                            min={0}
                            max={100}
                            precision={3}
                            placeholder="不会自动填入模型胜率"
                          />
                        </Form.Item>
                        <Form.Item name={["scenarios", key, "thesis"]} label="依据 / 触发条件">
                          <Input.TextArea autoSize={{ minRows: 3, maxRows: 7 }} maxLength={2000} />
                        </Form.Item>
                      </section>
                    ))}
                  </div>
                </>
              ),
            },
            {
              key: "risk",
              label: "③ 仓位与生活现金",
              children: (
                <>
                  <h3>判断错了可能亏多少？应该配多少仓位？</h3>
                  <p>均为手动预算假设。绝对资产、现金与生活保留金额不会发给研究模型。</p>
                  <div className={styles.fieldGrid}>
                    {(
                      [
                        ["capital_total_usd", "组合资产总额（USD）", "USD"],
                        ["available_cash_usd", "当前可用现金（USD）", "USD"],
                        ["essential_cash_usd", "生活支出保留金（USD）", "USD"],
                        ["current_position_pct", "当前单股仓位（%）", "%"],
                        ["target_position_pct", "计划单股仓位（%）", "%"],
                        ["current_theme_exposure_pct", "当前主题仓位（%）", "%"],
                        ["target_theme_exposure_pct", "计划主题仓位（%）", "%"],
                        ["current_drawdown_pct", "当前组合回撤（%）", "%"],
                      ] as const
                    ).map(([key, label, unit]) => (
                      <Form.Item name={["risk_inputs", key]} label={label} key={key}>
                        <InputNumber
                          min={0}
                          max={unit === "USD" ? 1e15 : 100}
                          precision={2}
                          placeholder="未知时留空"
                        />
                      </Form.Item>
                    ))}
                  </div>
                  <Form.Item name={["risk_inputs", "unlevered_long_only"]} valuePropName="checked">
                    <Checkbox>本计划仅按无杠杆多头资产讨论</Checkbox>
                  </Form.Item>
                  <Alert
                    showIcon
                    type="warning"
                    title="情景损失不等于最大损失"
                    description="无杠杆多头归零情景可按计划仓位估计组合损失；跳空、滑点、杠杆及衍生品可能改变风险。保护参考价不保证将亏损限制在该价位。"
                  />
                </>
              ),
            },
            {
              key: "review",
              label: "④ 执行纪律与复盘",
              children: (
                <>
                  {actionRuleLabels.map(([key, label]) => (
                    <Form.Item name={["action_rules", key]} label={label} key={key}>
                      <Input.TextArea {...area} />
                    </Form.Item>
                  ))}
                  <Form.Item name="invalidation" label="核心假设失效条件">
                    <Input.TextArea {...area} placeholder="写下哪些可观察事实会推翻自己的判断" />
                  </Form.Item>
                  <div className={styles.fieldGrid}>
                    <Form.Item name="review_on" label="下次手动复核日期（美东）">
                      <Input type="date" min={initial.analysis_date} />
                    </Form.Item>
                    <Form.Item name="status" label="本人跟踪状态">
                      <Select
                        options={Object.entries(investmentStatusLabels).map(([value, label]) => ({
                          value,
                          label,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item name="run_id" label="关联研究 ID（可留空）" className={styles.wide}>
                      <Input placeholder="仅能关联本账号同一证券的研究" maxLength={36} />
                    </Form.Item>
                  </div>
                  <Form.Item name="review_notes" label="复盘记录">
                    <Input.TextArea
                      {...area}
                      placeholder="发生了什么？原假设、证据与执行纪律有什么变化？区分运气和判断质量。"
                    />
                  </Form.Item>
                  {current && (
                    <Form.Item name="mark_reviewed" valuePropName="checked">
                      <Checkbox>本次保存登记为已手动复盘</Checkbox>
                    </Form.Item>
                  )}
                  {current && current.policy_snapshot.revision !== policy.revision && (
                    <Form.Item name="rebase_policy" valuePropName="checked">
                      <Checkbox>
                        明确改用当前体系 v{policy.revision} 重新检查（原为 v
                        {current.policy_snapshot.revision}）
                      </Checkbox>
                    </Form.Item>
                  )}
                  <p>复核日期只显示到期状态；不会后台定时调用模型、监控行情或替你执行交易。</p>
                </>
              ),
            },
          ]}
        />
        <div className={styles.formFooter}>
          <Button disabled={busy} onClick={onCancel}>
            取消
          </Button>
          <Button type="primary" loading={busy} onClick={() => form.submit()}>
            保存决策计划
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
