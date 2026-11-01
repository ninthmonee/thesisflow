import { installLive } from "./live.js";
const viewOverrides = new Map();
const actionOverrides = new Map();
const afterRender = [];

// Markup and layout follow the Open Design reference. Business records come from the authenticated host.
let TODAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());
let STORAGE_KEY = "tradingagents.design.preferences";
const iconPaths = {
  dashboard:
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  chart: '<path d="M3 3v18h18M7 14l4-5 4 3 6-8"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5Z"/>',
  plan: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M9 10h6M9 14h6M9 18h3"/>',
  library: '<path d="M4 3h5v18H4zM10 3h5v18h-5zM16 5l4-1 4 16-4 1z"/>',
  settings:
    '<path d="m9 3-1 3-3 1-2 5 2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 17h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  back: '<path d="M20 12H4m6-6-6 6 6 6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  chevrons: '<path d="m8 9 4-4 4 4m-8 6 4 4 4-4"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  circleCheck: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
  layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16M16 4v16"/>',
  refresh: '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
  file: '<path d="M14 2H5v20h14V7Zm0 0v5h5M8 12h8M8 16h8"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  link: '<path d="m10 13 4-4m-5 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"/>',
  edit: '<path d="m15 4 5 5M4 15 16 3l5 5L9 20l-6 1Z"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  external: '<path d="M14 3h7v7m0-7L10 14M9 3H3v18h18v-6"/>',
  compare: '<path d="M8 3v18M16 3v18M3 8l5-5 5 5m-2 8 5 5 5-5"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 17l9 5 9-5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  database:
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
};
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
const icon = (name, extra = "") =>
  `<svg class="od-icon ${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.file}</svg>`;
const button = (
  label,
  action,
  { primary = false, glyph = "", attributes = "", small = false, ghost = false } = {},
) =>
  `<button type="button" class="button${primary ? " primary" : ""}${small ? " small" : ""}${ghost ? " ghost" : ""}" data-action="${action}" ${attributes}>${glyph ? icon(glyph) : ""}${label}</button>`;
