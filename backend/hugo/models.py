from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    STRATEGY_PENDING = "strategy_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_FUNDING = "awaiting_funding"
    ACTIVE = "active"
    MEASURING = "measuring"
    LEARNING = "learning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DealStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    VETTED = "vetted"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    CONTACTED = "contacted"
    CONTRACTED = "contracted"
    DRAFT_QA = "draft_qa"
    DRAFT_APPROVED = "draft_approved"
    FINAL_QA = "final_qa"
    SUBMITTED = "submitted"
    QA_RUNNING = "qa_running"
    REVISION_REQUIRED = "revision_required"
    VERIFIED = "verified"
    PAYOUT_QUEUED = "payout_queued"
    TRANSFERRED = "transferred"
    REJECTED = "rejected"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Brand(Base, TimestampMixin):
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    niche: Mapped[str] = mapped_column(String(100), default="general")
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    campaigns: Mapped[list[Campaign]] = relationship(back_populates="brand")


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(30), default="tiktok")
    budget_cents: Mapped[int] = mapped_column(Integer)
    per_creator_cap_cents: Mapped[int] = mapped_column(Integer)
    payout_model: Mapped[str] = mapped_column(String(30), default="flat")
    compensation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compensation_source: Mapped[str] = mapped_column(String(30), default="legacy")
    compensation_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    operation_mode: Mapped[str] = mapped_column(String(50), default="full_autonomy")
    measurement_window_hours: Mapped[int] = mapped_column(Integer, default=72)
    qa_mode: Mapped[str] = mapped_column(String(20), default="two_stage")
    learning_mode: Mapped[str] = mapped_column(String(40), default="database")
    status: Mapped[str] = mapped_column(String(40), default=CampaignStatus.DRAFT.value)
    version: Mapped[int] = mapped_column(Integer, default=1)
    hermes_response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_views: Mapped[int] = mapped_column(Integer, default=0)
    actual_engagements: Mapped[int] = mapped_column(Integer, default=0)
    actual_conversions: Mapped[int] = mapped_column(Integer, default=0)
    metrics_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    brand: Mapped[Brand] = relationship(back_populates="campaigns")
    strategy: Mapped[CampaignStrategy | None] = relationship(
        back_populates="campaign", uselist=False
    )
    experiments: Mapped[list[Experiment]] = relationship(back_populates="campaign")
    deals: Mapped[list[Deal]] = relationship(back_populates="campaign")


class CampaignStrategy(Base, TimestampMixin):
    __tablename__ = "campaign_strategies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), unique=True)
    creator_tier: Mapped[str] = mapped_column(String(30))
    target_creators: Mapped[int] = mapped_column(Integer)
    target_rate_cents: Mapped[int] = mapped_column(Integer)
    primary_allocation: Mapped[float] = mapped_column(Float, default=0.8)
    challenger_allocation: Mapped[float] = mapped_column(Float, default=0.2)
    rationale: Mapped[str] = mapped_column(Text)
    projected_cost_per_result: Mapped[float] = mapped_column(Float, default=0)
    skill_version: Mapped[int] = mapped_column(Integer, default=1)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    campaign: Mapped[Campaign] = relationship(back_populates="strategy")


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("campaign_id", "name", name="uq_experiment_campaign_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    hypothesis: Mapped[str] = mapped_column(Text)
    variant: Mapped[str] = mapped_column(String(40))
    allocation: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="planned")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    campaign: Mapped[Campaign] = relationship(back_populates="experiments")


class Creator(Base, TimestampMixin):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_creator_platform_handle"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    handle: Mapped[str] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    platform: Mapped[str] = mapped_column(String(30), default="tiktok")
    followers: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0)
    fake_follower_percent: Mapped[float] = mapped_column(Float, default=0)
    stripe_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stripe_onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reputation: Mapped[CreatorReputation | None] = relationship(
        back_populates="creator", uselist=False
    )


class CreatorReputation(Base, TimestampMixin):
    __tablename__ = "creator_reputations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), unique=True)
    performance: Mapped[float] = mapped_column(Float, default=50)
    reliability: Mapped[float] = mapped_column(Float, default=50)
    audience_quality: Mapped[float] = mapped_column(Float, default=50)
    overall: Mapped[float] = mapped_column(Float, default=50)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    creator: Mapped[Creator] = relationship(back_populates="reputation")


