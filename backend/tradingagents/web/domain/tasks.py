"""Task state and ownership tokens used by the durable worker."""

from dataclasses import dataclass
from datetime import datetime


class ResearchError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LeaseLost(ResearchError):
    def __init__(self):
        super().__init__("lease_lost", "执行租约已失效，旧执行器不能提交结果")


class RunCancelled(ResearchError):
    def __init__(self):
        super().__init__("run_cancelled", "研究已请求取消")


class RunTimedOut(ResearchError):
    def __init__(self):
        super().__init__("run_timed_out", "研究超过执行时间限制")


@dataclass(frozen=True)
class TaskClaim:
    run_id: str
    workspace_id: str
    owner_id: str
    attempt_id: str
    attempt_no: int
    lease_owner: str
    lease_token: int
    lease_until: datetime
    checkpoint_ref: str | None = None
