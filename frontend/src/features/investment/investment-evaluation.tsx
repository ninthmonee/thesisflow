import { Alert, Tag } from "antd";
import type { InvestmentEvaluation } from "../../shared/api/generated/models";
import { formatInvestmentNumber, investmentEvaluationLabels } from "./investment-fields";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./investment.module.css";

const checkLabels = {
  within_limit: "已设规则内",
  breach: "超限",
  unknown: "待补充",
  not_applicable: "不适用",
};

export function InvestmentEvaluationView({ evaluation }: { evaluation: InvestmentEvaluation }) {
  const metrics = [
    ["按本人公允价值估计的安全边际", evaluation.margin_of_safety_pct, "%"],
    ["本人情景假设的毛期望收益", evaluation.gross_expected_return_pct, "%"],
    ["扣除估计成本的净期望收益", evaluation.net_expected_return_pct, "%"],
    ["乐观 / 悲观情景盈亏比", evaluation.reward_risk_ratio, " 倍"],
    ["悲观情景组合损失", evaluation.scenario_portfolio_loss_pct, "%"],
    ["无杠杆多头归零情景组合损失", evaluation.zero_value_portfolio_loss_pct, "%"],
    ["保护参考价对应组合损失", evaluation.stop_reference_loss_pct, "%"],
    ["计划调整后现金比例", evaluation.planned_cash_pct, "%"],
  ] as const;
  return (
    <div className={shared.stack}>
      <Alert
        showIcon
        type={evaluation.status === "breach" ? "warning" : "info"}
        title={investmentEvaluationLabels[evaluation.status]}
        description={evaluation.scope_notice}
      />
      <div className={styles.metrics}>
        {metrics.map(([label, value, suffix]) => (
          <div className={styles.metric} key={label}>
            <small>{label}</small>
            <strong>{formatInvestmentNumber(value, suffix)}</strong>
          </div>
        ))}
      </div>
      <section>
        <h3>六个核心问题</h3>
        <div className={styles.checks}>
          {evaluation.questions.map((question) => (
            <div className={styles.check} key={question.key}>
              <div>
                <strong>{question.question}</strong>
                <p style={{ whiteSpace: "pre-wrap" }}>{question.answer || "尚未记录"}</p>
              </div>
              <Tag color={question.status === "recorded" ? "cyan" : "default"}>
                {question.status === "recorded" ? "本人已记录" : "待补充"}
              </Tag>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h3>个人风险边界检查</h3>
        <div className={styles.checks}>
          {evaluation.checks.map((check) => (
            <div className={styles.check} key={check.key}>
              <div>
                <strong>{check.label}</strong>
                <p>
                  当前假设：
                  {formatInvestmentNumber(
                    check.actual,
                    check.key === "reward_risk"
                      ? " 倍"
                      : ["essential_cash", "funding"].includes(check.key)
                        ? " USD"
                        : "%",
                  )}
                  {" · "}本人边界：
                  {formatInvestmentNumber(
                    check.limit,
                    check.key === "reward_risk"
                      ? " 倍"
                      : ["essential_cash", "funding"].includes(check.key)
                        ? " USD"
                        : "%",
                  )}
                </p>
                <p>{check.message}</p>
              </div>
              <Tag
                color={
                  check.status === "breach"
                    ? "warning"
                    : check.status === "within_limit"
                      ? "cyan"
                      : "default"
                }
              >
                {checkLabels[check.status]}
              </Tag>
            </div>
          ))}
        </div>
      </section>
      {evaluation.notices.length > 0 && (
        <div className={shared.muted}>
          {evaluation.notices.map((notice) => (
            <p key={notice}>{notice}</p>
          ))}
        </div>
      )}
    </div>
  );
}