const badge = (label, tone = "") => `<span class="badge ${tone}">${label}</span>`;
const signed = (value, digits = 2) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : `${value > 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
const money = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const tone = (value) => (value == null ? "quiet" : value >= 0 ? "positive" : "negative");

const securities = [];
const security = (symbol) =>
  securities.find((item) => item.symbol === symbol) || {
    symbol: symbol || "",
    name: "证券资料待加载",
    en: "",
    type: "stock",
    price: null,
    change: null,
    group: "",
    theme: "待整理",
    tile: "",
  };
const initialState = {
  watchlist: /** @type {string[]} */ ([]),
  favorites: /** @type {string[]} */ ([]),
  notes: {},
  runs: [],
  plans: [],
  viewpoints: {},
  preferences: {
    density: "standard",
    displayName: "",
    riseColor: "green",
    defaultPage: "dashboard",
  },
  model: { name: "未配置", quick: "未配置", deep: "未配置", runtime: "未配置" },
  policy: { objective: "", maxPosition: "", minCash: "", interval: "14" },
};
const state = structuredClone(initialState);

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.preferences));
  } catch {
    /* Preferences remain usable for this session. */
  }
}
const ui = {
  page: ["dashboard", "watch", "market", "investment", "library"].includes(
    state.preferences.defaultPage,
  )
    ? state.preferences.defaultPage
    : "dashboard",
  previous: [],
  scroll: {},
  symbol: "",
  runId: "",
  watchMode: "workspace",
  group: "全部",
  watchQuery: "",
  favoritesOnly: false,
  period: "1月",
  interval: "1D",
  movingAverage: true,
  chartType: "candles",
  investmentTab: "plans",
  planQuery: "",
  planStatus: "all",
  planId: null,
  planStep: "company",
  planDraft: null,
  researchStep: 1,
  researchDraft: null,
  reportTab: null,
  marketTab: "overview",
  marketWindow: "1D",
  theme: "",
  professionalTab: "flows",
  libraryQuery: "",
  libraryStatus: "all",
  libraryPage: 1,
  compare: [],
  settingsTab: "models",
  context: [],
  noteDrafts: {},
};
const main = document.getElementById("main");
const dialog = document.getElementById("app-dialog");
let dialogReturn = null;
let toastTimer;

const navItems = [
  ["dashboard", "研究工作台", "dashboard"],
  ["watch", "自选行情", "chart"],
  ["market", "市场发现", "compass"],
  ["investment", "投资决策", "plan"],
  ["library", "研究库", "library"],
];
function activePage() {
  return ["researchNew", "researchDetail"].includes(ui.page)
    ? "dashboard"
    : ui.page === "planEditor"
      ? "investment"
      : ui.page;
}
function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((node) => {
    node.innerHTML = icon(node.dataset.icon);
  });
}
function toast(message, undo = false) {
  const element = document.getElementById("toast");
  clearTimeout(toastTimer);
  element.innerHTML = `${icon("circleCheck")}<span>${esc(message)}</span>${undo ? '<button type="button" class="text-button" data-action="undo-watch">撤销</button>' : ""}`;
  element.hidden = false;
  toastTimer = setTimeout(
    () => {
      element.hidden = true;
    },
    undo ? 8000 : 3800,
  );
}
function restoreFocus(key = "") {
  const target = key
    ? Array.from(document.querySelectorAll("[data-focus-key]")).find(
        (node) => node.dataset.focusKey === key,
      )
    : null;
  (target || main).focus({ preventScroll: true });
}
function navigate(page, extras = {}, remember = true) {
  if (remember && ui.page !== page)
    ui.previous.push({ page: ui.page, symbol: ui.symbol, runId: ui.runId, y: window.scrollY });
  ui.scroll[ui.page] = window.scrollY;
  Object.assign(ui, extras, { page });
  render();
  window.scrollTo({ top: ui.scroll[page] || 0, behavior: "instant" });
  main.focus({ preventScroll: true });
}
function goBack() {
  const old = ui.previous.pop();
  if (old) {
    Object.assign(ui, { page: old.page, symbol: old.symbol, runId: old.runId });
    render();
    window.scrollTo({ top: old.y, behavior: "instant" });
    main.focus({ preventScroll: true });
  } else navigate("dashboard", {}, false);
}
function renderNav() {
  const active = activePage();
  document.getElementById("main-nav").innerHTML = navItems
    .map(
      ([page, label, glyph]) =>
        `<button type="button" class="nav-link ${active === page ? "active" : ""}" ${active === page ? 'aria-current="page"' : ""} data-action="navigate" data-page="${page}" data-focus-key="nav-${page}" title="${label}"><span>${icon(glyph)}</span><span>${label}</span>${page === "library" ? `<span class="nav-count">${state.runs.length}</span>` : ""}</button>`,
    )
    .join("");
  document
    .querySelectorAll('.sidebar-bottom [data-page="settings"]')
    .forEach((node) => node.classList.toggle("active", ui.page === "settings"));
  const names = {
    researchNew: "新建研究",
    researchDetail: "研究详情",
    planEditor: "决策计划",
    settings: "设置中心",
  };
  document.getElementById("breadcrumb-current").textContent =
    names[ui.page] || navItems.find(([page]) => page === ui.page)?.[1] || "研究工作台";
  document.getElementById("task-dot").hidden = !state.runs.some((run) =>
    ["pending", "queued", "running", "cancelling"].includes(run.status),
  );
  document.body.classList.toggle(
    "density-comfortable",
    state.preferences.density === "comfortable",
  );
  const redRise = state.preferences.riseColor === "red";
  document.documentElement.style.setProperty("--positive", redRise ? "#f18e96" : "#72d1b1");
  document.documentElement.style.setProperty("--negative", redRise ? "#72d1b1" : "#f18e96");
}
function render() {
  const focused = document.activeElement?.closest?.("[data-action]");
  const focusData = focused ? { ...focused.dataset } : null;
  renderNav();
  const views = {
    dashboard: dashboardView,
    watch: () => "",
    researchNew: newResearchView,
    researchDetail: () => "",
    investment: () => "",
    planEditor: planEditorView,
    market: () => "",
    library: libraryView,
    settings: settingsView,
  };
  main.innerHTML = (viewOverrides.get(ui.page) || views[ui.page] || dashboardView)();
  const note = document.getElementById("stock-note");
  if (note && Object.prototype.hasOwnProperty.call(ui.noteDrafts, ui.symbol))
    note.value = ui.noteDrafts[ui.symbol];
  hydrateIcons(main);
  for (const callback of afterRender) callback();
  if (focusData && !document.activeElement?.closest?.("dialog[open]")) {
    const equivalent = Array.from(document.querySelectorAll("[data-action]")).find(
      (node) =>
        !node.disabled &&
        Object.entries(focusData).every(([key, value]) => node.dataset[key] === value),
    );
    equivalent?.focus({ preventScroll: true });
  }
}
function heading(title, description, actions = "", eyebrow = "") {
  return `<div class="page-heading"><div class="od-field">${eyebrow ? `<span class="eyebrow">${eyebrow}</span>` : ""}<h1>${title}</h1><p>${description}</p></div><div class="page-actions">${actions}</div></div>`;
}
function emptyState(title, detail, action = "") {
  return `<div class="empty-state">${icon("search")}<h3>${title}</h3><p>${detail}</p>${action}</div>`;
}
function tickerTile(stock, small = false) {
  return `<span class="ticker-tile ${stock.tile} ${small ? "small" : ""}">${esc(stock.symbol)}</span>`;
}

function marketStrip() {
  if (viewOverrides.has("marketStrip")) return viewOverrides.get("marketStrip")();
  return "";
}
function runStatus(run) {
  return (
    {
      pending: "等待执行",
      queued: "排队中",
      running: "研究中",
      completed: "已完成",
      cancelled: "已取消",
      cancelling: "取消中",
      failed: "失败",
      interrupted: "已中断",
    }[run.status] || "未知"
  );
}
function duePlans() {
  return state.plans.filter((plan) => plan.status !== "archived" && plan.reviewDue === true);
}
function dashboardView() {
  const active = state.runs.filter((run) =>
    ["pending", "queued", "running", "cancelling"].includes(run.status),
  );
  const current = active[0] || state.runs[0];
  const stock = current ? security(current.symbol) : security(ui.symbol);
  const recent = state.runs.filter((run) => run.status === "completed").slice(0, 3);
  const due = duePlans();
  return `<div class="page">${heading("研究工作台", "让每一次判断，都有证据与纪律。", button("新建研究", "new-research", { primary: true, glyph: "plus" }), "YOUR RESEARCH, IN FOCUS")}
    <div class="small-meta"><span>${esc(TODAY)}</span><span class="separator"></span><span>美东日期 · 当前工作区</span><span class="separator"></span><span>行情按来源时点展示</span></div>
    ${marketStrip()}
    <div class="dashboard-grid"><div class="stack">
      <section class="panel research-focus"><header class="panel-head"><h2>${active.length ? "继续你的研究" : "最近研究"}</h2>${badge(current ? runStatus(current) : "尚无研究", current?.status === "completed" || active.length ? "mint" : "amber")}</header><div class="panel-body">
        <div class="od-row"><div class="focus-title od-fill">${tickerTile(stock)}<div><h3>${stock.symbol}<span class="muted" style="font-size:14px;font-weight:400;margin-left:12px">${esc(stock.name)}</span></h3><p>${esc(current?.objective || "从一个值得验证的问题开始")}</p></div></div></div>
        <div class="stage-strip">${["分析师研究", "多空研究", "风险讨论", "报告汇总"]
          .map((label, i) => {
            const stage = current?.stage || 0;
            const mapped = [1, 2, 4, 5][i];
            return `<div class="stage-item ${stage > mapped ? "done" : stage === mapped ? "current" : ""}"><strong>${stage > mapped ? "✓ " : ""}${label}</strong><small>${stage > mapped ? "已有研究产出" : stage === mapped ? "当前执行阶段" : "等待前序阶段"}</small></div>`;
          })
          .join("")}</div>
        <div class="focus-bottom"><p>${current?.mode === "local" ? "Codex 独立研究" : "内置 12 角色"} · ${esc(current?.template || "标准研究")} · 研究任务</p>${current ? button("打开研究", "open-run", { glyph: "arrow", attributes: `data-id="${esc(current.id)}"` }) : button("开始研究", "new-research")}</div>
      </div></section>
      <section class="panel"><header class="panel-head"><h2>最近研究</h2><button type="button" class="text-button" data-action="navigate" data-page="library">全部记录 ${icon("arrow")}</button></header>
        <div class="summary-strip"><div class="od-stat"><strong>${state.runs.length.toString().padStart(2, "0")}</strong><small>研究</small></div><div class="od-stat"><strong>${active.length.toString().padStart(2, "0")}</strong><small>进行中</small></div><div class="od-stat"><strong>${due.length.toString().padStart(2, "0")}</strong><small>到期复盘</small></div></div>
        ${recent.length ? `<table class="data-table responsive-table"><thead><tr><th>研究标的</th><th>研究日期</th><th>判断与资格</th><th></th></tr></thead><tbody>${recent.map((run) => `<tr><td><button type="button" class="table-link od-row" data-action="open-run" data-id="${esc(run.id)}">${tickerTile(security(run.symbol), true)}<span class="od-field"><strong>${run.symbol}</strong><small>${esc(security(run.symbol).name)}</small></span></button></td><td class="mono mobile-extra">${run.date.slice(5)}</td><td>${judgmentCell(run)}</td><td class="row-end"><button type="button" class="icon-button" data-action="open-run" data-id="${esc(run.id)}" aria-label="打开 ${run.symbol} 研究">${icon("arrow")}</button></td></tr>`).join("")}</tbody></table>` : emptyState("还没有完成的研究", "启动一次研究，体验完整的研究与决策流程。")}
      </section>
      <section class="panel"><header class="panel-head"><h2>我的关注</h2><button class="text-button" type="button" data-action="navigate" data-page="watch">自选行情 ${icon("arrow")}</button></header><table class="data-table responsive-table"><thead><tr><th>证券</th><th>最近完整日收盘</th><th>日涨跌</th><th>研究方向</th></tr></thead><tbody>${state.watchlist
        .slice(0, 4)
        .map((symbol) => {
          const item = security(symbol);
          return `<tr><td><button type="button" class="table-link od-field" data-action="open-stock" data-symbol="${symbol}"><strong class="mono">${symbol}</strong><small>${esc(item.name)}</small></button></td><td class="number-cell" data-label="最近完整日收盘">${money(item.price)}</td><td class="number-cell ${tone(item.change)}" data-label="日涨跌">${signed(item.change)}%</td><td class="mobile-extra muted">${esc(item.theme)}</td></tr>`;
        })
        .join("")}</tbody></table></section>
    </div><aside class="stack">
      <section class="panel"><header class="panel-head"><h2>待复盘</h2>${badge(`${due.length} 项`, due.length ? "amber" : "")}</header>${due.length ? due.map((plan) => `<div class="review-item"><div class="od-row"><strong class="mono">${plan.symbol}</strong><span class="badge amber">待复盘</span></div><p>${esc(plan.title)}</p><small>计划复核 ${esc(plan.review)}</small><div>${button("开始复盘", "review-plan", { small: true, attributes: `data-id="${esc(plan.id)}"` })}</div></div>`).join("") : emptyState("暂无到期计划", "按计划记录复核日期，让研究持续积累。")}</section>
      <section class="panel"><header class="panel-head"><h2>我的投资纪律</h2>${icon("shield")}</header><div class="panel-body"><ol class="principle-list"><li><span class="principle-number">01</span><div><strong>先看周期，再选公司</strong><p>区分需求趋势与短期波动</p></div></li><li><span class="principle-number">02</span><div><strong>好公司也要好价格</strong><p>质量、估值、安全边际分别判断</p></div></li><li><span class="principle-number">03</span><div><strong>写下假设与失效条件</strong><p>用证据修正判断，按计划复盘</p></div></li></ol><div style="margin-top:24px">${button("查看个人体系", "policy", { ghost: true, small: true, glyph: "arrow" })}</div></div></section>
      <div class="note-box"><div class="od-row" style="margin-bottom:8px">${icon("info")}<strong style="font-size:12px">判断从证据开始</strong></div><p style="font-size:12px">执行完成、数据资格与个人确认分别记录。报告完成、数据资格与个人确认分别记录。</p></div>
    </aside></div>
  </div>`;
}

function matchesStock(stock, query) {
  return `${stock.symbol} ${esc(stock.name)} ${esc(stock.en)} ${esc(stock.theme)} ${state.notes[stock.symbol] || ""}`
    .toLowerCase()
    .includes(query.trim().toLowerCase());
}

function makeResearchDraft(symbol = ui.symbol) {
  const stock = securities.find((item) => item.symbol === symbol && item.type === "stock");
  return {
    symbol: stock?.symbol || "",
    objective: "",
    date: TODAY,
    horizon: "long",
    mode: "builtin",
    depth: "standard",
    language: "Chinese",
    analystMarket: true,
    analystFundamentals: true,
    analystNews: true,
    analystSocial: true,
    policy: true,
    planId: "",
    context: [...ui.context],
    maxCalls: "80",
    maxSeconds: "1800",
    instructions: "",
  };
}
const depthInfo = {
  quick: ["快速研究", "1 轮辩论 · 建立初步观察"],
  standard: ["标准研究", "2 轮辩论 · 综合验证假设"],
  deep: ["深入研究", "3 轮辩论 · 深挖分歧与风险"],
};
function researchSummary() {
  const draft = ui.researchDraft;
  const stock = security(draft.symbol);
  return `<header class="panel-head"><h2>本次研究摘要</h2>${icon("file")}</header><div class="panel-body stack-sm"><div class="od-row">${tickerTile(stock, true)}<div class="od-field"><strong class="mono">${stock.symbol}</strong><small class="quiet">${esc(stock.name)}</small></div></div><dl class="definition-list"><div><dt>分析日期</dt><dd>${esc(draft.date || "待选择")} ET</dd></div><div><dt>观察期限</dt><dd>${{ short: "数天至数周", medium: "1–6 个月", long: "超过 6 个月" }[draft.horizon]}</dd></div><div><dt>执行方式</dt><dd>${draft.mode === "local" ? "Codex 独立研究" : "内置 12 角色"}</dd></div><div><dt>研究方案</dt><dd>${draft.mode === "local" ? esc(state.model.runtime) : depthInfo[draft.depth][0]}</dd></div><div><dt>研究模型</dt><dd>${esc(draft.mode === "local" ? state.model.deep : `${state.model.quick} / ${state.model.deep}`)}</dd></div><div><dt>个人纪律</dt><dd>${draft.policy ? "应用个人投资体系" : "本次不附加"}</dd></div><div><dt>补充资料</dt><dd>${draft.context.length ? `${draft.context.length} 项来源快照` : "尚未附加"}</dd></div></dl><div class="note-box" style="font-size:12px">启动后由研究引擎执行，进度与产出保存在当前账号。</div></div><div class="panel-foot">启动前核对标的、执行配置和预算。</div>`;
}
function newResearchView() {
  if (!ui.researchDraft) ui.researchDraft = makeResearchDraft();
  const draft = ui.researchDraft;
  const step = ui.researchStep;
  let content = "";
  if (step === 1) {
    content = `<div class="form-section"><div class="form-section-heading"><span class="form-section-number">01</span><h2>研究对象与问题</h2></div><p class="muted" style="font-size:14px">从一个明确的问题开始，让研究有清晰的方向。</p><div class="form-grid"><div class="field"><label for="research-symbol">研究标的<span class="required">*</span></label><input id="research-symbol" class="input mono" data-research-field="symbol" value="${esc(draft.symbol)}" list="research-securities" maxlength="32" autocomplete="off" placeholder="输入证券代码，例如 NVDA" aria-describedby="research-symbol-error"><p id="research-symbol-error" class="field-error" hidden></p><datalist id="research-securities">${securities
      .filter((stock) => stock.type === "stock")
      .map((stock) => `<option value="${esc(stock.symbol)}">${esc(stock.name)}</option>`)
      .join(
        "",
      )}</datalist></div><div class="field"><label for="research-date">分析日期 · 美东<span class="required">*</span></label><input type="date" class="input" id="research-date" data-research-field="date" value="${esc(draft.date)}" min="1970-01-01" max="${TODAY}" aria-describedby="research-date-error"><p id="research-date-error" class="field-error" hidden></p></div><div class="field form-span"><label for="research-objective">你最想验证的问题<span class="required">*</span></label><textarea id="research-objective" class="textarea" data-research-field="objective" rows="4" maxlength="1000" placeholder="例如：AI 算力需求能否持续？当前价格反映了哪些增长假设？" aria-describedby="research-objective-error">${esc(draft.objective)}</textarea><p class="field-help">把关键假设、分歧或疑问写下来。</p><p id="research-objective-error" class="field-error" hidden></p></div><div class="field"><label for="research-horizon">观察期限</label><select id="research-horizon" data-research-field="horizon" class="select"><option value="long" ${draft.horizon === "long" ? "selected" : ""}>长期 · 超过 6 个月</option><option value="medium" ${draft.horizon === "medium" ? "selected" : ""}>中期 · 1–6 个月</option><option value="short" ${draft.horizon === "short" ? "selected" : ""}>短期 · 数天至数周</option></select></div><div class="field"><label for="research-language">报告语言</label><select id="research-language" class="select" data-research-field="language"><option value="Chinese" ${draft.language === "Chinese" ? "selected" : ""}>简体中文</option><option value="English" ${draft.language === "English" ? "selected" : ""}>English</option></select></div></div></div>`;
  } else if (step === 2) {
    content = `<div class="form-section"><div class="form-section-heading"><span class="form-section-number">02</span><h2>选择研究方案</h2></div><div class="choice-grid"><button type="button" class="choice ${draft.mode === "builtin" ? "active" : ""}" data-action="research-mode" data-mode="builtin" aria-pressed="${draft.mode === "builtin"}">${icon("layers")}<span class="od-field"><strong>内置 12 角色</strong><small>分析师、多空研究与风险讨论，分阶段呈现观点与证据。</small></span></button><button type="button" class="choice ${draft.mode === "local" ? "active" : ""}" data-action="research-mode" data-mode="local" aria-pressed="${draft.mode === "local"}">${icon("compass")}<span class="od-field"><strong>本地 Agent</strong><small>使用已配置的 Codex 运行环境，查看独立研究步骤。</small></span></button></div>${
      draft.mode === "builtin"
        ? `<div class="field"><span class="field-label">研究深度</span><div class="stack-sm">${Object.entries(
            depthInfo,
          )
            .map(
              ([key, [label, detail]]) =>
                `<button type="button" class="template-row ${draft.depth === key ? "active" : ""}" data-action="research-depth" data-depth="${key}" aria-pressed="${draft.depth === key}"><span class="od-field"><strong>${label}${key === "standard" ? ' <span class="quiet" style="font-size:11px">推荐</span>' : ""}</strong><small>${detail}</small></span>${draft.depth === key ? icon("circleCheck") : icon("chevron")}</button>`,
            )
            .join(
              "",
            )}</div></div><div class="note-box"><div class="workflow-mini"><span>分析师研究</span>${icon("arrow")}<span>多空研究</span>${icon("arrow")}<span>风险讨论</span>${icon("arrow")}<span>研究汇总</span></div></div><details class="inline-details"><summary>进阶设置 · 分析师与执行预算</summary><div class="stack-sm"><div class="filter-group">${[
            ["analystMarket", "技术分析"],
            ["analystFundamentals", "基本面"],
            ["analystNews", "新闻"],
            ["analystSocial", "情绪"],
          ]
            .map(
              ([key, label]) =>
                `<label class="check-label"><input type="checkbox" data-research-field="${key}" ${draft[key] ? "checked" : ""}>${label}</label>`,
            )
            .join(
              "",
            )}</div><p id="research-analysts-error" class="field-error" hidden></p><div class="form-grid"><div class="field"><label for="research-maxcalls">模型请求上限</label><input id="research-maxcalls" class="input" type="number" min="1" max="500" step="1" data-research-field="maxCalls" value="${esc(draft.maxCalls)}"><p id="research-maxcalls-error" class="field-error" hidden></p></div><div class="field"><label for="research-maxseconds">执行时长上限 · 秒</label><input id="research-maxseconds" class="input" type="number" min="60" max="14400" step="1" data-research-field="maxSeconds" value="${esc(draft.maxSeconds)}"><p id="research-maxseconds-error" class="field-error" hidden></p></div></div></div></details>`
        : `<div class="note-box"><div class="od-row">${icon("compass")}<strong>${esc(state.model.runtime)}</strong></div><p style="font-size:12px;margin-top:12px">运行环境 · 模型 ${esc(state.model.deep)}。执行位置、模型和授权由已配置的运行环境决定。</p></div><div class="field"><label for="research-instructions">补充要求 · 可选</label><textarea id="research-instructions" class="textarea" rows="4" data-research-field="instructions" maxlength="2000" placeholder="希望重点核对的资料、比较角度或假设">${esc(draft.instructions)}</textarea></div>`
    }</div>`;
  } else {
    const related = state.plans.filter(
      (plan) => plan.symbol === draft.symbol && plan.status !== "archived",
    );
    const availableContext = [...new Set(draft.context)];
    content = `<div class="form-section"><div class="form-section-heading"><span class="form-section-number">03</span><h2>纪律与补充资料</h2></div><label class="check-label"><input type="checkbox" data-research-field="policy" ${draft.policy ? "checked" : ""}><span class="od-field"><strong>应用我的投资体系</strong><small>围绕周期、公司、估值、风险和仓位纪律整理证据。</small></span></label><div class="note-box"><p style="font-size:14px">${esc(state.policy.objective)}</p><p class="field-help" style="margin-top:12px">个人规则会冻结为本次研究的配置快照。</p></div><div class="field"><label for="research-plan">关联决策计划 · 可选</label><select id="research-plan" class="select" data-research-field="planId"><option value="">暂不关联</option>${related.map((plan) => `<option value="${esc(plan.id)}" ${draft.planId === plan.id ? "selected" : ""}>${esc(plan.title)} · v${plan.revision}</option>`).join("")}</select></div><div class="field"><span class="field-label">补充资料</span><div class="stack-sm">${availableContext.map((label) => `<label class="check-label"><input type="checkbox" data-context="${esc(label)}" ${draft.context.includes(label) ? "checked" : ""}><span class="od-field"><strong>${esc(label)}</strong><small>截至 ${TODAY} · 已保存的来源快照</small></span></label>`).join("")}</div></div><div class="note-box amber">点击后将启动研究并使用已配置的模型或运行环境，请核对参数与预算。</div></div>`;
  }
  return `<div class="page">${heading("新建研究", "先明确要回答的问题，再让证据形成判断。", button("返回", "back", { glyph: "back", ghost: true }), "NEW RESEARCH")}
    <div class="wizard-steps" aria-label="研究创建步骤">${["研究问题", "研究方案", "纪律与资料"].map((label, index) => `<button class="wizard-step ${step === index + 1 ? "active" : ""}" type="button" data-action="research-step" data-step="${index + 1}" ${step === index + 1 ? 'aria-current="step"' : ""}><span class="step-circle">${index + 1 < step ? "✓" : index + 1}</span>${label}</button>`).join("")}</div>
    <div class="split-layout"><section class="panel"><form id="research-form" novalidate><div class="panel-body"><div id="research-error-summary" class="note-box amber" style="margin-bottom:20px" hidden></div>${content}</div><footer class="wizard-footer"><div>${step > 1 ? button("上一步", "research-prev", { glyph: "back", ghost: true }) : `<p>标有 * 的项目需要填写</p>`}</div>${step < 3 ? button("下一步", "research-next", { primary: true, glyph: "arrow" }) : button("启动研究", "start-research", { primary: true, glyph: "plus" })}</footer></form></section><aside id="research-summary" class="panel summary-card">${researchSummary()}</aside></div>
  </div>`;
}

function reportDecision(run) {
  const view = state.viewpoints[run.id];
  const plan = state.plans.find((item) => item.runId === run.id && item.status !== "archived");
  return `<div class="split-layout"><section class="panel"><header class="panel-head"><h2>我的研究观点</h2>${view ? badge("已保存", "mint") : badge("待本人确认")}</header><div class="panel-body stack">${run.status !== "completed" ? `<div class="note-box amber">研究尚未完成。你可以先建立自己的计划，完整报告形成后再确认研究观点。</div>` : ""}<form id="viewpoint-form" class="stack-sm" novalidate><div class="field"><label for="viewpoint-stance">我的立场</label><select id="viewpoint-stance" class="select"><option value="watch" ${view?.stance === "watch" ? "selected" : ""}>继续观察</option><option value="positive" ${view?.stance === "positive" ? "selected" : ""}>偏积极</option><option value="neutral" ${view?.stance === "neutral" ? "selected" : ""}>中性</option><option value="negative" ${view?.stance === "negative" ? "selected" : ""}>偏谨慎</option></select></div><div class="field"><label for="viewpoint-thesis">我认可的论据<span class="required">*</span></label><textarea id="viewpoint-thesis" class="textarea" maxlength="3000" rows="4" placeholder="用自己的话整理事实、推论与待核实之处" aria-describedby="viewpoint-thesis-error">${esc(view?.thesis || "")}</textarea><p id="viewpoint-thesis-error" class="field-error" hidden></p></div><div class="field"><label for="viewpoint-invalidation">哪些情况会使观点失效<span class="required">*</span></label><textarea id="viewpoint-invalidation" class="textarea" maxlength="2000" rows="3" placeholder="写下可以复核的具体条件" aria-describedby="viewpoint-invalidation-error">${esc(view?.invalidation || "")}</textarea><p id="viewpoint-invalidation-error" class="field-error" hidden></p></div><div class="field"><label for="viewpoint-review">下次手动复核</label><input id="viewpoint-review" type="date" class="input" value="${esc(view?.review || "")}"></div><label class="check-label"><input id="viewpoint-confirm" type="checkbox" ${view ? "checked" : ""}><span>这是我本人整理并认可的个人观点。</span></label><p id="viewpoint-confirm-error" class="field-error" hidden></p><div><button class="button" type="button" data-action="save-viewpoint" data-id="${esc(run.id)}" ${run.status !== "completed" ? "disabled" : ""}>${icon("check")}保存我的观点</button></div></form></div></section><aside class="stack"><section class="panel"><header class="panel-head"><h2>关联决策计划</h2></header><div class="panel-body">${plan ? `<h3>${esc(plan.title)}</h3><p class="muted" style="font-size:14px;margin-block:12px">${esc(plan.business)}</p>${button("查看并完善计划", "edit-plan", { attributes: `data-id="${esc(plan.id)}"`, glyph: "arrow" })}` : `<p class="muted" style="font-size:14px;margin-bottom:20px">将研究转化为自己的观察假设、风险边界与复核计划。</p>${button("建立决策计划", "plan-from-run", { attributes: `data-id="${esc(run.id)}"`, glyph: "plus" })}`}</div></section><section class="panel"><header class="panel-head"><h2>本次个人纪律</h2></header><div class="panel-body"><p class="muted" style="font-size:14px">${run.policy ? esc(run.policySnapshot?.objective || "请查看本次研究冻结的体系快照。") : "本次未附加个人投资体系。"}</p><p class="quiet" style="font-size:12px;margin-top:16px">确认观点与核验数据分别记录。个人确认不会改变研究资料的资格。</p></div></section></aside></div>`;
}

function planProgress(plan) {
  return [
    plan.cycle,
    plan.business,
    plan.moat,
    plan.valuation,
    plan.invalidation,
    plan.position,
  ].filter((value) => String(value ?? "").trim()).length;
}

function newPlanDraft(symbol = ui.symbol, runId = "") {
  return {
    id: null,
    symbol,
    title: "",
    status: "draft",
    business: "",
    cycle: "",
    moat: "",
    valuation: "",
    invalidation: "",
    review: "",
    reviewed: "",
    reviewNotes: "",
    position: "",
    referencePrice: "",
    fairValue: "",
    scenarios: [
      { price: "", probability: "" },
      { price: "", probability: "" },
      { price: "", probability: "" },
    ],
    revision: 0,
    runId,
  };
}
function openPlan(planId, review = false, symbol = ui.symbol, runId = "") {
  const plan = state.plans.find((item) => item.id === planId);
  ui.planDraft = JSON.parse(JSON.stringify(plan || newPlanDraft(symbol, runId)));
  ui.planId = plan?.id || null;
  ui.planStep = review ? "review" : "company";
  navigate("planEditor");
}
function planEditorView() {
  const draft = ui.planDraft || (ui.planDraft = newPlanDraft());
  const step = ui.planStep;
  let content;
  if (step === "company")
    content = `<div class="form-grid"><div class="field"><label for="plan-symbol">研究证券</label><input id="plan-symbol" class="input mono" data-plan-field="symbol" value="${esc(draft.symbol)}" list="plan-securities" maxlength="20" autocomplete="off" placeholder="输入证券代码，例如 NVDA"><datalist id="plan-securities">${securities
      .filter((stock) => stock.type === "stock")
      .map((stock) => `<option value="${esc(stock.symbol)}">${esc(stock.name)}</option>`)
      .join(
        "",
      )}</datalist></div><div class="field"><label for="plan-title">计划名称<span class="required">*</span></label><input id="plan-title" class="input" data-plan-field="title" maxlength="80" value="${esc(draft.title)}" placeholder="为这份计划命名"><p id="plan-title-error" class="field-error" hidden></p></div><div class="field form-span"><label for="plan-cycle">01 · 所处的行业周期如何？</label><textarea id="plan-cycle" class="textarea" data-plan-field="cycle" rows="3" maxlength="2000" placeholder="记录需求、供给和资本开支的观察">${esc(draft.cycle)}</textarea></div><div class="field form-span"><label for="plan-business">02 · 公司靠什么赚钱？</label><textarea id="plan-business" class="textarea" data-plan-field="business" rows="3" maxlength="2000" placeholder="客户、产品、收入与现金流从哪里来">${esc(draft.business)}</textarea></div><div class="field form-span"><label for="plan-moat">03 · 护城河与经营质量是什么？</label><textarea id="plan-moat" class="textarea" data-plan-field="moat" rows="3" maxlength="2000" placeholder="区分已证实的优势与待核实的假设">${esc(draft.moat)}</textarea></div></div>`;
  else if (step === "valuation")
    content = `<div class="field"><label for="plan-valuation">04 · 质量与估值如何匹配？</label><textarea id="plan-valuation" class="textarea" data-plan-field="valuation" rows="3" maxlength="2000" placeholder="哪些增长与现金流假设已经反映在价格中">${esc(draft.valuation)}</textarea></div><div class="form-grid"><div class="field"><label for="plan-reference">参考价格 · USD</label><input type="number" id="plan-reference" class="input" data-plan-field="referencePrice" min="0.01" step="0.01" value="${esc(draft.referencePrice)}" placeholder="输入自己的比较基准"><p id="plan-reference-error" class="field-error" hidden></p></div><div class="field"><label for="plan-fair">本人估值假设 · USD</label><input type="number" id="plan-fair" class="input" data-plan-field="fairValue" min="0.01" step="0.01" value="${esc(draft.fairValue)}" placeholder="保持未知或手动填写"><p id="plan-fair-error" class="field-error" hidden></p></div></div><h3>手填三种情景</h3><div class="scenario-grid">${["悲观情景", "基准情景", "乐观情景"].map((label, i) => `<div class="scenario-box"><h3>${label}</h3><div class="field"><label for="scenario-price-${i}">情景价格 · USD</label><input id="scenario-price-${i}" class="input" type="number" min="0" step="0.01" data-scenario="${i}" data-scenario-field="price" value="${esc(draft.scenarios[i].price)}" placeholder="待填写"></div><div class="field"><label for="scenario-probability-${i}">本人概率 · %</label><input id="scenario-probability-${i}" class="input" type="number" min="0" max="100" step="0.1" data-scenario="${i}" data-scenario-field="probability" value="${esc(draft.scenarios[i].probability)}" placeholder="待填写"></div></div>`).join("")}</div><div id="scenario-results">${scenarioResults(draft)}</div><p class="field-help">概率完整且合计为 100% 后才计算毛期望收益。费用和税务未纳入。</p>`;
  else if (step === "risk")
    content = `<div class="field"><label for="plan-invalidation">05 · 哪些情况会让判断失效？</label><textarea id="plan-invalidation" class="textarea" data-plan-field="invalidation" rows="5" maxlength="2000" placeholder="写明具体条件、对应证据与需要采取的复核动作">${esc(draft.invalidation)}</textarea></div><div class="field"><label for="plan-position">06 · 计划单股仓位 · %</label><input id="plan-position" class="input" type="number" data-plan-field="position" min="0" max="100" step="0.1" value="${esc(draft.position)}" placeholder="由本人决定，留空保持未知"><p id="plan-position-error" class="field-error" hidden></p></div><div id="risk-result">${riskResult(draft)}</div><div class="note-box">情景输入用于检视自己的假设。仓位、下行风险与生活现金边界需要分别考虑。</div>`;
  else
    content = `<div class="form-grid"><div class="field"><label for="plan-review">下次手动复核日期</label><input id="plan-review" type="date" class="input" data-plan-field="review" value="${esc(draft.review)}"></div><div class="field"><label for="plan-edit-status">计划状态</label><select id="plan-edit-status" class="select" data-plan-field="status"><option value="draft" ${draft.status === "draft" ? "selected" : ""}>草稿</option><option value="watching" ${draft.status === "watching" ? "selected" : ""}>观察中</option><option value="archived" ${draft.status === "archived" ? "selected" : ""}>已归档</option></select></div></div><div class="field"><label for="plan-review-notes">复盘记录</label><textarea id="plan-review-notes" class="textarea" data-plan-field="reviewNotes" rows="7" maxlength="3000" placeholder="原来的假设是否成立？出现了什么新证据？下一步需要修改什么？">${esc(draft.reviewNotes)}</textarea></div><label class="check-label"><input id="plan-mark-reviewed" type="checkbox" ${draft.markReviewed ? "checked" : ""} data-plan-field="markReviewed"><span class="od-field"><strong>本次已完成手动复盘</strong><small>保存时记录工作区日期 ${TODAY}。请同时填写复盘内容。</small></span></label><p id="plan-review-notes-error" class="field-error" hidden></p>${draft.reviewed ? `<div class="note-box">上次手动复盘：${esc(draft.reviewed)}</div>` : ""}`;
  return `<div class="page">${heading(draft.id ? `${draft.symbol} 决策计划` : "新的决策计划", "记录自己的判断，并为它留下可以复核的条件。", button("返回计划", "leave-plan", { glyph: "back", ghost: true }), "PERSONAL DECISION / 计划编辑")}
    <div class="editor-layout"><nav class="editor-nav" aria-label="计划编辑章节">${[
      ["company", "公司与周期"],
      ["valuation", "估值与情景"],
      ["risk", "仓位与风险"],
      ["review", "纪律与复盘"],
    ]
      .map(
        ([key, label], i) =>
          `<button type="button" class="${step === key ? "active" : ""}" data-action="plan-step" data-step="${key}"><span>${String(i + 1).padStart(2, "0")}</span>${label}</button>`,
      )
      .join(
        "",
      )}<div class="note-box" style="font-size:12px;margin-top:16px"><div class="od-field"><strong>六问记录进度</strong><span id="plan-progress" class="mono" style="font-size:24px;margin-top:8px">${planProgress(draft)} / 6</span><small style="margin-top:8px">记录完整度与判断正确性分别看待。</small></div></div></nav><section class="panel"><header class="panel-head"><h2>${{ company: "公司与周期", valuation: "估值与情景", risk: "仓位与风险", review: "纪律与复盘" }[step]}</h2><small>${draft.id ? `计划 v${draft.revision}` : "尚未保存"}</small></header><form id="plan-form" novalidate><div class="panel-body editor-content">${content}</div><footer class="wizard-footer"><div>${draft.id ? button("归档计划", "archive-plan", { ghost: true, attributes: `data-id="${esc(draft.id)}"` }) : '<span class="quiet" style="font-size:12px">可先保存草稿，再逐步完善</span>'}</div>${button("保存计划", "save-plan", { primary: true, glyph: "check" })}</footer></form></section></div>
  </div>`;
}
function parseOptional(value) {
  if (value === "" || value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function scenarioResults(draft) {
  const ref = parseOptional(draft.referencePrice),
    fair = parseOptional(draft.fairValue);
  const scenarios = draft.scenarios.map((item) => ({
    price: parseOptional(item.price),
    probability: parseOptional(item.probability),
  }));
  const allProbabilities = scenarios.every(
    (item) => item.probability !== null && item.probability >= 0 && item.probability <= 100,
  );
  const probabilitySum = allProbabilities
    ? scenarios.reduce((total, item) => total + item.probability, 0)
    : null;
  const valid =
    ref !== null &&
    ref > 0 &&
    scenarios.every((item) => item.price !== null && item.price >= 0) &&
    allProbabilities &&
    Math.abs(probabilitySum - 100) < 0.001;
  const expected = valid
    ? (scenarios.reduce((total, item) => total + (item.price * item.probability) / 100, 0) / ref -
        1) *
      100
    : null;
  const margin =
    ref !== null && ref > 0 && fair !== null && fair > 0 ? (1 - ref / fair) * 100 : null;
  return `<div class="scenario-result"><div class="od-stat"><small>安全边际 · 本人假设</small><strong>${margin === null ? "未知" : `${signed(margin)}%`}</strong></div><div class="od-stat"><small>毛期望收益 · 本人假设</small><strong>${expected === null ? "未知" : `${signed(expected)}%`}</strong></div><div class="od-stat"><small>情景概率合计</small><strong class="${probabilitySum !== null && Math.abs(probabilitySum - 100) >= 0.001 ? "warning" : ""}">${probabilitySum === null ? "待补齐" : `${Number(probabilitySum.toFixed(2))}%`}</strong></div></div>`;
}
function riskResult(draft) {
  const position = parseOptional(draft.position),
    limit = parseOptional(state.policy.maxPosition);
  if (position === null || limit === null)
    return '<div class="note-box">仓位或个人上限尚未填写，边界检查保持未知。</div>';
  if (position > limit)
    return `<div class="note-box amber">计划仓位 ${position}% 超过你设定的 ${limit}% 上限，请复核。</div>`;
  return `<div class="note-box">计划仓位 ${position}% 在你设定的 ${limit}% 上限以内。此项只比较本人输入的比例。</div>`;
}

function judgmentCell(run) {
  return `<div class="od-field">${badge(esc(run.rating || "无有效判断"), run.rating ? "mint" : "amber")}<small class="quiet">原始：${esc(run.rawRating || "未提供")}</small><small class="quiet">数据资格：${esc(run.quality?.status || "未提供")}</small></div>`;
}
function filteredRuns() {
  return state.runs.filter(
    (run) =>
      (ui.libraryStatus === "all" || run.status === ui.libraryStatus) &&
      `${run.symbol} ${esc(security(run.symbol).name)} ${run.objective} ${run.date}`
        .toLowerCase()
        .includes(ui.libraryQuery.trim().toLowerCase()),
  );
}
function libraryTable() {
  const all = filteredRuns();
  const page = Math.max(1, Math.min(ui.libraryPage || 1, Math.max(1, Math.ceil(all.length / 15))));
  ui.libraryPage = page;
  const rows = all.slice((page - 1) * 15, page * 15);
  return `<section class="panel">${rows.length ? `<table class="data-table responsive-table"><thead><tr><th>研究标的 / 问题</th><th>研究日期</th><th>执行方式</th><th>任务状态</th><th>判断与资格</th><th>对照</th></tr></thead><tbody>${rows.map((run) => `<tr><td><button type="button" class="table-link od-field" data-action="open-run" data-id="${esc(run.id)}"><strong class="mono">${run.symbol} <span class="quiet" style="font:12px var(--font)">${esc(security(run.symbol).name)}</span></strong><small class="od-clamp-2">${esc(run.objective)}</small></button></td><td class="mono" data-label="研究日期">${esc(run.date)}</td><td class="mobile-extra muted">${run.mode === "local" ? "本地 Agent" : "内置 12 角色"}</td><td>${badge(runStatus(run), run.status === "completed" ? "mint" : "")}</td><td class="mobile-extra">${judgmentCell(run)}</td><td class="row-end"><label class="check-label" style="align-items:center;min-height:32px"><input type="checkbox" data-compare-id="${esc(run.id)}" ${ui.compare.includes(run.id) ? "checked" : ""} ${run.status !== "completed" ? "disabled" : ""} aria-label="选择 ${run.symbol} ${run.date} 研究进行对照"><span style="font-size:12px">对照</span></label></td></tr>`).join("")}</tbody></table><footer class="panel-foot od-row" style="justify-content:space-between"><span>${all.length} 项研究记录 · 第 ${page} / ${Math.max(1, Math.ceil(all.length / 15))} 页</span><div class="od-row"><button type="button" class="button small ghost" data-action="library-page" data-page="${page - 1}" ${page === 1 ? "disabled" : ""}>上一页</button><button type="button" class="button small ghost" data-action="library-page" data-page="${page + 1}" ${page * 15 >= all.length ? "disabled" : ""}>下一页</button></div></footer>` : emptyState("没有匹配的研究", "清除筛选或创建一次新的研究。", button("清除筛选", "clear-library"))}</section>`;
}
function libraryView() {
  return `<div class="page">${heading("研究库", "保留每一次研究的依据，也保留判断的变化。", button("新建研究", "new-research", { primary: true, glyph: "plus" }), "YOUR RESEARCH LIBRARY")}
    <div class="filterbar"><div class="input-search compact">${icon("search")}<label for="library-search" class="sr-only">搜索证券、问题或日期</label><input id="library-search" class="input" placeholder="搜索证券、问题或日期" value="${esc(ui.libraryQuery)}"></div><div class="od-cluster"><label class="sr-only" for="library-status">筛选研究状态</label><select id="library-status" class="select" style="width:144px;min-height:40px">${[
      ["all", "全部状态"],
      ["pending", "等待执行"],
      ["queued", "排队中"],
      ["running", "研究中"],
      ["completed", "已完成"],
      ["cancelling", "取消中"],
      ["cancelled", "已取消"],
      ["failed", "失败"],
      ["interrupted", "中断"],
    ]
      .map(
        ([key, label]) =>
          `<option value="${key}" ${ui.libraryStatus === key ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select><button id="compare-button" class="button" type="button" data-action="compare-runs" ${ui.compare.length !== 2 ? "disabled" : ""}>${icon("compare")}对照研究 <span id="compare-count">${ui.compare.length}/2</span></button></div></div>
    <div id="library-results">${libraryTable()}</div><div class="note-box">选择两份同股已完成研究，查看日期、执行方式、模型配置与问题的差异。对照不代表固定输入下的模型评测。</div>
  </div>`;
}

function settingsView() {
  const tab = "preferences";
  const body = `<section class="panel"><header class="panel-head"><h2>显示与使用偏好</h2></header><div class="panel-body"><div class="settings-row"><div class="od-field"><strong>信息密度</strong><small>调整区块间距，保持正文字号</small></div><label class="sr-only" for="preference-density">信息密度</label><select id="preference-density" class="select"><option value="standard" ${state.preferences.density === "standard" ? "selected" : ""}>标准</option><option value="comfortable" ${state.preferences.density === "comfortable" ? "selected" : ""}>宽松</option></select></div><div class="settings-row"><div class="od-field"><strong>涨跌颜色</strong><small>颜色同时配合正负号表达</small></div><label class="sr-only" for="preference-color">涨跌颜色</label><select id="preference-color" class="select"><option value="green" ${state.preferences.riseColor === "green" ? "selected" : ""}>绿涨红跌</option><option value="red" ${state.preferences.riseColor === "red" ? "selected" : ""}>红涨绿跌</option></select></div><div class="settings-row"><div class="od-field"><strong>默认首页</strong><small>浏览器允许存储时，下次打开生效</small></div><label class="sr-only" for="preference-home">默认首页</label><select id="preference-home" class="select">${navItems.map(([key, label]) => `<option value="${key}" ${state.preferences.defaultPage === key ? "selected" : ""}>${label}</option>`).join("")}</select></div></div></section>`;
  return `<div class="page">${heading("设置中心", "让连接与配置各就其位，研究时专注于问题。", "", "WORKSPACE SETTINGS")}<div class="settings-layout"><nav class="settings-side" aria-label="设置分类">${[
    ["models", "模型与研究"],
    ["runtimes", "电脑与运行环境"],
    ["data", "数据来源"],
    ["preferences", "使用偏好"],
    ["account", "个人账号"],
  ]
    .map(
      ([key, label]) =>
        `<button type="button" class="${tab === key ? "active" : ""}" data-action="settings-tab" data-tab="${key}">${label}</button>`,
    )
    .join("")}</nav>${body}</div></div>`;
}

function showDialog(title, body, footer = "", options = {}) {
  if (dialog.open) closeDialog(false);
  dialogReturn = {
    element: document.activeElement,
    key: document.activeElement?.dataset?.focusKey || "",
  };
  dialog.classList.toggle("drawer", Boolean(options.drawer));
  document.getElementById("dialog-title").textContent = title;
  document.getElementById("dialog-body").innerHTML = body;
  const footerNode = document.getElementById("dialog-footer");
  footerNode.innerHTML = footer;
  footerNode.hidden = !footer;
  hydrateIcons(dialog);
  dialog.showModal();
  const firstInput = dialog.querySelector('input:not([type="checkbox"]), textarea, select');
  if (firstInput) firstInput.focus({ preventScroll: true });
  else dialog.querySelector("button")?.focus({ preventScroll: true });
}
function closeDialog(restore = true) {
  const returnTo = dialogReturn;
  if (dialog.open) dialog.close();
  if (restore) {
    if (returnTo?.element?.isConnected) returnTo.element.focus({ preventScroll: true });
    else restoreFocus(returnTo?.key || "");
  }
}

function showSearch() {
  showDialog(
    "搜索工作区",
    `<div class="field"><label for="global-query">证券、研究或计划</label><div class="input-search">${icon("search")}<input id="global-query" class="input" autocomplete="off" placeholder="搜索关注证券、研究问题或计划"></div></div><div id="global-results">${searchResults("")}</div>`,
    "",
    { mode: "search" },
  );
}
function searchResults(query) {
  const needle = query.trim().toLowerCase();
  const stocks = securities.filter((stock) => matchesStock(stock, needle)).slice(0, 5);
  const runs = state.runs
    .filter((run) =>
      `${run.symbol} ${esc(security(run.symbol).name)} ${run.objective}`
        .toLowerCase()
        .includes(needle),
    )
    .slice(0, 3);
  const plans = state.plans
    .filter(
      (plan) =>
        plan.status !== "archived" &&
        `${plan.symbol} ${plan.title} ${plan.business}`.toLowerCase().includes(needle),
    )
    .slice(0, 3);
  return `${stocks.length ? `<p class="dialog-section-label">证券</p>${stocks.map((stock) => `<button type="button" class="search-result" data-action="search-stock" data-symbol="${stock.symbol}"><span class="od-row">${tickerTile(stock, true)}<span class="od-field"><strong>${stock.symbol} · ${esc(stock.name)}</strong><small>${stock.type === "etf" ? "ETF" : "公司股票"} · ${esc(stock.en)}</small></span></span>${icon("arrow")}</button>`).join("")}` : ""}${runs.length ? `<p class="dialog-section-label" style="margin-top:20px">研究记录</p>${runs.map((run) => `<button type="button" class="search-result" data-action="search-run" data-id="${esc(run.id)}"><span class="od-field"><strong>${run.symbol} · ${esc(run.objective)}</strong><small>${run.date} · ${runStatus(run)}</small></span>${icon("arrow")}</button>`).join("")}` : ""}${plans.length ? `<p class="dialog-section-label" style="margin-top:20px">决策计划</p>${plans.map((plan) => `<button type="button" class="search-result" data-action="search-plan" data-id="${esc(plan.id)}"><span class="od-field"><strong>${plan.symbol} · ${esc(plan.title)}</strong><small>计划 v${plan.revision} · ${plan.status === "draft" ? "草稿" : "观察中"}</small></span>${icon("arrow")}</button>`).join("")}` : ""}${!stocks.length && !runs.length && !plans.length ? emptyState("没有找到匹配内容", "试试股票代码、中文公司名称或研究问题中的关键词。") : ""}`;
}

function setFieldError(id, message = "") {
  const element = document.getElementById(id);
  const feedback = document.getElementById(`${id}-error`);
  if (element) {
    if (message) element.setAttribute("aria-invalid", "true");
    else element.removeAttribute("aria-invalid");
  }
  if (feedback) {
    feedback.textContent = message;
    feedback.hidden = !message;
  }
}
function checkDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
  const date = new Date(`${value}T12:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
function researchErrors(step) {
  const draft = ui.researchDraft;
  const errors = [];
  if (step === 1) {
    if (!/^[A-Za-z0-9][A-Za-z0-9.=_-]{0,31}$/.test(draft.symbol || ""))
      errors.push(["research-symbol", "请输入有效的公司证券代码。"]);
    if (!draft.objective.trim())
      errors.push(["research-objective", "请写下至少一个希望验证的问题。"]);
    if (!checkDate(draft.date) || draft.date < "1970-01-01" || draft.date > TODAY)
      errors.push(["research-date", `请选择 1970-01-01 至 ${TODAY} 之间的有效日期。`]);
  }
  if (step === 2 && draft.mode === "builtin") {
    if (
      !["analystMarket", "analystFundamentals", "analystNews", "analystSocial"].some(
        (key) => draft[key],
      )
    )
      errors.push(["research-analysts", "请至少选择一类分析师。"]);
    if (!/^\d+$/.test(draft.maxCalls) || Number(draft.maxCalls) < 1 || Number(draft.maxCalls) > 500)
      errors.push(["research-maxcalls", "模型请求上限需为 1–500 的整数。"]);
    if (
      !/^\d+$/.test(draft.maxSeconds) ||
      Number(draft.maxSeconds) < 60 ||
      Number(draft.maxSeconds) > 14400
    )
      errors.push(["research-maxseconds", "执行时长需为 60–14400 秒的整数。"]);
  }
  return errors;
}
function displayResearchErrors(errors) {
  for (const id of [
    "research-symbol",
    "research-objective",
    "research-date",
    "research-analysts",
    "research-maxcalls",
    "research-maxseconds",
  ])
    setFieldError(id);
  const summary = document.getElementById("research-error-summary");
  if (summary) {
    summary.hidden = !errors.length;
    summary.innerHTML = errors.length
      ? `<strong style="display:block;font-size:14px;margin-bottom:8px">请补充以下内容</strong>${errors.map(([id, message]) => `<a href="#${id}" class="validation-jump" data-field-id="${id}" style="display:block;font-size:13px;padding:4px 0">${esc(message)}</a>`).join("")}`
      : "";
  }
  for (const [id, message] of errors) setFieldError(id, message);
  if (errors.length) {
    const target = document.getElementById(errors[0][0]);
    target?.closest("details")?.setAttribute("open", "");
    if (target) target.focus();
    else summary?.scrollIntoView({ block: "center", behavior: "instant" });
  }
  return !errors.length;
}
function goResearchStep(step) {
  if (step > ui.researchStep && !displayResearchErrors(researchErrors(ui.researchStep))) return;
  if (step === 3) {
    const issueOne = researchErrors(1);
    if (issueOne.length) {
      ui.researchStep = 1;
      render();
      displayResearchErrors(issueOne);
      return;
    }
    const issueTwo = researchErrors(2);
    if (issueTwo.length) {
      ui.researchStep = 2;
      render();
      displayResearchErrors(issueTwo);
      return;
    }
  }
  ui.researchStep = Math.max(1, Math.min(3, step));
  render();
  main.focus({ preventScroll: true });
}

function beginResearch(symbol = ui.symbol, overrides = {}) {
  if (dialog.open) closeDialog(false);
  ui.researchDraft = { ...makeResearchDraft(symbol), ...overrides };
  ui.researchStep = 1;
  navigate("researchNew");
}
function openRun(id) {
  const run = state.runs.find((item) => item.id === id);
  if (!run) {
    toast("这项研究记录已不存在");
    return;
  }
  if (dialog.open) closeDialog(false);
  navigate("researchDetail", { runId: run.id, symbol: run.symbol, reportTab: null });
}
function openStock(symbol) {
  if (dialog.open) closeDialog(false);
  navigate("watch", { symbol: security(symbol).symbol, watchMode: "workspace" });
}

document.addEventListener("click", (event) => {
  void handleClick(event).catch((error) => toast(error.message || "操作失败，请重试"));
});
async function handleClick(event) {
  const validationJump = event.target.closest?.(".validation-jump");
  if (validationJump) {
    event.preventDefault();
    const node = document.getElementById(validationJump.dataset.fieldId);
    node?.closest("details")?.setAttribute("open", "");
    node?.focus();
    return;
  }
  const control = event.target.closest?.("[data-action]");
  if (!control || control.disabled) return;
  const data = control.dataset;
  const action = data.action;
  if (actionOverrides.has(action)) {
    await performAction(action, data, control);
    return;
  }
  if (!safeActions.has(action)) {
    toast("请在完整配置中完成此操作");
    return;
  }
  switch (action) {
    case "navigate":
      navigate(data.page);
      break;
    case "back":
      goBack();
      break;
    case "search":
      showSearch();
      break;
    case "close-dialog":
      closeDialog();
      break;
    case "account":
      navigate("settings", { settingsTab: "account" });
      break;

    case "open-stock":
    case "search-stock":
      openStock(data.symbol);
      break;
    case "select-stock":
      ui.symbol = data.symbol;
      ui.watchMode = "workspace";
      render();
      main.focus({ preventScroll: true });
      break;

    case "watch-group":
      ui.group = data.group;
      render();
      break;
    case "favorites-only":
      ui.favoritesOnly = !ui.favoritesOnly;
      render();
      break;
    case "watch-mode":
      ui.watchMode = data.mode;
      render();
      break;
    case "clear-watch":
      ui.watchQuery = "";
      ui.favoritesOnly = false;
      ui.group = "全部";
      render();
      break;

    case "chart-period":
      ui.period = data.period;
      render();
      break;
    case "chart-interval":
      ui.interval = data.interval;
      render();
      break;
    case "chart-type":
      ui.chartType = ui.chartType === "candles" ? "line" : "candles";
      render();
      break;
    case "toggle-ma":
      ui.movingAverage = !ui.movingAverage;
      render();
      break;
    case "new-research":
      if (ui.researchDraft) navigate("researchNew");
      else beginResearch(data.symbol || ui.symbol);
      break;
    case "dialog-new-research":
      closeDialog(false);
      beginResearch();
      break;
    case "template-research":
      beginResearch(ui.symbol, { depth: data.depth });
      break;
    case "local-research":
      beginResearch(ui.symbol, { mode: "local" });
      break;
    case "research-next":
      goResearchStep(ui.researchStep + 1);
      break;
    case "research-prev":
      goResearchStep(ui.researchStep - 1);
      break;
    case "research-step":
      goResearchStep(Number(data.step));
      break;
    case "research-mode":
      ui.researchDraft.mode = data.mode;
      render();
      break;
    case "research-depth":
      ui.researchDraft.depth = data.depth;
      render();
      break;

    case "open-run":
    case "search-run":
      openRun(data.id);
      break;

    case "rerun": {
      const run = state.runs.find((item) => item.id === data.id);
      if (run) {
        beginResearch(run.symbol, {
          objective: run.objective,
          date: run.date,
          mode: run.mode,
          depth: run.depth,
          policy: run.policy,
          context: [...(run.sources || [])],
          planId: run.planId || "",
        });
        toast("已预填原研究，请核对参数后再创建");
      }
      break;
    }
    case "report-tab":
      ui.reportTab = data.tab;
      render();
      main.focus({ preventScroll: true });
      break;

    case "plan-from-run": {
      const run = state.runs.find((item) => item.id === data.id);
      if (!run) break;
      const existing = state.plans.find(
        (plan) => plan.runId === run.id && plan.status !== "archived",
      );
      openPlan(existing?.id, false, run.symbol, run.id);
      break;
    }

    case "copy-export": {
      const text = document.getElementById("export-text").value;
      try {
        await navigator.clipboard.writeText(text);
        toast("报告已复制");
      } catch {
        const area = document.getElementById("export-text");
        area.focus();
        area.select();
        toast("内容已选中，可按 ⌘C 或 Ctrl+C 复制");
      }
      break;
    }

    case "investment-tab":
      ui.investmentTab = data.tab;
      ui.planStatus = "all";
      render();
      break;
    case "policy":
      navigate("investment", { investmentTab: "policy" });
      break;
    case "new-plan":
      openPlan(null, false, data.symbol || ui.symbol);
      break;
    case "edit-plan":
      openPlan(data.id);
      break;
    case "search-plan":
      closeDialog(false);
      openPlan(data.id);
      break;
    case "review-plan":
      openPlan(data.id, true);
      break;
    case "plan-step":
      ui.planStep = data.step;
      render();
      main.focus({ preventScroll: true });
      break;

    case "leave-plan": {
      const original = state.plans.find((plan) => plan.id === ui.planId);
      if (
        JSON.stringify(ui.planDraft) !==
        JSON.stringify(original || newPlanDraft(ui.planDraft.symbol, ui.planDraft.runId))
      )
        showDialog(
          "保留当前计划修改？",
          '<p class="muted">当前修改尚未保存。你可以保存后返回，也可以放弃本次编辑。</p>',
          `${button("继续编辑", "close-dialog", { ghost: true })}${button("放弃修改", "discard-plan")}${button("保存并返回", "dialog-save-plan", { primary: true })}`,
        );
      else navigate("investment", { investmentTab: "plans" });
      break;
    }
    case "discard-plan":
      closeDialog(false);
      ui.planDraft = null;
      navigate("investment", { investmentTab: "plans" });
      break;

    case "market-tab":
      ui.marketTab = data.tab;
      render();
      break;
    case "market-window":
      ui.marketWindow = data.window;
      render();
      break;
    case "select-theme":
      ui.theme = data.theme;
      render();
      break;
    case "professional-tab":
      ui.professionalTab = data.tab;
      render();
      break;

    case "settings-data":
      navigate("settings", { settingsTab: "data" });
      break;
    case "settings-tab":
      ui.settingsTab = data.tab;
      render();
      break;

    case "clear-library":
      ui.libraryQuery = "";
      ui.libraryStatus = "all";
      ui.libraryPage = 1;
      render();
      break;
    case "library-page":
      ui.libraryPage = Number(data.page);
      render();
      break;
  }
}

function updateResearchSummary() {
  const container = document.getElementById("research-summary");
  if (container && ui.researchDraft) container.innerHTML = researchSummary();
}

function updatePlanCalculations() {
  if (!ui.planDraft) return;
  const progress = document.getElementById("plan-progress");
  if (progress) progress.textContent = `${planProgress(ui.planDraft)} / 6`;
  const result = document.getElementById("scenario-results");
  if (result) result.innerHTML = scenarioResults(ui.planDraft);
  const risk = document.getElementById("risk-result");
  if (risk) risk.innerHTML = riskResult(ui.planDraft);
}
document.addEventListener("input", (event) => {
  const field = event.target;
  if (
    !(
      field instanceof HTMLInputElement ||
      field instanceof HTMLTextAreaElement ||
      field instanceof HTMLSelectElement
    )
  )
    return;
  if (field.id === "global-query") {
    document.getElementById("global-results").innerHTML = searchResults(field.value);
    return;
  }

  if (field.id === "library-search") {
    ui.libraryQuery = field.value;
    ui.libraryPage = 1;
    document.getElementById("library-results").innerHTML = libraryTable();
    return;
  }
  if (field.id === "stock-note") {
    ui.noteDrafts[ui.symbol] = field.value;
    return;
  }
  if (field.dataset.researchField && ui.researchDraft) {
    ui.researchDraft[field.dataset.researchField] =
      field.type === "checkbox" ? field.checked : field.value;
    if (field.dataset.researchField === "symbol") {
      const plan = state.plans.find((item) => item.id === ui.researchDraft.planId);
      if (plan && plan.symbol !== field.value) ui.researchDraft.planId = "";
    }
    updateResearchSummary();
    return;
  }
  if (field.dataset.planField && ui.planDraft) {
    const key = field.dataset.planField;
    ui.planDraft[key] = field.type === "checkbox" ? field.checked : field.value;
    if (key === "symbol") {
      const run = state.runs.find((item) => item.id === ui.planDraft.runId);
      if (run && run.symbol !== ui.planDraft.symbol) ui.planDraft.runId = "";
    }
    updatePlanCalculations();
    return;
  }
  if (field.dataset.scenario !== undefined && ui.planDraft) {
    const index = Number(field.dataset.scenario);
    if (ui.planDraft.scenarios[index])
      ui.planDraft.scenarios[index][field.dataset.scenarioField] = field.value;
    updatePlanCalculations();
  }
});
document.addEventListener("change", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) return;
  if (field.id === "mobile-stock") {
    ui.symbol = field.value;
    render();
    return;
  }

  if (field.id === "library-status") {
    ui.libraryStatus = field.value;
    ui.libraryPage = 1;
    document.getElementById("library-results").innerHTML = libraryTable();
    return;
  }
  if (field.dataset.context && ui.researchDraft) {
    const selected = new Set(ui.researchDraft.context);
    if (field.checked) selected.add(field.dataset.context);
    else selected.delete(field.dataset.context);
    ui.researchDraft.context = [...selected];
    updateResearchSummary();
    return;
  }
  if (field.dataset.compareId) {
    const id = field.dataset.compareId;
    const run = state.runs.find((item) => item.id === id);
    if (field.checked) {
      const first = state.runs.find((item) => item.id === ui.compare[0]);
      if (ui.compare.length >= 2) {
        field.checked = false;
        toast("最多选择两份研究进行对照");
      } else if (first && first.symbol !== run?.symbol) {
        field.checked = false;
        toast("请选择同一证券的两份已完成研究");
      } else if (!ui.compare.includes(id)) ui.compare.push(id);
    } else ui.compare = ui.compare.filter((value) => value !== id);
    const compare = document.getElementById("compare-button");
    if (compare) compare.disabled = ui.compare.length !== 2;
    const count = document.getElementById("compare-count");
    if (count) count.textContent = `${ui.compare.length}/2`;
    return;
  }
  const preference = {
    "preference-density": "density",
    "preference-color": "riseColor",
    "preference-home": "defaultPage",
  }[field.id];
  if (preference) {
    state.preferences[preference] = field.value;
    persist();
    render();
    document.getElementById(field.id)?.focus({ preventScroll: true });
    toast("使用偏好已更新");
  }
});
document.addEventListener("focusout", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) return;
  if (field.dataset.researchField && ui.researchDraft) {
    const error = researchErrors(ui.researchStep).find(([id]) => id === field.id);
    setFieldError(field.id, error?.[1] || "");
  }
  if (field.id === "plan-title")
    setFieldError(field.id, field.value.trim() ? "" : "请先为计划填写一个名称。");
});
document.addEventListener("submit", (event) => {
  event.preventDefault();
});
document.addEventListener("keydown", (event) => {
  const field = event.target;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    showSearch();
    return;
  }
  if (event.key !== "Enter" || event.isComposing || event.shiftKey || event.altKey) return;
  if (field instanceof HTMLTextAreaElement) {
    if ((event.metaKey || event.ctrlKey) && field.id === "stock-note") {
      event.preventDefault();
      void performAction("save-note", { symbol: ui.symbol });
    }
    return;
  }
  if (field instanceof HTMLInputElement) {
    if (field.id === "global-query") {
      event.preventDefault();
      document.getElementById("global-results").querySelector("button[data-action]")?.click();
      return;
    }

    if (field.closest("#research-form")) {
      event.preventDefault();
      if (ui.researchStep < 3) goResearchStep(ui.researchStep + 1);
      else void performAction("start-research", {});
      return;
    }
    if (field.closest("#plan-form")) {
      event.preventDefault();
      void performAction("save-plan", {});
      return;
    }
  }
});
dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDialog();
});
dialog.addEventListener("click", (event) => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  )
    closeDialog();
});

const safeActions = new Set([
  "navigate",
  "back",
  "search",
  "close-dialog",
  "account",
  "select-stock",
  "open-stock",
  "search-stock",
  "watch-group",
  "favorites-only",
  "watch-mode",
  "clear-watch",
  "chart-period",
  "chart-interval",
  "chart-type",
  "toggle-ma",
  "new-research",
  "dialog-new-research",
  "template-research",
  "local-research",
  "research-next",
  "research-prev",
  "research-step",
  "research-mode",
  "research-depth",
  "open-run",
  "search-run",
  "rerun",
  "report-tab",
  "plan-from-run",
  "copy-export",
  "investment-tab",
  "policy",
  "new-plan",
  "edit-plan",
  "search-plan",
  "review-plan",
  "plan-step",
  "leave-plan",
  "discard-plan",
  "market-tab",
  "market-window",
  "select-theme",
  "professional-tab",
  "settings-data",
  "settings-tab",
  "clear-library",
  "library-page",
]);
async function performAction(name, data = {}, control) {
  const handler = actionOverrides.get(name);
  if (!handler) return;
  if (control?.dataset.busy) return;
  if (control) {
    control.dataset.busy = "1";
    control.disabled = true;
  }
  try {
    await handler(data, control);
  } catch (error) {
    toast(error instanceof Error ? error.message : "请求失败，请重试");
  } finally {
    if (control?.isConnected) {
      delete control.dataset.busy;
      control.disabled = false;
    }
  }
}

const ctx = {
  get state() {
    return state;
  },
  ui,
  securities,
  esc,
  icon,
  button,
  badge,
  signed,
  money,
  tone,
  emptyState,
  heading,
  tickerTile,
  render,
  toast,
  navigate,
  showDialog,
  closeDialog,
  setFieldError,
  newPlanDraft,
  planProgress,
  scenarioResults,
  riskResult,
  runStatus,
  beginResearch,
  openPlan,
  goResearchStep,
  researchErrors,
  displayResearchErrors,
  get today() {
    return TODAY;
  },
  setToday(value) {
    TODAY = value;
  },
  setStorageKey(value) {
    STORAGE_KEY = value;
    try {
      Object.assign(state.preferences, JSON.parse(localStorage.getItem(value) || "{}"));
    } catch {}
  },
  registerAction(name, handler) {
    actionOverrides.set(name, handler);
  },
  registerView(name, view) {
    viewOverrides.set(name, view);
  },
  registerAfterRender(callback) {
    afterRender.push(callback);
  },
  originalViews: {
    dashboard: dashboardView,
    researchNew: newResearchView,
    reportDecision,
    planEditor: planEditorView,
    library: libraryView,
    settings: settingsView,
    researchSummary,
  },
};
hydrateIcons();
void installLive(ctx).catch((error) => {
  main.innerHTML = `<div class="page">${emptyState("工作台暂时无法显示", esc(error.message), button("重新加载", "reload-workspace"))}</div>`;
});
