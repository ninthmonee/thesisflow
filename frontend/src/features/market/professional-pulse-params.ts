import type { GetProfessionalMarketPulseParams } from "../../shared/api/generated/models";
import { validAlternativeSymbol } from "../alternative/navigation-context";

export interface ProfessionalPulseSelection {
  options: string;
  etfs: string;
  news: string;
  includeNews: boolean;
  optionsProvider: "yahoo" | "massive" | "alpaca";
  flowsProvider: "ishares" | "intrinio";
  optionsConnection?: string;
  flowsConnection?: string;
  newsConnection?: string;
  lookbackDays: number;
  lookbackHours: number;
}

function symbols(value: string, maximum: number) {
  if (!value.trim()) return undefined;
  const parts = value.split(",").map((item) => validAlternativeSymbol(item));
  if (parts.some((item) => !item) || parts.length > maximum) {
    throw new Error(`请填写最多 ${maximum} 个有效美股代码，以英文逗号分隔。`);
  }
  return [...new Set(parts)].join(",");
}

export function professionalPulseParameters(
  input: ProfessionalPulseSelection,
): GetProfessionalMarketPulseParams {
  const options = symbols(input.options, 5),
    etfs = symbols(input.etfs, 10);
  const news =
    input.includeNews && input.news.trim() ? validAlternativeSymbol(input.news) : undefined;
  if (input.includeNews && input.news.trim() && !news) throw new Error("新闻样本代码无效。");
  return {
    ...(options ? { options_symbols: options } : {}),
    ...(etfs ? { etf_symbols: etfs } : {}),
    ...(news ? { news_symbol: news } : {}),
    include_news: input.includeNews,
    options_provider: input.optionsProvider,
    flows_provider: input.flowsProvider,
    news_provider: "massive",
    ...(options && input.optionsProvider !== "yahoo" && input.optionsConnection
      ? { options_connection_id: input.optionsConnection }
      : {}),
    ...(etfs && input.flowsProvider !== "ishares" && input.flowsConnection
      ? { flows_connection_id: input.flowsConnection }
      : {}),
    ...(input.includeNews && input.newsConnection
      ? { news_connection_id: input.newsConnection }
      : {}),
    lookback_days: input.lookbackDays,
    lookback_hours: input.lookbackHours,
  };
}
