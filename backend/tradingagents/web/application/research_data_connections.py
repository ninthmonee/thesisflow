"""Owner-scoped account configuration, frozen references, and private runtime resolution."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager, suppress
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select, text

from tradingagents.web.api.research_data_schemas import (
    ResearchDataConnectionCreate,
    ResearchDataConnectionError,
    ResearchDataConnectionTestResponse,
    ResearchDataConnectionUpdate,
    ResearchDataConnectionView,
    validated_jev_url,
)
from tradingagents.web.infrastructure.models import Membership, utc_now
from tradingagents.web.infrastructure.research_data_model import ResearchDataConnectionRecord
from tradingagents.web.infrastructure.secrets import KeyringSecretStore, SecretStoreError

PROVIDERS = ("fred", "alpha_vantage", "typesafe_jev", "sec_edgar")
ENVIRONMENT_NAMES = {
    "fred": "FRED_API_KEY",
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "typesafe_jev": "TYPESAFE_API_KEY",
    "sec_edgar": "SEC_EDGAR_USER_AGENT",
}
JEV_BASE = "https://api.typesafe.ai"
JEV_MODEL = "jev-latest"


class ResearchDataConnectionService:
    def __init__(
        self, database, local_workspace, *, secret_store=None, tester=None, environment_reader=None
    ):
        self.database = database
        self.workspace_id, self.owner_id = local_workspace.workspace_id, local_workspace.user_id
        self.secrets = (
            secret_store
            if secret_store is not None
            else KeyringSecretStore("TradingAgents.Web.ResearchData")
        )
        self.tester = tester or self._probe
        self.environment = environment_reader or os.getenv

    def _authorize(self, session, *, write=False):
        role = session.scalar(
            select(Membership.role).where(
                Membership.workspace_id == self.workspace_id, Membership.user_id == self.owner_id
            )
        )
        if role is None or (write and role != "owner"):
            raise ResearchDataConnectionError(
                "research_data_forbidden", "当前用户无权操作此研究数据配置", 403
            )

    def _rows(self, session):
        return session.scalars(
            select(ResearchDataConnectionRecord).where(
                ResearchDataConnectionRecord.workspace_id == self.workspace_id,
                ResearchDataConnectionRecord.owner_id == self.owner_id,
            )
        ).all()

    def _owned(self, session, identifier):
        row = session.scalar(
            select(ResearchDataConnectionRecord).where(
                ResearchDataConnectionRecord.id == identifier,
                ResearchDataConnectionRecord.workspace_id == self.workspace_id,
                ResearchDataConnectionRecord.owner_id == self.owner_id,
            )
        )
        if row is None:
            raise ResearchDataConnectionError("research_data_not_found", "研究数据连接不存在", 404)
        return row

    @staticmethod
    def _cas(row, revision):
        if row.revision != revision:
            raise ResearchDataConnectionError(
                "research_data_revision_conflict", "数据配置已更新，请刷新后重试", 409
            )

    @staticmethod
    def _view(row):
        return ResearchDataConnectionView(
            id=row.id,
            name=row.name,
            provider=row.provider,
            credential_source=row.credential_source,
            enabled=row.enabled,
            revision=row.revision,
            credential_configured=bool(row.credential_ref),
            user_agent_configured=bool(row.user_agent_ref),
            model=row.model,
            base_url=row.base_url,
            test_status=row.test_status,
            tested_at=row.tested_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_connections(self):
        with self.database.session() as session:
            self._authorize(session)
            return [
                self._view(row)
                for row in sorted(
                    self._rows(session), key=lambda row: PROVIDERS.index(row.provider)
                )
            ]

    @staticmethod
    def _raw(value, *, identity=False):
        raw = value.get_secret_value().strip()
        if (
            not raw
            or len(raw) > (512 if identity else 8192)
            or any(char in raw for char in ("\r", "\n", "\x00"))
        ):
            raise ResearchDataConnectionError(
                "invalid_research_credential", "凭据或联系身份为空、过长或包含控制字符", 422
            )
        if identity and not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", raw):
            raise ResearchDataConnectionError(
                "invalid_sec_identity", "SEC联系身份须包含自己的联系邮箱", 422
            )
        return raw

    def _put(self, raw):
        reference = f"research-data/{self.workspace_id}/{self.owner_id}/{uuid4()}"
        try:
            self.secrets.set(reference, raw)
        except SecretStoreError:
            raise ResearchDataConnectionError(
                "secret_store_unavailable", "系统密钥库不可用，未保存明文凭据", 503
            ) from None
        return reference

    def _discard(self, reference):
        if reference:
            with suppress(SecretStoreError):
                self.secrets.delete(reference)

    def _get(self, reference):
        if not reference or not reference.startswith(
            f"research-data/{self.workspace_id}/{self.owner_id}/"
        ):
            raise ResearchDataConnectionError(
                "research_credential_missing", "研究凭据不存在，请重新保存配置", 409
            )
        try:
            value = self.secrets.get(reference)
        except SecretStoreError:
            raise ResearchDataConnectionError(
                "secret_store_unavailable", "系统密钥库当前不可用", 503
            ) from None
        if not value:
            raise ResearchDataConnectionError(
                "research_credential_missing", "研究凭据不存在，请重新保存配置", 409
            )
        return value

    def _legacy_value(self, provider):
        from pydantic import SecretStr

        raw = self.environment(ENVIRONMENT_NAMES[provider])
        if not isinstance(raw, str) or not raw.strip():
            raise ResearchDataConnectionError(
                "legacy_research_credential_missing",
                "显式选择的本机环境凭据不存在，请填写凭据或联系身份",
                409,
            )
        return self._raw(SecretStr(raw), identity=provider == "sec_edgar")

    @staticmethod
    def _validate_fields(provider, source, api_key, user_agent, model, base_url):
        if provider != "typesafe_jev" and (model is not None or base_url is not None):
            raise ResearchDataConnectionError(
                "research_field_not_supported", "该数据商不接受模型或自定义入口设置", 422
            )
        if (provider == "sec_edgar" and api_key is not None) or (
            provider != "sec_edgar" and user_agent is not None
        ):
            raise ResearchDataConnectionError(
                "research_field_not_supported", "SEC仅需联系身份；其他数据商只接收对应API Key", 422
            )
        if source == "legacy_environment" and (api_key is not None or user_agent is not None):
            raise ResearchDataConnectionError(
                "ambiguous_research_source", "选择环境兼容来源时不要同时提交新凭据", 422
            )

    def create_connection(self, body):
        body = ResearchDataConnectionCreate.model_validate(body)
        self._validate_fields(
            body.provider,
            body.credential_source,
            body.api_key,
            body.user_agent,
            body.model,
            body.base_url,
        )
        if (
            body.enabled
            and body.credential_source == "keychain"
            and (body.user_agent if body.provider == "sec_edgar" else body.api_key) is None
        ):
            raise ResearchDataConnectionError(
                "research_credential_required", "启用前请填写该供应商所需的凭据或联系身份", 422
            )
        created = []
        try:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._authorize(session, write=True)
                if any(row.provider == body.provider for row in self._rows(session)):
                    raise ResearchDataConnectionError(
                        "research_provider_exists", "此供应商已有配置，请编辑现有连接", 409
                    )
                row = ResearchDataConnectionRecord(
                    id=str(uuid4()),
                    workspace_id=self.workspace_id,
                    owner_id=self.owner_id,
                    provider=body.provider,
                    name=body.name.strip(),
                    credential_source=body.credential_source,
                    enabled=body.enabled,
                    model=body.model.strip() if body.model else None,
                    base_url=body.base_url,
                    test_status="untested",
                    revision=1,
                )
                if body.credential_source == "legacy_environment" and body.enabled:
                    reference = self._put(self._legacy_value(body.provider))
                    created.append(reference)
                    setattr(
                        row,
                        "user_agent_ref" if body.provider == "sec_edgar" else "credential_ref",
                        reference,
                    )
                    if body.provider == "typesafe_jev":
                        settings = self._settings(row)
                        row.model, row.base_url = settings["model"], settings["base_url"]
                if not row.name:
                    raise ResearchDataConnectionError(
                        "invalid_research_name", "请输入连接名称", 422
                    )
                for field, value, identity in (
                    ("credential_ref", body.api_key, False),
                    ("user_agent_ref", body.user_agent, True),
                ):
                    if value is not None:
                        reference = self._put(self._raw(value, identity=identity))
                        created.append(reference)
                        setattr(row, field, reference)
                session.add(row)
                session.flush()
                result = self._view(row)
            return result
        except Exception:
            for reference in created:
                self._discard(reference)
            raise

    def update_connection(self, identifier, body):
        body = ResearchDataConnectionUpdate.model_validate(body)
        created, retired = [], []
        try:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._authorize(session, write=True)
                row = self._owned(session, identifier)
                self._cas(row, body.revision)
                source = body.credential_source or row.credential_source
                self._validate_fields(
                    row.provider, source, body.api_key, body.user_agent, body.model, body.base_url
                )
                if source != row.credential_source and source == "legacy_environment":
                    retired.extend((row.credential_ref, row.user_agent_ref))
                    row.credential_ref = row.user_agent_ref = None
                row.credential_source = source
                if (
                    body.credential_source == "legacy_environment"
                    and row.provider == "typesafe_jev"
                ):
                    # An explicit import takes the environment's public settings too.
                    row.model, row.base_url = body.model, body.base_url
                for field, value, identity in (
                    ("credential_ref", body.api_key, False),
                    ("user_agent_ref", body.user_agent, True),
                ):
                    if value is not None:
                        reference = self._put(self._raw(value, identity=identity))
                        created.append(reference)
                        retired.append(getattr(row, field))
                        setattr(row, field, reference)
                for field in ("name", "model", "base_url", "enabled"):
                    value = getattr(body, field)
                    if value is not None:
                        setattr(row, field, value.strip() if isinstance(value, str) else value)
                if (
                    source == "legacy_environment"
                    and row.enabled
                    and (
                        body.credential_source == "legacy_environment"
                        or not (
                            row.user_agent_ref
                            if row.provider == "sec_edgar"
                            else row.credential_ref
                        )
                    )
                ):
                    field = "user_agent_ref" if row.provider == "sec_edgar" else "credential_ref"
                    reference = self._put(self._legacy_value(row.provider))
                    created.append(reference)
                    retired.append(getattr(row, field))
                    setattr(row, field, reference)
                    if row.provider == "typesafe_jev":
                        settings = self._settings(row)
                        row.model, row.base_url = settings["model"], settings["base_url"]
                if not row.name:
                    raise ResearchDataConnectionError(
                        "invalid_research_name", "请输入连接名称", 422
                    )
                if (
                    row.enabled
                    and row.credential_source == "keychain"
                    and not (
                        row.user_agent_ref if row.provider == "sec_edgar" else row.credential_ref
                    )
                ):
                    raise ResearchDataConnectionError(
                        "research_credential_required", "启用前须保存凭据或SEC联系身份", 422
                    )
                row.revision += 1
                row.updated_at, row.tested_at, row.test_status = utc_now(), None, "untested"
                session.flush()
                result = self._view(row)
        except Exception:
            for reference in created:
                self._discard(reference)
            raise
        for reference in retired:
            self._discard(reference)
        return result

    def clear_connection(self, identifier, revision):
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            row = self._owned(session, identifier)
            self._cas(row, revision)
            retired = row.credential_ref, row.user_agent_ref
            row.credential_ref = row.user_agent_ref = None
            row.enabled, row.credential_source = False, "keychain"
            row.revision += 1
            row.updated_at, row.tested_at, row.test_status = utc_now(), None, "untested"
            session.flush()
            result = self._view(row)
        for reference in retired:
            self._discard(reference)
        return result

    def _settings(self, row):
        settings = {}
        if row.provider == "typesafe_jev":
            legacy = row.credential_source == "legacy_environment"
            try:
                settings["base_url"] = validated_jev_url(
                    row.base_url
                    or (self.environment("TYPESAFE_BASE_URL") if legacy else None)
                    or JEV_BASE
                )
            except ValueError:
                raise ResearchDataConnectionError(
                    "unsupported_jev_endpoint",
                    "已配置的Jev入口不在当前已适配官方入口中，请修改连接",
                    409,
                ) from None
            model = (
                row.model
                or (self.environment("TYPESAFE_DEFAULT_MODEL") if legacy else None)
                or JEV_MODEL
            )
            if (
                not isinstance(model, str)
                or not 1 <= len(model.strip()) <= 120
                or any(char in model for char in ("\r", "\n", "\x00"))
            ):
                raise ResearchDataConnectionError(
                    "invalid_jev_model", "Jev模型配置无效，请修改连接", 409
                )
            settings["model"] = model.strip()
        return settings

    def freeze_for_run(self, workspace_id, owner_id):
        if (workspace_id, owner_id) != (self.workspace_id, self.owner_id):
            raise ResearchDataConnectionError(
                "research_data_forbidden", "研究数据配置不属于此用户或工作区", 403
            )
        with self.database.session() as session:
            self._authorize(session)
            rows = {row.provider: row for row in self._rows(session)}
            frozen = {}
            for provider in PROVIDERS:
                row = rows.get(provider)
                frozen[provider] = (
                    {"state": "unconfigured"}
                    if row is None
                    else {
                        "connection_id": row.id,
                        "revision": row.revision,
                        "state": "configured" if row.enabled else "disabled",
                        "credential_source": row.credential_source,
                        "settings": self._settings(row) if row.enabled else {},
                    }
                )
            return {"schema_version": 1, "providers": frozen}

    def validate_snapshot(self, snapshot):
        if snapshot != self.freeze_for_run(self.workspace_id, self.owner_id):
            raise ResearchDataConnectionError(
                "research_data_configuration_changed",
                "研究数据配置已变更；请重新研究，不会切换凭据或回退环境",
                409,
            )

    def build_runtime_resolver(self, snapshot):
        frozen = deepcopy(snapshot)

        def verify():
            self.validate_snapshot(frozen)

        verify()

        def resolve(provider):
            verify()
            entry = frozen["providers"].get(provider, {"state": "unconfigured"})
            if entry.get("state") != "configured":
                return {}
            with self.database.session() as session:
                self._authorize(session)
                row = self._owned(session, entry["connection_id"])
                if row.revision != entry["revision"] or not row.enabled:
                    raise ResearchDataConnectionError(
                        "research_data_configuration_changed",
                        "研究数据配置已变更；请重新研究，不会切换凭据或回退环境",
                        409,
                    )
                result = deepcopy(entry["settings"])
                target = "user_agent" if provider == "sec_edgar" else "api_key"
                result[target] = self._get(
                    row.user_agent_ref if provider == "sec_edgar" else row.credential_ref
                )
                return result

        resolve.verify = verify
        return resolve

    @contextmanager
    def runtime_context_for_legacy(self, snapshot=None):
        from tradingagents.dataflows.credentials import data_credentials_scope

        frozen = (
            snapshot
            if snapshot is not None
            else self.freeze_for_run(self.workspace_id, self.owner_id)
        )
        resolver = self.build_runtime_resolver(frozen)
        with data_credentials_scope(resolver):
            yield resolver
            resolver.verify()

    def test_connection(self, identifier, revision):
        with self.database.session() as session:
            self._authorize(session, write=True)
            row = self._owned(session, identifier)
            self._cas(row, revision)
            if not row.enabled:
                raise ResearchDataConnectionError(
                    "research_data_disabled", "请先启用连接后再检测", 409
                )
            provider = row.provider
        resolver = self.build_runtime_resolver(
            self.freeze_for_run(self.workspace_id, self.owner_id)
        )
        success, message = False, "端点检测失败，请检查凭据、权限或额度；未自动购买订阅"
        try:
            settings = resolver(provider)
            self.tester(provider, settings)
            success, message = (
                True,
                "所选端点已响应；不代表其他付费端点、新闻全量或全部数据权限已获授权",
            )
        except ResearchDataConnectionError as error:
            message = error.message
        except Exception:
            pass
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._authorize(session, write=True)
            row = self._owned(session, identifier)
            self._cas(row, revision)
            row.revision += 1
            row.tested_at = row.updated_at = utc_now()
            row.test_status = "passed" if success else "failed"
            session.flush()
            result = self._view(row)
        return ResearchDataConnectionTestResponse(
            connection=result, success=success, message=message
        )

    @staticmethod
    def _probe(provider, settings):
        # Install query redaction for FRED/Alpha's protocol-required query authentication.
        from tradingagents.dataflows import net as _net
        from tradingagents.dataflows.vendors.market_alternatives import OfficialHTTP

        _net.ensure_query_redaction()

        http = OfficialHTTP()
        if provider == "fred":
            result = http.json(
                "https://api.stlouisfed.org/fred/series",
                params={
                    "series_id": "FEDFUNDS",
                    "file_type": "json",
                    "api_key": settings["api_key"],
                },
            )
            valid = bool(result.get("seriess")) and not result.get("error_code")
        elif provider == "alpha_vantage":
            result = http.json(
                "https://www.alphavantage.co/query",
                params={"function": "OVERVIEW", "symbol": "MSFT", "apikey": settings["api_key"]},
            )
            valid = bool(result.get("Symbol")) and not any(
                result.get(key) for key in ("Information", "Note", "Error Message")
            )
        elif provider == "sec_edgar":
            result = http.json(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": settings["user_agent"]},
            )
            valid = bool(result) and any(
                isinstance(item, dict) and "cik_str" in item for item in result.values()
            )
        else:
            from tradingagents.agents.post_screen import QUESTIONS, system_one

            result = system_one(
                {"instrument": "Microsoft (MSFT)", "post": "Microsoft develops software."},
                QUESTIONS,
                settings=settings,
            )
            valid = bool(result.get("about"))
        if not valid:
            raise ResearchDataConnectionError(
                "research_account_not_verified", "账号未通过此端点验证，请检查凭据与权限", 409
            )
