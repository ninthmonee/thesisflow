"""The display topology follows actual sources without executing the research runtime."""

import ast
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.web.api.workflow_router import create_workflow_router
from tradingagents.web.api.workflow_schemas import ResearchWorkflowDefinition
from tradingagents.web.domain.research_workflow import research_workflow_definition

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def assignment(tree, name):
    return next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        or isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )


def member_calls(tree, name, receiver=None):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and (
            receiver is None
            or isinstance(node.func.value, ast.Name)
            and node.func.value.id == receiver
        )
    ]


def definition():
    return ResearchWorkflowDefinition.model_validate(research_workflow_definition())


def test_role_ids_labels_and_tiers_match_actual_model_catalog_and_graph_model_bindings():
    contract = definition()
    nodes = {node.id: node for node in contract.nodes if node.kind == "role"}
    roles = ast.literal_eval(
        assignment(source("tradingagents/web/domain/model_configuration.py"), "ROLES")
    )
    assert len(nodes) == contract.role_count == len(roles) == 12
    assert {(node.id, node.label, node.tier) for node in nodes.values()} == set(roles)
    setup = source("tradingagents/graph/setup.py")
    model_bindings = {
        ast.literal_eval(call.args[0]): "deep"
        if any(
            keyword.arg == "deep" and ast.literal_eval(keyword.value) for keyword in call.keywords
        )
        else "quick"
        for call in member_calls(setup, "model_for")
    }
    assert model_bindings == {identifier: node.tier for identifier, node in nodes.items()}
    assert all(node.llm_usage == "required" for node in nodes.values())


def test_optional_analyst_names_reports_and_private_tool_loops_match_execution_plan():
    tree = source("tradingagents/graph/analyst_execution.py")
    specs = assignment(tree, "ANALYST_NODE_SPECS")
    contract = {node.id: node for node in definition().nodes}
    for key, value in zip(specs.keys, specs.values, strict=True):
        identifier = ast.literal_eval(key)
        parameters = {keyword.arg: keyword.value for keyword in value.keywords}
        node = contract[identifier]
        assert node.optional and node.selection_key == identifier
        assert node.graph_node == ast.literal_eval(parameters["agent_node"])
        assert node.output_keys == node.report_keys == [ast.literal_eval(parameters["report_key"])]
        assert (node.tool_loop is not None) == ("tools" in parameters)
        if node.tool_loop:
            assert node.tool_loop.node_ids == ["agent", "tools", "wrap_up"]
            assert node.tool_loop.rounds_parameter == "max_tool_rounds"
    setup = source("tradingagents/graph/setup.py")
    subgraph_nodes = {
        ast.literal_eval(call.args[0]) for call in member_calls(setup, "add_node", "graph")
    }
    assert subgraph_nodes == {"agent", "tools", "wrap_up"}


def test_declared_graph_nodes_and_parallel_join_match_setup_without_extra_postprocess_agents():
    setup = source("tradingagents/graph/setup.py")
    explicit_graph_nodes = {
        ast.literal_eval(call.args[0])
        for call in member_calls(setup, "add_node", "workflow")
        if isinstance(call.args[0], ast.Constant)
    }
    nodes = definition().nodes
    assert explicit_graph_nodes == {
        node.graph_node for node in nodes if node.graph_node is not None and not node.selection_key
    }
    joins = [
        call
        for call in member_calls(setup, "add_edge", "workflow")
        if isinstance(call.args[0], ast.Name) and call.args[0].id == "first_steps"
    ]
    assert len(joins) == 1 and ast.literal_eval(joins[0].args[1]) == "Bull Researcher"
    edge = next(edge for edge in definition().edges if edge.type == "join")
    assert edge.source_ids == ["market", "social", "news", "fundamentals", "memory_log"]
    assert edge.target_id == "bull"
    assert {node.id for node in nodes if node.graph_node is None} == {
        "rating_resolution",
        "archive",
    }


