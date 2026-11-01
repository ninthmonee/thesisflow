import { installInvestment } from "./live-investment.js";
import { installMarket } from "./live-market.js";

const protocol = "tradingagents-design-v1";
const pending = new Map();
let sequence = 0;

export async function installLive(ctx) {
  const { state, ui, esc, icon, button, badge, heading, emptyState } = ctx;
  const details = new Map();
  const detailLoading = new Set();
  const detailErrors = new Map();
  /** @type {Promise<void> | null} */
  let refreshing = null;
  let loaded = false;
  let routeWriting = false;
  let draftDirty = false;
  let decisionDraft = null;
  let session;
  let lastPath = "";
  let pendingPlanId = "";
  let guardedControl;
  const active = (run) => ["pending", "queued", "running", "cancelling"].includes(run.status);
  const request = (method, payload) =>
    new Promise((resolve, reject) => {
      const id = `${Date.now()}-${++sequence}`;
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error("服务响应超时，请检查连接后重试。"));
      }, 45000);
      pending.set(id, {
        resolve: (result) => {
          if (method === "host.open" && result?.url) {
            ctx.showDialog(
              "工作区配置与详情",
              `<iframe class="live-workspace-frame" title="完整工作区功能" src="${esc(result.url)}"></iframe>`,
              button("完成并返回", "close-dialog"),
            );
            document.getElementById("app-dialog").classList.add("live-workspace-dialog");
          }
          resolve(result);
        },
        reject,
        timeout,
      });
      window.parent.postMessage({ protocol, id, method, payload }, window.location.origin);
    });
  ctx.request = request;

  function readRoute(path) {
    const url = new URL(path || "/", location.origin);
    const parts = url.pathname.split("/").filter(Boolean);
    ui.page =
      {
        stocks: "watch",
        market: "market",
        sectors: "market",
        themes: "market",
        insights: "market",
        investment: "investment",
        reports: "library",
        settings: "settings",
        research: "researchNew",
        runs: "researchDetail",
      }[parts[0]] || "dashboard";
    if (parts[0] === "runs") ui.runId = parts[1] || "";
    if (parts[0] === "stocks" && parts[1]) ui.symbol = decodeURIComponent(parts[1]);
    if (parts[0] === "research") {
      ui.researchDraft = null;
      ui.researchStep = 1;
    }
    if (parts[0] === "investment" && parts[1]) {
      pendingPlanId = parts[1];
      if (loaded) ctx.openPlan(pendingPlanId === "new" ? null : pendingPlanId);
      return;
    }
    if (parts[0] === "sectors") ui.marketTab = "sectors";
    if (parts[0] === "themes") ui.marketTab = "themes";
    if (parts[0] === "insights") {
      ui.marketTab = "professional";
      ui.professionalTab = parts[1] || "flows";
    }
    if (parts[0] === "settings") ui.settingsTab = parts[1] || "models";
    if (parts[0] === "account") {
      ui.page = "settings";
      ui.settingsTab = "account";
    }
    const settings = {
      period: "period",
      interval: "interval",
      chart: "chartType",
      group: "group",
      report: "reportTab",
      tab: ui.page === "market" ? "marketTab" : "investmentTab",
    };
    for (const [key, target] of Object.entries(settings))
      if (url.searchParams.has(key)) ui[target] = url.searchParams.get(key);
  }
  function routePath() {
    const paths = {
      dashboard: "/",
      watch: `/stocks${ui.symbol ? "/" + encodeURIComponent(ui.symbol) : ""}`,
      market: "/market",
      investment: "/investment",
      planEditor: `/investment/${ui.planId || "new"}`,
      library: "/reports",
      settings: `/settings/${ui.settingsTab}`,
      researchNew: "/research/new",
      researchDetail: `/runs/${ui.runId}`,
    };
    const params = new URLSearchParams();
    if (ui.page === "watch") {
      params.set("period", ui.period);
      params.set("interval", ui.interval);
      params.set("chart", ui.chartType);
      if (ui.group !== "全部") params.set("group", ui.group);
    }
    if (ui.page === "market") params.set("tab", ui.marketTab);
    if (ui.page === "investment") params.set("tab", ui.investmentTab);
    if (ui.page === "researchDetail" && ui.reportTab) params.set("report", ui.reportTab);
    const query = params.toString();
    return (paths[ui.page] || "/") + (query ? "?" + query : "");
  }
  window.addEventListener("message", (event) => {
    if (
      event.source !== parent ||
      event.origin !== location.origin ||
      event.data?.protocol !== protocol
    )
      return;
    if (event.data.event === "location") {
      routeWriting = true;
      lastPath = event.data.path;
      readRoute(event.data.path);
      ctx.render();
      routeWriting = false;
      return;
    }
    const item = pending.get(event.data.id);
    if (!item) return;
    pending.delete(event.data.id);
    clearTimeout(item.timeout);
    if (event.data.error) item.reject(new Error(event.data.error));
    else item.resolve(event.data.result);
  });
  window.addEventListener("pagehide", () => {
    for (const item of pending.values()) {
      clearTimeout(item.timeout);
      item.reject(new Error("页面已关闭"));
    }
    pending.clear();
  });

  async function refresh() {
    if (refreshing) {
      await refreshing;
      return refresh();
    }
    let finish;
    refreshing = new Promise((resolve) => {
      finish = resolve;
    });
    try {
      const names = ["research.load", "investment.load", "market.load"];
      const results = await Promise.allSettled(names.map((name) => request(name)));
      const failures = [];
      results.forEach((result, index) => {
        if (result.status === "rejected") {
          failures.push(`${["研究记录", "投资决策", "自选行情"][index]}：${result.reason.message}`);
          return;
        }
        const data = result.value;
        if (data.today) ctx.setToday(data.today);
        if (data.securities) ctx.securities.splice(0, ctx.securities.length, ...data.securities);
        Object.assign(
          state,
          Object.fromEntries(
            Object.entries(data).filter(([key]) => !["securities", "today"].includes(key)),
          ),
        );
        if (index === 2) state.marketWarning = data.warning || null;
      });
      for (const record of [...state.runs, ...state.plans])
        if (!ctx.securities.some((stock) => stock.symbol === record.symbol))
          ctx.securities.push({
            symbol: record.symbol,
            name: record.symbol,
            en: "",
            type: "stock",
            tile: "",
            group: "研究标的",
            price: null,
            change: null,
            theme: "",
          });
      if (!ui.symbol) ui.symbol = state.watchlist[0] || ctx.securities[0]?.symbol || "";
      state.loadErrors = failures;
      loaded = true;
      if (pendingPlanId) {
        const id = pendingPlanId;
        pendingPlanId = "";
        ctx.openPlan(id === "new" ? null : id);
      }
      if (session) state.preferences.displayName = session.user.displayName;
      ctx.render();
    } finally {
      refreshing = null;
      finish();
    }
  }
  ctx.refresh = refresh;
  ctx.onSaved = () => {
    draftDirty = false;
    decisionDraft = null;
  };
  installInvestment(ctx);
  installMarket(ctx);

  ctx.registerView("dashboard", () => ctx.originalViews.dashboard());
  ctx.registerView(
    "library",
    () =>
      ctx.originalViews.library() +
      (state.nextCursor
        ? `<div class="page-actions" style="margin-top:20px">${button("加载后续研究", "more-research", { glyph: "plus" })}</div>`
        : ""),
  );
  ctx.registerView("researchNew", () => {
    const ready =
      ui.researchDraft?.mode === "local" ? state.readiness?.local : state.readiness?.builtin;
    let html = ctx.originalViews.researchNew();
    const blocker = !ready
      ? `<div class="note-box amber" style="margin-bottom:20px">${esc(ui.researchDraft?.mode === "local" ? "尚无可用运行环境，请先配置并检测连接。" : state.readiness?.issues?.join("；") || "尚未配置可用研究模型。")}${button("配置连接", "full-settings", { small: true, attributes: `data-tab="${ui.researchDraft?.mode === "local" ? "runtimes" : "models"}"` })}</div>`
      : "";
    html = html.replace('<div class="wizard-steps"', blocker + '<div class="wizard-steps"');
    if (!ready)
      html = html.replace('data-action="start-research"', 'disabled data-action="start-research"');
    return html;
  });

  function markdown(text) {
    return String(text || "")
      .split(/\n{2,}/)
      .map((block) => {
        const match = /^(#{1,6})\s+([^\n]+)\n?([\s\S]*)/.exec(block);
        if (match) {
          const level = Math.min(4, match[1].length + 1);
          return `<h${level}>${esc(match[2])}</h${level}>${match[3] ? `<p style="white-space:pre-wrap">${esc(match[3])}</p>` : ""}`;
        }
        return `<p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(block)}</p>`;
      })
      .join("");
  }
  const reportNames = {
    final_trade_decision: "研究汇总",
    investment_plan: "投资计划",
    market_report: "技术分析",
    fundamentals_report: "基本面",
    news_report: "新闻",
    sentiment_report: "情绪",
    research_manager_decision: "研究经理汇总",
    trader_investment_plan: "交易提案",
    risk_management_decision: "风险管理",
  };
  function qualityLabel(quality) {
    return quality ? `${quality.status} · ${quality.evidence_count} 项证据` : "未提供数据资格";
  }
  function reportBody(data, tab) {
    const { snapshot, run } = data;
    const reports = Object.entries(snapshot.reports || {});
    if (tab === "decision") return ctx.originalViews.reportDecision(run);
    if (tab === "evidence") {
      const evidence = data.evidence?.items || [];
      return `<div class="stack"><div class="evidence-stats"><div class="evidence-stat"><div class="od-stat"><strong>${evidence.length} 项已读取证据</strong><small>原始来源与时点分别展示</small></div></div><div class="evidence-stat"><div class="od-stat"><strong class="warning">${esc(qualityLabel(snapshot.quality))}</strong><small>与执行状态分开</small></div></div><div class="evidence-stat"><div class="od-stat"><strong>${esc(run.date)}</strong><small>研究日 · ET</small></div></div></div><section class="panel"><header class="panel-head"><h2>证据与来源</h2>${button("完整证据与附件", "full-report", { small: true })}</header>${evidence.length ? `<table class="data-table responsive-table"><thead><tr><th>证据</th><th>来源</th><th>数据时点</th><th>状态</th><th></th></tr></thead><tbody>${evidence.map((item) => `<tr><td><button class="table-link od-field" data-action="show-evidence" data-evidence="${esc(item.id)}"><strong>${esc(item.category)}</strong><small>${esc(item.id)}</small></button></td><td>${esc(item.provider)} · ${esc(item.method)}</td><td>${esc(item.data_as_of || item.published_at || "未知")}</td><td>${badge(esc(item.status), item.status === "success" ? "mint" : "amber")}</td><td>${button("查看", "show-evidence", { small: true, attributes: `data-evidence="${esc(item.id)}"` })}</td></tr>`).join("")}</tbody></table>` : emptyState("尚无可读取证据", data.errors?.evidence || "已有产出和数据缺失情况均以服务端记录为准。")}</section>${data.evidence?.next_cursor ? '<div class="note-box">当前显示首批证据，可在完整证据页查看后续记录。</div>' : ""}</div>`;
    }
    if (tab === "process")
      return `<div class="process-grid"><section class="panel"><header class="panel-head"><h2>${run.mode === "local" ? "Agent 执行步骤" : "研究决策路径"}</h2>${badge(esc(ctx.runStatus(run)), active(run) ? "mint" : "")}</header><div class="timeline">${(run.mode === "local" ? (snapshot.events || []).slice(-12).map((event) => event.message) : ["分析师研究", "多空研究", "交易提案", "风险讨论", "研究汇总", "报告完成"]).map((label, i) => `<div class="timeline-item ${run.mode === "local" ? "" : run.stage > i + 1 ? "done" : run.stage === i + 1 ? "current" : ""}"><span class="timeline-mark">${i + 1}</span><div class="od-field"><strong>${esc(label)}</strong><small>${run.mode === "local" ? esc(snapshot.runtime_execution?.effective_model || snapshot.runtime_execution?.requested_model || run.stageLabel) : i + 1 === run.stage ? esc(run.stageLabel) : "阶段状态以服务器记录为准"}</small></div></div>`).join("")}</div></section><div class="stack"><section class="panel"><header class="panel-head"><h2>执行动态</h2>${button("刷新", "refresh-report", { small: true, glyph: "refresh" })}</header><div class="panel-body">${snapshot.error ? `<div class="note-box amber">${esc(snapshot.error)}</div>` : ""}${
        (snapshot.events || [])
          .slice(-40)
          .map(
            (event) =>
              `<div class="event-row"><time>${esc(event.time || event.timestamp || event.created_at || "")}</time><span>${esc(event.message || event.content || JSON.stringify(event))}</span></div>`,
          )
          .join("") || '<p class="quiet">暂无已保存事件</p>'
      }</div></section><section class="panel"><header class="panel-head"><h2>模型与运行配置</h2>${button("查看", "run-config", { small: true })}</header><div class="panel-body"><dl class="definition-list"><div><dt>执行方式</dt><dd>${run.mode === "local" ? "本地 Agent" : "内置 12 角色"}</dd></div><div><dt>当前阶段</dt><dd>${esc(run.stageLabel)}</dd></div><div><dt>执行状态</dt><dd>${esc(ctx.runStatus(run))}</dd></div></dl>${snapshot.stats ? `<details class="inline-details"><summary>用量记录</summary><pre class="live-json">${esc(JSON.stringify(snapshot.stats, null, 2))}</pre></details>` : ""}</div></section></div></div>`;
    const preferred =
      reports.find(([key]) => /final_trade_decision|portfolio_manager|final_report/.test(key)) ||
      reports.at(-1);
    return `<div class="split-layout"><section class="panel panel-body"><div class="report-reading"><nav class="report-toc" aria-label="报告章节">${reports.map(([key]) => `<a href="#report-${esc(key)}">${esc(reportNames[key] || key)}</a>`).join("")}</nav><article class="report-article">${
      preferred
        ? `<section id="report-${esc(preferred[0])}"><p class="section-label">已保存研究产出</p>${markdown(preferred[1])}</section>${reports
            .filter(([key]) => key !== preferred[0])
            .map(
              ([key, content]) =>
                `<section id="report-${esc(key)}"><h2>${esc(reportNames[key] || key)}</h2>${markdown(content)}</section>`,
            )
            .join("")}`
        : emptyState(
            "完整结论尚未形成",
            "可在“过程”中查看服务器保存的执行状态。",
            button("查看研究过程", "report-tab", { attributes: 'data-tab="process"' }),
          )
    }</article></div></section><aside class="stack"><section class="panel"><header class="panel-head"><h2>研究边界</h2>${icon("shield")}</header><div class="panel-body"><dl class="definition-list"><div><dt>研究日期</dt><dd>${esc(run.date)}</dd></div><div><dt>数据资格</dt><dd>${esc(qualityLabel(snapshot.quality))}</dd></div><div><dt>有效判断</dt><dd>${esc(run.rating || "尚无有效判断")}</dd></div><div><dt>原始判断</dt><dd>${esc(run.rawRating || "未提供")}</dd></div><div><dt>个人确认</dt><dd>${state.viewpoints[run.id] ? "已记录" : "尚未确认"}</dd></div></dl></div></section><section class="panel"><header class="panel-head"><h2>我的决策</h2></header><div class="panel-body">${button("记录我的观点", "report-tab", { attributes: 'data-tab="decision"', glyph: "arrow" })}</div></section></aside></div>`;
  }
  ctx.registerView("researchDetail", () => {
    const data = details.get(ui.runId);
    const run = data?.run || state.runs.find((item) => item.id === ui.runId);
    const tab = ui.reportTab || (run?.status === "completed" ? "conclusion" : "process");
    return `<div class="page">${heading(esc(run?.symbol || "研究详情"), esc(run?.objective || "读取已保存的研究与证据"), `${button("返回", "back", { ghost: true, glyph: "back" })}${run ? button("建立决策计划", "plan-from-run", { primary: true, glyph: "plus", attributes: `data-id="${esc(run.id)}"` }) : ""}`, "EQUITY RESEARCH")}<div class="report-top"><div class="small-meta">${run ? `${badge(esc(ctx.runStatus(run)), active(run) ? "mint" : "")}<span>研究日 ${esc(run.date)}</span><span>${run.mode === "local" ? "本地 Agent" : "内置 12 角色"}</span>` : ""}</div><div class="page-actions">${button("重新研究", "rerun", { small: true, glyph: "refresh", attributes: `data-id="${esc(ui.runId)}"` })}${button("导出报告", "export-run", { small: true, glyph: "download", attributes: `data-id="${esc(ui.runId)}"` })}${run && active(run) ? button("取消任务", "cancel-run", { small: true, attributes: `data-id="${esc(run.id)}"` }) : ""}</div></div><div class="tabs">${[
      ["conclusion", "结论"],
      ["evidence", "证据"],
      ["process", "过程"],
      ["decision", "我的决策"],
    ]
      .map(
        ([key, label]) =>
          `<button class="tab ${key === tab ? "active" : ""}" data-action="report-tab" data-tab="${key}">${label}${key === "evidence" && data ? `<span class="tab-count">${data.evidence?.items?.length || 0}</span>` : ""}</button>`,
      )
      .join(
        "",
      )}</div>${data ? reportBody(data, tab) : `<section class="panel">${emptyState(detailErrors.get(ui.runId) ? "研究读取失败" : "正在读取研究…", detailErrors.get(ui.runId) || "从当前账号读取报告与证据。", detailErrors.has(ui.runId) ? button("重试", "refresh-report") : "")}</section>`}</div>`;
  });

  const fullSettings = (tab) =>
    request("host.open", {
      path: tab === "account" ? "/account" : `/settings/${tab === "data" ? "sources" : tab}`,
    });
  ctx.registerView("settings", () => {
    if (ui.settingsTab === "preferences")
      return ctx.originalViews
        .settings()
        .replace(
          /<div class="settings-row"><div class="od-field"><strong>演示记录<\/strong>[\s\S]*?<\/div><\/div><\/section>/,
          "</div></section>",
        );
    const tab = ui.settingsTab;
    let body;
    if (tab === "models")
      body = `<section class="panel"><header class="panel-head"><h2>模型与研究配置</h2>${badge(state.readiness?.builtin ? "可用于研究" : "待配置", state.readiness?.builtin ? "mint" : "amber")}</header><div class="connection-row"><div class="od-row"><div class="od-field od-fill"><h3>${esc(state.model.name)}</h3><small class="quiet">模型连接与研究角色</small></div>${button("编辑配置", "full-settings", { small: true, glyph: "edit", attributes: 'data-tab="models"' })}</div></div><div class="panel-body"><div class="form-grid"><div class="role-box"><strong>分析与研究角色</strong><p class="mono">${esc(state.model.quick)}</p></div><div class="role-box"><strong>研究与组合经理</strong><p class="mono">${esc(state.model.deep)}</p></div></div></div><footer class="panel-foot">${esc(state.readiness?.issues?.join("；") || "启动前在研究摘要中核对执行配置。")}</footer></section>`;
    else if (tab === "runtimes")
      body = `<section class="panel"><header class="panel-head"><h2>电脑与运行环境</h2>${button("管理运行环境", "full-settings", { small: true, attributes: 'data-tab="runtimes"' })}</header><div class="panel-body stack">${state.model.runtimes?.length ? state.model.runtimes.map((runtime) => `<div class="connection-row"><div class="od-row"><div class="od-field od-fill"><h3>${esc(runtime.name)}</h3><small>${esc(runtime.model || "未指定模型")}</small></div>${badge(runtime.ready ? "就绪" : "待检测", runtime.ready ? "mint" : "amber")}</div></div>`).join("") : emptyState("尚未配置运行环境", "创建连接后完成授权与能力检测。")}</div></section>`;
    else if (tab === "account")
      body = `<section class="panel"><header class="panel-head"><h2>个人账号</h2>${badge("已登录", "mint")}</header><div class="panel-body stack"><div class="od-field"><strong>${esc(session?.user.displayName || "")}</strong><small class="quiet">独立个人工作区</small></div><div class="page-actions">${button("账号与安全", "full-settings", { attributes: 'data-tab="account"', glyph: "user" })}${button("退出登录", "logout")}</div></div></section>`;
    else
      body = `<section class="panel"><header class="panel-head"><h2>数据来源与账号</h2>${button("管理数据源", "full-settings", { small: true, attributes: 'data-tab="data"' })}</header>${[
        ["行情与技术观察", "日线、分钟线、成交量与指标"],
        ["公司披露与财报", "披露时间、报告期与来源证据"],
        ["宏观与新闻", "宏观序列、供应商新闻样本"],
        ["期权与 ETF 份额", "账号权限、合约覆盖与估算口径"],
      ]
        .map(
          ([title, detail]) =>
            `<div class="connection-row"><div class="od-row"><div class="od-field od-fill"><h3>${title}</h3><small class="quiet">${detail}</small></div>${button("查看接入", "full-settings", { small: true, attributes: 'data-tab="data"' })}</div></div>`,
        )
        .join(
          "",
        )}<footer class="panel-foot">${button("缓存与数据设置", "full-settings", { ghost: true, small: true, attributes: 'data-tab="cache"' })}</footer></section>`;
    return `<div class="page">${heading("设置中心", "让连接与配置各就其位，研究时专注于问题。", "", "WORKSPACE SETTINGS")}<div class="settings-layout"><nav class="settings-side" aria-label="设置分类">${[
      ["models", "模型与研究"],
      ["runtimes", "电脑与运行环境"],
      ["data", "数据来源"],
      ["preferences", "使用偏好"],
      ["account", "个人账号"],
    ]
      .map(
        ([key, label]) =>
          `<button class="${tab === key ? "active" : ""}" data-action="settings-tab" data-tab="${key}">${label}</button>`,
      )
      .join("")}</nav>${body}</div></div>`;
  });

  async function loadDetail(force = false) {
    const id = ui.runId;
    if (!id || detailLoading.has(id) || (!force && (details.has(id) || detailErrors.has(id))))
      return;
    detailLoading.add(id);
    detailErrors.delete(id);
    try {
      const data = await request("research.detail", { id });
      details.set(id, data);
      const index = state.runs.findIndex((item) => item.id === id);
      if (index < 0) state.runs.push(data.run);
      else state.runs[index] = data.run;
      const views = await request("investment.loadViewpoints", { runId: id });
      Object.assign(state.viewpoints, views?.viewpoints || views || {});
      if (ui.page === "researchDetail" && ui.runId === id && !decisionDraft) ctx.render();
    } catch (error) {
      detailErrors.set(id, error.message);
      if (ui.runId === id) ctx.render();
    } finally {
      detailLoading.delete(id);
    }
  }
  ctx.registerAction("refresh-workspace", () => refresh());
  ctx.registerAction("reload-workspace", () => window.location.reload());
  ctx.registerAction("more-research", async () => {
    const result = await request("research.more", { cursor: state.nextCursor });
    for (const run of result.runs)
      if (!state.runs.some((item) => item.id === run.id)) state.runs.push(run);
    state.nextCursor = result.nextCursor;
    ctx.render();
  });
  ctx.registerAction("rerun", async (data) => {
    const detail = details.get(data.id) || (await request("research.detail", { id: data.id }));
    const run = detail.run;
    ctx.beginResearch(run.symbol, {
      ...run,
      context: run.sources || [],
      instructions: run.instructions || "",
    });
    ctx.toast("已预填原研究，请核对配置后再启动");
  });
  ctx.registerAction("refresh-report", () => loadDetail(true));
  ctx.registerAction("full-settings", (data) =>
    data.tab === "cache"
      ? request("host.open", { path: "/settings/data" })
      : fullSettings(data.tab || "models"),
  );
  ctx.registerAction("full-report", () => request("host.open", { path: `/runs/${ui.runId}` }));
  ctx.registerAction("logout", () => request("host.logout"));
  ctx.registerAction("start-research", async () => {
    for (const step of [1, 2]) {
      const errors = ctx.researchErrors(step);
      if (errors.length) {
        ui.researchStep = step;
        ctx.render();
        ctx.displayResearchErrors(errors);
        return;
      }
    }
    const run = await request("research.create", ui.researchDraft);
    state.runs.unshift(run);
    ui.researchDraft = null;
    ui.context = [];
    draftDirty = false;
    ctx.navigate("researchDetail", { runId: run.id, symbol: run.symbol, reportTab: "process" });
    ctx.toast("研究已提交，进度由研究引擎更新");
  });
  ctx.registerAction("cancel-run", (data) =>
    ctx.showDialog(
      "取消这项研究？",
      '<p class="muted">保留已经保存的产出。正在进行的调用可能需要片刻才能停止。</p>',
      `${button("返回", "close-dialog", { ghost: true })}${button("确认取消", "confirm-cancel-run", { attributes: `data-id="${esc(data.id)}"` })}`,
    ),
  );
  ctx.registerAction("confirm-cancel-run", async (data) => {
    await request("research.cancel", { id: data.id });
    ctx.closeDialog();
    await refresh();
    await loadDetail(true);
  });
  ctx.registerAction("show-evidence", (data) => {
    const item = details.get(ui.runId)?.evidence?.items?.find((row) => row.id === data.evidence);
    if (item)
      ctx.showDialog(
        `${item.category} · ${item.provider}`,
        `<dl class="definition-list">${[
          ["证据 ID", item.id],
          ["来源", item.provider],
          ["方法", item.method],
          ["数据时点", item.data_as_of],
          ["接收时点", item.received_at],
          ["状态", item.status],
        ]
          .map(([key, value]) => `<div><dt>${key}</dt><dd>${esc(value || "未知")}</dd></div>`)
          .join("")}</dl><pre class="live-json">${esc(item.body || "未保存正文")}</pre>`,
        button("关闭", "close-dialog"),
      );
  });
  ctx.registerAction("run-config", () =>
    ctx.showDialog(
      "本次研究配置",
      `<pre class="live-json">${esc(JSON.stringify(details.get(ui.runId)?.manifest || details.get(ui.runId)?.snapshot || {}, null, 2))}</pre>`,
      button("关闭", "close-dialog"),
    ),
  );
  ctx.registerAction("export-run", async (data) => {
    const result = await request("research.export", { id: data.id });
    ctx.showDialog(
      "导出研究报告",
      `<textarea id="export-text" class="textarea" rows="16" readonly>${esc(result.markdown)}</textarea>`,
      `${button("复制", "copy-export")}${button("下载 Markdown", "live-download", { primary: true, attributes: `data-filename="${esc(result.filename)}"` })}`,
    );
  });
  ctx.registerAction("live-download", (data) => {
    const url = URL.createObjectURL(
      new Blob([document.getElementById("export-text").value], {
        type: "text/markdown;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = data.filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  ctx.registerAction("compare-runs", async () => {
    const result = await request("research.compare", { ids: ui.compare });
    ctx.showDialog(
      "同股研究对照",
      `<div class="comparison-grid">${ui.compare
        .map((id) => {
          const run = state.runs.find((row) => row.id === id);
          return `<section class="comparison-column"><h3>${esc(run?.symbol)} · ${esc(run?.date)}</h3><p>${esc(run?.template)}</p><p>${esc(run?.objective)}</p></section>`;
        })
        .join(
          "",
        )}</div><div class="od-cluster" style="margin-block:20px">${badge(result.model_changed ? "模型有变化" : "模型未检测到变化")}${badge(result.same_captured_evidence == null ? "无法确认捕获证据一致性" : result.same_captured_evidence ? "捕获证据集合一致" : "捕获证据不同", "amber")}</div><p class="muted">变化的输入：${esc(result.request_changes?.join("、") || "未检测到变化")}</p><div class="note-box">${esc(result.scope_notice || "比较保存的输入、证据与文本，不代表投资表现。")}</div>${(result.changed_sections || []).map((key) => `<details class="inline-details"><summary>${esc(reportNames[key] || key)}</summary><pre class="live-json">${esc(result.section_diffs?.[key] || "没有可展示的文本差异")}</pre></details>`).join("")}`,
      button("关闭", "close-dialog"),
    );
  });
  ctx.registerAction("tasks", () => {
    const runs = state.runs.filter(active);
    ctx.showDialog(
      "任务中心",
      `<div class="small-meta">${runs.length} 项正在执行的研究</div>${runs.length ? runs.map((run) => `<button class="search-result" data-action="search-run" data-id="${esc(run.id)}"><span class="od-field"><strong>${esc(run.symbol)} · ${esc(run.objective)}</strong><small>${esc(run.stageLabel)}</small></span>${badge(esc(ctx.runStatus(run)), "mint")}</button>`).join("") : emptyState("没有正在运行的任务", "新建研究后可在这里查看执行状态。")}`,
      button("刷新工作区", "refresh-workspace", { glyph: "refresh" }),
      { drawer: true },
    );
  });
  ctx.registerAction("guide", () =>
    ctx.showDialog(
      "使用指引",
      '<div class="stack-sm"><h3>发现机会 → 研究验证 → 形成决策 → 跟踪复盘</h3><p class="muted">在自选行情观察公司；在新建研究中明确问题、选择执行方案；阅读结论、证据和过程；在投资决策中记录个人假设、边界和复核日期。</p><p class="muted">模型、数据源与运行环境在设置中心配置。所有保存操作均属于当前账号。</p></div>',
      button("开始研究", "dialog-new-research", { primary: true }),
    ),
  );

  ctx.registerAfterRender(() => {
    if (ui.page === "researchDetail") void loadDetail();
    const banner = document.getElementById("live-load-errors");
    banner?.remove();
    if (state.loadErrors?.length) {
      const node = document.createElement("div");
      node.id = "live-load-errors";
      node.className = "note-box amber";
      node.innerHTML = `${state.loadErrors.map(esc).join("<br>")}${button("重新读取", "refresh-workspace", { small: true, glyph: "refresh" })}`;
      document.getElementById("main").prepend(node);
    }
    if (loaded && !routeWriting && routePath() !== lastPath) {
      const replace = !lastPath;
      lastPath = routePath();
      void request("host.navigate", { path: lastPath, replace }).catch(() => {});
    }
    if (ui.page === "researchDetail" && decisionDraft?.id === ui.runId)
      for (const [id, value] of Object.entries(decisionDraft.values)) {
        const field = document.getElementById(id);
        if (field) {
          if (field.type === "checkbox") field.checked = value;
          else field.value = value;
        }
      }
  });
  let searchTimer;
  document.addEventListener("input", (event) => {
    if (event.target.id !== "global-query") return;
    clearTimeout(searchTimer);
    const query = event.target.value.trim();
    if (!query) return;
    searchTimer = setTimeout(async () => {
      try {
        const result = await request("market.search", { query });
        if (document.getElementById("global-query")?.value.trim() !== query) return;
        const stocks = (result.items || [])
          .filter((item) => !ctx.securities.some((stock) => stock.symbol === item.symbol))
          .slice(0, 6);
        if (!stocks.length) return;
        const root = document.getElementById("global-results");
        if (root)
          root.insertAdjacentHTML(
            "beforeend",
            `<p class="dialog-section-label">证券目录</p>${stocks.map((stock) => `<button type="button" class="search-result" data-action="live-open-symbol" data-symbol="${esc(stock.symbol)}"><span class="od-field"><strong>${esc(stock.symbol)} · ${esc(stock.name)}</strong><small>${stock.type === "etf" ? "ETF" : "公司股票"}</small></span>${icon("arrow")}</button>`).join("")}`,
          );
      } catch {
        /* Local research and plan search remain available when the catalog is unavailable. */
      }
    }, 250);
  });
  document.addEventListener("input", (event) => {
    if (event.target.closest("#research-form,#plan-form") || event.target.id?.startsWith("policy-"))
      draftDirty = true;
    if (event.target.closest("#viewpoint-form")) {
      decisionDraft = {
        id: ui.runId,
        values: Object.fromEntries(
          [
            ...document.querySelectorAll(
              "#viewpoint-form input,#viewpoint-form textarea,#viewpoint-form select",
            ),
          ].map((field) => [field.id, field.type === "checkbox" ? field.checked : field.value]),
        ),
      };
    }
  });
  const guarded = new Set([
    "navigate",
    "back",
    "new-research",
    "dialog-new-research",
    "template-research",
    "local-research",
    "open-run",
    "search-run",
    "open-stock",
    "search-stock",
    "edit-plan",
    "new-plan",
    "review-plan",
    "account",
    "full-settings",
    "full-report",
    "live-open",
    "logout",
  ]);
  document.addEventListener(
    "click",
    (event) => {
      const control = event.target.closest?.("[data-action]");
      if (!control || (!draftDirty && !decisionDraft) || !guarded.has(control.dataset.action))
        return;
      if (control.dataset.action === "navigate" && control.dataset.page === ui.page) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      guardedControl = { ...control.dataset };
      ctx.showDialog(
        "保留当前修改？",
        '<p class="muted">当前修改尚未保存。可以继续编辑，或放弃修改后离开。</p>',
        `${button("继续编辑", "close-dialog", { ghost: true })}${button("放弃修改并继续", "discard-navigation")}`,
      );
    },
    true,
  );
  ctx.registerAction("discard-navigation", () => {
    const data = guardedControl;
    ctx.onSaved();
    ctx.closeDialog(false);
    if (!data) return;
    const control = document.createElement("button");
    Object.assign(control.dataset, data);
    control.hidden = true;
    document.body.append(control);
    control.click();
    control.remove();
  });
  document.getElementById("app-dialog").addEventListener("close", () => {
    const frame = document.querySelector(".live-workspace-frame");
    document.getElementById("app-dialog").classList.remove("live-workspace-dialog");
    if (frame) {
      frame.remove();
      void refresh();
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (draftDirty || decisionDraft) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  if (parent === window) {
    document.getElementById("main").innerHTML =
      `<div class="page">${emptyState("请从工作台进入", "界面通过已登录账号读取数据。", '<a class="button primary" href="../">打开工作台</a>')}</div>`;
    return;
  }
  try {
    session = await request("host.session");
    ctx.setStorageKey(
      `tradingagents.design.preferences.${session.user.id}.${session.user.workspaceId}`,
    );
    readRoute(
      session.path === "/"
        ? {
            dashboard: "/",
            watch: "/stocks",
            market: "/market",
            investment: "/investment",
            library: "/reports",
          }[state.preferences.defaultPage] || "/"
        : session.path,
    );
    await refresh();
  } catch (error) {
    document.getElementById("main").innerHTML =
      `<div class="page">${emptyState("无法读取工作区", esc(error.message), button("重试", "refresh-workspace"))}</div>`;
  }
  setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.runs.some(active) && !draftDirty && !decisionDraft) {
      void refresh();
      if (ui.page === "researchDetail") void loadDetail(true);
    }
  }, 8000);
}
