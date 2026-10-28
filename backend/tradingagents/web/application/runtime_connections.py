"""Explicit, isolated local Agent runtimes with versioned, private model bindings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
from copy import deepcopy
from pathlib import Path
from threading import BoundedSemaphore
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, select, text

from tradingagents.web.api.model_schemas import ModelConfigurationError
from tradingagents.web.api.runtime_schemas import (
    RuntimeConfigurationError,
    RuntimeConnectionCreate,
)
from tradingagents.web.application.models import ModelConfigurationService
from tradingagents.web.domain.model_access import is_local_owner
from tradingagents.web.infrastructure.models import (
    Membership,
    ModelConnection,
    ModelProfile,
    utc_now,
)
from tradingagents.web.infrastructure.recovery_policy import requires_explicit_model_binding
from tradingagents.web.infrastructure.runtime_models import AgentRuntimeConnection


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RuntimeConnectionService:
    def __init__(self, database, workspace, *, secret_store=None, probe=None, model_service=None):
        self.database, self.workspace = database, workspace
        self.models = model_service or ModelConfigurationService(database, workspace, secret_store=secret_store)
        self.probe = probe or self._default_probe
        self._probe_slot = BoundedSemaphore(1)

    @staticmethod
    def _default_probe(options):
        from tradingagents.web.infrastructure.agent_runtimes.codex import CodexRuntimeAdapter

        return CodexRuntimeAdapter(options["executable_path"]).probe(auth_options=options["auth_options"])

    def _authorize(self, session):
        if session.scalar(select(Membership.id).where(
            Membership.workspace_id == self.workspace.workspace_id,
            Membership.user_id == self.workspace.user_id,
        )) is None:
            raise RuntimeConfigurationError("runtime_forbidden", "当前用户无权访问此工作区运行环境。", 403)

    def _require_host(self):
        with self.database.session() as session:
            self._authorize(session)
        if not is_local_owner(self.workspace.workspace_id, self.workspace.user_id):
            raise RuntimeConfigurationError("runtime_host_forbidden",
                "本机Runtime仅供原本机所有者使用；其他账号需连接自己的隔离运行环境。", 403)

    def _owned(self, session, identifier):
        self._authorize(session)
        item = session.scalar(select(AgentRuntimeConnection).where(
            AgentRuntimeConnection.id == identifier,
            AgentRuntimeConnection.workspace_id == self.workspace.workspace_id,
            AgentRuntimeConnection.owner_id == self.workspace.user_id,
        ))
        if item is None:
            raise RuntimeConfigurationError("runtime_not_found", "运行环境连接不存在。", 404)
        return item

    @staticmethod
    def _revision(item, revision):
        if item.revision != revision:
            raise RuntimeConfigurationError("revision_conflict", "运行环境配置已改变，请刷新后重试。", 409)

    @staticmethod
    def _executable(configured):
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                raise RuntimeConfigurationError("runtime_path_invalid", "请填写程序的绝对路径，不填写命令或参数。", 422)
        else:
            found = shutil.which("codex")
            if not found:
                found = next((str(path) for path in (
                    Path("/Applications/Codex.app/Contents/Resources/codex"),
                    Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
                ) if path.is_file()), None)
            if not found:
                raise RuntimeConfigurationError("runtime_not_installed", "未找到Codex CLI，请安装后检测或填写程序绝对路径。", 409)
            candidate = Path(found)
        try:
            path = candidate.resolve(strict=True)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
                raise OSError()
        except (OSError, RuntimeError):
            raise RuntimeConfigurationError("runtime_path_invalid", "程序路径不存在或不是可执行的普通文件。", 409) from None
        return str(path)

    def _home(self, identifier, *, create=False):
        # IDs are generated server-side and already checked against owned records.
        if not re.fullmatch(r"[a-f0-9-]{36}", identifier):
            raise RuntimeConfigurationError("runtime_scope_invalid", "运行环境归属无效。", 403)
        parent = self.database.path.parent / "runtime-auth"
        home = parent / identifier
        for path in (parent, home):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeConfigurationError("runtime_home_invalid", "独立Runtime目录不可用，请检查本机私有状态目录。", 409)
            if create:
                path.mkdir(mode=0o700, exist_ok=True)
                path.chmod(0o700)
        return home

    @staticmethod
    def _program_stamp(executable):
        info = Path(executable).stat()
        return {"path": executable, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "inode": info.st_ino, "device": info.st_dev}

    def _binding(self, item, *, require_tested=True):
        if item.auth_mode != "model_connection":
            if item.model_profile_id:
                raise RuntimeConfigurationError("runtime_binding_invalid", "本机登录模式不能绑定API模型配置。", 422)
            return None
        with self.database.session() as session:
            self._authorize(session)
            profile = session.scalar(select(ModelProfile).where(
                ModelProfile.id == item.model_profile_id,
                ModelProfile.workspace_id == self.workspace.workspace_id,
            ))
            connection = session.get(ModelConnection, profile.connection_id) if profile else None
            if (profile is None or connection is None
                or connection.workspace_id != self.workspace.workspace_id
                or connection.owner_id != self.workspace.user_id):
                raise RuntimeConfigurationError("runtime_model_not_found", "所选模型配置不存在于当前账号。", 404)
            if connection.auth_type == "oauth_local" or connection.provider == "chatgpt":
                raise RuntimeConfigurationError("runtime_model_auth_unsupported",
                    "Runtime不能搬运模型连接的本机OAuth；请选择独立登录或API模型连接。", 409)
            try:
                snapshot = self.models._snapshot(profile, connection)
            except ModelConfigurationError as error:
                raise RuntimeConfigurationError(error.code, error.message, error.status) from None
            if snapshot["api_protocol"] != "openai_responses":
                raise RuntimeConfigurationError("runtime_model_protocol_unsupported",
                    "Codex Runtime需要Responses协议，不能直接使用仅Chat Completions的模型连接。", 409)
            if item.model_id != profile.model_id:
                raise RuntimeConfigurationError("runtime_model_mismatch", "运行环境模型ID必须与绑定的模型配置一致。", 409)
            if snapshot.get("parameters"):
                raise RuntimeConfigurationError("runtime_model_parameters_unsupported",
                    "此Runtime入口仅复用服务地址、认证和模型ID；请使用无额外推理参数的模型配置，并在Runtime中设置思考档位。", 409)
            if require_tested and (profile.test_status != "passed" or not (
                profile.capabilities.get("tools") is True or profile.capabilities.get("tools") == "supported"
            )):
                raise RuntimeConfigurationError("runtime_model_not_tested", "绑定模型需要通过聊天和工具调用检测。", 409)
            return snapshot

    def _configuration(self, item):
        binding = self._binding(item, require_tested=False)
        return {
            "connection_id": item.id, "workspace_id": item.workspace_id, "owner_id": item.owner_id,
            "runtime_type": item.runtime_type, "executable_path": item.executable_path,
            "model_id": item.model_id, "reasoning_effort": item.reasoning_effort,
            "auth_mode": item.auth_mode, "model_profile_id": item.model_profile_id,
            "model_binding_hash": _digest(binding) if binding else None,
        }

    def _options(self, item, *, create_home=True):
        if item.auth_mode == "runtime_login" and requires_explicit_model_binding(self.database):
            raise RuntimeConfigurationError("runtime_recovery_auth_required",
                "恢复的工作区不能继承本机Runtime登录；请使用重新绑定并检测的API模型连接。", 409)
        executable = self._executable(item.executable_path)
        auth = {"mode": item.auth_mode, "runtime_home": str(self._home(item.id, create=create_home))}
        binding = self._binding(item)
        if binding:
            try:
                auth.update(endpoint=binding["base_url"], provider=binding["provider"],
                    auth_type="custom_headers" if binding["auth_type"] == "headers" else binding["auth_type"],
                    api_key=self.models._read_secret(binding["credential_ref"]),
                    headers=self.models._read_headers(binding.get("extra_header_ref")))
            except ModelConfigurationError as error:
                raise RuntimeConfigurationError(error.code, error.message, error.status) from None
        fingerprint = (item.probe_result or {}).get("auth_fingerprint")
        if isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            auth["expected_auth_fingerprint"] = fingerprint
        return {"executable_path": executable, "model_id": item.model_id,
            "reasoning_effort": item.reasoning_effort, "auth_options": auth}

    def _view(self, item):
        probe = item.probe_result or {}
        readiness, message = item.readiness, probe.get("message", "尚未检测程序、认证和研究工具协议。")
        if readiness == "ready":
            try:
                if probe.get("configuration_hash") != _digest(self._configuration(item)):
                    readiness, message = "stale", "模型或运行环境配置已改变，需要重新检测。"
                elif item.auth_mode == "runtime_login" and requires_explicit_model_binding(self.database):
                    readiness, message = "stale", "恢复的工作区需要重新绑定Runtime认证。"
            except RuntimeConfigurationError:
                readiness, message = "stale", "绑定的模型已不可用，需要重新配置和检测。"
        command = None
        if item.auth_mode == "runtime_login" and not requires_explicit_model_binding(self.database):
            executable = (probe.get("program_stamp") or {}).get("path") or item.executable_path or "codex"
            command = " ".join(shlex.quote(part) for part in (
                "env", f"CODEX_HOME={self._home(item.id)}", executable,
                "-c", 'cli_auth_credentials_store="keyring"', "login",
            ))
        return {
            "id": item.id, "name": item.name, "runtime_type": item.runtime_type,
            "transport": "stdio", "execution_host": "server_local", "executable_path": item.executable_path,
            "model_id": item.model_id, "reasoning_effort": item.reasoning_effort,
            "enabled": item.enabled, "auth_mode": item.auth_mode, "model_profile_id": item.model_profile_id,
            "login_command": command, "revision": item.revision, "readiness": readiness,
            "program_version": probe.get("program_version"), "auth_status": probe.get("auth_status", "unknown"),
            "capabilities": probe.get("capabilities", {}), "models": probe.get("models", []),
            "message": message, "checked_at": item.checked_at,
            "created_at": item.created_at, "updated_at": item.updated_at,
        }

    def list_types(self):
        with self.database.session() as session:
            self._authorize(session)
        available = is_local_owner(self.workspace.workspace_id, self.workspace.user_id)
        return [{"id": kind, "name": name, "implemented": kind == "codex", "host_access": available,
            "transport": "stdio" if kind == "codex" else "planned",
            "message": ("当前账号可配置独立Codex运行环境；检测不调用模型。" if available
                else "请使用自己的个人连接器；当前版本不向其他账号开放主人的机器。")
                if kind == "codex" else "尚未实现此Runtime适配，不能启动研究。"}
            for kind, name in (("codex", "Codex"), ("claude_code", "Claude Code"), ("opencode", "OpenCode"))]

    def list_connections(self):
        with self.database.session() as session:
            self._authorize(session)
            items = session.scalars(select(AgentRuntimeConnection).where(
                AgentRuntimeConnection.workspace_id == self.workspace.workspace_id,
                AgentRuntimeConnection.owner_id == self.workspace.user_id,
            ).order_by(AgentRuntimeConnection.created_at)).all()
            return [self._view(item) for item in items]

    def create_connection(self, values):
        self._require_host()
        data = self._validated_values(values)
        item = AgentRuntimeConnection(id=str(uuid4()), workspace_id=self.workspace.workspace_id,
            owner_id=self.workspace.user_id, **data)
        self._binding(item, require_tested=False)
        self._home(item.id, create=True)
        with self.database.session() as session:
            self._authorize(session)
            session.add(item)
            session.flush()
            return self._view(item)

    def update_connection(self, identifier, values):
        self._require_host()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            item = self._owned(session, identifier)
            self._revision(item, values["revision"])
            candidate = {key: getattr(item, key) for key in RuntimeConnectionCreate.model_fields}
            candidate.update({key: value for key, value in values.items() if key != "revision"})
            data = self._validated_values(candidate)
            changed = any(getattr(item, key) != value for key, value in data.items())
            invalidates = any(getattr(item, key) != value for key, value in data.items()
                if key not in {"name", "enabled"})
            for key, value in data.items():
                setattr(item, key, value)
            self._binding(item, require_tested=False)
            if invalidates:
                item.readiness, item.probe_result, item.checked_at = "stale", {}, None
            if changed:
                item.revision += 1
                item.updated_at = utc_now()
            session.flush()
            return self._view(item)

    @staticmethod
    def _validated_values(values):
        try:
            return RuntimeConnectionCreate.model_validate(values).model_dump()
        except ValidationError:
            raise RuntimeConfigurationError("runtime_configuration_invalid",
                "运行环境配置无效，请检查名称、模型和认证绑定。", 422) from None

    def delete_connection(self, identifier, revision):
        self._require_host()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            item = self._owned(session, identifier)
            self._revision(item, revision)
            session.execute(delete(AgentRuntimeConnection).where(AgentRuntimeConnection.id == item.id))
        # Authentication directories are private Runtime-owned data. Removing the
        # connection revokes access; do not silently delete the user's CLI login.

    @staticmethod
    def _sanitize_probe(raw):
        caps = raw.get("capabilities") or {}
        auth = raw.get("auth_status")
        result = {
            "program_version": str(raw.get("program_version", "unknown"))[:120],
            "auth_status": "authorized" if auth in {"authorized", "api_key"} else "not_authorized"
                if auth in {"missing", "not_authorized"} else "unknown",
            "capabilities": {
                "dynamic_tools": caps.get("dynamic_tools") is True,
                "cancel": caps.get("cancellation", caps.get("cancel")) is True,
                "resume": False,
                "model_discovery": isinstance(raw.get("models"), list),
            },
            "models": [{"id": value["id"]} for value in (raw.get("models") or [])[:500]
                if isinstance(value, dict) and isinstance(value.get("id"), str)
                and re.fullmatch(r"[^\s\x00-\x1f]{1,240}", value["id"])],
            "restricted_tools": caps.get("restricted_tools") is True,
        }
        identity = raw.get("auth_fingerprint")
        if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{64}", identity):
            result["auth_fingerprint"] = identity
        return result

    def test_connection(self, identifier, revision):
        self._require_host()
        if not self._probe_slot.acquire(blocking=False):
            raise RuntimeConfigurationError("runtime_probe_busy", "已有Runtime正在检测，请等待结果后再试。", 503)
        try:
            return self._test_connection(identifier, revision)
        finally:
            self._probe_slot.release()

    def _test_connection(self, identifier, revision):
        with self.database.session() as session:
            item = self._owned(session, identifier)
            self._revision(item, revision)
            config_hash = _digest(self._configuration(item))
        try:
            options = self._options(item)
            # An explicit test intentionally accepts a newly selected account;
            # frozen runs retain the prior identity and cannot inherit it.
            options["auth_options"].pop("expected_auth_fingerprint", None)
            before_stamp = self._program_stamp(options["executable_path"])
            probe = self._sanitize_probe(dict(self.probe(options)))
            after_path = self._executable(item.executable_path)
            if after_path != options["executable_path"] or self._program_stamp(after_path) != before_stamp:
                raise RuntimeConfigurationError("runtime_program_changed", "检测期间程序发生变化，请等待更新结束后重新检测。", 409)
            probe["program_stamp"] = before_stamp
            success = (probe["auth_status"] == "authorized"
                and probe["capabilities"]["dynamic_tools"] and probe["restricted_tools"])
            message = ("程序、认证和受控工具协议就绪；本次未执行模型调用，实际模型在研究启动后核对。" if success
                else "Runtime尚未就绪，请检查独立登录、程序版本和受控工具协议；本次未执行模型调用。")
        except Exception as error:
            probe, success = {}, False
            message = error.message if isinstance(error, RuntimeConfigurationError) else "Runtime检测失败，请检查程序、独立登录和协议版本；原始日志未回显。"
        probe.update(message=message, configuration_hash=config_hash)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            current = self._owned(session, identifier)
            self._revision(current, revision)
            if _digest(self._configuration(current)) != config_hash:
                raise RuntimeConfigurationError("runtime_configuration_changed", "检测期间配置发生变化，请重新检测。", 409)
            current.readiness, current.probe_result = "ready" if success else "failed", probe
            current.checked_at, current.updated_at = utc_now(), utc_now()
            current.revision += 1
            session.flush()
            return {"connection": self._view(current), "success": success, "message": message}

    def freeze_for_run(self, identifier):
        self._require_host()
        with self.database.session() as session:
            item = self._owned(session, identifier)
            self._require_ready(item)
            self._binding(item)
            snapshot = self._configuration(item)
            # Paths and credential references stay in storage, not the public
            # immutable run manifest. Only their binding digest is published.
            snapshot.pop("executable_path")
            snapshot.update(version=1, connection_revision=item.revision,
                id=item.id, name=item.name, transport="stdio", execution_host="server_local",
                configuration_hash=(item.probe_result or {}).get("configuration_hash"),
                program_version=(item.probe_result or {}).get("program_version"))
            snapshot["signature"] = _digest(snapshot)
            return snapshot

    def _require_ready(self, item):
        if not item.enabled:
            raise RuntimeConfigurationError("runtime_disabled", "运行环境尚未启用，请检测并启用后再启动研究。", 409)
        if self._view(item)["readiness"] != "ready":
            raise RuntimeConfigurationError("runtime_not_ready", "运行环境未通过当前配置的检测，请重新检测。", 409)
        try:
            if self._program_stamp(self._executable(item.executable_path)) != (item.probe_result or {}).get("program_stamp"):
                raise RuntimeConfigurationError("runtime_program_changed", "程序已更新或路径已改变，请重新检测。", 409)
        except OSError:
            raise RuntimeConfigurationError("runtime_program_changed", "程序已更新或路径已改变，请重新检测。", 409) from None
        if item.auth_mode == "runtime_login" and requires_explicit_model_binding(self.database):
            raise RuntimeConfigurationError("runtime_recovery_auth_required", "恢复的工作区需要重新绑定Runtime认证。", 409)

    def validate_snapshot(self, snapshot):
        self._require_host()
        if not isinstance(snapshot, dict) or snapshot.get("workspace_id") != self.workspace.workspace_id or snapshot.get("owner_id") != self.workspace.user_id:
            raise RuntimeConfigurationError("runtime_scope_invalid", "Runtime快照不属于当前账号或工作区。", 403)
        unsigned = {key: value for key, value in snapshot.items() if key != "signature"}
        if snapshot.get("signature") != _digest(unsigned):
            raise RuntimeConfigurationError("runtime_snapshot_invalid", "Runtime快照校验失败。", 409)
        current = self.freeze_for_run(snapshot.get("connection_id"))
        if current != snapshot:
            raise RuntimeConfigurationError("runtime_configuration_changed", "Runtime或绑定的模型已改变，请创建新的研究。", 409)

    def runtime_options(self, snapshot):
        self.validate_snapshot(snapshot)
        with self.database.session() as session:
            item = self._owned(session, snapshot["connection_id"])
            return deepcopy(self._options(item))
