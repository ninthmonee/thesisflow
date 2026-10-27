"""Reviewed research topology as inert data, without graph/config/provider imports.

The graph remains the execution authority. AST-based tests compare this display
contract with its role catalog, setup, routers and memory/rating implementation.
"""

from copy import deepcopy

_REPORTS = ("market_report", "sentiment_report", "news_report", "fundamentals_report")
_ANALYST_INPUTS = (
    "messages",
    "company_of_interest",
    "trade_date",
    "instrument_context",
    "asset_type",
)
_RESEARCH_INPUTS = (*_REPORTS, "investment_debate_state", "instrument_context", "asset_type")
_RISK_INPUTS = (
    *_REPORTS,
    "trader_investment_plan",
    "risk_debate_state",
    "instrument_context",
    "portfolio_context",
)
_REPORT_KEYS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "bull": "bull_history",
    "bear": "bear_history",
    "research_manager": "investment_plan",
    "trader": "trader_investment_plan",
    "risk_aggressive": "aggressive_history",
    "risk_conservative": "conservative_history",
    "risk_neutral": "neutral_history",
    "portfolio_manager": "final_trade_decision",
}
_TOOL_LOOP = {
    "node_ids": ["agent", "tools", "wrap_up"],
    "rounds_parameter": "max_tool_rounds",
    "description": "分析师有私有消息历史：agent请求工具后进入tools再返回agent；达到工具轮数上限后进入wrap_up再调用分析师要求写报告并结束。子图只向主图输出对应report字段。",
}


def _role(
    identifier,
    label,
    stage,
    graph_node,
    responsibility,
    inputs,
    outputs,
    *,
    tier="quick",
    analyst=False,
    tools=False,
    notes=(),
):
    return {
        "id": identifier,
        "label": label,
        "kind": "role",
        "stage_id": stage,
        "graph_node": graph_node,
        "tier": tier,
        "llm_usage": "required",
        "optional": analyst,
        "selection_key": identifier if analyst else None,
        "responsibility": responsibility,
        "input_keys": list(inputs),
        "output_keys": list(outputs),
        "report_keys": [_REPORT_KEYS[identifier]],
        "notes": list(notes),
        "tool_loop": deepcopy(_TOOL_LOOP) if tools else None,
    }


def _auxiliary(
    identifier,
    label,
    stage,
    graph_node,
    responsibility,
    inputs=(),
    outputs=(),
    *,
    tier=None,
    llm_usage="none",
    optional=False,
    reports=(),
    notes=(),
):
    return {
        "id": identifier,
        "label": label,
        "kind": "auxiliary",
        "stage_id": stage,
        "graph_node": graph_node,
        "tier": tier,
        "llm_usage": llm_usage,
        "optional": optional,
        "selection_key": None,
        "responsibility": responsibility,
        "input_keys": list(inputs),
        "output_keys": list(outputs),
        "report_keys": list(reports),
        "notes": list(notes),
        "tool_loop": None,
    }


