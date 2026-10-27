import type {
  ThemeMemberInput,
  ThemeTaxonomyTag,
  ThemeView,
  ThemeWrite,
} from "../../shared/api/generated/models";

export const categoryNames = {
  technology: "科技与算力",
  materials: "关键材料",
  energy: "电力与能源",
  healthcare: "生物医药",
  custom: "自建主题",
};
export const exposureNames = {
  direct_product: "直接产品",
  diversified_business: "多元业务暴露",
  manufacturing_service: "制造服务",
  supplier: "供应链",
  user_unverified: "用户添加 · 未核实",
};
export const taxonomyNames = {
  industry: "行业",
  theme: "投资主题",
  material: "关键材料",
  value_chain: "产业链环节",
};
export const evidenceNames = {
  documented: "维护者证据齐全",
  incomplete: "关系证据待补",
  unverified: "关系未核查",
};
export function themeVersionFromQuery(value: string | null): number | undefined {
  if (!value || !/^[1-9]\d*$/.test(value)) return undefined;
  const version = Number(value);
  return Number.isSafeInteger(version) ? version : undefined;
}
export function taxonomyKey(tag: ThemeTaxonomyTag) {
  return `${tag.kind}::${tag.label}`;
}
export function matchesTheme(theme: ThemeView, query: string) {
  const value = query.trim().toLowerCase();
  return (
    !value ||
    [
      theme.name,
      theme.description,
      ...(theme.aliases ?? []),
      ...(theme.taxonomy_tags ?? []).map((tag) => tag.label),
      ...(theme.members ?? []).map(
        (member) => member.symbol + " " + member.name + " " + member.role,
      ),
    ]
      .join(" ")
      .toLowerCase()
      .includes(value)
  );
}
export function themeDraft(theme?: ThemeView): ThemeWrite {
  return {
    name: theme?.name ?? "",
    description: theme?.description ?? "",
    category: theme?.category ?? "custom",
    aliases: [...(theme?.aliases ?? [])],
    taxonomy_tags: theme?.taxonomy_tags?.map((tag) => ({ ...tag })) ?? [],
    members:
      theme?.members?.map((member) => ({
        symbol: member.symbol,
        name: member.name,
        role: member.role,
        exposure: member.exposure,
        verified_at: member.verified_at,
        evidence_urls: [...(member.evidence_urls ?? [])],
      })) ?? [],
  };
}
export function validateThemeTaxonomy(tags: ThemeTaxonomyTag[]) {
  if (tags.length > 24) throw new Error("最多24个分类标签");
  const keys = new Set<string>();
  for (const tag of tags) {
    if (!(tag.kind in taxonomyNames) || !tag.label?.trim() || Array.from(tag.label).length > 80)
      throw new Error("分类标签请选择维度并填写1至80个字符");
    const key = taxonomyKey({ ...tag, label: tag.label.trim().toLowerCase() });
    if (keys.has(key)) throw new Error("同一维度的分类标签不能重复");
    keys.add(key);
  }
}
function themeMemberSymbol(value: string, row?: number) {
  const symbol = value.trim().toUpperCase();
  const label = symbol.slice(0, 32) || (row == null ? "观察成员" : `第${row + 1}行成员`);
  if (!/^[A-Z0-9][A-Z0-9.^=_-]{0,31}$/.test(symbol))
    throw new Error(`${label}：请输入有效证券代码（最多32个字符）`);
  if (
    (symbol.includes(".") && !/^[A-Z0-9]+\.[AB]$/.test(symbol)) ||
    symbol.includes("=") ||
    symbol.includes("^") ||
    /-(USD|USDT|USDC)$/.test(symbol)
  )
    throw new Error(
      `${label}：仅支持美股、美国上市ADR和ETF代码，不支持海外市场、外汇、期货或加密货币代码`,
    );
  return symbol;
}
export function validateThemeMembers(members: ThemeMemberInput[]): void {
  if (members.length > 100) throw new Error("单个主题最多100个观察样本，请移除多余成员后保存");
  const symbols = new Set<string>();
  for (const [index, member] of members.entries()) {
    const symbol = themeMemberSymbol(member.symbol, index);
    if (symbols.has(symbol)) throw new Error(`${symbol}：此证券已在当前主题中，不能重复添加`);
    symbols.add(symbol);
    if (Array.from(member.name ?? "").length > 240)
      throw new Error(`${symbol}：显示名称最多240个字符`);
    if (Array.from(member.role ?? "").length > 240)
      throw new Error(`${symbol}：产业链环节最多240个字符`);
    if (member.verified_at) {
      const value = member.verified_at;
      const stamp = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(value + "T00:00:00Z") : undefined;
      if (!stamp || Number.isNaN(stamp.valueOf()) || stamp.toISOString().slice(0, 10) !== value)
        throw new Error(`${symbol}：维护者核查日期须为有效的YYYY-MM-DD日期`);
    }
    const urls = member.evidence_urls ?? [];
    if (urls.length > 8) throw new Error(`${symbol}：来源证据最多8条链接`);
    for (const [position, url] of urls.entries()) {
      if (Array.from(url).length > 1500)
        throw new Error(`${symbol}：第${position + 1}条证据链接最多1500个字符`);
      if (!publicLink(url))
        throw new Error(`${symbol}：第${position + 1}条证据链接须为不含账号密码的HTTP(S)地址`);
    }
  }
}
export function addDraftMember(members: ThemeMemberInput[], symbol: string, name = "") {
  const normalized = themeMemberSymbol(symbol);
  if (members.some((member) => member.symbol.trim().toUpperCase() === normalized))
    throw new Error(`${normalized}：此证券已在当前主题中`);
  if (members.length >= 100) throw new Error(`${normalized}：单个主题最多100个观察样本`);
  const member: ThemeMemberInput = {
    symbol: normalized,
    name,
    role: "",
    exposure: "user_unverified",
    evidence_urls: [],
    verified_at: null,
  };
  validateThemeMembers([member]);
  return [...members, member];
}
export function publicLink(value?: string | null) {
  try {
    if (
      !value ||
      !/^https?:\/\/[^/?#\\\s]+/i.test(value) ||
      Array.from(value).some(
        (character) =>
          character === "\\" || character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127,
      )
    )
      return undefined;
    const url = new URL(value ?? "");
    return ["https:", "http:"].includes(url.protocol) &&
      url.hostname &&
      !url.username &&
      !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
