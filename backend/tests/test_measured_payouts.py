import pytest
from hugo.config import get_settings
from hugo.db import SessionLocal
from hugo.models import (
    Brand,
    Campaign,
    CampaignStatus,
    CampaignStrategy,
    Creator,
    CreatorReputation,
    Deal,
    DealStatus,
    FundingPayment,
    Payout,
)
from hugo.providers import build_providers
from hugo.schemas import MetricsCreate
from hugo.services import record_metrics_and_close
from sqlalchemy import select


def _measuring_campaign(db, payout_model: str) -> tuple[Campaign, Deal]:
    brand = Brand(name="Measured brand", niche="fitness")
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name=f"{payout_model} campaign",
        goal="Verify measured payout settlement",
        budget_cents=50_000,
        per_creator_cap_cents=20_000,
        payout_model=payout_model,
        status=CampaignStatus.MEASURING.value,
    )
    db.add(campaign)
    db.flush()
    db.add(
        CampaignStrategy(
            campaign_id=campaign.id,
            creator_tier="micro",
            target_creators=1,
            target_rate_cents=10_000,
            rationale="Measured payout test",
            projected_cost_per_result=0.02,
            approved=True,
        )
    )
    creator = Creator(
        handle=f"measured-{payout_model}",
        stripe_account_id=f"acct_test_{payout_model}",
        stripe_onboarding_complete=True,
    )
    db.add(creator)
    db.flush()
    db.add(CreatorReputation(creator_id=creator.id))
    deal = Deal(
        campaign_id=campaign.id,
        creator_id=creator.id,
        status=(
            DealStatus.TRANSFERRED.value
            if payout_model == "hybrid"
            else DealStatus.VERIFIED.value
        ),
        agreed_rate_cents=10_000,
        terms_accepted=True,
    )
    db.add(deal)
    db.flush()
    db.add(
        FundingPayment(
            campaign_id=campaign.id,
            amount_cents=campaign.budget_cents,
            status="succeeded",
            payment_intent_id=f"pi_test_{payout_model}",
            source_charge_id=f"ch_test_{payout_model}",
        )
    )
    if payout_model == "hybrid":
        db.add(
            Payout(
                campaign_id=campaign.id,
                deal_id=deal.id,
                creator_id=creator.id,
                payout_model="hybrid",
                component="base",
                amount_cents=10_000,
                status="transferred",
                stripe_transfer_id="tr_test_base",
            )
        )
    else:
        db.add(
            Payout(
                campaign_id=campaign.id,
                deal_id=deal.id,
                creator_id=creator.id,
                payout_model=payout_model,
                component="performance",
                amount_cents=0,
                status="awaiting_measurement",
            )
        )
    db.commit()
    return campaign, deal


@pytest.mark.parametrize(
    (
        "payout_model",
        "views",
        "engagements",
        "conversions",
        "expected_amount",
        "expected_metric",
    ),
    [
        ("cpm", 1_000, 100, 0, 10_000, 1_000),
        ("engagement", 2_000, 500, 0, 5_000, 500),
        ("hybrid", 1_000, 100, 0, 5_000, 1_000),
        ("affiliate", 2_000, 100, 2, 20_000, 2),
    ],
)
def test_measured_models_settle_after_metrics_and_learn_after_transfer(
    payout_model, views, engagements, conversions, expected_amount, expected_metric
):
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        campaign, deal = _measuring_campaign(db, payout_model)
        result = record_metrics_and_close(
            db,
            campaign,
            MetricsCreate(views=views, engagements=engagements, conversions=conversions),
            settings,
            providers,
        )

        assert result.status == CampaignStatus.COMPLETED.value
        assert result.metrics_recorded is True
        performance = db.scalar(
            select(Payout).where(
                Payout.deal_id == deal.id,
                Payout.component == "performance",
            )
        )
        assert performance.status == "transferred"
        assert performance.amount_cents == expected_amount
        assert performance.measured_metric == expected_metric

        assert campaign.status == CampaignStatus.COMPLETED.value
        assert campaign.metrics_recorded is True
        assert campaign.deals[0].status == DealStatus.TRANSFERRED.value
        assert campaign.run_id


def test_measured_payout_is_clamped_to_remaining_creator_cap():
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        campaign, deal = _measuring_campaign(db, "cpm")
        record_metrics_and_close(
            db,
            campaign,
            MetricsCreate(views=100_000, engagements=10_000),
            settings,
            providers,
        )
        performance = db.scalar(select(Payout).where(Payout.deal_id == deal.id))
        assert performance.amount_cents == campaign.per_creator_cap_cents