_NODES = [
    _role(
        "market",
        "技术分析",
        "analysts",
        "Market Analyst",
        "读取行情及技术指标证据，形成技术市场报告，不在此阶段替代最终决策。",
        _ANALYST_INPUTS,
        ("market_report",),
        analyst=True,
        tools=True,
        notes=("私有工具子图可读取历史行情、指标及可核实市场快照；主图不共享其工具消息。",),
    ),
    _role(
        "social",
        "社交情绪",
        "analysts",
        "Sentiment Analyst",
        "预先获取新闻、StockTwits与Reddit材料，再由模型形成社交情绪报告。",
        _ANALYST_INPUTS,
        ("sentiment_report",),
        analyst=True,
        notes=(
            "没有ToolNode循环；材料在模型调用前获取。TypeSafe Jev筛选在配置后可增加独立调用。",
            "社交来源主要提供近期内容，历史分析不能据此承诺严格时点归档。",
        ),
    ),
    _role(
        "news",
        "新闻分析",
        "analysts",
        "News Analyst",
        "检索与标的及宏观环境相关的新闻证据，形成新闻报告。",
        _ANALYST_INPUTS,
        ("news_report",),
        analyst=True,
        tools=True,
    ),
    _role(
        "fundamentals",
        "基本面分析",
        "analysts",
        "Fundamentals Analyst",
        "读取公司概况、财务报表和内部人交易等证据，形成基本面报告。",
        _ANALYST_INPUTS,
        ("fundamentals_report",),
        analyst=True,
        tools=True,
    ),
    _role(
        "bull",
        "多头研究",
        "debate",
        "Bull Researcher",
        "以分析师报告为证据提出多头论点，并回应最近空头观点，追加研究辩论历史。",
        _RESEARCH_INPUTS,
        ("investment_debate_state",),
        notes=("先发言；每次调用递增investment_debate_state.count，不单独输出最终评级。",),
    ),
    _role(
        "bear",
        "空头研究",
        "debate",
        "Bear Researcher",
        "检验下行风险和多头假设，回应最近多头观点，追加研究辩论历史。",
        _RESEARCH_INPUTS,
        ("investment_debate_state",),
        notes=("与多头交替；未选分析师的报告为空，研究员按缺失材料处理。",),
    ),
    _role(
        "research_manager",
        "研究经理",
        "research_plan",
        "Research Manager",
        "评估多空辩论证据并形成供交易提案使用的研究计划。",
        ("investment_debate_state", "instrument_context"),
        ("investment_debate_state", "investment_plan"),
        tier="deep",
        notes=("结构化ResearchPlan优先，供应商不支持时使用文本回退；不是最终组合评级。",),
    ),
    _role(
        "trader",
        "交易提案",
        "proposal",
        "Trader",
        "根据研究计划、可用技术报告与调用方持仓上下文形成交易提案。",
        (
            "company_of_interest",
            "instrument_context",
            "investment_plan",
            "market_report",
            "portfolio_context",
        ),
        ("messages", "trader_investment_plan"),
        notes=("只生成提案，不连接券商或执行订单；未选技术分析时不宣称已有对应技术证据。",),
    ),
    _role(
        "risk_aggressive",
        "激进风险观点",
        "risk_debate",
        "Aggressive Analyst",
        "从收益机会与较高风险承受角度检验交易提案，并回应其他风险观点。",
        _RISK_INPUTS,
        ("risk_debate_state",),
        notes=("风险辩论首发；真实循环顺序为激进→保守→中立。",),
    ),
    _role(
        "risk_conservative",
        "保守风险观点",
        "risk_debate",
        "Conservative Analyst",
        "从资本保护、损失与稳健性角度检验提案，并回应激进和中立观点。",
        _RISK_INPUTS,
        ("risk_debate_state",),
    ),
    _role(
        "risk_neutral",
        "中立风险观点",
        "risk_debate",
        "Neutral Analyst",
        "权衡收益机会与下行风险，回应激进和保守观点。",
        _RISK_INPUTS,
        ("risk_debate_state",),
    ),
    _role(
        "portfolio_manager",
        "组合经理",
        "final_decision",
        "Portfolio Manager",
        "综合研究计划、交易提案、风险辩论和可用记忆上下文，形成最终决策与五档评级。",
        (
            "investment_plan",
            "trader_investment_plan",
            "risk_debate_state",
            "past_context",
            "instrument_context",
            "portfolio_context",
        ),
        ("risk_debate_state", "final_trade_decision", "final_rating"),
        tier="deep",
        notes=(
            "只有组合经理读取past_context。结构化PortfolioDecision.rating优先；文本回退不可解析时为REVIEW。",
            "Buy/Overweight/Hold/Underweight/Sell是模型研究结论，不表示已交易或已验证收益。",
        ),
    ),
    _auxiliary(
        "memory_log",
        "记忆日志辅助",
        "analysts",
        "Memory Log",
        "与分析师并行，尝试结算所在日志中到期历史决策，再读取按分析时点可用的经验。",
        ("company_of_interest", "trade_date"),
        ("past_context", "memory_note"),
        tier="quick",
        llm_usage="conditional",
        optional=True,
        reports=("memory_note",),
        notes=(
            "GraphSetup允许不注入此节点；标准TradingAgentsGraph会注入。它不属于12个可配置角色。",
            "结算可能读取价格并调用quick层Reflector反思；无到期决策或同日志锁被占等情况不会发生这次反思。wait=False是锁非阻塞，不是承诺后台异步完成。",
            "结算/读取失败写memory_note并继续研究；成功返回的past_context仅由组合经理消费。",
            "当前持久Web研究按运行/尝试隔离记忆日志，此定义不表示已启用跨研究共享记忆。",
        ),
    ),
    _auxiliary(
        "rating_resolution",
        "评级读取与兼容解析",
        "postprocess",
        None,
        "在运行入口需要评级信号时，确定性读取组合经理final_rating；旧状态缺字段时解析其最终决策。",
        ("final_rating", "final_trade_decision"),
        notes=(
            "不是额外评级Agent或额外LLM调用，也不是LangGraph节点；最终评级已在组合经理阶段生成。",
            "返回五档评级或REVIEW；REVIEW代表未识别评级，不自动转换成Hold。此函数读取状态，不向AgentState新增输出键。",
        ),
    ),
    _auxiliary(
        "archive",
        "状态与决策记录",
        "postprocess",
        None,
        "图结束后由运行入口记录状态，并在存在最终决策时向所在记忆日志追加待结算决策与评级。",
        ("company_of_interest", "trade_date", "final_trade_decision", "final_rating"),
        notes=(
            "不是LangGraph节点；写状态日志/记忆日志等持久产物，不改AgentState，因此output_keys为空。没有最终决策时不会凭空写一条决策。",
            "记录本次决策不等于已经有后验收益或完成反思；成熟历史的结算/反思在后续Memory Log或显式结算操作中执行。",
            "报告落盘、决策记账与检查点清理由运行入口负责；此静态定义不声称这些操作已经执行成功。",
        ),
    ),
]

