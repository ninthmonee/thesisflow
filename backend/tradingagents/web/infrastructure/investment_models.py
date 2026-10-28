"""Owned editable policy, immutable revisions and manual decision plans."""

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime, Versioned


class InvestmentPolicyRecord(Versioned, Base):
    __tablename__ = "investment_policies"
    __table_args__ = (UniqueConstraint("workspace_id", "owner_id"), CheckConstraint("revision >= 1", name="revision"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[dict] = mapped_column(JSON)


class InvestmentPolicyVersionRecord(Base):
    __tablename__ = "investment_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("investment_policies.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column()
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(UTCDateTime())


class InvestmentPlanRecord(Versioned, Base):
    __tablename__ = "investment_plans"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision"),
        Index("ix_investment_plans_workspace_owner", "workspace_id", "owner_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    body: Mapped[dict] = mapped_column(JSON)
    policy_snapshot: Mapped[dict] = mapped_column(JSON)
    reviewed_at: Mapped[object | None] = mapped_column(UTCDateTime())
