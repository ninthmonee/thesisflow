"""Idempotent local-mode identity; no credentials are created or imported."""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from .models import Membership, User, Watchlist, Workspace, utc_now

LOCAL_USER_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/user"))
LOCAL_WORKSPACE_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/workspace"))
LOCAL_WATCHLIST_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/watchlist"))
LOCAL_MEMBERSHIP_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/membership"))


@dataclass(frozen=True)
class LocalWorkspace:
    user_id: str
    workspace_id: str
    watchlist_id: str
    name: str


def ensure_local_workspace(session: Session) -> LocalWorkspace:
    """Caller owns the transaction; repeated/concurrent initialization is safe."""
    now = utc_now()
    records = [
        (User, {"id": LOCAL_USER_ID, "name": "本机用户", "created_at": now}),
        (
            Workspace,
            {
                "id": LOCAL_WORKSPACE_ID,
                "owner_id": LOCAL_USER_ID,
                "name": "本机工作区",
                "created_at": now,
            },
        ),
        (
            Membership,
            {
                "id": LOCAL_MEMBERSHIP_ID,
                "workspace_id": LOCAL_WORKSPACE_ID,
                "user_id": LOCAL_USER_ID,
                "role": "owner",
                "created_at": now,
            },
        ),
    ]
    for model, values in records:
        session.execute(
            insert(model).values(**values).on_conflict_do_nothing(index_elements=["id"])
        )
    workspace = session.get(Workspace, LOCAL_WORKSPACE_ID)
    assert workspace is not None
    watchlist_id = session.scalar(
        select(Watchlist.id)
        .where(
            Watchlist.workspace_id == LOCAL_WORKSPACE_ID,
            Watchlist.owner_id == LOCAL_USER_ID,
        )
        .order_by(Watchlist.position, Watchlist.created_at)
        .limit(1)
    )
    if watchlist_id is None:
        session.execute(
            insert(Watchlist)
            .values(
                id=LOCAL_WATCHLIST_ID,
                workspace_id=LOCAL_WORKSPACE_ID,
                owner_id=LOCAL_USER_ID,
                name="我的自选",
                position=0,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        watchlist_id = LOCAL_WATCHLIST_ID
    return LocalWorkspace(LOCAL_USER_ID, LOCAL_WORKSPACE_ID, watchlist_id, workspace.name)