_STAGES = [
    {
        "id": "analysts",
        "label": "并行证据分析",
        "mode": "parallel",
        "node_ids": ["market", "social", "news", "fundamentals", "memory_log"],
        "description": "至少选择一个分析师；本次启用的分析师和已注入Memory Log并行。只在这些实际首阶段分支都返回后开始多头研究。",
    },
    {
        "id": "debate",
        "label": "多空交替辩论",
        "mode": "alternating",
        "node_ids": ["bull", "bear"],
        "description": "多头先发言，多头与空头交替，达到配置的总发言数后交研究经理。",
    },
    {
        "id": "research_plan",
        "label": "研究计划",
        "mode": "sequence",
        "node_ids": ["research_manager"],
        "description": "研究经理裁决研究辩论并给出计划，随后进入交易提案。",
    },
    {
        "id": "proposal",
        "label": "交易提案",
        "mode": "sequence",
        "node_ids": ["trader"],
        "description": "交易角色生成提案，随后交激进风险观点开始风控辩论。",
    },
    {
        "id": "risk_debate",
        "label": "三方风险辩论",
        "mode": "cycle",
        "node_ids": ["risk_aggressive", "risk_conservative", "risk_neutral"],
        "description": "按激进→保守→中立循环；达到配置的总发言数后交组合经理。",
    },
    {
        "id": "final_decision",
        "label": "最终决策与评级",
        "mode": "sequence",
        "node_ids": ["portfolio_manager"],
        "description": "组合经理输出最终决策与评级，主图进入END；没有自动下单节点。",
    },
    {
        "id": "postprocess",
        "label": "图外记录与结果读取",
        "mode": "postprocess",
        "node_ids": ["rating_resolution", "archive"],
        "description": "这些是运行入口的图外操作，列表顺序不是额外LangGraph拓扑或独立Agent调用顺序；record_decision内部也会读取评级。",
    },
]


def _edge(identifier, sources, target, kind, condition="", optional=False):
    return {
        "id": identifier,
        "source_ids": list(sources),
        "target_id": target,
        "type": kind,
        "condition": condition,
        "optional": optional,
    }


_EDGES = [
    *[
        _edge(
            "start-" + identifier,
            ("start",),
            identifier,
            "parallel",
            "仅当该分析师被本次研究选中",
            True,
        )
        for identifier in ("market", "social", "news", "fundamentals")
    ],
    _edge(
        "start-memory",
        ("start",),
        "memory_log",
        "parallel",
        "仅当GraphSetup注入memory_node；标准图会注入",
        True,
    ),
    _edge(
        "first-stage-join",
        ("market", "social", "news", "fundamentals", "memory_log"),
        "bull",
        "join",
        "只汇合本次实际启用的分析师与已注入Memory Log；未选分析师不参与屏障",
    ),
    _edge(
        "bull-bear",
        ("bull",),
        "bear",
        "loop",
        "研究发言count < 2×max_debate_rounds且current_response以Bull开头",
    ),
    _edge(
        "bear-bull",
        ("bear",),
        "bull",
        "loop",
        "研究发言count < 2×max_debate_rounds且current_response不以Bull开头",
    ),
    *[
        _edge(
            identifier + "-research-manager",
            (identifier,),
            "research_manager",
            "sequence",
            "investment_debate_state.count >= 2×max_debate_rounds",
        )
        for identifier in ("bull", "bear")
    ],
    _edge("research-manager-trader", ("research_manager",), "trader", "sequence"),
    _edge("trader-aggressive", ("trader",), "risk_aggressive", "sequence"),
    _edge(
        "aggressive-conservative",
        ("risk_aggressive",),
        "risk_conservative",
        "loop",
        "风险发言count < 3×max_risk_discuss_rounds且latest_speaker以Aggressive开头",
    ),
    _edge(
        "conservative-neutral",
        ("risk_conservative",),
        "risk_neutral",
        "loop",
        "风险发言count < 3×max_risk_discuss_rounds且latest_speaker以Conservative开头",
    ),
    _edge(
        "neutral-aggressive",
        ("risk_neutral",),
        "risk_aggressive",
        "loop",
        "风险发言count < 3×max_risk_discuss_rounds且latest_speaker不以Aggressive或Conservative开头",
    ),
    *[
        _edge(
            identifier + "-portfolio-manager",
            (identifier,),
            "portfolio_manager",
            "sequence",
            "risk_debate_state.count >= 3×max_risk_discuss_rounds",
        )
        for identifier in ("risk_aggressive", "risk_conservative", "risk_neutral")
    ],
    _edge("portfolio-manager-end", ("portfolio_manager",), "end", "sequence"),
]

