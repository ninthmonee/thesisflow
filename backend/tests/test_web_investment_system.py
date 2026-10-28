"""Personal policies preserve unknown inputs, arithmetic and owner boundaries."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from tradingagents.web.api.investment_schemas import (
    InvestmentPlanUpdate,
    InvestmentPlanWrite,
    InvestmentPolicyUpdate,
    InvestmentPolicyWrite,
)
from tradingagents.web.application.investment_system import InvestmentSystemService
from tradingagents.web.application.research import ResearchService
from tradingagents.web.domain.investment_system import evaluate_plan
from tradingagents.web.domain.tasks import ResearchError
from tradingagents.web.infrastructure.database import initialize_database
from tradingagents.web.infrastructure.investment_models import (
    InvestmentPolicyRecord,
    InvestmentPolicyVersionRecord,
)
from tradingagents.web.infrastructure.models import Membership, User

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
DAY = "2026-10-29"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.web.application.research.current_research_date", lambda: DAY)
    database = initialize_database(tmp_path / "state.sqlite3")
    owner = database.local_workspace()
    research = ResearchService(database, tmp_path / "research", clock=lambda: NOW)
    service = InvestmentSystemService(database, owner, research=research, clock=lambda: NOW)
    with database.session() as session:
        session.add(User(id="friend", name="Friend"))
        session.flush()
        session.add(Membership(workspace_id=owner.workspace_id, user_id="friend", role="researcher"))
    friend = InvestmentSystemService(database, SimpleNamespace(workspace_id=owner.workspace_id, user_id="friend"),
        research=research, clock=lambda: NOW)
    yield database, service, friend, research
    database.dispose()


def plan(**values):
    return InvestmentPlanWrite.model_validate({"ticker": "NVDA", "analysis_date": DAY, **values})


def rich_plan(**values):
    return plan(industry_cycle="需求增长待验证", business="销售计算平台", moat="软硬件生态待验证",
        valuation="对比现金流和增长假设", invalidation="需求与现金流不再支持假设",
        entry_price=100, fair_value=125, estimated_cost_pct=2, stop_loss_price=90,
        scenarios={"bear": {"target_price": 60, "probability_pct": 25},
            "base": {"target_price": 110, "probability_pct": 50},
            "bull": {"target_price": 160, "probability_pct": 25}},
        risk_inputs={"capital_total_usd": 10000, "available_cash_usd": 4000, "essential_cash_usd": 3000,
            "current_position_pct": 5, "target_position_pct": 10, "target_theme_exposure_pct": 25,
            "current_drawdown_pct": 8, **values})


def test_default_policy_is_read_only_and_thresholds_are_unknown(site):
    database, service, _, _ = site
    policy = service.get_policy()
    assert policy.revision == 0 and not policy.saved
    assert len(type(policy.principles).model_fields) == 8 and len(type(policy.action_rules).model_fields) == 5
    assert all(value is None for value in policy.risk_limits.model_dump().values())
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(InvestmentPolicyRecord)) == 0
    result = evaluate_plan(plan(), policy)
    assert result.status == "incomplete" and all(check.status == "unknown" for check in result.checks)
    assert result.gross_expected_return_pct is None and result.net_expected_return_pct is None
    assert result.zero_value_portfolio_loss_pct is None and not result.eligible_for_rating


def test_policy_cas_and_immutable_versions_are_owner_scoped(site):
    database, service, friend, _ = site
    first = service.update_policy(InvestmentPolicyUpdate(revision=0, risk_limits={"max_single_position_pct": 10}))
    assert first.revision == 1 and first.saved
    with pytest.raises(ResearchError, match="已被修改"):
        service.update_policy(InvestmentPolicyUpdate(revision=0))
    service.update_policy(InvestmentPolicyUpdate(revision=1, name="New", risk_limits={"max_single_position_pct": 5}))
    assert friend.get_policy().revision == 0
    with database.session() as session:
        versions = session.scalars(select(InvestmentPolicyVersionRecord).order_by(InvestmentPolicyVersionRecord.revision)).all()
        assert len(versions) == 2 and versions[0].body["risk_limits"]["max_single_position_pct"] == 10


def test_math_is_explicit_and_protective_reference_is_not_maximum_loss():
    policy = InvestmentPolicyWrite(risk_limits={"max_single_position_pct": 8,
        "max_theme_exposure_pct": 30, "min_cash_pct": 30, "max_drawdown_pct": 15,
        "max_loss_per_idea_pct": 3, "minimum_margin_of_safety_pct": 15, "min_reward_risk": 1.4})
    result = evaluate_plan(rich_plan(), policy)
    assert result.margin_of_safety_pct == 20
    assert result.gross_expected_return_pct == 10 and result.net_expected_return_pct == 8
    assert result.reward_risk_ratio == 1.5 and result.scenario_portfolio_loss_pct == 4
    assert result.zero_value_portfolio_loss_pct == 10 and result.stop_reference_loss_pct == 1
    assert result.planned_cash_usd == 3500 and result.planned_cash_pct == 35
    checks = {check.key: check for check in result.checks}
    assert checks["single_position"].status == checks["idea_loss"].status == "breach"
    assert checks["essential_cash"].status == "within_limit" and result.status == "breach"
    assert "跳空" in " ".join(result.notices) and not result.eligible_for_rating


def test_unknown_probabilities_cost_cash_and_unsupported_exposure_are_not_zero():
    draft = plan(entry_price=100, scenarios={"bear": {"target_price": 0}, "bull": {"target_price": 150}},
        risk_inputs={"target_position_pct": 10})
    result = evaluate_plan(draft, InvestmentPolicyWrite())
    assert result.bear_return_pct == -100 and result.scenario_portfolio_loss_pct == 10
    assert result.gross_expected_return_pct is None and result.net_expected_return_pct is None
    assert result.planned_cash_usd is None and result.planned_cash_pct is None
    leveraged = evaluate_plan(rich_plan(unlevered_long_only=False), InvestmentPolicyWrite())
    assert leveraged.zero_value_portfolio_loss_pct is None
    assert leveraged.scenario_portfolio_loss_pct is None and leveraged.stop_reference_loss_pct is None
    assert leveraged.planned_cash_usd is None and leveraged.status == "incomplete"
    no_cost = rich_plan().model_copy(update={"estimated_cost_pct": None})
    assert evaluate_plan(no_cost, InvestmentPolicyWrite()).net_expected_return_pct is None


@pytest.mark.parametrize("values", [
    {"entry_price": True}, {"entry_price": "100"}, {"entry_price": float("inf")},
    {"entry_price": float("nan")}, {"entry_price": 1e-300},
    {"risk_inputs": {"target_position_pct": True}}, {"risk_inputs": {"capital_total_usd": 0}},
    {"risk_inputs": {"capital_total_usd": 10, "available_cash_usd": 11}},
    {"risk_inputs": {"capital_total_usd": 100, "available_cash_usd": 80, "current_position_pct": 30}},
    {"risk_inputs": {"target_position_pct": 20, "target_theme_exposure_pct": 10}},
    {"scenarios": {"bear": {"probability_pct": 70}, "base": {"probability_pct": 40}}},
    {"scenarios": {"bear": {"probability_pct": 20}, "base": {"probability_pct": 20}, "bull": {"probability_pct": 20}}},
    {"scenarios": {"bear": {"target_price": 200}, "bull": {"target_price": 150}}},
    {"stop_loss_price": 110, "entry_price": 100}, {"analysis_date": "2026-02-30"},
    {"review_on": "2024-01-01"},
])
def test_manual_financial_values_and_dates_are_validated(values):
    with pytest.raises(ValidationError):
        plan(**values)


def test_plan_policy_snapshot_cas_archive_restore_and_review(site):
    _, service, _, _ = site
    service.update_policy(InvestmentPolicyUpdate(revision=0, risk_limits={"max_single_position_pct": 20}))
    first = service.create_plan(plan(review_on=DAY, risk_inputs={"target_position_pct": 10}))
    assert first.review_due and first.policy_snapshot.revision == 1
    service.update_policy(InvestmentPolicyUpdate(revision=1, risk_limits={"max_single_position_pct": 5}))
    before = service.get_plan(first.id)
    assert before.policy_snapshot.revision == 1
    data = plan(review_on=DAY).model_dump()
    revised = service.update_plan(first.id, InvestmentPlanUpdate(**data, revision=1, mark_reviewed=True))
    assert revised.reviewed_at == NOW and not revised.review_due and revised.policy_snapshot.revision == 1
    with pytest.raises(ResearchError, match="已被修改"):
        service.update_plan(first.id, InvestmentPlanUpdate(**data, revision=1))
    archived = service.update_plan(first.id, InvestmentPlanUpdate(**{**data, "status": "archived"}, revision=2))
    assert archived.status == "archived" and not service.list_plans()["items"]
    assert service.list_plans(include_archived=True)["items"][0].id == first.id
    restored = service.update_plan(first.id, InvestmentPlanUpdate(**data, revision=3, rebase_policy=True))
    assert restored.policy_snapshot.revision == 2


def test_foreign_plan_read_update_freeze_and_revoked_membership_are_denied(site):
    database, service, friend, _ = site
    first = service.create_plan(plan())
    assert friend.list_plans()["items"] == []
    for operation in (lambda: friend.get_plan(first.id),
        lambda: friend.update_plan(first.id, InvestmentPlanUpdate(**plan().model_dump(), revision=1)),
        lambda: friend.freeze_for_research("NVDA", DAY, first.id)):
        with pytest.raises(ResearchError, match="不存在"):
            operation()
    with database.session() as session:
        member = session.scalar(select(Membership).where(Membership.user_id == "friend"))
        session.delete(member)
    with pytest.raises(ResearchError, match="工作区不存在"):
        friend.get_policy()


def test_freeze_redacts_financial_private_notes_is_immutable_and_checks_scope(site):
    _, service, _, _ = site
    service.update_policy(InvestmentPolicyUpdate(revision=0, life_notes="PRIVATE-LIFE", tax_notes="PRIVATE-TAX"))
    first = service.create_plan(rich_plan())
    packet = service.freeze_for_research("NVDA", DAY, first.id)
    public = json.dumps(packet, ensure_ascii=False)
    for private in ("capital_total_usd", "available_cash_usd", "essential_cash_usd", "life_notes", "tax_notes", "PRIVATE-LIFE", "PRIVATE-TAX"):
        assert private not in public
    frozen = packet["snapshot"]
    digest = frozen["content_hash"]
    canonical = json.dumps({key: value for key, value in frozen.items() if key != "content_hash"}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(canonical.encode()).hexdigest() == digest
    assert frozen["plan"]["revision"] == 1 and frozen["policy"]["revision"] == 1
    service.update_policy(InvestmentPolicyUpdate(revision=1, name="changed"))
    assert packet["snapshot"]["policy"]["name"] == "我的投资体系"
    assert "未经验证" in packet["prompt"] and "六问" in packet["prompt"]
    with pytest.raises(ResearchError, match="证券不一致"):
        service.freeze_for_research("AAPL", DAY, first.id)
    with pytest.raises(ResearchError, match="晚于本次研究"):
        service.freeze_for_research("NVDA", "2024-01-02", first.id)
    service.update_policy(InvestmentPolicyUpdate(revision=2, enabled=False))
    with pytest.raises(ResearchError, match="已停用"):
        service.freeze_for_research("NVDA", DAY)


def test_plan_run_link_requires_owned_completed_matching_date_and_ticker(site):
    _, service, friend, research = site
    scope = service.scope
    queued = research.create_run({"ticker": "NVDA", "date": DAY}, **scope)
    with pytest.raises(ResearchError, match="研究完成"):
        service.create_plan(plan(run_id=queued["id"]))
    research.tasks.finish(research.tasks.claim_next("test"), "completed")
    assert str(service.create_plan(plan(run_id=queued["id"])).run_id) == queued["id"]
    with pytest.raises(ResearchError):
        friend.create_plan(plan(run_id=queued["id"]))
    with pytest.raises(ResearchError, match="同一证券"):
        service.create_plan(plan(ticker="AAPL", run_id=queued["id"]))
    with pytest.raises(ResearchError, match="同一证券"):
        service.create_plan(plan(analysis_date="2026-10-28", run_id=queued["id"]))
    with pytest.raises(ResearchError, match="晚于美东"):
        service.create_plan(plan(analysis_date="2026-10-30"))


def test_negative_planned_cash_is_a_breach_without_a_user_threshold():
    proposal = plan(risk_inputs={"capital_total_usd": 10000, "available_cash_usd": 500,
        "current_position_pct": 0, "target_position_pct": 80})
    result = evaluate_plan(proposal, InvestmentPolicyWrite())
    checks = {item.key: item for item in result.checks}
    assert result.planned_cash_usd == -7500 and checks["funding"].status == "breach"
    assert checks["cash_reserve"].status == "unknown" and result.status == "breach"


def test_create_inherits_rules_and_optional_review_interval_but_explicit_null_clears_it(site):
    _, service, _, _ = site
    policy = service.update_policy(InvestmentPolicyUpdate(revision=0, review_interval_days=30,
        action_rules={"buy": "自己的买入条件"}))
    inherited = service.create_plan(plan())
    assert inherited.review_on == "2026-11-28" and inherited.action_rules.buy == policy.action_rules.buy
    explicit = service.create_plan(plan(review_on=None, action_rules={"buy": "个股条件"}))
    assert explicit.review_on is None and explicit.action_rules.buy == "个股条件"


def test_extreme_accepted_numbers_still_serialize_as_finite_json():
    proposal = plan(entry_price=0.000001, fair_value=1e12,
        scenarios={"bear": {"target_price": 0, "probability_pct": 25},
            "base": {"target_price": 1e12, "probability_pct": 50},
            "bull": {"target_price": 1e12, "probability_pct": 25}},
        risk_inputs={"capital_total_usd": 1e-300, "available_cash_usd": 1e-300,
            "current_position_pct": 0, "target_position_pct": 100})
    result = evaluate_plan(proposal, InvestmentPolicyWrite())
    json.dumps(result.model_dump(), allow_nan=False)
    assert result.zero_value_portfolio_loss_pct == 100 and result.reward_risk_ratio is not None
