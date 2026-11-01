/** Investment UI keeps the Open Design layout and persists through the authenticated parent. */
export function installInvestment(ctx) {
  const { state, ui, esc, icon, button, badge, heading, emptyState } = ctx;
  let busy = false;
  const field = (id) => document.getElementById(id);
  const value = (id) => field(id)?.value ?? "";
  const due = (plan) => plan.status !== "archived" && plan.reviewDue === true;
  const statusLabels = {
    draft: "草稿",
    watching: "观察中",
    holding: "持有中",
    reviewed: "已复盘",
    archived: "已归档",
  };
  const optional = (input) => (input === "" || input == null ? null : Number(input));

  async function perform(element, operation) {
    if (busy) return;
    busy = true;
    if (element) {
      element.disabled = true;
      element.setAttribute("aria-busy", "true");
    }
    try {
      await operation();
    } catch (error) {
      ctx.toast(error?.message || "保存失败，请检查连接后重试。");
    } finally {
      busy = false;
      if (element?.isConnected) {
        element.disabled = false;
        element.removeAttribute("aria-busy");
      }
    }
  }

  async function refreshAfterSave() {
    try {
      await ctx.refresh();
    } catch {
      ctx.toast("内容已保存，工作区刷新失败。请稍后刷新页面。");
    }
  }

  function replacePlan(plan) {
    const index = state.plans.findIndex((item) => item.id === plan.id);
    if (index < 0) state.plans.unshift(plan);
    else state.plans[index] = plan;
  }

  function planCards(plans) {
    if (!plans.length)
      return `<section class="panel">${emptyState(ui.investmentTab === "reviews" ? "当前没有到期的计划" : "没有符合条件的计划", ui.investmentTab === "reviews" ? "已复核的计划仍保留在“我的计划”中。" : "清除关键词或创建一份新的决策计划。", button("新增计划", "new-plan", { glyph: "plus" }))}</section>`;
    return `<div class="plan-list">${plans
      .map((plan) => {
        const progress = ctx.planProgress(plan);
        return `<article class="panel plan-card"><div class="plan-card-main"><div class="od-row"><span class="ticker-tile small">${esc(plan.symbol)}</span><div class="od-field od-fill"><div class="plan-card-title"><h3>${esc(plan.symbol)}</h3>${badge(statusLabels[plan.status] || "未知状态", plan.status === "archived" ? "" : "mint")}${due(plan) ? badge("待复盘", "amber") : ""}</div><strong style="font-size:14px;font-weight:400;color:var(--muted);margin-top:8px">${esc(plan.title)}</strong></div></div><p class="od-clamp-2">${esc(plan.business || "从公司如何赚钱开始，逐步补齐自己的研究问题。")}</p><div class="small-meta"><span>计划 v${esc(plan.revision)}</span><span class="separator"></span><span>${plan.runId ? "已关联研究" : "独立计划"}</span><span class="separator"></span><span>个人工作区</span></div><div class="od-cluster">${button("查看计划", "edit-plan", { small: true, glyph: "arrow", attributes: `data-id="${esc(plan.id)}"` })}${plan.status !== "archived" ? button("手动复盘", "review-plan", { ghost: true, small: true, attributes: `data-id="${esc(plan.id)}"` }) : button("恢复观察", "restore-plan", { small: true, attributes: `data-id="${esc(plan.id)}"` })}${plan.runId ? `<button class="text-button" type="button" data-action="open-run" data-id="${esc(plan.runId)}">关联研究</button>` : ""}</div></div><div class="plan-card-side"><div><div class="small-meta" style="justify-content:space-between"><span>六问记录</span><span class="mono">${progress} / 6</span></div><div class="question-progress" aria-label="六问已记录 ${progress} 项">${Array.from({ length: 6 }, (_, i) => `<span class="${i < progress ? "filled" : ""}"></span>`).join("")}</div></div><div class="plan-card-stats"><div class="od-stat"><small>计划仓位</small><strong class="mono">${plan.position === "" || plan.position == null ? "尚未填写" : esc(plan.position) + "%"}</strong></div><div class="od-stat"><small>下次复核</small><strong class="mono ${due(plan) ? "warning" : ""}">${esc(plan.review || "未设置")}</strong></div></div><div class="small-meta">${plan.reviewed ? `最近复盘 ${esc(plan.reviewed)}` : "风险边界由本人填写"}</div></div></article>`;
      })
      .join("")}</div>`;
  }

  const principles = [
    ["cycles", "先看周期，再选公司", "区分行业趋势、资本周期与单个公司的竞争力。"],
    ["goal", "明确个人长期目标", "用自己的语言解释长期目标与资金用途。"],
    ["quality", "核实护城河与质量", "区分已经形成的优势与仍待兑现的叙事。"],
    ["valuation", "质量与价格分开判断", "优秀公司也需要检验估值和隐含增长假设。"],
    ["life", "为生活保留现金边界", "分别记录生活支出、现金需要与流动性。"],
    ["probability", "用概率思考不确定性", "列出情景与条件，保持未知项的原貌。"],
    ["sizing", "让仓位服从风险边界", "根据个人资金和风险容忍度，明确计划仓位。"],
    ["discipline", "写下纪律，定期复盘", "保存关键假设、失效条件和自己的修订。"],
  ];
  function policyView() {
    if (!Number.isInteger(state.policy.revision))
      return `<section class="panel">${emptyState("个人投资体系尚未读取", "请重新读取工作区后再编辑，避免覆盖已有体系。", button("重新读取", "refresh-workspace", { glyph: "refresh" }))}</section>`;
    return `<section class="panel"><div class="policy-heading"><span class="eyebrow">PERSONAL INVESTMENT SYSTEM / 个人体系 v${esc(state.policy.revision ?? 0)}</span><h2>用纪律连接每一次判断。</h2><p>${esc(state.policy.objective || "填写自己的长期目标与风险边界。")}</p></div><div class="policy-grid">${principles.map(([key, title], i) => `<div class="policy-rule"><span>${String(i + 1).padStart(2, "0")}</span><div><h3>${title}</h3><p>${esc(state.policy.principles?.[key] || "尚未记录个人原则。")}</p></div></div>`).join("")}</div><div class="panel-body stack"><h2>我的目标与风险边界</h2><div class="field"><label for="policy-objective">个人目标</label><textarea id="policy-objective" class="textarea" maxlength="2000" rows="3">${esc(state.policy.objective)}</textarea></div><div class="form-grid"><div class="field"><label for="policy-max">单股仓位上限 · %</label><input type="number" class="input" id="policy-max" min="0" max="100" step="0.1" value="${esc(state.policy.maxPosition)}" placeholder="由本人填写"><p id="policy-max-error" class="field-error" hidden></p></div><div class="field"><label for="policy-cash">最低现金比例 · %</label><input type="number" class="input" id="policy-cash" min="0" max="100" step="0.1" value="${esc(state.policy.minCash)}" placeholder="由本人填写"><p id="policy-cash-error" class="field-error" hidden></p></div><div class="field"><label for="policy-interval">默认复核间隔 · 天</label><input type="number" class="input" id="policy-interval" min="1" max="3650" step="1" value="${esc(state.policy.interval)}"><p id="policy-interval-error" class="field-error" hidden></p></div></div><p class="field-help">留空表示未知。保存将建立新的体系版本，原有计划的体系快照会保留。</p><div>${button("保存个人体系", "save-policy", { glyph: "check" })}</div></div></section>`;
  }

  ctx.registerView("investment", () => {
    const search = (ui.planQuery || "").trim().toLowerCase();
    const plans = state.plans.filter(
      (plan) =>
        (ui.investmentTab === "reviews"
          ? due(plan)
          : ui.planStatus === "archived"
            ? plan.status === "archived"
            : plan.status !== "archived") &&
        (ui.planStatus === "all" || plan.status === ui.planStatus) &&
        `${plan.symbol} ${plan.title} ${plan.business}`.toLowerCase().includes(search),
    );
    return `<div class="page">${heading("投资决策", "先写下假设，再用证据修正判断。", button("新增计划", "new-plan", { primary: true, glyph: "plus" }), "THINK CLEARLY / ACT WITH DISCIPLINE")}<div class="tabs">${[
      ["plans", "我的计划"],
      ["reviews", "到期复盘"],
      ["policy", "个人体系"],
    ]
      .map(
        ([key, label]) =>
          `<button class="tab ${ui.investmentTab === key ? "active" : ""}" type="button" data-action="investment-tab" data-tab="${key}">${label}${key !== "policy" ? `<span class="tab-count">${key === "reviews" ? state.plans.filter(due).length : state.plans.filter((plan) => plan.status !== "archived").length}</span>` : ""}</button>`,
      )
      .join("")}</div>${
      ui.investmentTab === "policy"
        ? policyView()
        : `<div class="filterbar"><div class="input-search compact">${icon("search")}<label class="sr-only" for="plan-search">搜索计划</label><input id="plan-search" class="input" placeholder="搜索代码、计划或假设" value="${esc(ui.planQuery)}"></div><div class="field"><label class="sr-only" for="plan-status">计划状态</label><select id="plan-status" class="select" style="min-height:40px">${[
            ["all", "全部状态"],
            ["watching", "观察中"],
            ["holding", "持有中"],
            ["reviewed", "已复盘"],
            ["draft", "草稿"],
            ["archived", "已归档"],
          ]
            .map(
              ([key, label]) =>
                `<option value="${key}" ${ui.planStatus === key ? "selected" : ""}>${label}</option>`,
            )
            .join(
              "",
            )}</select></div></div>${ui.investmentTab === "reviews" ? '<div class="note-box">复核日期与到期状态由工作区服务计算。请手动记录复盘发现，并设置下次日期。</div>' : ""}<div id="plan-results">${planCards(plans)}</div><p class="small-meta">计划与研究分别保存，个人确认不会改变来源资格。手填情景不代表预测或已验证估值。</p>`
    }</div>`;
  });

  function showErrors(errors) {
    if (!errors.length) return false;
    if (errors[0][2]) {
      ui.planStep = errors[0][2];
      ctx.render();
    }
    errors.forEach(([id, message, step]) => {
      if (!step || step === ui.planStep) {
        if (!field(`${id}-error`) && field(id)) {
          const error = document.createElement("p");
          error.id = `${id}-error`;
          error.className = "field-error";
          field(id).insertAdjacentElement("afterend", error);
        }
        ctx.setFieldError(id, message);
      }
    });
    field(errors[0][0])?.focus();
    ctx.toast(errors[0][1]);
    return true;
  }

  async function savePlan(_data, element) {
    const draft = ui.planDraft;
    if (!draft) return;
    for (const input of document.querySelectorAll("#plan-form [data-plan-field]")) {
      draft[input.dataset.planField] = input.type === "checkbox" ? input.checked : input.value;
    }
    for (const input of document.querySelectorAll("#plan-form [data-scenario]")) {
      const scenario = draft.scenarios[Number(input.dataset.scenario)];
      if (scenario) scenario[input.dataset.scenarioField] = input.value;
    }
    draft.symbol = String(draft.symbol || "")
      .trim()
      .toUpperCase();
    if (!draft.analysisDate) draft.analysisDate = ctx.today;
    const errors = [];
    if (!String(draft.symbol || "").trim())
      errors.push(["plan-symbol", "请填写研究证券代码。", "company"]);
    if (!draft.title.trim()) errors.push(["plan-title", "请先为计划填写一个名称。", "company"]);
    for (const [key, id] of [
      ["referencePrice", "plan-reference"],
      ["fairValue", "plan-fair"],
    ]) {
      const amount = optional(draft[key]);
      if (amount !== null && (!Number.isFinite(amount) || amount <= 0))
        errors.push([id, "请输入大于 0 的金额，或留空。", "valuation"]);
    }
    const position = optional(draft.position);
    if (position !== null && (!Number.isFinite(position) || position < 0 || position > 100))
      errors.push(["plan-position", "仓位需在 0–100% 之间，或留空。", "risk"]);
    draft.scenarios.forEach((row, i) => {
      const price = optional(row.price),
        probability = optional(row.probability);
      if (price !== null && (!Number.isFinite(price) || price < 0))
        errors.push([`scenario-price-${i}`, "情景价格需要为不小于 0 的数值。", "valuation"]);
      if (
        probability !== null &&
        (!Number.isFinite(probability) || probability < 0 || probability > 100)
      )
        errors.push([`scenario-probability-${i}`, "情景概率需在 0–100% 之间。", "valuation"]);
    });
    const probabilities = draft.scenarios.map((row) => optional(row.probability));
    if (
      probabilities.every((p) => p !== null && Number.isFinite(p)) &&
      Math.abs(probabilities.reduce((sum, p) => sum + p, 0) - 100) > 0.000001
    )
      errors.push([
        "scenario-probability-0",
        "完整三情景概率必须合计 100%；未估计的概率请留空。",
        "valuation",
      ]);
    if (draft.markReviewed && !draft.reviewNotes.trim())
      errors.push(["plan-review-notes", "请写下本次复盘的发现与修改，再标记已复盘。", "review"]);
    if (showErrors(errors)) return;
    await perform(element, async () => {
      const saved = await ctx.request("investment.savePlan", draft);
      replacePlan(saved);
      ui.planDraft = saved;
      ui.planId = saved.id;
      ctx.onSaved?.("plan");
      ctx.closeDialog(false);
      await refreshAfterSave();
      ctx.navigate("investment", {
        investmentTab: "plans",
        planQuery: "",
        planStatus: saved.status === "archived" ? "archived" : "all",
      });
      ctx.toast(draft.markReviewed ? "复盘记录已保存。" : "决策计划已保存。");
    });
  }
  ctx.registerAction("save-plan", savePlan);
  ctx.registerAction("dialog-save-plan", savePlan);

  ctx.registerAction("save-policy", async (_data, element) => {
    if (!Number.isInteger(state.policy.revision)) {
      ctx.toast("请先重新读取个人投资体系。");
      return;
    }
    const next = {
      objective: value("policy-objective").trim(),
      maxPosition: value("policy-max"),
      minCash: value("policy-cash"),
      interval: value("policy-interval"),
      revision: state.policy.revision,
    };
    const errors = [];
    for (const [key, id, maximum, integer] of [
      ["maxPosition", "policy-max", 100, false],
      ["minCash", "policy-cash", 100, false],
      ["interval", "policy-interval", 3650, true],
    ]) {
      ctx.setFieldError(id);
      const number = optional(next[key]);
      if (
        number !== null &&
        (!Number.isFinite(number) ||
          number < (integer ? 1 : 0) ||
          number > maximum ||
          (integer && !Number.isInteger(number)))
      )
        errors.push([
          id,
          integer ? "复核间隔需要为 1–3650 天的整数。" : "请输入 0–100 的比例，或留空。",
        ]);
    }
    if (showErrors(errors)) return;
    await perform(element, async () => {
      state.policy = await ctx.request("investment.savePolicy", next);
      ctx.onSaved?.("policy");
      await refreshAfterSave();
      ctx.render();
      ctx.toast("个人投资体系新版本已保存。");
    });
  });

  ctx.registerAction("archive-plan", (data) => {
    ctx.showDialog(
      "归档这份计划？",
      '<p class="muted">归档后仍可从计划状态筛选中找到并恢复。未保存的编辑将放弃。</p>',
      `${button("返回", "close-dialog", { ghost: true })}<button type="button" class="button danger" data-action="confirm-archive-plan" data-id="${esc(data.id)}">归档计划</button>`,
    );
  });
  ctx.registerAction("confirm-archive-plan", async (data, element) => {
    await perform(element, async () => {
      replacePlan(await ctx.request("investment.archivePlan", { id: data.id }));
      ctx.closeDialog(false);
      ui.planDraft = null;
      ctx.onSaved?.("plan");
      await refreshAfterSave();
      ctx.navigate("investment", { investmentTab: "plans", planStatus: "all" });
      ctx.toast("计划已归档，可通过筛选恢复。");
    });
  });
  ctx.registerAction("restore-plan", async (data, element) => {
    await perform(element, async () => {
      replacePlan(await ctx.request("investment.restorePlan", { id: data.id }));
      ui.planStatus = "all";
      await refreshAfterSave();
      ctx.render();
      ctx.toast("计划已恢复为观察中。");
    });
  });
  ctx.registerAction("save-viewpoint", async (data, element) => {
    const next = {
      runId: data.id,
      thesis: value("viewpoint-thesis").trim(),
      invalidation: value("viewpoint-invalidation").trim(),
      stance: value("viewpoint-stance"),
      review: value("viewpoint-review"),
      confirmed: field("viewpoint-confirm")?.checked === true,
    };
    const errors = [];
    for (const id of ["viewpoint-thesis", "viewpoint-invalidation", "viewpoint-confirm"])
      ctx.setFieldError(id);
    if (!next.thesis) errors.push(["viewpoint-thesis", "请用自己的话写下认可的论据。"]);
    if (!next.invalidation)
      errors.push(["viewpoint-invalidation", "请写下至少一个让观点失效的条件。"]);
    if (!next.confirmed) errors.push(["viewpoint-confirm", "请确认这是你本人整理的观点。"]);
    if (showErrors(errors)) return;
    await perform(element, async () => {
      state.viewpoints[data.id] = await ctx.request("investment.saveViewpoint", next);
      ctx.onSaved?.("viewpoint");
      await refreshAfterSave();
      ctx.render();
      ctx.toast("你的研究观点已保存。");
    });
  });

  ctx.registerAfterRender(() => {
    if (ui.page === "planEditor") {
      const eyebrow = document.querySelector("#main .page-heading .eyebrow");
      if (eyebrow) eyebrow.textContent = "PERSONAL DECISION / 决策计划";
      const reviewed = field("plan-mark-reviewed")?.closest("label")?.querySelector("small");
      if (reviewed) reviewed.textContent = "保存时由服务记录复盘时间。请同时填写复盘内容。";
      const select = field("plan-symbol");
      if (select?.tagName === "SELECT") {
        const input = document.createElement("input");
        input.id = "plan-symbol";
        input.className = "input";
        input.dataset.planField = "symbol";
        input.value = ui.planDraft?.symbol || "";
        input.maxLength = 32;
        input.placeholder = "证券代码，例如 NVDA";
        input.autocapitalize = "characters";
        input.addEventListener("input", () => {
          input.value = input.value.toUpperCase().replace(/[^A-Z0-9._^=-]/g, "");
        });
        select.replaceWith(input);
      }
      const status = field("plan-edit-status");
      if (status) {
        for (const key of ["holding", "reviewed"])
          if (![...status.options].some((option) => option.value === key)) {
            const option = document.createElement("option");
            option.value = key;
            option.textContent = statusLabels[key];
            status.append(option);
          }
        status.value = ui.planDraft?.status || "draft";
      }
    }
    if (ui.page === "researchDetail" && field("viewpoint-form")) {
      const label = field("viewpoint-confirm")?.closest("label")?.querySelector("span");
      if (label) label.textContent = "这是我本人整理并认可的研究观点。";
      if (!state.viewpoints[ui.runId] && field("viewpoint-review"))
        field("viewpoint-review").value = "";
      const saved = field("viewpoint-form")
        ?.closest("section")
        ?.querySelector(".panel-head .badge");
      if (saved && state.viewpoints[ui.runId]) saved.textContent = "已保存";
    }
    if (ui.page === "investment") {
      const input = field("plan-search"),
        status = field("plan-status");
      // Original prototype's narrow update routine renders illustrative cards.
      // Stop its delegated input handling and rerender our authenticated view.
      input?.addEventListener("input", (event) => {
        event.stopPropagation();
        const position = input.selectionStart;
        ui.planQuery = input.value;
        ctx.render();
        const replacement = field("plan-search");
        replacement?.focus();
        replacement?.setSelectionRange(position, position);
      });
      status?.addEventListener("change", (event) => {
        event.stopPropagation();
        ui.planStatus = status.value;
        ctx.render();
      });
    }
  });
}
