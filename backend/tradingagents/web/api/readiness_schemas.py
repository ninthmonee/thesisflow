"""Public local runtime readiness, distinct from API liveness and private worker names."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import ContractModel


class WorkerReadiness(ContractModel):
    state: Literal["ready", "busy", "offline"]
    active_workers: int = Field(ge=0)
    idle_workers: int = Field(ge=0)
    busy_workers: int = Field(ge=0)
    last_heartbeat_at: datetime | None
    current_run_id: str | None


class QueueReadiness(ContractModel):
    queued_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    cancelling_count: int = Field(ge=0)
    stalled_count: int = Field(ge=0)
    oldest_queued_at: datetime | None


class RuntimeReadiness(ContractModel):
    status: Literal["ready", "degraded"]
    api: Literal["ready"] = "ready"
    checked_at: datetime
    worker: WorkerReadiness
    queue: QueueReadiness
    scope_notice: str = "API可用不等于研究执行器在线；队列与当前研究仅显示当前工作区所有者范围。心跳在线也不承诺模型或数据供应商连通。"
