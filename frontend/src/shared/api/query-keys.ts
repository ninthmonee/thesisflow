export interface QueryScope {
  userId: string;
  workspaceId: string;
}

export function scopeForWorkspace(workspace: { owner_id: string; id: string }): QueryScope {
  return { userId: workspace.owner_id, workspaceId: workspace.id };
}

export const queryKeys = {
  scope: (scope: QueryScope) => ["workspace", scope.userId, scope.workspaceId] as const,
  bootstrap: (scope: QueryScope) => [...queryKeys.scope(scope), "bootstrap"] as const,
  resource: (
    scope: QueryScope,
    resource: string,
    parameters: Readonly<Record<string, unknown>> = {},
  ) => [...queryKeys.scope(scope), resource, parameters] as const,
};
