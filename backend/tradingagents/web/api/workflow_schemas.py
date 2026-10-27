"""Static research topology contracts, separate from live task status and runtime config."""

from typing import Literal

from pydantic import Field

from .schemas import Analyst, ContractModel


class WorkflowToolLoop(ContractModel):
    node_ids: list[str]
    rounds_parameter: Literal["max_tool_rounds"]
    description: str


class ResearchWorkflowNode(ContractModel):
    id: str
    label: str
    kind: Literal["role", "auxiliary"]
    stage_id: str
    graph_node: str | None
    tier: Literal["quick", "deep"] | None
    llm_usage: Literal["required", "conditional", "none"]
    optional: bool
    selection_key: Analyst | None
    responsibility: str
    input_keys: list[str]
    output_keys: list[str]
    report_keys: list[str] = Field(default_factory=list)
    notes: list[str]
    tool_loop: WorkflowToolLoop | None


class ResearchWorkflowStage(ContractModel):
    id: str
    label: str
    mode: Literal["parallel", "alternating", "sequence", "cycle", "postprocess"]
    node_ids: list[str]
    description: str


class ResearchWorkflowEdge(ContractModel):
    id: str
    source_ids: list[str]
    target_id: str
    type: Literal["parallel", "join", "sequence", "loop"]
    condition: str
    optional: bool


class ResearchWorkflowLoop(ContractModel):
    id: str
    label: str
    stage_id: str
    node_ids: list[str]
    rounds_parameter: Literal["max_debate_rounds", "max_risk_discuss_rounds"]
    turns_per_round: Literal[2, 3]
    counter_key: str
    exit_node_id: str
    exit_condition: str
    description: str


class ResearchWorkflowDefinition(ContractModel):
    version: Literal["research-workflow-v1"]
    role_count: Literal[12]
    nodes: list[ResearchWorkflowNode]
    stages: list[ResearchWorkflowStage]
    edges: list[ResearchWorkflowEdge]
    loops: list[ResearchWorkflowLoop]
    notes: list[str]
    source_refs: list[str]
