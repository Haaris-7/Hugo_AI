from hugo.config import Settings
from hugo.db import SessionLocal
from hugo.demo import seed_demo_data
from hugo.main import _action_queue_items
from hugo.models import (
    Brand,
    Campaign,
    CampaignStatus,
    CampaignStrategy,
    Creator,
    Deal,
    DealMessage,
    DealStatus,
    HermesTask,
    LearningRun,
    LedgerEntry,
)
from hugo.providers import build_providers
from hugo.services import confirm_browser_email, generate_strategy, send_outreach
import pytest
from sqlalchemy import select


def test_seeded_learning_has_renderable_prior_snapshots():
    with SessionLocal() as db:
        seed_demo_data(db)
        learning = db.scalar(select(LearningRun))

        prior = learning.database_updates["strategy_prior"]
        assert prior["before"]["observations"] < prior["after"]["observations"]
        assert "mean_cost_per_result" in prior["after"]
        assert "win_rate" in prior["after"]


def test_summer_glow_demo_matches_strategy_creator_count():
    with SessionLocal() as db:
        seed_demo_data(db)
        campaign = db.scalar(select(Campaign).where(Campaign.name == "Summer Glow Launch (demo)"))
        strategy = db.scalar(
            select(CampaignStrategy).where(CampaignStrategy.campaign_id == campaign.id)
        )
        deals = db.scalars(select(Deal).where(Deal.campaign_id == campaign.id)).all()

        assert strategy.target_creators == 4
        assert len(deals) == 4
        assert all(deal.status == DealStatus.TRANSFERRED.value for deal in deals)


def test_demo_seeds_action_queue_items_and_balanced_ledger():
    with SessionLocal() as db:
        seed_demo_data(db)
        summer = db.scalar(select(Campaign).where(Campaign.name == "Summer Glow Launch (demo)"))
        ledger = db.scalars(select(LedgerEntry).where(LedgerEntry.campaign_id == summer.id)).all()
        funded = sum(row.amount_cents for row in ledger if row.amount_cents > 0)
        spent = abs(sum(row.amount_cents for row in ledger if row.amount_cents < 0))

        assert funded == 120_000
        assert spent == 48_100
        assert spent / funded == pytest.approx(0.400833, rel=1e-4)

        actions = _action_queue_items(db)
        assert len(actions) == 2
        assert {item["type"] for item in actions} == {"deal", "service_spend"}
        assert all(item["campaign_name"] == "Hydration Series (demo)" for item in actions)


def test_demo_strategy_task_can_resume_from_strategy_pending():
    with SessionLocal() as db:
        seed_demo_data(db)
        campaign = db.scalar(select(Campaign).where(Campaign.name.like("Fall Collection%")))

        strategy = generate_strategy(
            db,
            campaign,
            build_providers(Settings(demo_mode=True)),
        )

        assert strategy.campaign_id == campaign.id
        assert campaign.status == CampaignStatus.AWAITING_FUNDING.value


def test_browser_email_queues_and_requires_matching_sender_confirmation():
    settings = Settings(
        demo_mode=True,
        email_transport="browser",
        browser_email_provider="outlook",
        browser_email_sender="operator@example.com",
    )
    providers = build_providers(settings)
    with SessionLocal() as db:
        brand = Brand(name="Browser mail brand", niche="fitness")
        db.add(brand)
        db.flush()
        campaign = Campaign(
            brand_id=brand.id,
            name="Browser outreach",
            goal="Verify browser email handoff",
            budget_cents=20_000,
            per_creator_cap_cents=10_000,
            compensation={"components": [{"kind": "base", "rate_cents": 10_000}]},
            status=CampaignStatus.ACTIVE.value,
        )
        creator = Creator(handle="browser.creator", email="creator@example.com")
        db.add_all([campaign, creator])
        db.flush()
        db.add(
            CampaignStrategy(
                campaign_id=campaign.id,
                creator_tier="micro",
                target_creators=1,
                target_rate_cents=10_000,
                rationale="Browser email test",
                approved=True,
            )
        )
        deal = Deal(
            campaign_id=campaign.id,
            creator_id=creator.id,
            status=DealStatus.APPROVED.value,
            compensation={"components": [{"kind": "base", "rate_cents": 10_000}]},
        )
        db.add(deal)
        db.commit()

        result = send_outreach(db, deal, providers, 10_000)
        task = db.get(HermesTask, result["task_id"])
        assert result["status"] == "browser_action_required"
        assert task.task_type == "browser_email"
        assert task.payload["provider"] == "outlook"
        assert deal.status == DealStatus.APPROVED.value

        confirmed = confirm_browser_email(
            db,
            task.id,
            settings,
            sender="operator@example.com",
        )
        message = db.scalar(select(DealMessage).where(DealMessage.deal_id == deal.id))
        assert confirmed.status == "completed"
        assert deal.status == DealStatus.CONTACTED.value
        assert message.channel == "browser_outlook"
