/* ThesisFlow workspace, powered by the TradingAgents research engine. No external scripts and no model calls on load. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const TERMINAL = new Set(["completed", "failed", "interrupted"]);
  const LABELS = { market: "技术分析", fundamentals: "基本面", news: "新闻分析", social: "情绪分析" };
  const REPORT_LABELS = {
    final_trade_decision: "最终决策", market_report: "技术分析", fundamentals_report: "基本面",
    news_report: "新闻分析", sentiment_report: "社交情绪", investment_plan: "研究决策",
    trader_investment_plan: "交易计划", bull_history: "多头观点", bear_history: "空头观点",
    aggressive_history: "激进风险", conservative_history: "保守风险", neutral_history: "中立风险",
    memory_note: "历史经验",
  };
  const REPORT_ORDER = Object.keys(REPORT_LABELS);
  const STAGES = ["并行分析", "多空辩论", "交易提案", "风险审视", "最终决策"];
  const STAGE_INDEX = { initializing: -1, analysts: 0, research: 1, trading: 2, risk: 3, portfolio: 4, completed: 5 };
  const STAGE_LABELS = {
    initializing: "正在初始化研究环境", analysts: "分析师正在并行研究", research: "研究团队正在进行多空辩论",
    trading: "正在制定交易提案", risk: "风险团队正在审视研究结论", portfolio: "组合经理正在形成最终判断",
    completed: "研究已完成", failed: "研究未能完成", interrupted: "研究已中断",
  };
  const STATUS_LABELS = { pending: "等待启动", running: "研究中", completed: "已完成", failed: "运行失败", interrupted: "已中断" };
  const PROVIDERS = { chatgpt: "ChatGPT 订阅", openai: "OpenAI", anthropic: "Anthropic", google: "Google Gemini", deepseek: "DeepSeek", ollama: "Ollama 本地模型", openrouter: "OpenRouter", azure: "Azure OpenAI", bedrock: "Amazon Bedrock" };
  const state = { config: null, runs: [], activeRun: null, historyRun: null, page: "workspace", connected: false, submitting: false, timer: null, refreshing: false, toastTimer: null, tabs: new Map(), failures: 0 };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function icon(name) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }
  function button(text, className, onClick, iconName) {
    const node = el("button", `button ${className || "button-secondary"}`);
    node.type = "button";
    if (iconName) node.append(icon(iconName));
    node.append(el("span", "", text));
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }
  function clear(node) { node.replaceChildren(); }
  function safeText(value, fallback = "—") { return value === undefined || value === null || value === "" ? fallback : String(value); }
  function isRunning(run) { return run && !TERMINAL.has(run.status); }
  function statusClass(status) { return status === "completed" ? "pill-green" : status === "failed" || status === "interrupted" ? "pill-red" : status === "running" ? "pill-blue" : "pill-neutral"; }
  function localDate() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
  function formatNumber(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—"; }
  function formatDuration(seconds) {
    const n = Math.max(0, Math.floor(Number(seconds) || 0));
    if (n < 60) return `${n} 秒`;
    if (n < 3600) return `${Math.floor(n / 60)} 分 ${n % 60} 秒`;
    return `${Math.floor(n / 3600)} 时 ${Math.floor((n % 3600) / 60)} 分`;
  }
  function formatDate(value, onlyTime = false) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 20);
    return new Intl.DateTimeFormat("zh-CN", onlyTime ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false } : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
  }
  function rememberRun(id) { try { if (id) localStorage.setItem("tradingagents.lastRun", id); else localStorage.removeItem("tradingagents.lastRun"); } catch (_) { /* Storage can be unavailable in private mode. */ } }
  function rememberedRun() { try { return localStorage.getItem("tradingagents.lastRun"); } catch (_) { return null; } }
  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4300);
  }
  function showMessage(message) {
    $("global-message").textContent = message;
    $("global-message").hidden = !message;
  }
  function setConnected(connected) {
    state.connected = connected;
    const label = $("connection-label");
    label.firstElementChild.className = `connection-dot ${connected ? "connected" : "error"}`;
    label.lastElementChild.textContent = connected ? "本地服务已连接" : "连接已断开";
    $("sidebar-connection").className = `connection-dot ${connected ? "connected" : "error"}`;
    $("sidebar-status").textContent = connected ? "运行正常" : "正在尝试重新连接";
    $("start-analysis").disabled = !connected || state.submitting || state.config?.model_ready === false;
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { ...options, headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }, signal: controller.signal, credentials: "same-origin", cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail || data.error;
        let message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((x) => x.msg || "参数无效").join("；") : `请求失败（${response.status}）`;
        if (response.status === 422 && Array.isArray(data.errors) && data.errors.length) {
          const fields = { ticker: "证券代码", date: "分析日期", analysts: "分析师", depth: "研究深度", language: "报告语言", checkpoint: "恢复配置" };
          message = data.errors.map((item) => `${fields[item.field] || item.field || "参数"}：${item.message || "取值不符合要求"}`).join("；");
        }
        if (response.status === 409) message = typeof detail === "string" ? detail : "已有研究正在运行，请等待当前任务完成。";
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      setConnected(true);
      return data;
    } catch (error) {
      if (!error.status) setConnected(false);
      if (error.name === "AbortError") throw new Error("本地服务响应超时，正在重试连接。");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function closeSidebar() {
    $("sidebar").classList.remove("open");
    $("sidebar-scrim").hidden = true;
    $("menu-toggle").setAttribute("aria-expanded", "false");
    syncSidebarAccessibility();
  }
  function syncSidebarAccessibility() {
    const hidden = window.matchMedia("(max-width: 760px)").matches && !$("sidebar").classList.contains("open");
    if (hidden && $("sidebar").contains(document.activeElement)) $("menu-toggle").focus({ preventScroll: true });
    $("sidebar").inert = hidden;
    if (hidden) $("sidebar").setAttribute("aria-hidden", "true"); else $("sidebar").removeAttribute("aria-hidden");
  }
  function setPage(page) {
    state.page = ["workspace", "history", "settings"].includes(page) ? page : "workspace";
    document.querySelectorAll(".page").forEach((node) => { node.hidden = node.id !== `page-${state.page}`; });
    document.querySelectorAll(".nav-item").forEach((node) => {
      const current = node.dataset.page === state.page;
      node.classList.toggle("active", current);
      if (current) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current");
    });
    const names = { workspace: "研究工作台", history: "历史报告", settings: "运行设置" };
    $("breadcrumb-current").textContent = names[state.page];
    document.title = `${names[state.page]} · 研衡 · ThesisFlow`;
    closeSidebar();
    if (state.page === "history") { renderHistory(); refreshHistory(false); }
    if (state.page === "settings") renderSettings();
  }
  function showWorkspace() { if (location.hash !== "#workspace") location.hash = "workspace"; else setPage("workspace"); }
  function newAnalysis() {
    showWorkspace();
    if (isRunning(state.activeRun)) {
      toast("当前研究正在运行，完成后即可开始新的分析。");
      $("active-run-container").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    state.activeRun = null;
    rememberRun(null);
    $("active-run-container").hidden = true;
    $("setup-workspace").hidden = false;
    updateMetrics();
    $("ticker").focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function updateMetrics() {
    const config = state.config;
    if (config) {
      $("metric-model").textContent = safeText(config.deep_model, "未配置模型");
      $("metric-model").title = `深度模型：${safeText(config.deep_model)}；快速模型：${safeText(config.quick_model)}`;
      $("metric-provider").textContent = `${PROVIDERS[config.deep_provider || config.provider] || config.provider} · 深度推理`;
    }
    const analysts = state.activeRun?.analysts || [...document.querySelectorAll('input[name="analysts"]:checked')].map((x) => x.value);
    const depth = state.activeRun?.depth || Number(document.querySelector('input[name="depth"]:checked')?.value || 1);
    $("metric-analysts").textContent = analysts.length;
    $("metric-depth").textContent = depth;
    $("metric-depth-description").textContent = { 1: "快速研究，聚焦核心观点", 2: "交叉验证，充分讨论分歧", 3: "深入辩论，审视关键假设" }[depth] || "多轮辩论与观点审视";
    const run = state.activeRun;
    $("metric-status").textContent = run ? STATUS_LABELS[run.status] || run.status : !config ? "读取配置" : config.model_ready === false ? "等待配置" : "准备就绪";
    $("metric-status-description").textContent = run ? `${run.ticker} · ${STAGE_LABELS[run.stage] || "研究任务"}` : !config ? "正在连接本地研究服务" : config.model_ready === false ? "模型服务需要登录或配置凭据" : "开始你的下一次市场研究";
  }
  function applyConfig(config) {
    const firstConfig = !state.config;
    state.config = config;
    $("analysis-date").max = config.today || localDate();
    if (!$("analysis-date").value) $("analysis-date").value = config.today || localDate();
    if (firstConfig) $("output-language").value = config.output_language === "English" ? "English" : "Chinese";
    const version = document.querySelector(".version-label");
    if (version && config.version) version.textContent = `v${config.version}`;
    if (config.model_ready === false) showMessage("模型服务尚未就绪。请在本机完成 ChatGPT 登录或配置模型凭据，再刷新页面。");
    else showMessage("");
    $("start-analysis").disabled = !state.connected || state.submitting || config.model_ready === false;
    updateMetrics();
    if (state.page === "settings") renderSettings();
  }

  async function submitAnalysis(event) {
    event.preventDefault();
    if (state.submitting) return;
    const form = $("analysis-form");
    if (!form.reportValidity()) return;
    const ticker = $("ticker").value.trim().toUpperCase();
    const analysts = [...form.querySelectorAll('input[name="analysts"]:checked')].map((node) => node.value);
    const errorNode = $("form-error");
    errorNode.hidden = true;
    if (!analysts.length) { errorNode.textContent = "请至少选择一位分析师。"; errorNode.hidden = false; return; }
    if (!/^[A-Z0-9^][A-Z0-9.^=/:_-]{0,31}$/.test(ticker)) { errorNode.textContent = "请输入有效的证券代码，例如 NVDA、0700.HK 或 BTC-USD。"; errorNode.hidden = false; $("ticker").focus(); return; }
    state.submitting = true;
    const start = $("start-analysis");
    start.disabled = true;
    start.setAttribute("aria-busy", "true");
    clear(start);
    start.append(el("span", "spinner"), el("span", "", "正在启动"));
    try {
      const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ ticker, date: $("analysis-date").value, analysts, depth: Number(form.querySelector('input[name="depth"]:checked').value), language: $("output-language").value }) });
      state.activeRun = run;
      rememberRun(run.id);
      upsertRun(run);
      renderActiveRun();
      toast(`${run.ticker} 研究已启动，可在此实时查看进展。`);
      $("active-run-container").scrollIntoView({ behavior: "smooth", block: "start" });
      schedulePoll(1000);
    } catch (error) {
      errorNode.textContent = error.message || "启动失败，请稍后重试。";
      errorNode.hidden = false;
      if (error.status === 409) await restoreActiveRun();
    } finally {
      state.submitting = false;
      start.disabled = !state.connected || state.config?.model_ready === false;
      start.removeAttribute("aria-busy");
      clear(start);
      start.append(icon("spark"), el("span", "", "启动研究"), icon("arrow"));
    }
  }
  function upsertRun(run) {
    const index = state.runs.findIndex((item) => item.id === run.id);
    if (index >= 0) state.runs[index] = run; else state.runs.unshift(run);
    $("history-count").textContent = state.runs.length;
    $("history-total").textContent = state.runs.length;
  }
  async function restoreActiveRun() {
    try {
      const config = await api("/api/config");
      applyConfig(config);
      if (config.active_run_id) {
        state.activeRun = await api(`/api/runs/${encodeURIComponent(config.active_run_id)}`);
        rememberRun(state.activeRun.id);
        renderActiveRun();
      }
    } catch (_) { /* The polling loop will retry. */ }
  }
  function renderActiveRun() {
    const run = state.activeRun;
    $("setup-workspace").hidden = !!run;
    $("active-run-container").hidden = !run;
    if (run) renderRun(run, $("active-run-container"));
    updateMetrics();
  }
  function runLink(run, suffix) { return `/api/runs/${encodeURIComponent(run.id)}${suffix}`; }

  function createRunView(run, container) {
    clear(container);
    container.dataset.runId = run.id;
    const card = el("section", "card run-card");
    card.setAttribute("aria-label", `${run.ticker} 研究详情`);
    const header = el("div", "run-header");
    const titleBlock = el("div");
    const titleRow = el("div", "run-title-row");
    const ticker = el("h2", "ticker-title", run.ticker);
    const status = el("span", "pill");
    titleRow.append(ticker, status);
    const subtitle = el("div", "run-subtitle");
    titleBlock.append(titleRow, subtitle);
    const actions = el("div", "run-actions");
    const htmlLink = el("a", "button button-secondary");
    htmlLink.append(icon("download"), el("span", "", "HTML 报告"));
    htmlLink.href = runLink(run, "/report");
    htmlLink.setAttribute("download", `${run.ticker}-${run.date}.html`);
    const download = el("a", "button button-secondary");
    download.append(icon("download"), el("span", "", "Markdown"));
    download.href = runLink(run, "/report.md");
    download.setAttribute("download", `${run.ticker}-${run.date}.md`);
    const next = button("新建研究", "button-primary", newAnalysis, "plus");
    actions.append(htmlLink, download, next);
    header.append(titleBlock, actions);

    const progress = el("div", "run-progress");
    const progressHeading = el("div", "progress-heading");
    const stageTitle = el("h3");
    stageTitle.setAttribute("aria-live", "polite");
    const stageCount = el("span");
    progressHeading.append(stageTitle, stageCount);
    const track = el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "研究流程进度");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "5");
    const fill = el("div", "progress-fill");
    track.append(fill);
    const stages = el("div", "progress-stages");
    STAGES.forEach((label, index) => { const stage = el("div", "progress-stage"); stage.append(el("span", "", String(index + 1).padStart(2, "0")), el("div", "", label)); stages.append(stage); });
    progress.append(progressHeading, track, stages);
    const metricGrid = el("div", "run-metrics");
    const metricNodes = [];
    ["模型调用", "工具调用", "Token 用量", "研究耗时"].forEach((label) => {
      const item = el("div", "run-metric"); const value = el("strong", "", "—"); item.append(el("span", "", label), value); metricGrid.append(item); metricNodes.push(value);
    });
    const body = el("div", "run-body");
    const reportPane = el("section", "report-pane");
    const feedPane = el("aside", "feed-pane");
    const feedHeading = el("div", "feed-heading");
    const feedBadge = el("span", "", "LIVE");
    feedHeading.append(el("h3", "", "研究动态"), feedBadge);
    const feed = el("ol", "event-list");
    feed.setAttribute("aria-label", "研究运行事件");
    feedPane.append(feedHeading, feed);
    body.append(reportPane, feedPane);
    card.append(header, progress, metricGrid, body);
    container.append(card);
    const view = { card, status, subtitle, next, htmlLink, download, stageTitle, stageCount, track, fill, stages, metricNodes, reportPane, feedBadge, feed, reportSignature: null, eventSignature: null, tab: null };
    container._runView = view;
    return view;
  }
  function elapsed(run) {
    if (run.stats?.elapsed_seconds) return run.stats.elapsed_seconds;
    if (!run.started_at) return 0;
    const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
    const start = new Date(run.started_at).getTime();
    return Number.isFinite(end - start) ? Math.max(0, (end - start) / 1000) : 0;
  }
  function renderRun(run, container) {
    const view = container.dataset.runId === run.id && container._runView ? container._runView : createRunView(run, container);
    const running = isRunning(run);
    view.status.className = `pill ${statusClass(run.status)}`;
    view.status.textContent = STATUS_LABELS[run.status] || run.status;
    clear(view.subtitle);
    view.subtitle.append(el("span", "", `分析日期 ${run.date}`), el("span", "", `${(run.analysts || []).map((a) => LABELS[a] || a).join(" / ")}`), el("span", "", `${run.depth || 1} 轮辩论`));
    view.next.hidden = running;
    view.htmlLink.hidden = run.status !== "completed" || !run.report_url;
    view.download.hidden = run.status !== "completed" || !run.markdown_url;
    view.stageTitle.textContent = STAGE_LABELS[run.stage] || STATUS_LABELS[run.status] || "正在准备研究";
    const index = STAGE_INDEX[run.stage] ?? -1;
    const finished = run.status === "completed";
    view.stageCount.textContent = finished ? "5 / 5" : index >= 0 ? `${Math.min(index + 1, 5)} / 5` : "准备中";
    view.fill.style.width = `${finished ? 100 : index < 0 ? 4 : (index + .5) * 20}%`;
    view.track.setAttribute("aria-valuenow", finished ? "5" : String(Math.max(0, index)));
    view.track.setAttribute("aria-valuetext", view.stageTitle.textContent);
    [...view.stages.children].forEach((node, i) => {
      node.className = `progress-stage${finished || i < index ? " done" : running && i === index ? " current" : ""}`;
      node.firstElementChild.textContent = finished || i < index ? "✓" : String(i + 1).padStart(2, "0");
    });
    const stats = run.stats || {};
    view.metricNodes[0].textContent = formatNumber(stats.llm_calls ?? 0);
    view.metricNodes[1].textContent = formatNumber(stats.tool_calls ?? 0);
    const tokenIn = Number(stats.tokens_in) || 0;
    const tokenOut = Number(stats.tokens_out) || 0;
    view.metricNodes[2].textContent = tokenIn + tokenOut > 0 ? formatNumber(tokenIn + tokenOut) : running ? "等待统计" : "未返回";
    view.metricNodes[2].title = `输入 ${formatNumber(tokenIn)} / 输出 ${formatNumber(tokenOut)} token；部分模型不提供用量。`;
    view.metricNodes[2].style.fontSize = tokenIn + tokenOut > 0 ? "" : "13px";
    view.metricNodes[3].textContent = formatDuration(elapsed(run));
    view.metricNodes[3].style.fontSize = "17px";
    view.feedBadge.textContent = running ? "LIVE" : "ARCHIVE";
    renderEvents(run, view);
    const reportSignature = JSON.stringify([run.status, run.error, run.rating, run.reports, state.tabs.get(run.id)]);
    if (view.reportSignature !== reportSignature) {
      view.reportSignature = reportSignature;
      renderReports(run, view);
    }
  }
  function renderEvents(run, view) {
    const events = Array.isArray(run.events) ? run.events : [];
    const signature = `${events.length}:${events.at(-1)?.id}:${events.at(-1)?.message}`;
    if (signature === view.eventSignature) return;
    view.eventSignature = signature;
    const wasBottom = view.feed.scrollHeight - view.feed.scrollTop - view.feed.clientHeight < 50;
    const previousScroll = view.feed.scrollTop;
    clear(view.feed);
    if (!events.length) {
      const item = el("li", "feed-empty", isRunning(run) ? "研究准备就绪，运行动态将显示在这里。" : "该记录没有保存运行动态。");
      view.feed.append(item);
      return;
    }
    events.slice(-100).forEach((event) => {
      const item = el("li", `event-item${event.type === "error" ? " error" : ""}`);
      const time = el("time", "", formatDate(event.time, true));
      if (event.time) time.dateTime = event.time;
      item.append(time, el("p", "", event.message || "状态更新"));
      view.feed.append(item);
    });
    view.feed.scrollTop = wasBottom ? view.feed.scrollHeight : previousScroll;
  }
  function emptyState(iconName, title, description) {
    const box = el("div", "empty-state");
    const symbol = el("div", "empty-icon"); symbol.append(icon(iconName));
    box.append(symbol, el("h3", "", title), el("p", "", description));
    return box;
  }
  function reportEntries(run) {
    const reports = run.reports && typeof run.reports === "object" ? run.reports : {};
    const keys = Object.keys(reports).filter((key) => typeof reports[key] === "string" && reports[key].trim());
    keys.sort((a, b) => (REPORT_ORDER.includes(a) ? REPORT_ORDER.indexOf(a) : 99) - (REPORT_ORDER.includes(b) ? REPORT_ORDER.indexOf(b) : 99));
    return keys.map((key) => [key, reports[key]]);
  }
  function renderReports(run, view) {
    clear(view.reportPane);
    if (run.error) {
      const error = el("div", "run-error"); error.setAttribute("role", "alert"); error.append(icon("info"), el("p", "", String(run.error).slice(0, 4000))); view.reportPane.append(error);
    }
    const entries = reportEntries(run);
    if (!entries.length) {
      const box = emptyState(isRunning(run) ? "spark" : "file", isRunning(run) ? "研究正在发生，洞察即将抵达。" : "暂无可阅读的研究报告", isRunning(run) ? "分析师正在收集证据并形成观点。首份报告生成后，会自动显示在这里，无需刷新。" : "任务结束前尚未生成报告。你可以查看右侧运行动态，或调整参数后重新开始。");
      if (isRunning(run)) { const indicator = el("div", "research-loading"); indicator.append(el("span", "spinner"), el("span", "", "持续接收研究进展")); box.append(indicator); }
      else box.append(button("重新开始研究", "button-secondary", () => { newAnalysis(); if (run.ticker) $("ticker").value = run.ticker; }, "refresh"));
      view.reportPane.append(box);
      return;
    }
    if (run.rating) {
      const banner = el("div", "decision-banner"); banner.append(el("span", "", "组合经理 · 最终评级"), el("strong", "", run.rating)); view.reportPane.append(banner);
    }
    const tabs = el("div", "report-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "研究报告章节");
    const content = el("div", "report-content");
    content.setAttribute("role", "tabpanel");
    content.id = `report-panel-${run.id}`;
    let selected = state.tabs.get(run.id);
    if (!entries.some(([key]) => key === selected) && selected !== "full") selected = entries[0][0];
    const fullReportAvailable = run.status === "completed" && !!run.report_url;
    if (selected === "full" && !fullReportAvailable) selected = entries[0][0];
    const allTabs = entries.map(([key, value]) => ({ key, value, label: REPORT_LABELS[key] || key }));
    if (fullReportAvailable) allTabs.push({ key: "full", label: "完整报告" });
    allTabs.forEach(({ key, label, value }) => {
      const tab = el("button", "report-tab", label);
      tab.type = "button";
      tab.id = `tab-${run.id}-${key}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(key === selected));
      tab.setAttribute("aria-controls", content.id);
      tab.tabIndex = key === selected ? 0 : -1;
      tab.addEventListener("click", () => {
        state.tabs.set(run.id, key);
        view.reportSignature = null;
        renderRun(run, view.card.parentElement);
        view.reportPane.querySelector(`[id="${tab.id}"]`)?.focus({ preventScroll: true });
      });
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const index = allTabs.findIndex((item) => item.key === key);
        const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? allTabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + allTabs.length) % allTabs.length;
        state.tabs.set(run.id, allTabs[nextIndex].key);
        view.reportSignature = null;
        renderRun(run, view.card.parentElement);
        view.reportPane.querySelector(`[id="tab-${run.id}-${allTabs[nextIndex].key}"]`)?.focus({ preventScroll: true });
      });
      tabs.append(tab);
      if (key === selected) {
        content.setAttribute("aria-labelledby", tab.id);
        if (key === "full") {
          const frame = el("iframe", "report-frame");
          frame.title = `${run.ticker} 完整研究报告`;
          frame.setAttribute("sandbox", "");
          frame.setAttribute("referrerpolicy", "no-referrer");
          frame.src = runLink(run, "/report");
          content.append(frame);
        } else content.append(renderMarkdown(value));
      }
    });
    view.reportPane.append(tabs, content);
    view.reportPane.append(el("div", "report-source-note", "AI 生成的研究观点可能存在错误。请结合原始数据及个人情况独立判断。"));
  }

  /* Render a small Markdown subset using DOM text nodes only. Raw HTML is always text. */
  function appendInline(parent, value, depth = 0) {
    const text = String(value || "");
    if (depth > 5) { parent.append(document.createTextNode(text)); return; }
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^\s)]+\))/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      let node;
      if (token.startsWith("`")) node = el("code", "", token.slice(1, -1));
      else if (token.startsWith("**") || token.startsWith("__")) { node = el("strong"); appendInline(node, token.slice(2, -2), depth + 1); }
      else if (token.startsWith("*") || token.startsWith("_")) { node = el("em"); appendInline(node, token.slice(1, -1), depth + 1); }
      else {
        const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        if (link) {
          let url;
          try { const candidate = new URL(link[2]); if (["https:", "http:"].includes(candidate.protocol)) url = candidate.href; } catch (_) { /* Unsafe or invalid URL stays plain text. */ }
          if (url) { node = el("a", "", link[1]); node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer"; node.referrerPolicy = "no-referrer"; }
        }
        if (!node) node = document.createTextNode(token);
      }
      parent.append(node);
      cursor = match.index + token.length;
    }
    parent.append(document.createTextNode(text.slice(cursor)));
  }
  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
  }
  function renderMarkdown(markdown) {
    const result = el("div", "report-text");
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const isTableRule = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line || "");
    const isBlock = (line, next) => /^(#{1,6})\s|^\s*```|^\s*~~~|^\s*>|^\s*[-*+]\s|^\s*\d+[.)]\s|^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line || "") || isTableRule(next);
    let i = 0;
    while (i < lines.length) {
      let line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      const fence = line.match(/^\s*(```+|~~~+)\s*(.*)$/);
      if (fence) {
        const codeLines = []; const marker = fence[1][0]; i += 1;
        while (i < lines.length && !new RegExp(`^\\s*${marker === "`" ? "`" : "~"}{3,}\\s*$`).test(lines[i])) { codeLines.push(lines[i]); i += 1; }
        if (i < lines.length) i += 1;
        const pre = el("pre"); const code = el("code", "", codeLines.join("\n"));
        if (fence[2]) code.setAttribute("data-language", fence[2].slice(0, 30));
        pre.append(code); result.append(pre); continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) { const node = el(`h${heading[1].length}`); appendInline(node, heading[2]); result.append(node); i += 1; continue; }
      if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) { result.append(el("hr")); i += 1; continue; }
      if (i + 1 < lines.length && isTableRule(lines[i + 1])) {
        const headers = tableCells(line); const table = el("table"); const head = el("thead"); const row = el("tr");
        headers.forEach((value) => { const cell = el("th"); cell.scope = "col"; appendInline(cell, value); row.append(cell); }); head.append(row); table.append(head); i += 2;
        const body = el("tbody");
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const cells = tableCells(lines[i]); const tr = el("tr");
          headers.forEach((_, n) => { const td = el("td"); appendInline(td, cells[n] || ""); tr.append(td); }); body.append(tr); i += 1;
        }
        table.append(body); const wrap = el("div", "table-scroll"); wrap.append(table); result.append(wrap); continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
        const node = el("blockquote"); const child = renderMarkdown(quote.join("\n")); node.append(...child.childNodes); result.append(node); continue;
      }
      const listMatch = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.+)$/);
      if (listMatch) {
        const ordered = /^\d/.test(listMatch[2]); const list = el(ordered ? "ol" : "ul");
        if (ordered) list.start = parseInt(listMatch[2], 10) || 1;
        let lastItem = null;
        while (i < lines.length) {
          const match = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.+)$/);
          if (match && /^\d/.test(match[2]) === ordered) { lastItem = el("li"); appendInline(lastItem, match[3]); list.append(lastItem); i += 1; }
          else if (lastItem && /^\s{2,}\S/.test(lines[i])) { lastItem.append(document.createTextNode(" ")); appendInline(lastItem, lines[i].trim()); i += 1; }
          else break;
        }
        result.append(list); continue;
      }
      const paragraph = [line.trim()]; i += 1;
      while (i < lines.length && lines[i].trim() && !isBlock(lines[i], lines[i + 1])) { paragraph.push(lines[i].trim()); i += 1; }
      const p = el("p"); appendInline(p, paragraph.join(" ")); result.append(p);
    }
    return result;
  }

  async function refreshHistory(notify = true) {
    const refresh = $("refresh-history");
    if (notify) { refresh.disabled = true; refresh.setAttribute("aria-busy", "true"); }
    try {
      const result = await api("/api/runs");
      state.runs = Array.isArray(result.runs) ? result.runs : [];
      $("history-count").textContent = state.runs.length;
      $("history-total").textContent = state.runs.length;
      renderHistory();
      if (notify) toast("研究记录已更新。");
    } catch (error) {
      if (notify) toast(error.message);
      if (!state.runs.length) {
        clear($("history-list"));
        const box = emptyState("link", "暂时无法读取历史记录", "请确认本地服务仍在运行，稍后点击刷新记录重试。");
        $("history-list").append(box);
      }
    } finally { refresh.disabled = false; refresh.removeAttribute("aria-busy"); }
  }
  function renderHistory() {
    const target = $("history-list");
    const term = $("history-search").value.trim().toLowerCase();
    const filter = $("history-filter").value;
    const runs = state.runs.filter((run) => {
      const match = `${run.ticker || ""} ${run.date || ""} ${run.rating || ""}`.toLowerCase().includes(term);
      const status = filter === "all" || filter === "completed" && run.status === "completed" || filter === "running" && isRunning(run) || filter === "failed" && ["failed", "interrupted"].includes(run.status);
      return match && status;
    });
    clear(target);
    $("history-total").textContent = state.runs.length;
    if (!runs.length) {
      const filtered = term || filter !== "all";
      const box = emptyState(filtered ? "search" : "file", filtered ? "没有匹配的研究记录" : "你的第一份研究，将从这里开始。", filtered ? "试试其他证券代码，或调整状态筛选。" : "完成一次分析后，报告和研究过程会保存在本地，随时可以回来查看。");
      if (!filtered) box.append(button("开始第一份研究", "button-primary", newAnalysis, "plus"));
      target.append(box);
      return;
    }
    const wrap = el("div", "history-table-scroll");
    const table = el("table", "history-table");
    const head = el("thead"); const header = el("tr");
    ["研究标的", "分析日期", "研究深度", "任务状态", "创建时间", ""].forEach((text) => { const th = el("th", "", text); th.scope = "col"; header.append(th); }); head.append(header); table.append(head);
    const body = el("tbody");
    runs.forEach((run) => {
      const row = el("tr", state.historyRun?.id === run.id ? "history-selected" : "");
      const ticker = el("td"); ticker.append(el("span", "history-ticker", run.ticker));
      const date = el("td", "history-date", run.date);
      const depth = el("td", "", `${run.depth || 1} 轮 · ${(run.analysts || []).length} 位分析师`);
      const status = el("td"); status.append(el("span", `pill ${statusClass(run.status)}`, STATUS_LABELS[run.status] || run.status));
      const created = el("td", "history-date", formatDate(run.created_at));
      const action = el("td");
      const open = button("查看研究", "button-ghost", () => openHistoryRun(run.id), "arrow");
      open.setAttribute("aria-label", `查看 ${run.ticker} ${run.date} 的研究`);
      action.append(open);
      row.append(ticker, date, depth, status, created, action); body.append(row);
    });
    table.append(body); wrap.append(table); target.append(wrap);
  }
  async function openHistoryRun(id) {
    try {
      state.historyRun = await api(`/api/runs/${encodeURIComponent(id)}`);
      const target = $("history-run-container"); target.hidden = false;
      renderRun(state.historyRun, target);
      renderHistory();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { toast(error.message); }
  }
  function settingRow(label, value, mono = false) { const row = el("div", "settings-row"); row.append(el("span", "", label), el("strong", mono ? "mono" : "", safeText(value))); return row; }
  function renderSettings() {
    const root = $("settings-content"); clear(root);
    const config = state.config;
    if (!config) { root.append(emptyState("link", "正在读取研究环境", "连接本地服务后，模型和数据源配置将显示在这里。")); return; }
    const grid = el("div", "settings-grid");
    const model = el("section", "card settings-card");
    const modelTitle = el("h2"); modelTitle.append(icon("cpu"), document.createTextNode("模型与推理")); model.append(modelTitle);
    model.append(settingRow("模型提供方", PROVIDERS[config.provider] || config.provider), settingRow("快速模型", config.quick_model, true), settingRow("深度模型", config.deep_model, true), settingRow("快速模型来源", PROVIDERS[config.quick_provider || config.provider] || config.quick_provider || config.provider), settingRow("深度模型来源", PROVIDERS[config.deep_provider || config.provider] || config.deep_provider || config.provider));
    model.append(el("p", "settings-description", "快速模型负责分析与辩论，深度模型负责研究和组合经理的最终权衡。"));
    const workspace = el("section", "card settings-card");
    const workspaceTitle = el("h2"); workspaceTitle.append(icon("sliders"), document.createTextNode("工作空间")); workspace.append(workspaceTitle);
    workspace.append(settingRow("应用版本", `ThesisFlow ${safeText(config.version)}`, true), settingRow("运行方式", "本地 Web 工作台"), settingRow("默认报告语言", config.output_language === "English" ? "English" : "简体中文"), settingRow("当前服务日期", config.today, true), settingRow("模型凭据", config.model_ready ? "已配置" : "尚未配置"));
    workspace.append(el("p", "settings-description", "模型与数据配置来自本机环境。研究参数可在每次启动分析前单独选择。"));
    const sources = el("section", "card settings-card wide");
    const sourceTitle = el("h2"); sourceTitle.append(icon("layers"), document.createTextNode("数据来源与服务")); sources.append(sourceTitle);
    const sourceList = el("div", "source-list");
    const descriptions = { model: "当前模型身份或 API 凭据", yfinance: "行情、公司资料与新闻", sec_edgar: "美国上市公司公开披露", fred: "宏观经济指标 · 可选", alpha_vantage: "补充行情及基本面 · 可选", jev: "社交帖相关性与立场筛选 · 可选" };
    (Array.isArray(config.services) ? config.services : []).forEach((source) => {
      const item = el("div", "source-item"); const label = el("div"); label.append(el("strong", "", source.name || source.id), el("small", "", descriptions[source.id] || "外部研究服务"));
      const keyless = source.status === "keyless";
      const configured = source.configured === true;
      item.append(label, el("span", `pill ${configured ? "pill-green" : "pill-neutral"}`, keyless ? "无需凭据" : configured ? "已配置" : source.status === "missing" ? "待配置" : "未配置 · 可选"));
      sourceList.append(item);
    });
    sources.append(sourceList, el("p", "settings-description", "这里显示的是配置状态。实际数据覆盖和服务可用性，以研究过程中返回的结果为准。"));
    grid.append(model, workspace, sources); root.append(grid);
    const note = el("div", "settings-local-note"); note.append(icon("shield"), el("p", "", "凭据不会显示在此页面。若需要修改服务，请更新项目本地环境配置并重启 Web 服务；ChatGPT 订阅模式需要先在本机完成登录。")); root.append(note);
  }

  function schedulePoll(delay) { clearTimeout(state.timer); state.timer = setTimeout(poll, delay); }
  async function poll() {
    if (state.refreshing) { schedulePoll(1500); return; }
    state.refreshing = true;
    try {
      const ids = new Set();
      if (isRunning(state.activeRun)) ids.add(state.activeRun.id);
      if (isRunning(state.historyRun)) ids.add(state.historyRun.id);
      if (!ids.size || !state.connected) {
        const config = await api("/api/config");
        applyConfig(config);
        if (config.active_run_id && !isRunning(state.activeRun)) ids.add(config.active_run_id);
      }
      await Promise.all([...ids].map(async (id) => {
        const run = await api(`/api/runs/${encodeURIComponent(id)}`);
        const wasRunning = state.activeRun?.id === id && isRunning(state.activeRun);
        if (state.activeRun?.id === id || !state.activeRun || state.config?.active_run_id === id && !isRunning(state.activeRun)) { state.activeRun = run; rememberRun(id); renderActiveRun(); }
        if (state.historyRun?.id === id) { state.historyRun = run; renderRun(run, $("history-run-container")); }
        upsertRun(run);
        if (wasRunning && TERMINAL.has(run.status)) { toast(run.status === "completed" ? `${run.ticker} 研究已完成，报告已保存。` : `${run.ticker} 研究已结束，请查看运行详情。`); if (state.page === "history") renderHistory(); }
      }));
      state.failures = 0;
      if (state.page === "settings") renderSettings();
      if (state.page === "history" && !ids.size) await refreshHistory(false);
    } catch (_) { state.failures += 1; }
    finally {
      state.refreshing = false;
      const running = isRunning(state.activeRun) || isRunning(state.historyRun);
      schedulePoll(state.failures ? Math.min(15000, 2500 * state.failures) : running ? 1700 : 10000);
    }
  }
  async function initialize() {
    $("analysis-date").value = "";
    $("start-analysis").disabled = true;
    updateMetrics();
    setPage(location.hash.slice(1) || "workspace");
    const loading = el("div", "history-loading"); loading.append(el("span", "spinner"), el("span", "", "正在读取研究记录")); $("history-list").replaceChildren(loading);
    try {
      const [config, result] = await Promise.all([api("/api/config"), api("/api/runs")]);
      applyConfig(config);
      state.runs = Array.isArray(result.runs) ? result.runs : [];
      $("history-count").textContent = state.runs.length;
      $("history-total").textContent = state.runs.length;
      const id = config.active_run_id || rememberedRun();
      const saved = state.runs.find((run) => run.id === id);
      if (saved) { state.activeRun = saved; renderActiveRun(); }
      else if (config.active_run_id) { state.activeRun = await api(`/api/runs/${encodeURIComponent(config.active_run_id)}`); renderActiveRun(); }
      renderHistory();
      if (state.page === "settings") renderSettings();
    } catch (error) {
      showMessage("暂时无法连接本地研究服务。请保持启动终端运行，页面会自动尝试重新连接。");
      $("analysis-date").value = localDate();
      setConnected(false);
      renderHistory();
    } finally { schedulePoll(isRunning(state.activeRun) ? 1500 : 7000); }
  }

  $("analysis-form").addEventListener("submit", submitAnalysis);
  $("new-analysis").addEventListener("click", newAnalysis);
  $("refresh-history").addEventListener("click", () => refreshHistory(true));
  $("history-search").addEventListener("input", renderHistory);
  $("history-filter").addEventListener("change", renderHistory);
  $("analysis-form").addEventListener("change", updateMetrics);
  $("ticker").addEventListener("input", () => { document.querySelectorAll("[data-ticker]").forEach((chip) => chip.classList.toggle("selected", chip.dataset.ticker === $("ticker").value.toUpperCase())); });
  document.querySelectorAll("[data-ticker]").forEach((chip) => chip.addEventListener("click", () => { $("ticker").value = chip.dataset.ticker; $("ticker").dispatchEvent(new Event("input")); $("ticker").focus(); }));
  $("menu-toggle").addEventListener("click", () => { const open = !$("sidebar").classList.contains("open"); $("sidebar").classList.toggle("open", open); $("sidebar-scrim").hidden = !open; $("menu-toggle").setAttribute("aria-expanded", String(open)); syncSidebarAccessibility(); if (open) $("sidebar").querySelector(".nav-item.active")?.focus(); });
  $("sidebar-scrim").addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && $("sidebar").classList.contains("open")) { closeSidebar(); $("menu-toggle").focus(); } });
  window.addEventListener("hashchange", () => { setPage(location.hash.slice(1)); window.scrollTo({ top: 0 }); });
  window.addEventListener("resize", syncSidebarAccessibility);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) schedulePoll(100); });
  initialize();
})();