def test_alternating_and_risk_cycle_edges_follow_the_actual_pure_conditional_logic():
    tree = source("tradingagents/graph/conditional_logic.py")
    logic_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ConditionalLogic"
    )
    namespace = {"AgentState": dict}
    exec(
        compile(ast.Module(body=[logic_class], type_ignores=[]), "conditional_logic_ast", "exec"),
        namespace,
    )
    contract = definition()
    graph_ids = {node.graph_node: node.id for node in contract.nodes}
    transitions = {
        edge.source_ids[0]: edge.target_id for edge in contract.edges if edge.type == "loop"
    }
    for depth in (1, 2, 3):
        logic = namespace["ConditionalLogic"](
            max_debate_rounds=depth, max_risk_discuss_rounds=depth
        )
        for response, current in (
            ("Bull Analyst: evidence", "bull"),
            ("Bear Analyst: evidence", "bear"),
        ):
            result = logic.should_continue_debate(
                {"investment_debate_state": {"count": 1, "current_response": response}}
            )
            assert graph_ids[result] == transitions[current]
        debate = next(loop for loop in contract.loops if loop.stage_id == "debate")
        assert (
            logic.should_continue_debate(
                {
                    "investment_debate_state": {
                        "count": depth * debate.turns_per_round,
                        "current_response": "Bull",
                    }
                }
            )
            == "Research Manager"
        )
        for speaker, current in (
            ("Aggressive", "risk_aggressive"),
            ("Conservative", "risk_conservative"),
            ("Neutral", "risk_neutral"),
        ):
            result = logic.should_continue_risk_analysis(
                {"risk_debate_state": {"count": 1, "latest_speaker": speaker}}
            )
            assert graph_ids[result] == transitions[current]
        risk = next(loop for loop in contract.loops if loop.stage_id == "risk_debate")
        assert (
            logic.should_continue_risk_analysis(
                {
                    "risk_debate_state": {
                        "count": depth * risk.turns_per_round,
                        "latest_speaker": "Neutral",
                    }
                }
            )
            == "Portfolio Manager"
        )
    assert next(stage for stage in contract.stages if stage.id == "risk_debate").node_ids == [
        "risk_aggressive",
        "risk_conservative",
        "risk_neutral",
    ]


def test_state_fields_and_published_report_keys_remain_authoritative():
    tree = source("tradingagents/agents/state.py")
    state = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentState"
    )
    fields = {node.target.id for node in state.body if isinstance(node, ast.AnnAssign)} | {
        "messages"
    }
    contract = definition()
    assert all(set(node.input_keys + node.output_keys) <= fields for node in contract.nodes)
    published = ast.literal_eval(
        assignment(source("tradingagents/web/application/research.py"), "REPORT_LABELS")
    )
    assert {key for node in contract.nodes for key in node.report_keys} == set(published)
    worker = source("tradingagents/web/workers/research.py")
    publish_reports = next(
        node
        for node in ast.walk(worker)
        if isinstance(node, ast.FunctionDef) and node.name == "publish_reports"
    )
    nested = next(
        node
        for node in ast.walk(publish_reports)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
    )
    nested_fields = {key for _, keys in ast.literal_eval(nested.iter) for key in keys}
    history_nodes = [
        node
        for node in contract.nodes
        if node.id in {"bull", "bear", "risk_aggressive", "risk_conservative", "risk_neutral"}
    ]
    assert {key for node in history_nodes for key in node.report_keys} == nested_fields
    assert next(node for node in contract.nodes if node.id == "portfolio_manager").report_keys == [
        "final_trade_decision"
    ]