class Deal(Base, TimestampMixin):
    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("campaign_id", "creator_id", name="uq_deal_campaign_creator"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default=DealStatus.DISCOVERED.value)
    fit_score: Mapped[float] = mapped_column(Float, default=0)
    agreed_rate_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compensation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    draft_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    final_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    replacement_attempt: Mapped[int] = mapped_column(Integer, default=0)
    replacement_for_id: Mapped[str | None] = mapped_column(ForeignKey("deals.id"), nullable=True)
    campaign: Mapped[Campaign] = relationship(back_populates="deals")
    creator: Mapped[Creator] = relationship()
    deliverables: Mapped[list[Deliverable]] = relationship(back_populates="deal")
    messages: Mapped[list[DealMessage]] = relationship(back_populates="deal")


class DealMessage(Base, TimestampMixin):
    __tablename__ = "deal_messages"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_message_external"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    direction: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(30), default="gmail")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    proposed_rate_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deal: Mapped[Deal] = relationship(back_populates="messages")


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Deliverable(Base, TimestampMixin):
    __tablename__ = "deliverables"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    caption: Mapped[str] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    post_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default="final")
    qa_status: Mapped[str] = mapped_column(String(30), default="pending")
    deal: Mapped[Deal] = relationship(back_populates="deliverables")
    checks: Mapped[list[QACheck]] = relationship(back_populates="deliverable")


class QACheck(Base, TimestampMixin):
    __tablename__ = "qa_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deliverable_id: Mapped[str] = mapped_column(ForeignKey("deliverables.id"), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(Boolean)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(150))
    deliverable: Mapped[Deliverable] = relationship(back_populates="checks")


class FundingPayment(Base, TimestampMixin):
    __tablename__ = "funding_payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    checkout_session_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    source_charge_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class ServiceSpend(Base, TimestampMixin):
    __tablename__ = "service_spend"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending_approval")
    spend_request_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    context: Mapped[str] = mapped_column(Text)


class Payout(Base, TimestampMixin):
    __tablename__ = "payouts"
    __table_args__ = (UniqueConstraint("deal_id", "component", name="uq_payout_deal_component"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), index=True)
    payout_model: Mapped[str] = mapped_column(String(30), default="flat")
    component: Mapped[str] = mapped_column(String(30), default="base")
    amount_cents: Mapped[int] = mapped_column(Integer)
    measured_metric: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="blocked")
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, default=new_id)


class LedgerEntry(Base, TimestampMixin):
    __tablename__ = "ledger_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(40))
    amount_cents: Mapped[int] = mapped_column(Integer)
    reference_id: Mapped[str] = mapped_column(String(150))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class StrategyPrior(Base, TimestampMixin):
    __tablename__ = "strategy_priors"
    __table_args__ = (UniqueConstraint("niche", "creator_tier", name="uq_prior_niche_tier"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    niche: Mapped[str] = mapped_column(String(100))
    creator_tier: Mapped[str] = mapped_column(String(30))
    observations: Mapped[int] = mapped_column(Integer, default=0)
    mean_cost_per_result: Mapped[float] = mapped_column(Float, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0)


class AlgorithmPlaybook(Base, TimestampMixin):
    __tablename__ = "algorithm_playbooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class DomainEvent(Base):
    __tablename__ = "domain_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxJob(Base, TimestampMixin):
    __tablename__ = "outbox_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(80))
    dedupe_key: Mapped[str] = mapped_column(String(150), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HermesTask(Base, TimestampMixin):
    """Durable work item for the Hermes cron loop.

    Lifecycle: pending → claimed → completed | failed.
    Leases expire after lease_duration_seconds (default 180).
    Expired leases revert to pending on the next claim sweep.
    """

    __tablename__ = "hermes_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    deal_id: Mapped[str | None] = mapped_column(ForeignKey("deals.id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    LEASE_SECONDS = 180


class ApprovalRequest(Base, TimestampMixin):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decision_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MessagingChannel(Base, TimestampMixin):
    __tablename__ = "messaging_channels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(30), unique=True, default="telegram")
    chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    pairing_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_update_id: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class MessagingReceipt(Base):
    __tablename__ = "messaging_receipts"
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), default="telegram")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningRun(Base, TimestampMixin):
    __tablename__ = "learning_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), unique=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    baseline_status: Mapped[str] = mapped_column(String(30), default="pending")
    database_updates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    patch_status: Mapped[str] = mapped_column(String(30), default="disabled")
    patch_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    skill_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkillVersion(Base, TimestampMixin):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_name", "version", name="uq_skill_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    skill_name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    governance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
