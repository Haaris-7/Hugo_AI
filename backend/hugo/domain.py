from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Campaign, CampaignStatus, DomainEvent, OutboxJob

CAMPAIGN_TRANSITIONS: dict[str, set[str]] = {
    CampaignStatus.DRAFT.value: {
        CampaignStatus.STRATEGY_PENDING.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.STRATEGY_PENDING.value: {
        CampaignStatus.AWAITING_APPROVAL.value,
        CampaignStatus.AWAITING_FUNDING.value,
        CampaignStatus.FAILED.value,
    },
    CampaignStatus.AWAITING_APPROVAL.value: {
        CampaignStatus.AWAITING_FUNDING.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.AWAITING_FUNDING.value: {
        CampaignStatus.ACTIVE.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.ACTIVE.value: {
        CampaignStatus.MEASURING.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.MEASURING.value: {
        CampaignStatus.COMPLETED.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.LEARNING.value: {CampaignStatus.COMPLETED.value, CampaignStatus.FAILED.value},
}


def emit(db: Session, campaign_id: str, event_type: str, payload: dict | None = None) -> None:
    db.add(DomainEvent(campaign_id=campaign_id, event_type=event_type, payload=payload or {}))


def transition_campaign(
    db: Session, campaign: Campaign, target: str, *, force: bool = False
) -> None:
    if not force and target not in CAMPAIGN_TRANSITIONS.get(campaign.status, set()):
        raise HTTPException(
            status_code=409, detail=f"Invalid campaign transition {campaign.status} -> {target}"
        )
    previous = campaign.status
    campaign.status = target
    campaign.version += 1
    emit(db, campaign.id, "campaign.status_changed", {"from": previous, "to": target})
    if target in {
        CampaignStatus.COMPLETED.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.CANCELLED.value,
    }:
        dedupe_key = f"campaign-learning:{campaign.run_id}"
        if not db.query(OutboxJob).filter_by(dedupe_key=dedupe_key).first():
            db.add(
                OutboxJob(
                    job_type="campaign_learning",
                    dedupe_key=dedupe_key,
                    payload={"campaign_id": campaign.id, "terminal_status": target},
                )
            )
        emit(
            db,
            campaign.id,
            "campaign_run_closed",
            {"run_id": campaign.run_id, "status": target},
        )


def weighted_fit_score(
    *,
    niche_match: float,
    audience_quality: float,
    engagement_rate: float,
    brand_fit: float,
    reputation: float,
) -> float:
    engagement_score = min(max(engagement_rate * 10, 0), 100)
    return round(
        niche_match * 0.30
        + audience_quality * 0.20
        + engagement_score * 0.15
        + brand_fit * 0.20
        + reputation * 0.15,
        2,
    )


def evaluate_creator_policy(
    policy: dict | None,
    *,
    fit: float,
    fake_follower_percent: float,
    reputation_overall: float,
    is_known: bool,
) -> tuple[str, str]:
    """Apply a brand's creator-handling policy to a discovered creator.

    Returns ``(decision, reason)`` where decision is ``"approved"`` (auto-proceed),
    ``"rejected"`` (auto-blocked by a hard threshold), or ``"pending"`` (held for
    operator approval). This is the backbone of policy-gated autonomy: thresholds
    auto-filter unsafe creators, and ``approval_mode`` controls how much human
    sign-off the brand requires.
    """
    policy = policy or {}
    min_fit = policy.get("min_fit_score", 0)
    max_fake = policy.get("max_fake_follower_percent", 100)
    min_reputation = policy.get("min_reputation", 0)
    mode = policy.get("approval_mode", "approve_all")

    if fit < min_fit:
        return "rejected", f"Fit score {fit} is below the brand minimum {min_fit}."
    if fake_follower_percent > max_fake:
        return (
            "rejected",
            f"Fake-follower share {fake_follower_percent}% exceeds the {max_fake}% cap.",
        )
    if is_known and reputation_overall < min_reputation:
        return (
            "rejected",
            f"Reputation {reputation_overall} is below the brand floor {min_reputation}.",
        )

    if mode == "full_autonomy":
        return "approved", "Full-autonomy policy auto-approved within all thresholds."
    if mode == "new_creators":
        if is_known and reputation_overall >= min_reputation:
            return "approved", "Known creator above the reputation floor was auto-approved."
        return "pending", "New creator held for operator approval per policy."
    return "pending", "Approve-all policy holds every creator for operator approval."


def payout_amount(
    model: str,
    rate_cents: int,
    *,
    views: int = 0,
    engagements: int = 0,
    conversions: int = 0,
) -> int:
    if model == "flat":
        return rate_cents
    if model == "cpm":
        return round(rate_cents * views / 1000)
    if model == "engagement":
        return round(rate_cents * engagements / 1000)
    if model == "hybrid":
        return rate_cents + round(rate_cents * 0.5 * views / 1000)
    if model == "affiliate":
        return rate_cents * conversions
    raise ValueError(f"Unsupported payout model: {model}")
