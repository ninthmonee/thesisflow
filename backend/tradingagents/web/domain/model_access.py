"""Only the original local workspace owns process-wide provider authentication."""

from uuid import NAMESPACE_URL, uuid5

_LOCAL_USER_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/user"))
_LOCAL_WORKSPACE_ID = str(uuid5(NAMESPACE_URL, "tradingagents://local/workspace"))


def is_local_owner(workspace_id, user_id):
    """A new account or a different workspace never inherits the host's sign-in."""
    return (workspace_id, user_id) == (_LOCAL_WORKSPACE_ID, _LOCAL_USER_ID)
