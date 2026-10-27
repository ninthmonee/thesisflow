"""Persistent model connections, versioned role defaults and explicit credentials."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from threading import RLock
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from tradingagents.web.api.model_schemas import (
    ModelConfigurationError,
    TemplateWrite,
    validated_endpoint,
)
from tradingagents.web.domain.model_configuration import (
    PROVIDERS,
    ROLES,
    provider_definition,
    resolve_protocol,
    validate_parameters,
)
from tradingagents.web.infrastructure.database import Database
from tradingagents.web.infrastructure.models import (
    ModelConnection,
    ModelProfile,
    ResearchTemplateVersion,
    RoleBinding,
    utc_now,
)
from tradingagents.web.infrastructure.secrets import (
    KeyringSecretStore,
    SecretStore,
    SecretStoreError,
)
from tradingagents.web.infrastructure.seeds import LocalWorkspace

from .model_transport import client_arguments, header_values


def _hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _secret_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
    if not isinstance(raw, str):
        raise ModelConfigurationError("invalid_credential", "凭据格式无效")
    raw = raw.strip()
    if len(raw) > 16000:
        raise ModelConfigurationError("invalid_credential", "凭据长度超出限制")
    return raw or None


class ModelConfigurationService:
    def __init__(
        self,
        database: Database,
        workspace: LocalWorkspace,
        *,
        secret_store: SecretStore | None = None,
        tester: Callable[[dict, str | None], dict] | None = None,
        discoverer: Callable[[dict, str | None], list[str]] | None = None,
        local_authorization_reader: Callable[[], dict] | None = None,
    ):
        self.database, self.workspace = database, workspace
        self.secrets = secret_store if secret_store is not None else KeyringSecretStore()
        self.tester = tester or self._test_configured_model
        self.discoverer = discoverer or self._discover_configured_models
        self.local_authorization_reader = local_authorization_reader

    def _local_authorization(self) -> dict:
        try:
            if self.local_authorization_reader is None:
                from tradingagents.llm_clients.chatgpt_auth import authorization_status

                result = authorization_status()
            else:
                result = self.local_authorization_reader()
            status = result.get("status")
            identity = result.get("identity_fingerprint")
            if status == "authorized" and isinstance(identity, str) and len(identity) == 64:
                return {"status": status, "identity_fingerprint": identity}
            if status == "not_authorized":
                return {"status": status}
        except Exception:
            pass
        return {"status": "unavailable"}

    def _local_authorization_view(self) -> dict:
        status = self._local_authorization()["status"]
        return {
            "status": status,
            "message": {
                "authorized": "本机已保存ChatGPT订阅使用授权；实际可调用模型、额度和工具能力仍需检测。",
                "not_authorized": "本机尚无可用的ChatGPT订阅使用授权。请在项目根目录执行登录命令，并在浏览器同意使用订阅。",
                "unavailable": "本机授权状态暂时无法读取；未视为授权或检测通过。",
            }[status],
            "login_command": "backend/.venv/bin/tradingagents login chatgpt",
            "logout_command": "backend/.venv/bin/tradingagents logout chatgpt",
        }

    @staticmethod
    def _validate_local_connection(provider, endpoint, auth_type, scope, secret, headers):
        if provider != "chatgpt":
            return
        if (
            auth_type != "oauth_local"
            or endpoint != provider_definition("chatgpt")["default_endpoint"]
            or scope != "private"
            or secret
            or headers
        ):
            raise ModelConfigurationError(
                "invalid_local_authorization",
                "ChatGPT连接仅复用本机OAuth授权，须为私有连接及官方地址，不能填写API Key或请求头。",
            )

    def providers(self) -> list[dict]:
        return [
            {**deepcopy(item), "local_authorization": self._local_authorization_view()}
            if item["id"] == "chatgpt"
            else deepcopy(item)
            for item in PROVIDERS
        ]

    def _connection(self, session, connection_id: str, *, edit: bool = False) -> ModelConnection:
        statement = select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.workspace_id == self.workspace.workspace_id,
        )
        statement = (
            statement.where(ModelConnection.owner_id == self.workspace.user_id)
            if edit
            else statement.where(
                or_(
                    ModelConnection.owner_id == self.workspace.user_id,
                    ModelConnection.scope == "workspace",
                )
            )
        )
        item = session.scalar(statement)
        if item is None:
            raise ModelConfigurationError("connection_not_found", "模型连接不存在", 404)
        return item

    def _profile(self, session, profile_id: str, *, edit: bool = False):
        item = session.scalar(
            select(ModelProfile).where(
                ModelProfile.id == profile_id,
                ModelProfile.workspace_id == self.workspace.workspace_id,
            )
        )
        if item is None:
            raise ModelConfigurationError("profile_not_found", "模型配置不存在", 404)
        connection = self._connection(session, item.connection_id, edit=edit)
        return item, connection

    def _connection_view(self, item: ModelConnection) -> dict:
        return {
            "id": item.id,
            "name": item.name,
            "provider": item.provider,
            "vendor_name": item.vendor_name or provider_definition(item.provider)["name"],
            "api_protocol": item.api_protocol,
            "resolved_protocol": resolve_protocol(item.provider, item.endpoint, item.api_protocol),
            "headers_configured": bool(item.extra_header_ref),
            "header_names": list(item.header_names or []),
            "auth_type": item.auth_type,
            "endpoint": item.endpoint,
            "scope": item.scope,
            "enabled": item.enabled,
            "credential_configured": bool(item.credential_ref),
            "credential_mask": "••••••••" if item.credential_ref else None,
            "revision": item.revision,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "local_authorization": self._local_authorization_view()
            if item.provider == "chatgpt"
            else None,
        }

    @staticmethod
    def _profile_view(item: ModelProfile, connection: ModelConnection) -> dict:
        return {
            "id": item.id,
            "connection_id": connection.id,
            "connection_name": connection.name,
            "provider": connection.provider,
            "model_id": item.model_id,
            "parameters": deepcopy(item.parameters),
            "capabilities": deepcopy(item.capabilities),
            "test_status": item.test_status,
            "tested_at": item.tested_at,
            "revision": item.revision,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def _new_secret(self, raw: str) -> str:
        reference = f"models/{self.workspace.workspace_id}/{self.workspace.user_id}/{uuid4()}"
        try:
            self.secrets.set(reference, raw)
        except SecretStoreError:
            raise ModelConfigurationError(
                "secret_store_unavailable", "系统密钥存储不可用；凭据未写入文件。", 503
            ) from None
        return reference

    def _read_secret(self, reference: str | None) -> str | None:
        if reference is None:
            return None
        if not reference.startswith(f"models/{self.workspace.workspace_id}/"):
            raise ModelConfigurationError("credential_scope", "凭据引用不属于当前工作区", 403)
        try:
            value = self.secrets.get(reference)
        except SecretStoreError:
            raise ModelConfigurationError(
                "secret_store_unavailable", "系统密钥存储不可用。", 503
            ) from None
        if not value:
            raise ModelConfigurationError(
                "credential_missing", "模型凭据不存在，请重新保存 API Key。", 409
            )
        return value

    def _read_headers(self, reference: str | None) -> dict[str, str]:
        if not reference:
            return {}
        try:
            return header_values(json.loads(self._read_secret(reference)))
        except (TypeError, ValueError):
            raise ModelConfigurationError(
                "invalid_custom_headers", "已保存的请求头凭据无效，请重新保存。", 409
            ) from None

    def _discard_secret(self, reference: str | None) -> None:
        if reference:
            with suppress(SecretStoreError):
                self.secrets.delete(reference)

    @staticmethod
    def _revision(current: int, expected: int) -> None:
        if current != expected:
            raise ModelConfigurationError("revision_conflict", "配置已更新，请刷新后重试。", 409)

    def list_connections(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ModelConnection)
                .where(
                    ModelConnection.workspace_id == self.workspace.workspace_id,
                    or_(
                        ModelConnection.owner_id == self.workspace.user_id,
                        ModelConnection.scope == "workspace",
                    ),
                )
                .order_by(ModelConnection.created_at)
            ).all()
            return [self._connection_view(row) for row in rows]

    def create_connection(self, values: dict) -> dict:
        provider = provider_definition(values["provider"])
        auth_type = values.get("auth_type", "api_key")
        if auth_type not in provider["auth_types"]:
            raise ModelConfigurationError("invalid_auth_type", "该供应商不支持此认证方式")
        endpoint = validated_endpoint(values.get("endpoint")) or provider["default_endpoint"]
        if values["provider"] in {"openai_compatible", "custom"} and not endpoint:
            raise ModelConfigurationError("endpoint_required", "自定义模型服务需要填写地址")
        protocol = values.get("api_protocol", "auto")
        resolve_protocol(values["provider"], endpoint, protocol)
        vendor_name = values.get("vendor_name")
        if vendor_name is not None:
            vendor_name = vendor_name.strip()
            if not vendor_name:
                raise ModelConfigurationError("invalid_name", "自定义厂商名称不能为空")
        headers = header_values(values.get("extra_headers"))
        secret = _secret_value(values.get("api_key"))
        self._validate_local_connection(
            values["provider"], endpoint, auth_type, values.get("scope", "private"), secret, headers
        )
        if auth_type == "api_key" and not secret:
            raise ModelConfigurationError("credential_required", "请输入 API Key")
        if auth_type != "api_key" and secret:
            raise ModelConfigurationError("invalid_credential", "当前认证方式不应填写 API Key")
        if auth_type == "headers" and not headers:
            raise ModelConfigurationError(
                "credential_required", "请求头认证需要至少一个自定义请求头"
            )
        if auth_type == "none" and headers:
            raise ModelConfigurationError(
                "invalid_credential", "无需认证连接不应填写凭据请求头；请选择请求头认证。"
            )
        reference = self._new_secret(secret) if secret else None
        header_reference = None
        try:
            header_reference = self._new_secret(json.dumps(headers)) if headers else None
            with self.database.session() as session:
                item = ModelConnection(
                    workspace_id=self.workspace.workspace_id,
                    owner_id=self.workspace.user_id,
                    name=values["name"].strip(),
                    provider=values["provider"],
                    auth_type=auth_type,
                    credential_ref=reference,
                    endpoint=endpoint,
                    vendor_name=vendor_name,
                    api_protocol=protocol,
                    extra_header_ref=header_reference,
                    header_names=sorted(headers, key=str.lower),
                    scope=values.get("scope", "private"),
                    enabled=values.get("enabled", True),
                )
                if not item.name:
                    raise ModelConfigurationError("invalid_name", "请输入连接名称")
                session.add(item)
                session.flush()
                result = self._connection_view(item)
            return result
        except BaseException:
            self._discard_secret(reference)
            self._discard_secret(header_reference)
            raise

    def update_connection(self, connection_id: str, values: dict) -> dict:
        raw = _secret_value(values.get("api_key"))
        headers = header_values(values.get("extra_headers"))
        if headers and values.get("clear_extra_headers"):
            raise ModelConfigurationError(
                "invalid_custom_headers", "不能同时替换与清除自定义请求头。"
            )
        new_reference = new_header_reference = None
        try:
            with self.database.session() as session:
                item = self._connection(session, connection_id, edit=True)
                self._revision(item.revision, values["revision"])
                updates = {
                    key: values[key]
                    for key in ("name", "scope", "enabled")
                    if key in values and values[key] is not None
                }
                if "name" in updates:
                    updates["name"] = updates["name"].strip()
                    if not updates["name"]:
                        raise ModelConfigurationError("invalid_name", "请输入连接名称")
                if "endpoint" in values:
                    endpoint = validated_endpoint(values["endpoint"])
                    endpoint = endpoint or provider_definition(item.provider)["default_endpoint"]
                    if item.provider in {"openai_compatible", "custom"} and not endpoint:
                        raise ModelConfigurationError(
                            "endpoint_required", "自定义模型服务需要填写地址"
                        )
                    updates["endpoint"] = endpoint
                if values.get("api_protocol") is not None:
                    updates["api_protocol"] = values["api_protocol"]
                resolve_protocol(
                    item.provider,
                    updates.get("endpoint", item.endpoint),
                    updates.get("api_protocol", item.api_protocol),
                )
                if "vendor_name" in values:
                    label = values["vendor_name"]
                    updates["vendor_name"] = label.strip() if label is not None else None
                    if label is not None and not updates["vendor_name"]:
                        raise ModelConfigurationError("invalid_name", "自定义厂商名称不能为空")
                auth = values.get("auth_type") or item.auth_type
                if auth not in provider_definition(item.provider)["auth_types"]:
                    raise ModelConfigurationError("invalid_auth_type", "该供应商不支持此认证方式")
                self._validate_local_connection(
                    item.provider,
                    updates.get("endpoint", item.endpoint),
                    auth,
                    updates.get("scope", item.scope),
                    raw,
                    headers,
                )
                updates["auth_type"] = auth
                if raw:
                    if auth != "api_key":
                        raise ModelConfigurationError(
                            "invalid_credential", "当前连接不使用 API Key"
                        )
                    new_reference = self._new_secret(raw)
                    updates["credential_ref"] = new_reference
                if headers:
                    new_header_reference = self._new_secret(json.dumps(headers))
                    updates.update(
                        extra_header_ref=new_header_reference,
                        header_names=sorted(headers, key=str.lower),
                    )
                elif values.get("clear_extra_headers"):
                    updates.update(extra_header_ref=None, header_names=[])
                if auth != "api_key":
                    updates["credential_ref"] = None
                if auth == "none":
                    if headers:
                        raise ModelConfigurationError(
                            "invalid_credential",
                            "无需认证连接不应填写凭据请求头；请选择请求头认证。",
                        )
                    updates.update(extra_header_ref=None, header_names=[])
                if auth == "api_key" and not updates.get("credential_ref", item.credential_ref):
                    raise ModelConfigurationError("credential_required", "请输入 API Key")
                if auth == "headers" and not updates.get("extra_header_ref", item.extra_header_ref):
                    raise ModelConfigurationError(
                        "credential_required", "请求头认证需要至少一个自定义请求头"
                    )
                invalidates_probe = any(
                    key in updates and updates[key] != getattr(item, key)
                    for key in (
                        "credential_ref",
                        "extra_header_ref",
                        "endpoint",
                        "enabled",
                        "api_protocol",
                        "auth_type",
                    )
                )
                updates.update(revision=item.revision + 1, updated_at=utc_now())
                changed = session.execute(
                    update(ModelConnection)
                    .where(
                        ModelConnection.id == item.id,
                        ModelConnection.revision == values["revision"],
                    )
                    .values(**updates)
                ).rowcount
                if changed != 1:
                    raise ModelConfigurationError("revision_conflict", "配置已更新，请刷新。", 409)
                if invalidates_probe:
                    session.execute(
                        update(ModelProfile)
                        .where(ModelProfile.connection_id == item.id)
                        .values(
                            test_status="untested",
                            tested_at=None,
                            config_hash=None,
                            revision=ModelProfile.revision + 1,
                            updated_at=utc_now(),
                        )
                    )
                session.expire_all()
                result = self._connection_view(self._connection(session, item.id))
            # Retired references are retained: an immutable queued run may still use one.
            return result
        except BaseException:
            self._discard_secret(new_reference)
            self._discard_secret(new_header_reference)
            raise

    def delete_connection(self, connection_id: str, revision: int) -> None:
        with self.database.session() as session:
            item = self._connection(session, connection_id, edit=True)
            self._revision(item.revision, revision)
            profiles = list(
                session.scalars(
                    select(ModelProfile.id).where(ModelProfile.connection_id == item.id)
                )
            )
            if profiles and session.scalar(
                select(RoleBinding.id).where(RoleBinding.model_profile_id.in_(profiles)).limit(1)
            ):
                raise ModelConfigurationError(
                    "connection_in_use", "连接被研究模板引用，请先停用连接。", 409
                )
            session.execute(delete(ModelProfile).where(ModelProfile.connection_id == item.id))
            changed = session.execute(
                delete(ModelConnection).where(
                    ModelConnection.id == item.id,
                    ModelConnection.revision == revision,
                )
            ).rowcount
            if changed != 1:
                raise ModelConfigurationError("revision_conflict", "连接已更新，请刷新。", 409)
        # Credential references are not destroyed here: historical manifests may reference them.

    def list_profiles(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.execute(
                select(ModelProfile, ModelConnection)
                .join(ModelConnection, ModelConnection.id == ModelProfile.connection_id)
                .where(
                    ModelProfile.workspace_id == self.workspace.workspace_id,
                    or_(
                        ModelConnection.owner_id == self.workspace.user_id,
                        ModelConnection.scope == "workspace",
                    ),
                )
                .order_by(ModelProfile.created_at)
            ).all()
            return [self._profile_view(profile, connection) for profile, connection in rows]

    def create_profile(self, values: dict) -> dict:
        with self.database.session() as session:
            connection = self._connection(session, values["connection_id"], edit=True)
            parameters = validate_parameters(
                values.get("parameters", {}),
                connection.provider,
                resolve_protocol(connection.provider, connection.endpoint, connection.api_protocol),
            )
            item = ModelProfile(
                workspace_id=self.workspace.workspace_id,
                connection_id=connection.id,
                model_id=values["model_id"],
                parameters=parameters,
                capabilities={},
            )
            session.add(item)
            session.flush()
            return self._profile_view(item, connection)

    def update_profile(self, profile_id: str, values: dict) -> dict:
        with self.database.session() as session:
            item, connection = self._profile(session, profile_id, edit=True)
            self._revision(item.revision, values["revision"])
            updates = {
                "test_status": "untested",
                "tested_at": None,
                "config_hash": None,
                "capabilities": {},
                "revision": item.revision + 1,
                "updated_at": utc_now(),
            }
            if values.get("model_id") is not None:
                updates["model_id"] = values["model_id"]
            if values.get("parameters") is not None:
                updates["parameters"] = validate_parameters(
                    values["parameters"],
                    connection.provider,
                    resolve_protocol(
                        connection.provider, connection.endpoint, connection.api_protocol
                    ),
                )
            changed = session.execute(
                update(ModelProfile)
                .where(
                    ModelProfile.id == item.id,
                    ModelProfile.revision == values["revision"],
                )
                .values(**updates)
            ).rowcount
            if changed != 1:
                raise ModelConfigurationError("revision_conflict", "模型配置已更新，请刷新。", 409)
            session.expire_all()
            return self._profile_view(*self._profile(session, profile_id))

    def _snapshot(self, profile: ModelProfile, connection: ModelConnection) -> dict:
        if not connection.enabled:
            raise ModelConfigurationError("connection_disabled", "模型连接已停用", 409)
        protocol = resolve_protocol(
            connection.provider, connection.endpoint, connection.api_protocol
        )
        parameters = validate_parameters(profile.parameters, connection.provider, protocol)
        result = {
            "workspace_id": self.workspace.workspace_id,
            "connection_id": connection.id,
            "connection_revision": connection.revision,
            "profile_id": profile.id,
            "profile_revision": profile.revision,
            "provider": connection.provider,
            "model": profile.model_id,
            "base_url": connection.endpoint,
            "auth_type": connection.auth_type,
            "credential_ref": connection.credential_ref,
            "api_protocol": protocol,
            "vendor_name": connection.vendor_name
            or provider_definition(connection.provider)["name"],
            "extra_header_ref": connection.extra_header_ref,
            "parameters": deepcopy(parameters),
            "capabilities": deepcopy(profile.capabilities),
        }
        if connection.provider == "chatgpt":
            self._validate_local_connection(
                connection.provider,
                connection.endpoint,
                connection.auth_type,
                connection.scope,
                connection.credential_ref,
                connection.extra_header_ref,
            )
            current = self._local_authorization()
            if current["status"] != "authorized":
                raise ModelConfigurationError(
                    "local_authorization_required", "请先完成本机ChatGPT订阅授权，再检测模型。", 409
                )
            result["local_authorization_fingerprint"] = current["identity_fingerprint"]
        return result

    @staticmethod
    def _require_tested_authorization(profile, snapshot):
        if (
            snapshot["provider"] == "chatgpt"
            and profile.capabilities.get("local_authorization_fingerprint")
            != snapshot["local_authorization_fingerprint"]
        ):
            raise ModelConfigurationError(
                "local_authorization_changed", "本机ChatGPT授权账号已改变，请重新检测模型。", 409
            )

    def test_profile(self, profile_id: str, revision: int) -> dict:
        with self.database.session() as session:
            profile, connection = self._profile(session, profile_id, edit=True)
            self._revision(profile.revision, revision)
            snapshot = self._snapshot(profile, connection)
            fingerprint = _hash(
                {key: value for key, value in snapshot.items() if key != "capabilities"}
            )
        secret = self._read_secret(snapshot["credential_ref"])
        try:
            capabilities = dict(self.tester(deepcopy(snapshot), secret))
            # Tester returns only named verification facts, not arbitrary vendor content.
            capabilities = {
                key: value
                for key, value in capabilities.items()
                if key in {"chat", "tools", "structured_output"}
                and (
                    isinstance(value, bool) or value in {"not_checked", "supported", "unsupported"}
                )
            }
            success = capabilities.get("chat") is True or capabilities.get("chat") == "supported"
            if snapshot["provider"] == "chatgpt":
                if (
                    self._local_authorization().get("identity_fingerprint")
                    != snapshot["local_authorization_fingerprint"]
                ):
                    raise ModelConfigurationError(
                        "local_authorization_changed", "本机授权已改变。", 409
                    )
                if success:
                    capabilities["local_authorization_fingerprint"] = snapshot[
                        "local_authorization_fingerprint"
                    ]
            if not success:
                message = "聊天检测未通过，请检查模型与连接。"
            elif capabilities.get("tools") is True or capabilities.get("tools") == "supported":
                message = "聊天与最小工具调用检测通过；结构化输出能力未单独检测。"
            elif capabilities.get("tools") is False or capabilities.get("tools") == "unsupported":
                message = "聊天检测通过，但工具调用检测未通过；不能用于需要工具的分析师角色。"
            else:
                message = "聊天检测通过，工具调用尚未验证；不能用于需要工具的分析师角色。"
        except Exception:
            capabilities, success = {}, False
            message = "模型检测失败，请检查凭据、模型 ID、接口地址和网络。"
        with self.database.session() as session:
            current, connection = self._profile(session, profile_id, edit=True)
            self._revision(current.revision, revision)
            self._revision(connection.revision, snapshot["connection_revision"])
            changed = session.execute(
                update(ModelProfile)
                .where(
                    ModelProfile.id == current.id,
                    ModelProfile.revision == revision,
                )
                .values(
                    test_status="passed" if success else "failed",
                    tested_at=utc_now(),
                    config_hash=fingerprint,
                    capabilities=capabilities,
                    revision=revision + 1,
                    updated_at=utc_now(),
                )
            ).rowcount
            if changed != 1:
                raise ModelConfigurationError("revision_conflict", "模型配置已更新，请刷新。", 409)
            session.expire_all()
            result = self._profile_view(*self._profile(session, profile_id))
        return {"profile": result, "success": success, "message": message}

    def discover_models(self, connection_id: str) -> dict:
        with self.database.session() as session:
            connection = self._connection(session, connection_id)
            if not connection.enabled:
                raise ModelConfigurationError("connection_disabled", "模型连接已停用", 409)
            protocol = resolve_protocol(
                connection.provider, connection.endpoint, connection.api_protocol
            )
            if not provider_definition(connection.provider)[
                "discovery_supported"
            ] or protocol not in {"openai_chat_completions", "openai_responses"}:
                return {
                    "supported": False,
                    "models": [],
                    "message": "本版本未接入该厂商模型发现，请手动填写模型 ID。",
                }
            snapshot = {
                "provider": connection.provider,
                "base_url": connection.endpoint,
                "auth_type": connection.auth_type,
                "api_protocol": protocol,
                "extra_header_ref": connection.extra_header_ref,
            }
            reference = connection.credential_ref
        secret = self._read_secret(reference)
        try:
            models = self.discoverer(snapshot, secret)
            safe_ids = sorted(
                {
                    item
                    for item in models
                    if isinstance(item, str)
                    and 0 < len(item) <= 240
                    and not any(c.isspace() for c in item)
                }
            )
            return {
                "supported": True,
                "models": [{"id": item} for item in safe_ids[:1000]],
                "message": "模型列表已读取；列表存在不代表模型已通过调用检测。",
            }
        except Exception:
            raise ModelConfigurationError(
                "discovery_failed", "无法读取模型列表，可手动填写模型 ID。", 503
            ) from None

    def _template_id(self) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"tradingagents://{self.workspace.workspace_id}/{self.workspace.user_id}/default-models",
            )
        )

    def _latest_template(self, session):
        return session.scalar(
            select(ResearchTemplateVersion)
            .where(
                ResearchTemplateVersion.workspace_id == self.workspace.workspace_id,
                ResearchTemplateVersion.owner_id == self.workspace.user_id,
                ResearchTemplateVersion.template_id == self._template_id(),
            )
            .order_by(ResearchTemplateVersion.version.desc())
            .limit(1)
        )

    @staticmethod
    def _template_view(item: ResearchTemplateVersion) -> dict:
        config = item.configuration
        return {
            "id": item.id,
            "template_id": item.template_id,
            "version": item.version,
            "name": item.name,
            "default_profile_id": config["default_profile_id"],
            "role_bindings": deepcopy(config.get("role_bindings", {})),
            "analysts": config["analysts"],
            "depth": config["depth"],
            "language": config["language"],
            "created_at": item.created_at,
        }

    def settings(self) -> dict:
        with self.database.session() as session:
            current = self._latest_template(session)
            return {
                "roles": [
                    {"id": role, "label": label, "tier": tier} for role, label, tier in ROLES
                ],
                "default_template": self._template_view(current) if current else None,
            }

    def save_settings(self, values: dict) -> dict:
        values = TemplateWrite.model_validate(values).model_dump()
        try:
            with self.database.session() as session:
                current = self._latest_template(session)
                version = current.version if current else 0
                self._revision(version, values["expected_version"])
                bindings = {
                    role: values["role_bindings"].get(role, values["default_profile_id"])
                    for role, _, _ in ROLES
                }
                for profile_id in set(bindings.values()):
                    _, connection = self._profile(session, profile_id)
                    if not connection.enabled:
                        raise ModelConfigurationError(
                            "connection_disabled", "模板包含已停用的模型连接", 409
                        )
                item = ResearchTemplateVersion(
                    workspace_id=self.workspace.workspace_id,
                    owner_id=self.workspace.user_id,
                    template_id=self._template_id(),
                    version=version + 1,
                    name=values["name"],
                    state="published",
                    configuration={
                        key: values[key]
                        for key in (
                            "default_profile_id",
                            "role_bindings",
                            "analysts",
                            "depth",
                            "language",
                        )
                    },
                )
                session.add(item)
                session.flush()
                for role, profile_id in bindings.items():
                    session.add(
                        RoleBinding(
                            workspace_id=self.workspace.workspace_id,
                            template_version_id=item.id,
                            role=role,
                            model_profile_id=profile_id,
                            settings={"inherited": role not in values["role_bindings"]},
                        )
                    )
                session.flush()
                return self._template_view(item)
        except IntegrityError:
            raise ModelConfigurationError(
                "revision_conflict", "研究配置已更新，请刷新。", 409
            ) from None

    def list_templates(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ResearchTemplateVersion)
                .where(
                    ResearchTemplateVersion.workspace_id == self.workspace.workspace_id,
                    ResearchTemplateVersion.owner_id == self.workspace.user_id,
                )
                .order_by(ResearchTemplateVersion.created_at.desc())
            ).all()
            return [self._template_view(row) for row in rows]

    def resolve_model_snapshot(
        self,
        template_version_id: str | None = None,
        *,
        require_tested: bool = True,
        analysts: list[str] | None = None,
    ) -> dict:
        with self.database.session() as session:
            if template_version_id:
                template = session.scalar(
                    select(ResearchTemplateVersion).where(
                        ResearchTemplateVersion.id == template_version_id,
                        ResearchTemplateVersion.workspace_id == self.workspace.workspace_id,
                        ResearchTemplateVersion.owner_id == self.workspace.user_id,
                    )
                )
            else:
                template = self._latest_template(session)
            if template is None:
                raise ModelConfigurationError(
                    "models_not_configured", "请先保存默认模型配置。", 409
                )
            bindings = session.scalars(
                select(RoleBinding).where(RoleBinding.template_version_id == template.id)
            ).all()
            selected_analysts = set(
                template.configuration["analysts"] if analysts is None else analysts
            )
            analyst_roles = {"market", "social", "news", "fundamentals"}
            if not selected_analysts or not selected_analysts <= analyst_roles:
                raise ModelConfigurationError("invalid_analysts", "请选择有效分析师", 422)
            active_roles = selected_analysts | {
                role for role, _, _ in ROLES if role not in analyst_roles
            }
            configured = {binding.role: binding.model_profile_id for binding in bindings}
            ids = {
                role: configured.get(role, template.configuration["default_profile_id"])
                for role, _, _ in ROLES
                if role in active_roles
            }
            ids.update(
                quick=template.configuration["default_profile_id"],
                deep=template.configuration["default_profile_id"],
            )
            tool_roles = selected_analysts & {"market", "news", "fundamentals"}
            roles, resolved_profiles = {}, {}
            for role, profile_id in ids.items():
                profile, connection = self._profile(session, profile_id)
                if require_tested and profile.test_status != "passed":
                    raise ModelConfigurationError(
                        "model_not_tested", f"角色 {role} 使用的模型尚未通过聊天检测。", 409
                    )
                if (
                    require_tested
                    and role in tool_roles
                    and not (
                        profile.capabilities.get("tools") is True
                        or profile.capabilities.get("tools") == "supported"
                    )
                ):
                    raise ModelConfigurationError(
                        "model_tools_not_verified",
                        f"角色 {role} 的工具调用检测未通过，请检测该模型或调整绑定。",
                        409,
                    )
                if profile_id not in resolved_profiles:
                    resolved_profiles[profile_id] = self._snapshot(profile, connection)
                    if require_tested:
                        self._require_tested_authorization(profile, resolved_profiles[profile_id])
                roles[role] = deepcopy(resolved_profiles[profile_id])
            snapshot = {
                "template_version_id": template.id,
                "template_id": template.template_id,
                "version": template.version,
                "roles": roles,
            }
            snapshot["signature"] = _hash(snapshot)
            return snapshot

    def resolve_profile_snapshot(self, profile_id: str, *, require_tested: bool = True) -> dict:
        """Resolve one model for a bounded use case without requiring unrelated roles."""
        with self.database.session() as session:
            profile, connection = self._profile(session, profile_id)
            if require_tested and profile.test_status != "passed":
                raise ModelConfigurationError("model_not_tested", "所选模型尚未通过检测。", 409)
            snapshot = self._snapshot(profile, connection)
            if require_tested:
                self._require_tested_authorization(profile, snapshot)
            return snapshot

    def build_role_model_resolver(self, snapshot: dict, *, callbacks=None):
        """Return a cached graph resolver; credentials never enter the manifest."""
        from tradingagents.llm_clients.factory import create_llm_client

        cache = {}
        lock = RLock()

        def resolve(role: str):
            model = snapshot["roles"].get(role) or snapshot["roles"]["quick"]
            if model["workspace_id"] != self.workspace.workspace_id:
                raise ModelConfigurationError("credential_scope", "模型快照不属于当前工作区", 403)
            if model["provider"] == "chatgpt" and self._local_authorization().get(
                "identity_fingerprint"
            ) != model.get("local_authorization_fingerprint"):
                raise ModelConfigurationError(
                    "local_authorization_changed",
                    "本机ChatGPT授权已改变，请重新检测并创建研究。",
                    409,
                )
            identity = _hash(model)
            with lock:
                if identity not in cache:
                    provider, parameters = client_arguments(
                        model,
                        self._read_secret(model["credential_ref"]),
                        self._read_headers(model.get("extra_header_ref")),
                        callbacks=callbacks,
                    )
                    cache[identity] = create_llm_client(
                        provider, model["model"], base_url=model["base_url"], **parameters
                    ).get_llm()
                return cache[identity]

        return resolve

    def _test_configured_model(self, snapshot: dict, secret: str | None) -> dict:
        return self._test_model(
            snapshot, secret, headers=self._read_headers(snapshot.get("extra_header_ref"))
        )

    def _discover_configured_models(self, snapshot: dict, secret: str | None) -> list[str]:
        return self._discover_models(
            snapshot, secret, headers=self._read_headers(snapshot.get("extra_header_ref"))
        )

    @staticmethod
    def _test_model(snapshot: dict, secret: str | None, *, headers=None) -> dict:
        from tradingagents.llm_clients.factory import create_llm_client

        provider, parameters = client_arguments(snapshot, secret, headers or {})
        parameters.update(timeout=min(parameters.get("timeout", 30), 30), max_retries=0)
        if provider != "chatgpt":
            output_limit = min(
                parameters.pop("max_output_tokens", parameters.pop("max_tokens", 512)), 512
            )
            parameters["max_output_tokens" if provider == "google" else "max_tokens"] = output_limit
        model = create_llm_client(
            provider, snapshot["model"], base_url=snapshot["base_url"], **parameters
        ).get_llm()
        response = model.invoke("Reply only OK.")
        if not getattr(response, "content", None):
            raise RuntimeError("empty model response")
        tool_schema = {
            "type": "function",
            "function": {
                "name": "verify_model_connection",
                "description": "Verify a model connection by returning the requested integer.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        tools = "unsupported"
        try:
            response = model.bind_tools([tool_schema]).invoke(
                "Call verify_model_connection exactly once with value 7. Do not answer with text."
            )
            calls = getattr(response, "tool_calls", None)
            if isinstance(calls, list) and len(calls) == 1:
                call = calls[0]
                args = call.get("args") if isinstance(call, dict) else None
                if (
                    isinstance(call, dict)
                    and call.get("name") == "verify_model_connection"
                    and isinstance(args, dict)
                    and set(args) == {"value"}
                    and type(args["value"]) is int
                    and args["value"] == 7
                ):
                    tools = "supported"
        except Exception:
            # Chat remains independently usable; never expose a provider's raw error.
            pass
        return {"chat": True, "tools": tools, "structured_output": "not_checked"}

    @staticmethod
    def _discover_models(snapshot: dict, secret: str | None, *, headers=None) -> list[str]:
        import requests

        request_headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        request_headers.update(headers or {})
        response = requests.get(
            f"{snapshot['base_url']}/models",
            headers=request_headers,
            timeout=(5, 15),
            allow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise RuntimeError("model discovery failed")
        data = response.json()
        return [
            item["id"] for item in data.get("data", []) if isinstance(item, dict) and "id" in item
        ]