def test_memory_conditional_model_use_and_rating_resolution_match_real_implementations():
    contract = {node.id: node for node in definition().nodes}
    memory = contract["memory_log"]
    assert memory.kind == "auxiliary" and memory.optional and memory.tier == "quick"
    assert memory.llm_usage == "conditional" and memory.report_keys == ["memory_note"]
    graph = source("tradingagents/graph/trading_graph.py")
    memory_step = next(
        node
        for node in ast.walk(graph)
        if isinstance(node, ast.FunctionDef) and node.name == "_memory_step"
    )
    settles = member_calls(memory_step, "settle_all_pending")
    assert len(settles) == 1 and any(
        keyword.arg == "wait" and ast.literal_eval(keyword.value) is False
        for keyword in settles[0].keywords
    )
    injection = member_calls(graph, "setup_graph")
    assert any(
        keyword.arg == "memory_node"
        and isinstance(keyword.value, ast.Attribute)
        and keyword.value.attr == "_memory_step"
        for call in injection
        for keyword in call.keywords
    )
    reflection = source("tradingagents/memory/reflection.py")
    assert any(
        isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "quick_thinking_llm"
        for call in member_calls(reflection, "invoke")
    )
    assert [node.id for node in contract.values() if "past_context" in node.input_keys] == [
        "portfolio_manager"
    ]
    run_rating = next(
        node
        for node in source("tradingagents/agents/rating.py").body
        if isinstance(node, ast.FunctionDef) and node.name == "run_rating"
    )
    assert not member_calls(run_rating, "invoke")
    assert contract["rating_resolution"].llm_usage == contract["archive"].llm_usage == "none"
    assert contract["rating_resolution"].graph_node is contract["archive"].graph_node is None


def test_definition_is_isolated_and_contains_no_live_task_states_or_user_paths():
    original = research_workflow_definition()
    changed = research_workflow_definition()
    changed["nodes"][0]["notes"].append("local change")
    assert research_workflow_definition() == original
    assert all(
        not reference.startswith("/") and (ROOT.parent / reference.split(":", 1)[0]).is_file()
        for reference in original["source_refs"]
    )
    assert all(
        "status" not in node and "run_id" not in node and "completed" not in node
        for node in original["nodes"]
    )
    node_ids = {node["id"] for node in original["nodes"]} | {"start", "end"}
    assert all(
        set(edge["source_ids"]) <= node_ids and edge["target_id"] in node_ids
        for edge in original["edges"]
    )


def test_router_is_read_only_and_returns_the_explicit_static_contract():
    app = FastAPI()
    app.include_router(create_workflow_router(research_workflow_definition))
    with TestClient(app) as client:
        response = client.get("/api/v1/research-workflow")
        assert response.status_code == 200
        assert response.json() == research_workflow_definition()
        assert client.post("/api/v1/research-workflow").status_code == 405
    schema = app.openapi()
    assert (
        schema["paths"]["/api/v1/research-workflow"]["get"]["operationId"] == "getResearchWorkflow"
    )
    unavailable = FastAPI()
    unavailable.include_router(create_workflow_router())
    with TestClient(unavailable) as client:
        assert client.get("/api/v1/research-workflow").status_code == 503


def test_offline_schema_and_definition_can_serve_without_importing_runtime_or_reading_config(
    tmp_path,
):
    script = ROOT / "scripts/export_openapi.py"
    domain = ROOT / "tradingagents/web/domain/research_workflow.py"
    code = """
import importlib.util, socket, sqlite3, sys, types
from fastapi import FastAPI
from fastapi.testclient import TestClient
def blocked(*args, **kwargs):
    raise AssertionError('Workflow runtime side effect')
dotenv = types.ModuleType('dotenv')
dotenv.find_dotenv = dotenv.load_dotenv = blocked
sys.modules['dotenv'] = dotenv
socket.create_connection = sqlite3.connect = blocked
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
exporter = load('offline_contract', sys.argv[1])
schema = exporter.contract_document()
assert '/api/v1/research-workflow' in schema['paths']
domain = load('static_workflow_definition', sys.argv[2])
router = sys.modules['_tradingagents_offline_api_contract.workflow_router']
app = FastAPI()
app.include_router(router.create_workflow_router(domain.research_workflow_definition))
with TestClient(app) as client:
    assert client.get('/api/v1/research-workflow').json()['role_count'] == 12
assert 'tradingagents' not in sys.modules
assert 'tradingagents.web.server' not in sys.modules
assert 'langgraph' not in sys.modules
print('STATIC_WORKFLOW_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(script), str(domain)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "STATIC_WORKFLOW_OK"
    assert list(tmp_path.iterdir()) == []
