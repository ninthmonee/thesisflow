import styles from "./investment.module.css";
import type { InvestmentPrinciples as PrincipleValues } from "../../shared/api/generated/models";
import { policyPrincipleLabels } from "./investment-fields";

const investmentPrinciples = [
  ["01", "长期财富增长", "先守本金，再追收益。用时间与复利积累财富，避免一次失误破坏长期积累。"],
  [
    "02",
    "先看周期，再选资产",
    "分别判断宏观、行业与估值周期。记录证据、时点和未知，不把市场情绪当作确定方向。",
  ],
  ["03", "好赛道与好公司", "核查需求、营收利润、自由现金流、护城河、定价权、管理层及竞争地位。"],
  ["04", "好公司也要好价格", "把公司质量、估值与安全边际分开判断。公允价值是需要论据支撑的估计。"],
  [
    "05",
    "仓位管理优先",
    "分批建仓、控制单股与主题集中度、保留现金。先考虑判断错误时可能承受的损失。",
  ],
  [
    "06",
    "用概率思维做决策",
    "比较不同情景的空间、概率和盈亏比。概率由你填写，模型观点不等于验证过的胜率。",
  ],
  [
    "07",
    "用纪律代替情绪",
    "明确买入、加仓、减仓、退出与复盘条件，选择适合自己资金、时间和性格的方式。",
  ],
  ["08", "让投资服务于生活", "区分长期资金与生活支出，考虑流动性、分散、税务和家庭现金安排。"],
] as const;

export function InvestmentPrinciples({ values }: { values?: PrincipleValues }) {
  return (
    <div className={styles.principles}>
      {investmentPrinciples.map(([number, title, description], index) => (
        <article className={styles.principle} key={number}>
          <small>{number}</small>
          <h3>{title}</h3>
          <p>{values?.[policyPrincipleLabels[index]?.[0] ?? "goal"] ?? description}</p>
        </article>
      ))}
    </div>
  );
}
