import type { ConnectionView, ProfileView, RoleView } from "../../shared/api/generated/models";

export function modelReadiness({
  defaultId,
  bindings,
  analysts,
  roles,
  profiles,
  connections,
}: {
  defaultId?: string;
  bindings: Readonly<Record<string, string>>;
  analysts: readonly string[];
  roles: readonly RoleView[];
  profiles: readonly ProfileView[];
  connections: readonly ConnectionView[];
}) {
  const issues: string[] = [];
  const profileMap = new Map(profiles.map((profile) => [profile.id, profile]));
  const connectionMap = new Map(connections.map((connection) => [connection.id, connection]));
  if (!roles.length) issues.push("角色定义尚未读取");
  if (!defaultId) issues.push("请选择默认模型");
  if (!analysts.length) issues.push("请至少启用一类分析师");
  const effective = roles.map((role) => {
    const id = bindings[role.id] || defaultId;
    const profile = id ? profileMap.get(id) : undefined;
    const connection = profile ? connectionMap.get(profile.connection_id) : undefined;
    const problems: string[] = [];
    if (!profile) problems.push("模型配置不存在或未选择");
    else {
      if (!connection?.enabled) problems.push("连接不可用或已停用");
      if (
        connection?.provider === "chatgpt" &&
        connection.local_authorization?.status !== "authorized"
      )
        problems.push("本机订阅授权未就绪");
      if (profile.test_status !== "passed") problems.push("聊天检测未通过或已失效");
      if (
        analysts.includes(role.id) &&
        profile.capabilities.tools !== true &&
        profile.capabilities.tools !== "supported"
      )
        problems.push("所选分析师需要工具检测通过");
    }
    for (const problem of problems) issues.push(`${role.label}：${problem}`);
    return { role, profile, inherited: !bindings[role.id], problems };
  });
  return { effective, issues, ready: !issues.length };
}
