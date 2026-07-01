"""Payments-infrastructure and policy-gate tests.

These exercise the money and autonomy guarantees directly against the service
layer in deterministic (Stripe-simulated) mode: the Link service-spend gate, payout
idempotency, per-creator caps, and brand-policy creator filtering.
"""

import pytest
from fastapi import HTTPException
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
    LedgerEntry,
    Payout,
    ServiceSpend,
)
from hugo.providers import build_providers
from hugo.services import (
    discover,
    record_service_spend,
    request_payout,
)
from sqlalchemy import select


def _funded_campaign(db) -> Campaign:
    brand = Brand(name="Gate brand", niche="fitness")
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name="Gate campaign",
        goal="Prove the discovery spend gate",
        budget_cents=60_000,
        per_creator_cap_cents=15_000,
        payout_model="flat",
        status=CampaignStatus.AWAITING_FUNDING.value,
    )
    db.add(campaign)
    db.flush()
    db.add(
        CampaignStrategy(
            campaign_id=campaign.id,
            creator_tier="micro",
            target_creators=3,
            target_rate_cents=10_000,
            rationale="Gate test",
            projected_cost_per_result=0.02,
            approved=True,
        )
    )
    db.add(
        FundingPayment(
            campaign_id=campaign.id,
            amount_cents=campaign.budget_cents,
            status="succeeded",
            payment_intent_id="pi_test_gate",
            source_charge_id="ch_test_gate",
        )
    )
    db.commit()
    return campaign


def _ready_flat_payout(db, *, amount_cents: int, cap_cents: int = 20_000) -> Payout:
    brand = Brand(name="Pay brand", niche="fitness")
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name="Pay campaign",
        goal="Prove payout gates",
        budget_cents=50_000,
        per_creator_cap_cents=cap_cents,
        payout_model="flat",
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(campaign)
    db.flush()
    creator = Creator(
        handle="payc",
        stripe_account_id="acct_test_pay",
        stripe_onboarding_complete=True,
    )
    db.add(creator)
    db.flush()
    db.add(CreatorReputation(creator_id=creator.id))
    deal = Deal(
        campaign_id=campaign.id,
        creator_id=creator.id,
        status=DealStatus.VERIFIED.value,
        agreed_rate_cents=amount_cents,
        terms_accepted=True,
    )
    db.add(deal)
    db.flush()
    db.add(
        FundingPayment(
            campaign_id=campaign.id,
            amount_cents=campaign.budget_cents,
            status="succeeded",
            payment_intent_id="pi_test_pay",
            source_charge_id="ch_test_pay",
        )
    )
    payout = Payout(
        campaign_id=campaign.id,
        deal_id=deal.id,
        creator_id=creator.id,
        payout_model="flat",
        component="base",
        amount_cents=amount_cents,
        status="ready",
    )
    db.add(payout)
    db.commit()
    return payout


def _discoverable_campaign(db, policy: dict) -> Campaign:
    brand = Brand(name="Policy brand", niche="fitness", policy=policy)
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name="Policy campaign",
        goal="Prove policy filtering",
        budget_cents=60_000,
        per_creator_cap_cents=15_000,
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(campaign)
    db.flush()
    db.add(
        CampaignStrategy(
            campaign_id=campaign.id,
            creator_tier="micro",
            target_creators=5,
            target_rate_cents=10_000,
            rationale="Policy test",
            projected_cost_per_result=0.02,
            approved=True,
        )
    )
    db.commit()
    return campaign


def test_service_spend_is_recorded_and_ledgered():
    settings = get_settings()
    with SessionLocal() as db:
        campaign = _funded_campaign(db)

        spend = ServiceSpend(
            campaign_id=campaign.id,
            provider="influencers.club",
            amount_cents=settings.discovery_refill_cents,
            status="operator_approved",
            context="Test service spend",
        )
        db.add(spend)
        db.commit()

        record_service_spend(db, campaign, spend.id, "lsrq_test_ok", "completed")
        assert spend.status == "completed"

        ledger = db.scalar(
            select(LedgerEntry).where(
                LedgerEntry.campaign_id == campaign.id,
                LedgerEntry.entry_type == "service_spend",
            )
        )
        assert ledger is not None
        assert ledger.amount_cents == -settings.discovery_refill_cents


def test_double_payout_release_is_idempotent():
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        payout = _ready_flat_payout(db, amount_cents=10_000)
        campaign_id = payout.campaign_id

        request_payout(db, payout, providers, settings)
        assert payout.status == "transferred"
        first_transfer = payout.stripe_transfer_id

        request_payout(db, payout, providers, settings)
        assert payout.stripe_transfer_id == first_transfer

        transfers = db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.campaign_id == campaign_id,
                LedgerEntry.entry_type == "creator_transfer",
            )
        ).all()
        assert len(transfers) == 1


def test_payout_rejected_over_per_creator_cap():
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        payout = _ready_flat_payout(db, amount_cents=25_000, cap_cents=20_000)
        with pytest.raises(HTTPException) as exc:
            request_payout(db, payout, providers, settings)
        assert exc.value.status_code == 409
        assert payout.status != "transferred"


def test_full_autonomy_policy_auto_approves_within_thresholds():
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        campaign = _discoverable_campaign(
            db, {"approval_mode": "full_autonomy", "min_fit_score": 0}
        )
        deals = discover(db, campaign, providers)
        assert deals
        assert all(deal.status == DealStatus.APPROVED.value for deal in deals)


def test_strict_policy_auto_rejects_low_fit_creators():
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        campaign = _discoverable_campaign(
            db, {"approval_mode": "new_creators", "min_fit_score": 95}
        )
        deals = discover(db, campaign, providers)
        assert deals
        assert all(deal.status == DealStatus.REJECTED.value for deal in deals)