_LOOPS = [
    {
        "id": "research-debate",
        "label": "多空研究轮次",
        "stage_id": "debate",
        "node_ids": ["bull", "bear"],
        "rounds_parameter": "max_debate_rounds",
        "turns_per_round": 2,
        "counter_key": "investment_debate_state.count",
        "exit_node_id": "research_manager",
        "exit_condition": "count >= 2 * max_debate_rounds",
        "description": "正常路径每轮多头、空头各一次；Web运行的depth写入max_debate_rounds。条件路由在每次发言后检查计数。",
    },
    {
        "id": "risk-debate",
        "label": "三方风险轮次",
        "stage_id": "risk_debate",
        "node_ids": ["risk_aggressive", "risk_conservative", "risk_neutral"],
        "rounds_parameter": "max_risk_discuss_rounds",
        "turns_per_round": 3,
        "counter_key": "risk_debate_state.count",
        "exit_node_id": "portfolio_manager",
        "exit_condition": "count >= 3 * max_risk_discuss_rounds",
        "description": "正常路径每轮依次为激进、保守、中立；Web运行的depth写入max_risk_discuss_rounds。不是三个风险角色并行。",
    },
]

_DEFINITION = {
    "version": "research-workflow-v1",
    "role_count": 12,
    "nodes": _NODES,
    "stages": _STAGES,
    "edges": _EDGES,
    "loops": _LOOPS,
    "notes": [
        "此定义是代码拓扑与职责说明，不是某次任务的执行轨迹、节点状态或运行成功证明。start/end是流程边界，不是模型角色。",
        "12个角色不等于12次模型调用；未选分析师不运行，辩论轮次、工具循环、结构化回退、可选社交筛选和记忆反思可能改变调用次数。",
        "tier是默认quick/deep模型层级；角色模型模板可以逐角色覆盖，具体供应商/模型取决于运行时已选配置。此接口不读取它们。",
        "当前事件提供阶段、报告和统计等有限事实，没有逐角色turn/running/completed事件；第一版辩论历史或阶段标签不能证明某角色全部轮数已完成。",
        "input_keys/output_keys描述AgentState字段；图外持久产物由辅助节点notes解释。只有实际启用的首阶段节点参与join。",
        "研究经理与组合经理各自产生自己的建议/评级；最终评级以组合经理输出为准，不能用多头或交易角色的建议替代。",
    ],
    "source_refs": [
        "backend/tradingagents/web/domain/model_configuration.py:ROLES",
        "backend/tradingagents/graph/setup.py:GraphSetup.setup_graph",
        "backend/tradingagents/graph/setup.py:_analyst_graph",
        "backend/tradingagents/graph/analyst_execution.py:ANALYST_NODE_SPECS",
        "backend/tradingagents/graph/conditional_logic.py:ConditionalLogic",
        "backend/tradingagents/agents/state.py:AgentState",
        "backend/tradingagents/agents/managers/portfolio_manager.py:create_portfolio_manager",
        "backend/tradingagents/graph/trading_graph.py:_memory_step/record_decision",
        "backend/tradingagents/memory/reflection.py:Reflector",
        "backend/tradingagents/agents/rating.py:run_rating",
        "backend/tradingagents/web/workers/research.py:ResearchWorker",
    ],
}


def research_workflow_definition():
    """Return an isolated static definition; never create a graph or inspect user state."""
    return deepcopy(_DEFINITION)
