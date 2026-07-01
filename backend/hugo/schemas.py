from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = None
    niche: str = "general"
    policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "approval_mode": "new_creators",
            "min_fit_score": 70,
            "max_fake_follower_percent": 15,
            "min_reputation": 60,
            "max_revision_rounds": 2,
        }
    )


class BrandRead(ORMModel):
    id: str
    name: str
    website: str | None
    niche: str
    policy: dict[str, Any]


class CompensationComponent(BaseModel):
    kind: Literal["base", "cpm", "engagement", "affiliate"]
    rate_cents: int = Field(ge=1)


class CompensationPlan(BaseModel):
    pricing_mode: Literal["user", "hugo"] = "user"
    components: list[CompensationComponent] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def unique_components(self):
        kinds = [component.kind for component in self.components]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Each compensation component may appear only once")
        if self.pricing_mode == "user" and not self.components:
            raise ValueError("User-priced compensation requires at least one component")
        return self


class CampaignCreate(BaseModel):
    brand_id: str
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=5)
    platform: Literal["tiktok", "instagram", "youtube"] = "tiktok"
    budget_cents: int = Field(ge=100)
    per_creator_cap_cents: int = Field(ge=100)
    payout_model: Literal["flat", "cpm", "engagement", "hybrid", "affiliate"] = "flat"
    compensation: CompensationPlan | None = None
    operation_mode: Literal["strategy_creators", "strategy_creators_payments", "full_autonomy"] = (
        "full_autonomy"
    )
    measurement_window_hours: int = Field(default=72, ge=1, le=720)
    learning_mode: Literal["database", "database_and_skill_patch"] = "database"


class CampaignUpdate(BaseModel):
    learning_mode: Literal["database", "database_and_skill_patch"]


class CampaignRead(ORMModel):
    id: str
    run_id: str
    brand_id: str
    name: str
    goal: str
    platform: str
    budget_cents: int
    per_creator_cap_cents: int
    payout_model: str
    compensation: dict[str, Any]
    compensation_source: str
    compensation_locked: bool
    operation_mode: str
    measurement_window_hours: int
    learning_mode: str
    status: str
    version: int
    actual_conversions: int
    metrics_recorded: bool


class StrategyRead(ORMModel):
    id: str
    campaign_id: str
    creator_tier: str
    target_creators: int
    target_rate_cents: int
    primary_allocation: float
    challenger_allocation: float
    rationale: str
    projected_cost_per_result: float
    skill_version: int
    approved: bool


class ApprovalCreate(BaseModel):
    campaign_id: str
    resource_type: Literal["strategy", "deal", "service_spend", "payout"]
    resource_id: str
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    expected_version: int | None = None


class OutreachCreate(BaseModel):
    proposed_rate_cents: int | None = Field(default=None, ge=1)


class CreatorResponseCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    external_id: str = Field(min_length=1, max_length=255)
    thread_id: str | None = Field(default=None, max_length=255)


class DeliverableCreate(BaseModel):
    caption: str
    media_url: str | None = None
    post_url: str | None = None
    stage: Literal["draft", "final"] = "final"


class DealMetricsCreate(BaseModel):
    deal_id: str
    views: int = Field(ge=0)
    engagements: int = Field(ge=0)
    conversions: int = Field(default=0, ge=0)
    source_url: str | None = None
    evidence: str | None = Field(default=None, max_length=1000)


class MetricsCreate(BaseModel):
    views: int = Field(ge=0)
    engagements: int = Field(ge=0)
    conversions: int = Field(default=0, ge=0)
    deal_metrics: list[DealMetricsCreate] = Field(default_factory=list)


class AgentActionRequest(BaseModel):
    campaign_id: str | None = None
    resource_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class LearningCommit(BaseModel):
    summary: str = Field(min_length=10, max_length=4000)
    evidence_ids: list[str]
    skill_name: str = Field(pattern=r"^hugo-[a-z0-9-]+$")
    change_type: Literal["patch", "no_op"] = "patch"
    heuristic: str | None = Field(default=None, max_length=2000)
    no_op_reason: str | None = Field(default=None, max_length=2000)
    governance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_change(self):
        if self.change_type == "patch" and (not self.heuristic or len(self.heuristic) < 10):
            raise ValueError("A patch requires a heuristic of at least 10 characters")
        if self.change_type == "no_op" and not self.no_op_reason:
            raise ValueError("A no-op requires an evidence-based reason")
        return self


class QAResult(BaseModel):
    passed: bool
    severity: Literal["none", "minor", "major"]
    findings: list[dict[str, Any]]
    model: str


class DiscoveryCandidate(BaseModel):
    handle: str
    email: str | None = None
    followers: int = 0
    engagement_rate: float = 0
    fake_follower_percent: float = 0
    niche_match: float = 50
    audience_quality: float = 50
    brand_fit: float = 50
    profile_data: dict[str, Any] = Field(default_factory=dict)


class HermesStrategy(BaseModel):
    creator_tier: Literal["nano", "micro", "mid"]
    target_rate_cents: int = Field(ge=1)
    rationale: str
    projected_cost_per_result: float = Field(ge=0)
    compensation_components: list[CompensationComponent] = Field(default_factory=list)


class HermesLearning(BaseModel):
    summary: str
    change_type: Literal["patch", "no_op"] = "patch"
    heuristic: str | None = None
    no_op_reason: str | None = None
    skill_name: str = "hugo-performance-learning"
    evidence_ids: list[str]
    governance: dict[str, Any] = Field(default_factory=dict)
