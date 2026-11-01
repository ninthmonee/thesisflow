/** Live market views share the Open Design markup and use the authenticated host API. */
export function installMarket(ctx) {
  const { esc, icon, button, badge, emptyState, heading, tickerTile } = ctx;
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const number = (value, digits = 2) =>
    finite(value)
      ? value.toLocaleString("en-US", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        })
      : "—";
  const percent = (value, suffix = "%") =>
    finite(value) ? `${value > 0 ? "+" : ""}${number(value)}${suffix}` : "—";
  const color = (value) => (!finite(value) ? "quiet" : value >= 0 ? "positive" : "negative");
  const compact = (value) =>
    finite(value)
      ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(
          value,
        )
      : "—";
  const getStock = (symbol = ctx.ui.symbol) =>
    ctx.securities.find((item) => item.symbol === symbol);
  const cache = new Map();
  let searchTimer;
  let searchVersion = 0;
  let removed;
  const pending = (key) => cache.get(key);
  const state = () => ctx.state;
  const sourceText = (meta) =>
    meta
      ? `${meta.source || "来源未知"} / ${meta.feed || "feed 未提供"} · ${meta.as_of || "时点缺失"} · ${meta.quality || "资格未知"}`
      : "行情尚未取得 · 数值保持缺失";
  const errorBlock = (key, item) =>
    `<div class="note-box amber">${esc(item?.error || "正在读取来源数据…")}${item?.error ? button("重新读取", "live-retry", { small: true, attributes: `data-key="${esc(key)}"` }) : ""}</div>`;
  async function load(key, method, payload) {
    if (cache.has(key)) return;
    const page = ctx.ui.page;
    cache.set(key, { loading: true });
    try {
      cache.set(key, { data: await ctx.request(method, payload) });
    } catch (error) {
      cache.set(key, { error: error.message || "来源请求未完成" });
    }
    if (ctx.ui.page !== page || (key.startsWith("bars:") && key !== barKey())) return;
    const active = document.activeElement;
    const input =
      active?.id && ["live-watch-query", "stock-note"].includes(active.id) ? active : null;
    const selection = input ? [input.selectionStart, input.selectionEnd] : null;
    ctx.render();
    if (input) {
      const replacement = document.getElementById(input.id);
      replacement?.focus({ preventScroll: true });
      if (selection) replacement?.setSelectionRange(...selection);
    }
  }
  async function mutate(method, payload, message) {
    try {
      await ctx.request(method, payload);
      await ctx.refresh();
      ctx.toast(message);
    } catch (error) {
      ctx.toast(error.message || "保存失败，请刷新后重试");
      throw error;
    }
  }
  function visibleStocks() {
    const query = (ctx.ui.watchQuery || "").trim().toLowerCase();
    return state()
      .watchlist.map((symbol) => getStock(symbol))
      .filter(
        (stock) =>
          stock &&
          (ctx.ui.group === "全部" || !ctx.ui.group || stock.groups?.includes(ctx.ui.group)) &&
          (!ctx.ui.favoritesOnly || state().favorites.includes(stock.symbol)) &&
          `${stock.symbol} ${stock.name} ${state().notes[stock.symbol] || ""}`
            .toLowerCase()
            .includes(query),
      );
  }
  const favoriteButton = (stock) =>
    `<button type="button" class="icon-button ${state().favorites.includes(stock.symbol) ? "favorited" : ""}" data-action="favorite" data-symbol="${esc(stock.symbol)}" aria-label="收藏 ${esc(stock.symbol)}" aria-pressed="${state().favorites.includes(stock.symbol)}">${icon("star")}</button>`;
  function rail(rows) {
    return rows.length
      ? rows
          .map(
            (stock) =>
              `<button type="button" class="watch-item ${stock.symbol === ctx.ui.symbol ? "active" : ""}" data-action="select-stock" data-symbol="${esc(stock.symbol)}"><span class="od-field"><strong>${esc(stock.symbol)}</strong><small>${esc(stock.name)}</small></span><span class="watch-price"><span>${number(stock.price)}</span><span class="${color(stock.change)}">${percent(stock.change)}</span></span></button>`,
          )
          .join("")
      : emptyState("没有匹配标的", "清除筛选或添加证券。");
  }
  function table(rows) {
    return `<section class="panel">${rows.length ? `<table class="data-table responsive-table"><thead><tr><th>证券</th><th>日收盘</th><th>日涨跌</th><th>技术观察</th><th>我的备注</th><th></th></tr></thead><tbody>${rows.map((stock) => `<tr><td><button class="table-link od-row" type="button" data-action="select-stock" data-symbol="${esc(stock.symbol)}">${tickerTile(stock, true)}<span class="od-field"><strong class="mono">${esc(stock.symbol)}</strong><small>${esc(stock.name)}</small></span></button></td><td class="number-cell" data-label="完整日收盘">${number(stock.price)}</td><td class="number-cell ${color(stock.change)}" data-label="日涨跌">${percent(stock.change)}</td><td class="mobile-extra">${badge(esc(stock.theme))}</td><td class="mobile-extra" style="max-width:220px"><button class="table-link od-clamp-2" type="button" data-action="edit-note" data-symbol="${esc(stock.symbol)}">${esc(state().notes[stock.symbol] || "添加研究备注")}</button></td><td class="row-end">${favoriteButton(stock)}<button type="button" class="icon-button" data-action="select-stock" data-symbol="${esc(stock.symbol)}" aria-label="查看 ${esc(stock.symbol)} 图表">${icon("arrow")}</button></td></tr>`).join("")}</tbody></table>` : emptyState("没有匹配的证券", "清除关键词、切换分组，或添加关注标的。", button("清除筛选", "clear-watch"))}</section>`;
  }
  const barKey = () => `bars:${ctx.ui.symbol}:${ctx.ui.period}:${ctx.ui.interval}`;
  function priceChart(stock) {
    const key = barKey(),
      entry = pending(key);
    if (!entry?.data) return errorBlock(key, entry);
    const bars = entry.data.bars || [];
    const valid = bars.filter((bar) => [bar.open, bar.high, bar.low, bar.close].every(finite));
    if (!valid.length)
      return emptyState(
        "暂无可绘制的行情",
        entry.data.meta?.warning || "当前来源未返回完整 OHLC 数据。",
        button("重新读取", "live-retry", { attributes: `data-key="${esc(key)}"` }),
      );
    const minimum = Math.min(...valid.map((bar) => bar.low)),
      maximum = Math.max(...valid.map((bar) => bar.high));
    const range = Math.max(maximum - minimum, maximum * 0.02, 0.01),
      low = minimum - range * 0.08,
      high = maximum + range * 0.08;
    const y = (price) => 20 + ((high - price) / (high - low)) * 260;
    const step = 665 / Math.max(1, bars.length),
      x = (index) => 12 + step * (index + 0.5);
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = high - ((high - low) / 4) * index,
        position = y(value);
      return `<line x1="0" x2="690" y1="${position}" y2="${position}" stroke="#253340" stroke-dasharray="3 5"/><text x="708" y="${position + 4}" fill="#8b9fb0" font-size="11" font-family="monospace">${number(value)}</text>`;
    }).join("");
    const candles = bars
      .map((bar, index) => {
        if (![bar.open, bar.high, bar.low, bar.close].every(finite)) return "";
        const fill = bar.close >= bar.open ? "var(--positive)" : "var(--negative)",
          width = Math.max(1, step * 0.52);
        return `<g><line x1="${x(index)}" x2="${x(index)}" y1="${y(bar.high)}" y2="${y(bar.low)}" stroke="${fill}"/><rect x="${x(index) - width / 2}" y="${Math.min(y(bar.open), y(bar.close))}" width="${width}" height="${Math.max(1.6, Math.abs(y(bar.open) - y(bar.close)))}" fill="${fill}"/></g>`;
      })
      .join("");
    let segment = "";
    const segments = [];
    bars.forEach((bar, index) => {
      if (finite(bar.close)) segment += `${x(index)},${y(bar.close)} `;
      else if (segment) {
        segments.push(segment);
        segment = "";
      }
    });
    if (segment) segments.push(segment);
    const lines = segments
      .map(
        (points) =>
          `<polyline points="${points}" fill="none" stroke="var(--accent)" stroke-width="2"/>`,
      )
      .join("");
    const average = ctx.ui.movingAverage
      ? bars
          .map((bar, index) => {
            if (index < 6) return "";
            const values = bars.slice(index - 6, index + 1).map((item) => item.close);
            if (!values.every(finite)) return "";
            const previous = bars.slice(index - 7, index).map((item) => item.close);
            if (previous.length !== 7 || !previous.every(finite)) return "";
            return `<line x1="${x(index - 1)}" x2="${x(index)}" y1="${y(previous.reduce((a, b) => a + b, 0) / 7)}" y2="${y(values.reduce((a, b) => a + b, 0) / 7)}" stroke="#c4b677" stroke-width="1.3"/>`;
          })
          .join("")
      : "";
    const maxVolume = Math.max(...bars.map((bar) => (finite(bar.volume) ? bar.volume : 0)), 1);
    const volumes = bars
      .map((bar, index) =>
        finite(bar.volume)
          ? `<rect x="${x(index) - step * 0.26}" y="${344 - (bar.volume / maxVolume) * 52}" width="${Math.max(1, step * 0.52)}" height="${(bar.volume / maxVolume) * 52}" fill="${bar.close >= bar.open ? "var(--positive)" : "var(--negative)"}" opacity=".35"/>`
          : "",
      )
      .join("");
    const labels = [0, 0.25, 0.5, 0.75, 1]
      .map((part) => {
        const index = Math.round(part * (bars.length - 1)),
          label = (bars[index]?.date || "").slice(5, 10);
        return `<text x="${12 + part * 650}" y="370" text-anchor="${part === 0 ? "start" : part === 1 ? "end" : "middle"}" fill="#8b9fb0" font-size="11" font-family="monospace">${esc(label)}</text>`;
      })
      .join("");
    return `<svg class="price-chart" width="760" height="380" viewBox="0 0 760 380" role="img" aria-label="${esc(stock.symbol)} ${esc(ctx.ui.period)} 价格与成交量"><title>${esc(sourceText(entry.data.meta))}</title>${grid}${ctx.ui.chartType === "line" ? lines : candles}${average}<line x1="0" x2="690" y1="286" y2="286" stroke="#2a3742"/>${volumes}${labels}</svg>`;
  }
  function watchView() {
    const rows = visibleStocks(),
      stock = getStock();
    const groups = state().groups || [];
    const run = state().runs.find((item) => item.symbol === stock?.symbol);
    const plan = state().plans.find(
      (item) => item.symbol === stock?.symbol && item.status !== "archived",
    );
    const bars = pending(barKey())?.data,
      lastBar = bars?.bars?.filter((bar) => finite(bar.close)).at(-1);
    const note = stock
      ? (ctx.ui.noteDrafts[stock.symbol] ?? state().notes[stock.symbol] ?? "")
      : "";
    const context = stock
      ? `<aside class="panel context-panel"><section class="context-section"><h3>最近研究</h3>${run ? `${badge(run.status === "completed" ? "已完成" : run.status === "failed" ? "执行失败" : "处理中")}<p>${esc(run.objective)}</p><small>${esc(run.date)}</small>${button("查看研究", "open-run", { glyph: "arrow", attributes: `data-id="${esc(run.id)}"` })}` : "<p>这只证券尚无研究记录。</p>"}${stock.type === "stock" ? button("研究这家公司", "new-research", { ghost: true, attributes: `data-symbol="${esc(stock.symbol)}"` }) : ""}</section><section class="context-section"><h3>我的决策计划</h3>${plan ? `<p>${esc(plan.title)}</p>${button("打开计划", "edit-plan", { glyph: "arrow", attributes: `data-id="${esc(plan.id)}"` })}` : `<p>先写下关键假设，再用证据完善判断。</p>${button("建立计划", "new-plan", { glyph: "plus", attributes: `data-symbol="${esc(stock.symbol)}"` })}`}</section><section class="context-section"><h3>我的备注</h3><label for="stock-note" class="sr-only">证券备注</label><textarea id="stock-note" class="textarea" rows="3" maxlength="2000" placeholder="记录下一步要核实的问题">${esc(note)}</textarea>${button("保存备注", "save-note", { small: true })}<div style="margin-top:12px">${state().watchlist.includes(stock.symbol) ? button("移除关注", "remove-watch", { small: true, ghost: true, attributes: `data-symbol="${esc(stock.symbol)}"` }) : button("加入自选", "add-watch", { small: true, attributes: `data-symbol="${esc(stock.symbol)}"` })}</div></section></aside>`
      : "";
    return `<div class="page">${heading("自选行情", "从价格变化，回到值得验证的问题。", button("添加证券", "add-security", { primary: true, glyph: "plus" }), "WATCHLIST / MARKET VIEW")}<div class="filterbar"><div class="filter-group">${[{ id: "全部", name: "全部" }, ...groups].map((group) => `<button type="button" class="filter-chip ${(ctx.ui.group || "全部") === group.id ? "active" : ""}" data-action="watch-group" data-group="${esc(group.id)}">${esc(group.name)}</button>`).join("")}<button type="button" class="filter-chip ${ctx.ui.favoritesOnly ? "active" : ""}" data-action="favorites-only">${icon("star")}收藏</button></div><div class="segmented"><button type="button" class="${ctx.ui.watchMode === "list" ? "active" : ""}" data-action="watch-mode" data-mode="list">${icon("list")}跟踪列表</button><button type="button" class="${ctx.ui.watchMode !== "list" ? "active" : ""}" data-action="watch-mode" data-mode="workspace">${icon("layout")}个股工作区</button></div></div><div class="filterbar"><div class="input-search compact">${icon("search")}<label class="sr-only" for="live-watch-query">搜索代码、公司或备注</label><input id="live-watch-query" class="input" value="${esc(ctx.ui.watchQuery || "")}" placeholder="搜索代码、公司或备注"></div><div class="small-meta">${rows.length} 个关注标的 · 最近完整日收盘</div></div>${state().marketWarning ? `<div class="note-box amber">${esc(state().marketWarning)}</div>` : ""}<div id="watch-content">${
      ctx.ui.watchMode === "list"
        ? table(rows)
        : stock
          ? `<div class="stock-workspace"><section class="panel watch-rail"><header class="panel-head"><h2>关注标的</h2><small>${rows.length}</small></header><div>${rail(rows)}</div></section><div class="stack"><div class="field mobile-stock-select"><label for="mobile-stock">当前证券</label><select id="mobile-stock" class="select">${ctx.securities.map((item) => `<option value="${esc(item.symbol)}" ${item.symbol === stock.symbol ? "selected" : ""}>${esc(item.symbol)} · ${esc(item.name)}</option>`).join("")}</select></div><section class="panel quote-panel"><div class="quote-header"><div class="od-field"><div class="od-row"><h2>${esc(stock.symbol)}</h2>${badge(esc(stock.exchange || stock.type))}</div><small>${esc(stock.name)}</small></div><div class="od-row">${favoriteButton(stock)}<button type="button" class="icon-button" data-action="stock-info" data-symbol="${esc(stock.symbol)}" aria-label="证券与数据说明">${icon("info")}</button></div></div><div class="quote-price"><strong>${number(stock.price)}<span style="font-size:14px;color:var(--quiet);margin-left:8px">${esc(stock.currency || "")}</span></strong><span class="${color(stock.change)}">${percent(stock.change)}</span></div><p class="quote-asof">完整日收盘 · ${esc(stock.asOf || "时点缺失")} · 非实时行情</p><div class="chart-controls"><div class="period-buttons">${["1周", "1月", "3月", "1年"].map((period) => `<button type="button" class="period-button ${ctx.ui.period === period ? "active" : ""}" data-action="chart-period" data-period="${period}">${period}</button>`).join("")}</div><div class="segmented"><button type="button" class="${ctx.ui.interval === "1D" ? "active" : ""}" data-action="chart-interval" data-interval="1D">日 K</button><button type="button" class="${ctx.ui.interval === "1W" ? "active" : ""}" data-action="chart-interval" data-interval="1W">周 K</button><button type="button" data-action="chart-type" aria-label="切换图表类型">${icon("chart")}</button></div></div><div class="chart-topline"><span>OHLC · 来源历史价格</span><button type="button" class="chart-toggle" data-action="toggle-ma" aria-pressed="${ctx.ui.movingAverage}">MA 7</button><span class="mono muted">VOL</span></div><div class="chart-root">${priceChart(stock)}</div><div class="chart-caption"><span>${esc(sourceText(bars?.meta))}</span><span>${esc(bars?.adjustment_label || "")}</span></div><div class="quote-stats">${[
              ["开盘", lastBar?.open],
              ["最高", lastBar?.high],
              ["最低", lastBar?.low],
              ["成交量", lastBar?.volume],
            ]
              .map(
                ([label, value]) =>
                  `<div class="od-stat"><small>${label}</small><strong>${label === "成交量" ? compact(value) : number(value)}</strong></div>`,
              )
              .join(
                "",
              )}</div></section><div class="note-box"><span class="accent">研究提示</span>　${stock.type === "etf" ? "ETF 用于行情与行业观察；从成分公司开始个股基本面研究。" : "将价格变化与公司质量、估值和失效条件放在一起看。"}</div></div>${context}</div>`
          : emptyState(
              "建立你的关注列表",
              "添加第一只证券，开始观察和研究。",
              button("添加证券", "add-security", { primary: true }),
            )
    }</div><p class="small-meta">数值由本机行情服务返回；未取得的数据保持缺失。${button("完整行情与分组管理", "live-open", { small: true, ghost: true, attributes: 'data-path="/stocks"' })}</p></div>`;
  }
  function marketStrip() {
    const snapshot = pending("pulse:1D")?.data;
    if (!snapshot)
      return `<div class="market-strip">${["SPY", "QQQ", "IWM", "VIX"].map((symbol) => `<div class="market-cell"><div class="od-field"><span class="market-cell-title">${symbol}</span><strong class="market-price">—</strong><small class="quiet">${pending("pulse:1D")?.error ? "来源暂不可用" : "正在读取"}</small></div></div>`).join("")}</div>`;
    return `<div class="market-strip">${snapshot.benchmarks
      .filter((item) => ["SPY", "QQQ", "IWM", "^VIX", "VIX"].includes(item.symbol))
      .slice(0, 4)
      .map(
        (item) =>
          `<button type="button" class="market-cell" data-action="live-benchmark" data-symbol="${esc(item.symbol)}" title="${esc(snapshot.source || "来源未知")} · ${esc(snapshot.as_of || "时点缺失")}"><span class="od-field"><span class="market-cell-title">${esc(item.symbol)}<small>${esc(item.name)}</small></span><strong class="market-price">${number(item.price)}</strong><small class="market-change ${color(item.return_percent)}">${percent(item.return_percent)}</small></span></button>`,
      )
      .join(
        "",
      )}</div><p class="small-meta" style="margin-top:-12px">${esc(snapshot.source || "来源未知")} · 截至 ${esc(snapshot.as_of || "时点缺失")} · ${esc(snapshot.quality || "资格未知")}</p>`;
  }
  function heatmap(snapshot) {
    return `<div class="heat-grid">${snapshot.sectors.map((item) => `<button type="button" class="heat-cell ${item.return_percent < 0 ? "loss" : !finite(item.return_percent) || item.return_percent < 0.5 ? "soft" : ""}" data-action="sector-detail" data-symbol="${esc(item.symbol)}"><strong>${esc(item.name)}</strong><span>${percent(item.return_percent)}</span><small>${esc(item.symbol)} · ETF 代理</small></button>`).join("")}</div>`;
  }
  function marketView() {
    const tab = ctx.ui.marketTab,
      win = ctx.ui.marketWindow,
      key = `pulse:${win}`,
      entry = pending(key),
      snapshot = entry?.data;
    let body;
    if (tab === "themes") body = themesView();
    else if (tab === "professional")
      body = `<div class="tabs">${[
        ["flows", "ETF 份额流"],
        ["options", "期权样本"],
        ["news", "新闻证据"],
      ]
        .map(
          ([id, label]) =>
            `<button class="tab ${ctx.ui.professionalTab === id ? "active" : ""}" type="button" data-action="professional-tab" data-tab="${id}">${label}</button>`,
        )
        .join(
          "",
        )}</div><section class="panel"><header class="panel-head"><h2>专业数据与来源</h2>${badge("按需读取")}</header><div class="panel-body stack-sm"><p class="muted">查看已连接账号的数据覆盖、截止时间、统计口径和来源证据。</p>${button("打开专业数据", "live-open", { primary: true, attributes: `data-path="/insights/${esc(ctx.ui.professionalTab || "flows")}"` })}</div></section>`;
    else if (!snapshot) body = errorBlock(key, entry);
    else if (tab === "sectors")
      body = `<div class="market-main-grid"><section class="panel"><header class="panel-head"><h2>行业 ETF 收益</h2>${badge(win)}</header><div class="panel-body">${heatmap(snapshot)}</div></section><section class="panel"><header class="panel-head"><h2>相对 SPY 强弱</h2><small>百分点 pp</small></header><div class="relative-chart">${snapshot.sectors.map((item) => `<div class="relative-row"><span class="muted" style="font-size:12px">${esc(item.name)}</span><div class="bar-track"><div class="bar-fill ${item.relative_return_pp < 0 ? "red" : ""}" style="width:${finite(item.relative_return_pp) ? Math.min(100, Math.abs(item.relative_return_pp) * 15) : 0}%"></div></div><strong class="${color(item.relative_return_pp)}">${percent(item.relative_return_pp, " pp")}</strong></div>`).join("")}</div></section></div><section class="panel"><header class="panel-head"><h2>行业观察明细</h2><small>成交活跃度与资金净流入分别定义</small></header><table class="data-table responsive-table"><thead><tr><th>行业 / ETF</th><th>价格收益</th><th>相对 SPY</th><th>日 RVOL</th></tr></thead><tbody>${snapshot.sectors.map((item) => `<tr><td><button class="table-link od-field" type="button" data-action="sector-detail" data-symbol="${esc(item.symbol)}"><strong>${esc(item.name)}</strong><small>${esc(item.symbol)}</small></button></td><td class="number-cell ${color(item.return_percent)}">${percent(item.return_percent)}</td><td class="number-cell ${color(item.relative_return_pp)}">${percent(item.relative_return_pp, " pp")}</td><td class="number-cell mobile-extra">${finite(item.daily_rvol) ? `${number(item.daily_rvol)}×` : "—"}</td></tr>`).join("")}</tbody></table></section>`;
    else {
      const breadth = snapshot.breadth,
        denominator = Math.max(1, breadth.available_count);
      body = `${win === "1D" ? marketStrip() : `<div class="market-strip">${snapshot.benchmarks.map((item) => `<div class="market-cell"><span class="od-field"><strong>${esc(item.symbol)}</strong><span class="market-price">${number(item.price)}</span><small class="${color(item.return_percent)}">${percent(item.return_percent)}</small></span></div>`).join("")}</div>`}<div class="market-main-grid"><section class="panel"><header class="panel-head"><h2>行业表现</h2><button type="button" class="text-button" data-action="market-tab" data-tab="sectors">行业轮动 ${icon("arrow")}</button></header><div class="panel-body">${heatmap(snapshot)}</div><footer class="panel-foot">色块等面积 · ${esc(win)} 价格收益 · 行业 ETF 代理</footer></section><div class="stack"><section class="panel"><header class="panel-head"><h2>观察样本广度</h2>${badge(`${breadth.available_count}/${breadth.requested_count} 可用`)}</header><div class="panel-body"><div class="breadth-bars">${[
        ["上涨", breadth.advancers, ""],
        ["下跌", breadth.decliners, "red"],
        ["持平", breadth.unchanged, "neutral"],
      ]
        .map(
          ([label, value, fill]) =>
            `<div class="breadth-row"><span class="muted">${label}</span><div class="bar-track"><div class="bar-fill ${fill}" style="width:${finite(value) ? (value / denominator) * 100 : 0}%"></div></div><strong class="mono">${finite(value) ? value : "—"}</strong></div>`,
        )
        .join(
          "",
        )}</div><p class="quiet" style="font-size:12px;margin-top:24px">${esc(breadth.scope_notice || breadth.universe_name)}</p></div></section><section class="panel"><header class="panel-head"><h2>关注与分歧</h2>${icon("compass")}</header><div class="panel-body">${snapshot.observed_divergences.length ? snapshot.observed_divergences.map((item) => `<div class="insight-line"><p>${esc(item.text || item.description || item.label || "当前数据存在分歧，请检查证据。")}</p></div>`).join("") : '<p class="muted">当前快照没有形成可核实的分歧观察。</p>'}<div class="insight-line">${button("市场简报与完整证据", "live-open", { small: true, attributes: 'data-path="/market"' })}</div></div></section></div></div>`;
    }
    return `<div class="page">${heading("市场发现", "先理解市场背景，再找到值得研究的公司。", button("附加到研究", "attach-market", { glyph: "plus" }), "MARKET DISCOVERY")}<div class="filterbar"><div class="tabs">${[
      ["overview", "市场概览"],
      ["sectors", "行业轮动"],
      ["themes", "主题产业链"],
      ["professional", "专业数据"],
    ]
      .map(
        ([id, label]) =>
          `<button type="button" class="tab ${tab === id ? "active" : ""}" data-action="market-tab" data-tab="${id}">${label}</button>`,
      )
      .join(
        "",
      )}</div><div class="segmented">${["1D", "5D", "1M"].map((value) => `<button type="button" class="${win === value ? "active" : ""}" data-action="market-window" data-window="${value}">${value}</button>`).join("")}</div></div>${body}${snapshot && tab !== "themes" && tab !== "professional" ? `<p class="small-meta">${esc(snapshot.source || "来源未知")} / ${esc(snapshot.feed || "feed 未提供")} · 截至 ${esc(snapshot.as_of || "缺失")} · ${esc(snapshot.quality)}${snapshot.warnings?.length ? ` · ${snapshot.warnings.map(esc).join("；")}` : ""}</p>` : ""}</div>`;
  }
  function themesView() {
    const listing = pending("themes"),
      themes = listing?.data?.items || [];
    if (!listing?.data) return errorBlock("themes", listing);
    if (!themes.length)
      return emptyState(
        "暂无主题",
        "在主题管理中创建观察方向。",
        button("管理主题", "live-open", { attributes: 'data-path="/themes"' }),
      );
    const selected = themes.find((item) => item.id === ctx.ui.theme) || themes[0],
      key = `theme:${selected.id}:${ctx.ui.marketWindow}`,
      entry = pending(key);
    const theme = entry?.data?.theme || selected,
      analytics = entry?.data?.analytics;
    const roles = new Map();
    for (const member of theme.members || []) {
      const role = member.role || "观察成员";
      roles.set(role, [...(roles.get(role) || []), member]);
    }
    return `<div class="theme-layout"><aside class="panel"><header class="panel-head"><h2>主题目录</h2><small>${themes.length}</small></header><div class="theme-catalog">${themes.map((item) => `<button class="theme-button ${selected.id === item.id ? "active" : ""}" type="button" data-action="select-theme" data-theme="${esc(item.id)}"><span class="od-field"><strong>${esc(item.name)}</strong><small>${item.members?.length || 0} 个观察成员 · v${item.version}</small></span>${icon("chevron")}</button>`).join("")}</div></aside><div class="stack"><section class="panel"><header class="panel-head"><div class="od-field"><h2>${esc(theme.name)}</h2><small>v${theme.version} · ${esc(theme.effective_from)}</small></div>${button("带入研究", "attach-theme", { small: true, glyph: "plus" })}</header><div class="panel-body stack-sm"><p class="muted" style="font-size:14px">${esc(theme.description || "尚无主题说明")}</p><div class="chain-flow">${[...roles].map(([role, members]) => `<div class="chain-node"><h3>${esc(role)}</h3><div class="od-cluster">${members.map((member) => `<button type="button" data-action="live-open-symbol" data-symbol="${esc(member.symbol)}">${esc(member.symbol)} ${icon("arrow")}</button>`).join("")}</div><small>关系证据独立于行情资格</small></div>`).join("")}</div></div></section><section class="panel"><header class="panel-head"><h2>成员观察</h2>${badge(ctx.ui.marketWindow)}</header>${analytics ? `<table class="data-table responsive-table"><thead><tr><th>证券</th><th>价格收益</th><th>环节 / 证据资格</th><th></th></tr></thead><tbody>${analytics.members.map((member) => `<tr><td><button type="button" class="table-link od-field" data-action="live-open-symbol" data-symbol="${esc(member.symbol)}"><strong>${esc(member.symbol)}</strong><small>${esc(member.name)}</small></button></td><td class="number-cell ${color(member.return_percent)}">${percent(member.return_percent)}</td><td class="mobile-extra muted">${esc(member.role)} · ${esc(member.evidence_quality)}</td><td class="row-end">${button("研究", "live-research-symbol", { small: true, attributes: `data-symbol="${esc(member.symbol)}"` })}</td></tr>`).join("")}</tbody></table><footer class="panel-foot">${esc(analytics.source || "来源未知")} / ${esc(analytics.feed || "feed 未提供")} · ${esc(analytics.window_end || "时点缺失")} · ${esc(analytics.quality)}</footer>` : `<div class="panel-body">${errorBlock(key, entry)}</div>`}</section><div>${button("版本与关系证据", "live-open", { attributes: `data-path="/themes?theme=${encodeURIComponent(theme.id)}"` })}</div></div></div>`;
  }
  function addDialog() {
    ctx.showDialog(
      "添加关注证券",
      `<div class="input-search">${icon("search")}<label class="sr-only" for="live-add-query">搜索证券</label><input id="live-add-query" class="input" placeholder="输入代码或公司名称" maxlength="80" autocomplete="off"></div><div id="live-add-results" class="stack-sm" style="margin-top:20px"><p class="muted">输入关键词，从证券目录中搜索。</p></div>`,
      button("完成", "close-dialog"),
    );
  }
  async function ensureSymbol(symbol) {
    if (getStock(symbol)) return;
    const result = await ctx.request("market.search", { query: symbol });
    const found = result.items.find((item) => item.symbol === symbol);
    if (!found) throw new Error("证券目录未返回此证券");
    ctx.securities.push(found);
  }
  ctx.registerView("watch", watchView);
  ctx.registerView("priceChart", priceChart);
  ctx.registerView("market", marketView);
  ctx.registerView("marketStrip", marketStrip);
  ctx.registerAction("live-retry", (data) => {
    cache.delete(data.key);
    ctx.render();
  });
  ctx.registerAction("live-open", (data) => ctx.request("host.open", { path: data.path }));
  ctx.registerAction("live-open-symbol", async (data) => {
    await ensureSymbol(data.symbol);
    ctx.navigate("watch", { symbol: data.symbol, watchMode: "workspace" });
  });
  ctx.registerAction("live-research-symbol", async (data) => {
    await ensureSymbol(data.symbol);
    ctx.closeDialog(false);
    ctx.navigate("researchNew", { symbol: data.symbol, researchDraft: null, researchStep: 1 });
  });
  ctx.registerAction("live-benchmark", (data) =>
    ctx.request("host.open", { path: `/stocks?symbol=${encodeURIComponent(data.symbol)}` }),
  );
  ctx.registerAction("add-security", addDialog);
  ctx.registerAction("add-watch", async (data) => {
    await mutate("market.add", { symbol: data.symbol }, `${data.symbol} 已加入自选`);
  });
  ctx.registerAction("live-add-watch", async (data) => {
    await mutate("market.add", { symbol: data.symbol }, `${data.symbol} 已加入自选`);
    ctx.closeDialog(false);
    ctx.navigate("watch", { symbol: data.symbol, watchMode: "workspace" });
  });
  ctx.registerAction("favorite", async (data) => {
    const stock = getStock(data.symbol);
    await mutate(
      "market.favorite",
      {
        symbol: data.symbol,
        revision: stock?.observationRevision,
        favorite: !state().favorites.includes(data.symbol),
      },
      "收藏状态已保存",
    );
  });
  ctx.registerAction("save-note", async () => {
    const stock = getStock();
    const notes = document.getElementById("stock-note")?.value || "";
    await mutate(
      "market.note",
      { symbol: stock.symbol, revision: stock.observationRevision, notes },
      "研究备注已保存",
    );
    delete ctx.ui.noteDrafts[stock.symbol];
  });
  ctx.registerAction("edit-note", (data) => {
    ctx.showDialog(
      `${data.symbol} · 我的备注`,
      `<div class="field"><label for="dialog-note">下一步要核实的问题</label><textarea id="dialog-note" class="textarea" maxlength="2000" rows="5">${esc(state().notes[data.symbol] || "")}</textarea></div>`,
      button("保存备注", "save-dialog-note", {
        primary: true,
        attributes: `data-symbol="${esc(data.symbol)}"`,
      }),
    );
  });
  ctx.registerAction("save-dialog-note", async (data) => {
    await mutate(
      "market.note",
      {
        symbol: data.symbol,
        revision: getStock(data.symbol)?.observationRevision,
        notes: document.getElementById("dialog-note").value,
      },
      "研究备注已保存",
    );
    ctx.closeDialog();
  });
  ctx.registerAction("remove-watch", (data) => {
    ctx.showDialog(
      "移除关注证券？",
      `<p class="muted">将 ${esc(data.symbol)} 从关注分组中移除，保留研究和计划。</p>`,
      `${button("返回", "close-dialog", { ghost: true })}${button("移除关注", "confirm-remove-watch", { attributes: `data-symbol="${esc(data.symbol)}"` })}`,
    );
  });
  ctx.registerAction("confirm-remove-watch", async (data) => {
    removed = { symbol: data.symbol, memberships: getStock(data.symbol)?.memberships || [] };
    await mutate("market.remove", { symbol: data.symbol }, `已移除 ${data.symbol}`);
    ctx.closeDialog();
    ctx.toast(`已移除 ${data.symbol}`, true);
  });
  ctx.registerAction("undo-watch", async () => {
    if (!removed) return;
    for (const membership of removed.memberships)
      await ctx.request("market.add", { symbol: removed.symbol, groupId: membership.groupId });
    removed = null;
    await ctx.refresh();
    ctx.toast("已恢复关注证券");
  });
  ctx.registerAction("stock-info", (data) => {
    const stock = getStock(data.symbol);
    ctx.showDialog(
      `${data.symbol} · 证券说明`,
      `<dl class="definition-list"><div><dt>公司 / 基金</dt><dd>${esc(stock.name)}</dd></div><div><dt>证券类型</dt><dd>${esc(stock.type)}</dd></div><div><dt>交易所 / 币种</dt><dd>${esc(stock.exchange)} / ${esc(stock.currency)}</dd></div><div><dt>来源与时点</dt><dd>${esc(sourceText(stock.meta))}</dd></div></dl>`,
      button("关闭", "close-dialog"),
    );
  });
  ctx.registerAction("sector-detail", (data) => {
    const snapshot = pending(`pulse:${ctx.ui.marketWindow}`)?.data,
      sector = snapshot?.sectors.find((item) => item.symbol === data.symbol);
    if (!sector) return;
    ctx.showDialog(
      `${sector.name} · ${sector.symbol}`,
      `<dl class="definition-list"><div><dt>窗口</dt><dd>${esc(ctx.ui.marketWindow)}</dd></div><div><dt>价格收益</dt><dd>${percent(sector.return_percent)}</dd></div><div><dt>相对 SPY</dt><dd>${percent(sector.relative_return_pp, " pp")}</dd></div><div><dt>日 RVOL</dt><dd>${number(sector.daily_rvol)}</dd></div><div><dt>截止 / 资格</dt><dd>${esc(sector.as_of || "缺失")} / ${esc(sector.quality)}</dd></div></dl><p class="muted">${esc(sector.reason || "行业 ETF 作为行业观察代理。")}</p>`,
      button("关闭", "close-dialog"),
    );
  });
  ctx.registerAction("attach-market", () => ctx.request("host.open", { path: "/market" }));
  ctx.registerAction("attach-theme", () => {
    const themes = pending("themes")?.data?.items || [];
    const theme = themes.find((item) => item.id === ctx.ui.theme) || themes[0];
    return ctx.request("host.open", {
      path: `/themes?theme=${encodeURIComponent(theme?.id || "")}&window=${encodeURIComponent(ctx.ui.marketWindow)}`,
    });
  });
  document.addEventListener("input", (event) => {
    const field = event.target;
    if (field.id === "live-watch-query") {
      ctx.ui.watchQuery = field.value;
      const selection = [field.selectionStart, field.selectionEnd];
      ctx.render();
      const replacement = document.getElementById(field.id);
      replacement?.focus();
      replacement?.setSelectionRange(...selection);
    }
    if (field.id !== "live-add-query") return;
    clearTimeout(searchTimer);
    const version = ++searchVersion,
      query = field.value.trim();
    const target = document.getElementById("live-add-results");
    if (!query) {
      target.innerHTML = '<p class="muted">输入关键词，从证券目录中搜索。</p>';
      return;
    }
    target.innerHTML = '<p class="muted">正在搜索证券目录…</p>';
    searchTimer = setTimeout(async () => {
      try {
        const result = await ctx.request("market.search", { query });
        if (version !== searchVersion || !target.isConnected) return;
        for (const stock of result.items) if (!getStock(stock.symbol)) ctx.securities.push(stock);
        target.innerHTML = result.items.length
          ? result.items
              .map(
                (stock) =>
                  `<div class="search-result"><div class="od-row">${tickerTile(stock, true)}<div class="od-field"><strong>${esc(stock.symbol)} · ${esc(stock.name)}</strong><small>${esc(stock.exchange)} · ${esc(stock.type)}</small></div></div>${state().watchlist.includes(stock.symbol) ? badge("已关注") : button("加入自选", "live-add-watch", { small: true, attributes: `data-symbol="${esc(stock.symbol)}"` })}</div>`,
              )
              .join("")
          : emptyState("没有匹配证券", result.warning || "尝试完整证券代码或公司名称。");
      } catch (error) {
        if (version === searchVersion && target.isConnected)
          target.innerHTML = `<p class="warning">${esc(error.message)}</p>`;
      }
    }, 280);
  });
  ctx.registerAfterRender(() => {
    if (ctx.ui.page === "watch" && getStock())
      void load(barKey(), "market.bars", {
        symbol: ctx.ui.symbol,
        interval: ctx.ui.interval,
        period: ctx.ui.period,
      });
    if (ctx.ui.page === "dashboard") void load("pulse:1D", "market.pulse", { window: "1D" });
    if (ctx.ui.page !== "market") return;
    if (ctx.ui.marketTab === "themes") {
      void load("themes", "market.themes", {});
      const themes = pending("themes")?.data?.items || [],
        selected = themes.find((item) => item.id === ctx.ui.theme) || themes[0];
      if (selected)
        void load(`theme:${selected.id}:${ctx.ui.marketWindow}`, "market.theme", {
          id: selected.id,
          window: ctx.ui.marketWindow,
        });
    } else if (ctx.ui.marketTab !== "professional") {
      void load(`pulse:${ctx.ui.marketWindow}`, "market.pulse", { window: ctx.ui.marketWindow });
    }
  });
}
