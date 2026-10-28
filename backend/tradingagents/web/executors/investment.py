"""Use the frozen research-facing policy, never the current private portfolio inputs."""

import hashlib
import json
from collections.abc import Mapping

from tradingagents.web.domain.tasks import ResearchError


def investment_prompt(manifest):
    if not manifest.get("request", {}).get("apply_investment_system", False):
        return ""
    packet = manifest.get("selection", {}).get("investment_system")
    if not isinstance(packet, Mapping) or not isinstance(packet.get("snapshot"), Mapping):
        raise ResearchError("investment_system_invalid", "本次投资体系快照缺失，未启动模型", 409)
    snapshot = packet["snapshot"]
    if (snapshot.get("version") != 1
        or not isinstance(snapshot.get("owner_id"), str) or not snapshot.get("owner_id")
        or not isinstance(snapshot.get("workspace_id"), str) or not snapshot.get("workspace_id")
        or (snapshot["owner_id"], snapshot["workspace_id"]) != (manifest.get("owner_id"), manifest.get("workspace_id"))):
        raise ResearchError("investment_system_scope_invalid", "投资体系快照不属于本次研究账号或工作区", 403)
    policy = snapshot.get("policy")
    if (not isinstance(policy, Mapping) or policy.get("enabled") is not True
        or not isinstance(policy.get("name"), str) or not policy["name"].strip()
        or type(policy.get("revision")) is not int or policy["revision"] < 0):
        raise ResearchError("investment_system_invalid", "本次投资体系规则版本或启用状态无效", 409)
    try:
        raw = json.dumps({key: value for key, value in snapshot.items() if key != "content_hash"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError):
        raise ResearchError("investment_system_invalid", "本次投资体系快照内容无效", 409) from None
    if len(raw) > 120_000 or hashlib.sha256(raw).hexdigest() != snapshot.get("content_hash"):
        raise ResearchError("investment_system_invalid", "本次投资体系快照校验失败", 409)
    plan = snapshot.get("plan")
    requested_plan = manifest.get("request", {}).get("investment_plan_id")
    if plan is not None and (
        not isinstance(plan, Mapping) or not plan.get("id") or plan.get("id") != requested_plan
        or plan.get("ticker") != manifest.get("request", {}).get("ticker")
        or not isinstance(plan.get("analysis_date"), str)
        or plan["analysis_date"] > manifest.get("request", {}).get("date", "")
        or type(plan.get("revision")) is not int or plan["revision"] < 1
    ):
        raise ResearchError("investment_system_plan_invalid", "投资计划快照与本次研究不一致", 409)
    if requested_plan and plan is None:
        raise ResearchError("investment_system_plan_invalid", "本次研究缺少已选投资计划快照", 409)
    prompt = packet.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 64_000:
        raise ResearchError("investment_system_invalid", "本次投资体系研究要求无效，未启动模型", 409)
    return prompt


def investment_checklist(manifest):
    """Record the input contract, without grading model prose or copying personal amounts."""
    if not investment_prompt(manifest):
        return ""
    snapshot = manifest["selection"]["investment_system"]["snapshot"]
    policy = snapshot.get("policy")
    policy = policy if isinstance(policy, Mapping) else {}
    plan = snapshot.get("plan")
    plan = plan if isinstance(plan, Mapping) else {}
    name = str(policy.get("name", "投资体系"))[:120].replace("\n", " ")
    revision = policy.get("revision", 0)
    revision = revision if type(revision) is int and revision >= 0 else "未知"
    rows = [
        "# 投资体系与决策核对",
        f"冻结规则：{name} · 版本 {revision}。",
        "本章只记录本次研究采用的规则及待核对问题，不对模型结论打合规分，也不证明模型已逐项遵守。",
        "研究顺序：宏观周期 → 行业周期 → 估值周期 → 公司质量与护城河 → 价格和安全边际 → 下行情景、仓位纪律与复盘。",
        "## 六个决策问题 · 均须结合证据和个人情况复核",
    ]
    questions = (
        "行业趋势怎么样？核对需求、供给、周期位置与证据日期。",
        "公司靠什么赚钱？核对营收、利润、自由现金流及增长可持续性。",
        "护城河是什么？核对竞争地位、定价权、管理层与可能的侵蚀因素。",
        "当前估值贵不贵？区分手填公允价值与已验证事实，核对假设、价格和安全边际。",
        "判断错了最多亏多少？区分下行情景、归零风险和止损计划；跳空与流动性可能使止损失效。",
        "应该买多少仓位？核对单股与主题集中度、现金、生活备用金及可承担亏损；缺信息时保持未知。",
    )
    rows.extend(f"{index}. 待人工核对：{question}" for index, question in enumerate(questions, 1))
    rows.extend([
        "## 操作与复盘纪律",
        "分批建仓；分别核对买入、加仓、减仓、退出及失效条件。保存判断依据，按计划复核，不由页面自动执行交易。",
        "概率与公允价值是待核验假设；未提供概率不补齐胜率，未提供组合信息不推算可执行仓位。税费、滑点和流动性缺失时说明限制。",
        "家庭支出、资金用途、税务与跨资产分散仍需本人核对。个人资本、现金、生活保留金及税务备忘不作为模型输入。",
    ])
    if plan.get("id"):
        rows.append("本次附加了用户决策草稿，采用创建任务时冻结的版本；其观点与价位不等于工具证据。")
    else:
        rows.append("本次未附加个股决策草稿；不得据此假定用户没有持仓或拥有可用现金。")
    return "\n\n".join(rows)
