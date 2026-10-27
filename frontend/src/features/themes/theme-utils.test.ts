import { describe, expect, it } from "vitest";
import {
  addDraftMember,
  matchesTheme,
  publicLink,
  taxonomyKey,
  themeDraft,
  themeVersionFromQuery,
  validateThemeMembers,
  validateThemeTaxonomy,
} from "./theme-utils";
import type { ThemeMemberInput, ThemeView } from "../../shared/api/generated/models";

describe("thematic observation configuration", () => {
  it("finds aliases and supply-chain roles without equating them to GICS sectors", () => {
    const view = {
      name: "磷化铟",
      aliases: ["InP"],
      members: [{ symbol: "AXTI", role: "衬底", name: "AXT" }],
    } as ThemeView;
    expect(matchesTheme(view, "inp")).toBe(true);
    expect(matchesTheme(view, "衬底")).toBe(true);
    expect(matchesTheme(view, "WMT")).toBe(false);
  });
  it("rejects duplicate and unsafe symbols while marking user additions unverified", () => {
    const list = addDraftMember([], " nvda ", "NVIDIA");
    expect(list[0]).toMatchObject({
      symbol: "NVDA",
      exposure: "user_unverified",
      verified_at: null,
    });
    expect(() => addDraftMember(list, "NVDA")).toThrow("已在");
    expect(() => addDraftMember([], "../../secret")).toThrow("有效");
  });
  it.each(["NVDA", "TSM", "BRK.A", "BRK.B", "BF-B"])(
    "allows US share classes and normalizes %s without claiming listing verification",
    (symbol) => {
      expect(addDraftMember([], ` ${symbol.toLowerCase()} `)[0]?.symbol).toBe(symbol);
      expect(() => validateThemeMembers([{ symbol }])).not.toThrow();
    },
  );
  it.each([
    "0700.HK",
    "7203.T",
    "AZN.L",
    "EURUSD=X",
    "GC=F",
    "BTC-USD",
    "ETH-USDT",
    "ETH-USDC",
    "^GSPC",
    "A^B",
  ])("rejects unsupported market or currency notation %s both on add and save", (symbol) => {
    expect(() => addDraftMember([], symbol)).toThrow(symbol);
    expect(() => validateThemeMembers([{ symbol }])).toThrow(symbol);
  });
  it("detects duplicate normalized symbols before saving without mutating the draft", () => {
    const members = [{ symbol: "MU" }, { symbol: "mu" }];
    expect(() => validateThemeMembers(members)).toThrow("MU：此证券已在");
    expect(() => addDraftMember([{ symbol: "mu" }], " MU ")).toThrow("MU：此证券已在");
    expect(members[1]?.symbol).toBe("mu");
  });
  it("accepts an empty material theme and exactly 100 members but blocks overflow", () => {
    const members = Array.from({ length: 100 }, (_, index) => ({ symbol: `S${index}` }));
    expect(() => validateThemeMembers([])).not.toThrow();
    expect(() => validateThemeMembers(members)).not.toThrow();
    expect(() => addDraftMember(members, "MU")).toThrow("MU：单个主题最多100个");
    expect(() => validateThemeMembers([...members, { symbol: "MU" }])).toThrow("最多100个");
  });
  it("checks display and role character limits with the same Unicode counting as Python", () => {
    const member = { symbol: "MU", name: "😀".repeat(240), role: "存".repeat(240) };
    expect(() => validateThemeMembers([member])).not.toThrow();
    expect(() => validateThemeMembers([{ ...member, name: member.name + "A" }])).toThrow(
      "MU：显示名称最多240",
    );
    expect(() => validateThemeMembers([{ ...member, role: member.role + "A" }])).toThrow(
      "MU：产业链环节最多240",
    );
    expect(() => addDraftMember([], "MU", "A".repeat(241))).toThrow("MU：显示名称最多240");
  });
  it("checks evidence count and length at save time with an actionable member and link position", () => {
    const prefix = "https://example.com/";
    const url = prefix + "a".repeat(1500 - prefix.length);
    const member: ThemeMemberInput = {
      symbol: "MU",
      evidence_urls: Array.from({ length: 8 }, () => url),
    };
    expect(() => validateThemeMembers([member])).not.toThrow();
    expect(() =>
      validateThemeMembers([{ ...member, evidence_urls: [...(member.evidence_urls ?? []), url] }]),
    ).toThrow("MU：来源证据最多8");
    expect(() =>
      validateThemeMembers([{ ...member, evidence_urls: ["https://www.sec.gov/", url + "a"] }]),
    ).toThrow("MU：第2条证据链接最多1500");
  });
  it("copies editable membership without mutating the persisted version", () => {
    const view = {
      name: "Memory",
      members: [{ symbol: "MU", evidence_urls: ["https://example.com"] }],
    } as ThemeView;
    const draft = themeDraft(view);
    draft.members?.[0]?.evidence_urls?.push("https://example.org");
    expect(view.members?.[0]?.evidence_urls).toHaveLength(1);
  });
  it("keeps computed relationship quality out of writes and copies multidimensional tags", () => {
    const view = {
      name: "Storage / HBM / DRAM",
      aliases: ["HBM"],
      taxonomy_tags: [
        { kind: "industry", label: "半导体" },
        { kind: "theme", label: "半导体" },
      ],
      members: [
        {
          symbol: "MU",
          exposure: "supplier",
          evidence_quality: "documented",
          evidence_urls: ["https://company.example/product"],
        },
      ],
    } as ThemeView;
    const draft = themeDraft(view);
    expect(draft.members?.[0]).not.toHaveProperty("evidence_quality");
    expect(draft.taxonomy_tags).toEqual(view.taxonomy_tags);
    if (draft.taxonomy_tags?.[0]) draft.taxonomy_tags[0].label = "modified";
    draft.aliases?.push("DRAM");
    expect(view.taxonomy_tags?.[0]?.label).toBe("半导体");
    expect(view.aliases).toHaveLength(1);
    expect(matchesTheme(view, "半导体")).toBe(true);
  });
  it("allows the same label across taxonomy dimensions while rejecting invalid or duplicate tags", () => {
    expect(() =>
      validateThemeTaxonomy([
        { kind: "industry", label: "半导体" },
        { kind: "theme", label: "半导体" },
      ]),
    ).not.toThrow();
    expect(taxonomyKey({ kind: "material", label: "InP" })).toBe("material::InP");
    expect(() =>
      validateThemeTaxonomy([
        { kind: "theme", label: "AI" },
        { kind: "theme", label: " ai " },
      ]),
    ).toThrow("不能重复");
    expect(() => validateThemeTaxonomy([{ kind: "material", label: " " }])).toThrow("填写");
    expect(() => validateThemeTaxonomy([{ kind: "material", label: "A".repeat(81) }])).toThrow(
      "80",
    );
    expect(() =>
      validateThemeTaxonomy(
        Array.from({ length: 25 }, (_, index) => ({ kind: "theme", label: String(index) })),
      ),
    ).toThrow("24");
  });
  it.each([null, "", "0", "-1", "1.5", "01", "1e2", "9007199254740992"])(
    "does not silently accept invalid historical version %s",
    (value) => {
      expect(themeVersionFromQuery(value)).toBeUndefined();
    },
  );
  it("selects an explicit positive historical configuration version", () => {
    expect(themeVersionFromQuery("2")).toBe(2);
    expect(themeVersionFromQuery("100")).toBe(100);
  });
  it("validates maintainer dates without relabeling them as platform verification", () => {
    expect(() => validateThemeMembers([{ symbol: "MU", verified_at: "2024-02-29" }])).not.toThrow();
    expect(() => validateThemeMembers([{ symbol: "MU", verified_at: "2025-02-29" }])).toThrow(
      "维护者核查日期",
    );
    expect(() => validateThemeMembers([{ symbol: "MU", verified_at: "2026-2-01" }])).toThrow(
      "维护者核查日期",
    );
  });
  it("keeps external evidence links free from script schemes and embedded credentials", () => {
    expect(publicLink("javascript:alert(1)")).toBeUndefined();
    expect(publicLink("https://user:secret@example.com")).toBeUndefined();
    expect(publicLink("https://www.sec.gov/")).toBe("https://www.sec.gov/");
  });
  it.each([
    "javascript:alert(1)",
    "file:///etc/passwd",
    "//example.com/research",
    "https:example.com",
    "https:///example.com",
    "https://user:private-value@example.com/research",
    "https://user@example.com/research",
    "https://example.com\\@other.example/research",
    "https://example.com/\nresearch",
  ])("rejects unsafe evidence %s without echoing link credentials in the error", (url) => {
    expect(publicLink(url)).toBeUndefined();
    expect(() => validateThemeMembers([{ symbol: "MU", evidence_urls: [url] }])).toThrow(
      "MU：第1条证据链接须为不含账号密码",
    );
    expect(() => validateThemeMembers([{ symbol: "MU", evidence_urls: [url] }])).not.toThrow(
      "private-value",
    );
  });
  it("preserves a valid public HTTP(S) source and leaves the supplied evidence untouched", () => {
    const members = [
      {
        symbol: "MU",
        evidence_urls: [
          "https://www.sec.gov/Archives/?document=1#item",
          "http://example.com/research",
        ],
      },
    ];
    const snapshot = structuredClone(members);
    expect(() => validateThemeMembers(members)).not.toThrow();
    expect(members).toEqual(snapshot);
  });
});
