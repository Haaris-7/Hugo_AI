"""Demo seed data for the Hugo setup wizard."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .domain import emit
from .models import (
    AlgorithmPlaybook,
    Brand,
    Campaign,
    CampaignStatus,
    CampaignStrategy,
    Creator,
    CreatorReputation,
    Deal,
    DealMessage,
    DealStatus,
    Deliverable,
    DomainEvent,
    FundingPayment,
    HermesTask,
    LearningRun,
    LedgerEntry,
    Payout,
    QACheck,
    ServiceSpend,
    SkillVersion,
    StrategyPrior,
)

DEMO_META: dict[str, Any] = {"demo_seed": True}
DEMO_NICHE = "lumina_skincare_demo"


def _demo_policy() -> dict[str, Any]:
    return {**DEMO_META}


def _demo_compensation(rate_cents: int = 12_000) -> dict[str, Any]:
    return {
        "pricing_mode": "flat",
        "components": [{"kind": "base", "rate_cents": rate_cents}],
        **DEMO_META,
    }


def _seed_transferred_deal(
    db: Session,
    *,
    campaign: Campaign,
    creator: Creator,
    rate_cents: int,
    fit_score: float,
    thread_key: str,
    offer_body: str,
    accept_body: str,
    caption: str,
    media_url: str,
    post_url: str,
    transfer_id: str,
    idempotency_key: str,
    include_failed_draft_qa: bool = False,
) -> Deal:
    deal = Deal(
        campaign_id=campaign.id,
        creator_id=creator.id,
        status=DealStatus.TRANSFERRED.value,
        fit_score=fit_score,
        agreed_rate_cents=rate_cents,
        compensation=_demo_compensation(rate_cents),
        terms_accepted=True,
        draft_approved=True,
        final_approved=True,
    )
    db.add(deal)
    db.flush()
    db.add_all(
        [
            DealMessage(
                deal_id=deal.id,
                direction="outbound",
                external_id=f"msg_demo_out_{thread_key}",
                provider_thread_id=f"thread_demo_{thread_key}",
                body=offer_body,
                intent="offer",
            ),
            DealMessage(
                deal_id=deal.id,
                direction="inbound",
                external_id=f"msg_demo_in_{thread_key}",
                provider_thread_id=f"thread_demo_{thread_key}",
                body=accept_body,
                intent="accept",
            ),
        ]
    )
    deliverable = Deliverable(
        deal_id=deal.id,
        caption=caption,
        media_url=media_url,
        post_url=post_url,
        stage="final",
        qa_status="passed",
    )
    db.add(deliverable)
    db.flush()
    checks = [
        QACheck(
            deliverable_id=deliverable.id,
            severity="none",
            passed=True,
            findings=[],
            model="nvidia/nemotron-nano-12b-v2-vl",
        )
    ]
    if include_failed_draft_qa:
        checks.insert(
            0,
            QACheck(
                deliverable_id=deliverable.id,
                severity="major",
                passed=False,
                findings=[
                    {
                        "code": "missing_disclosure",
                        "message": "FTC disclosure missing on draft.",
                    }
                ],
                model="nvidia/nemotron-nano-12b-v2-vl",
            ),
        )
    db.add_all(checks)
    db.add(
        Payout(
            campaign_id=campaign.id,
            deal_id=deal.id,
            creator_id=creator.id,
            amount_cents=rate_cents,
            status="transferred",
            stripe_transfer_id=transfer_id,
            idempotency_key=idempotency_key,
        )
    )
    db.add(
        LedgerEntry(
            campaign_id=campaign.id,
            entry_type="creator_transfer",
            amount_cents=-rate_cents,
            reference_id=transfer_id,
            metadata_json={**DEMO_META, "provider": "stripe"},
        )
    )
    return deal


def seed_demo_data(db: Session) -> dict[str, int]:
    """Populate realistic sample campaigns across all lifecycle stages."""
    clear_demo_data(db)

    brand = Brand(
        name="Lumina Skincare",
        website="https://lumina-skincare.example",
        niche="skincare",
        policy=_demo_policy(),
    )
    db.add(brand)
    db.flush()

    creators_spec = [
        ("glowwithmia", "tiktok", 84_000, 0.062, 42, 78, 72, 81, 74),
        ("skincarebyjade", "instagram", 52_000, 0.048, 38, 85, 88, 80, 83),
        ("dermdoclena", "youtube", 120_000, 0.035, 12, 92, 90, 88, 90),
        ("spfwithsam", "tiktok", 31_000, 0.071, 55, 70, 65, 68, 67),
        ("cleanbeautyelle", "instagram", 67_000, 0.054, 28, 76, 74, 82, 77),
        ("hydrationhero", "tiktok", 45_000, 0.058, 33, 80, 77, 75, 78),
        ("nightserumnoah", "youtube", 98_000, 0.041, 18, 88, 86, 84, 86),
        ("lumenglow", "tiktok", 22_000, 0.083, 61, 68, 70, 71, 69),
        ("ritualwithren", "instagram", 39_000, 0.049, 44, 73, 71, 79, 74),
        ("vitamincvibes", "tiktok", 58_000, 0.066, 25, 82, 79, 77, 80),
    ]
    creators: list[Creator] = []
    for index, creator_spec in enumerate(creators_spec, start=1):
        handle, platform, followers, engagement, fake_pct, perf, rel, aud, overall = creator_spec
        creator = Creator(
            handle=handle,
            email=f"demo-creator-{index}@example.test",
            platform=platform,
            followers=followers,
            engagement_rate=engagement,
            fake_follower_percent=fake_pct,
            stripe_account_id=f"acct_demo_{handle}",
            stripe_onboarding_complete=True,
            profile_data={"niche": "skincare", "verified": True, **DEMO_META},
        )
        db.add(creator)
        db.flush()
        db.add(
            CreatorReputation(
                creator_id=creator.id,
                performance=perf,
                reliability=rel,
                audience_quality=aud,
                overall=overall,
                observations=3,
            )
        )
        creators.append(creator)

    db.add(
        AlgorithmPlaybook(
            platform="tiktok",
            signals=[{"signal": "authentic routine content", "weight": 0.8, **DEMO_META}],
            sources=[{"title": "TikTok creator playbook", "demo_seed": True}],
            confidence=0.82,
        )
    )
    db.add(
        StrategyPrior(
            niche=DEMO_NICHE,
            creator_tier="micro",
            observations=4,
            mean_cost_per_result=0.018,
            win_rate=0.72,
        )
    )
    db.add(
        SkillVersion(
            skill_name="hugo-strategy-engine",
            version=2,
            content_hash="demo_seed_hash",
            summary="Prefer micro-creators with strong routine storytelling for skincare launches.",
            evidence_ids=["demo-run-summer-glow"],
            governance={"reviewed": True, **DEMO_META},
            previous_version=1,
            validated=True,
        )
    )
    db.flush()

    campaigns_created = 0

    completed = Campaign(
        brand_id=brand.id,
        name="Summer Glow Launch (demo)",
        goal="Drive qualified TikTok views for vitamin C serum launch",
        platform="tiktok",
        budget_cents=120_000,
        per_creator_cap_cents=18_000,
        payout_model="flat",
        compensation=_demo_compensation(12_000),
        compensation_source="hugo",
        compensation_locked=True,
        operation_mode="full_autonomy",
        status=CampaignStatus.COMPLETED.value,
        actual_views=248_500,
        actual_engagements=18_420,
        actual_conversions=312,
        metrics_recorded=True,
    )
    db.add(completed)
    db.flush()
    campaigns_created += 1

    completed_strategy = CampaignStrategy(
        campaign_id=completed.id,
        creator_tier="micro",
        target_creators=4,
        target_rate_cents=12_000,
        rationale="Micro TikTok creators with strong skincare routines outperform mid-tier on CPM.",
        projected_cost_per_result=0.017,
        skill_version=2,
        approved=True,
    )
    db.add(completed_strategy)
    db.add(
        FundingPayment(
            campaign_id=completed.id,
            amount_cents=120_000,
            status="succeeded",
            checkout_session_id="cs_demo_summer_glow",
            payment_intent_id="pi_demo_summer_glow",
            source_charge_id="ch_demo_summer_glow",
        )
    )
    db.add(
        ServiceSpend(
            campaign_id=completed.id,
            provider="influencers.club",
            amount_cents=100,
            status="completed",
            spend_request_id="lsrq_demo_discovery",
            context="Demo creator discovery credits",
        )
    )
    summer_rate = completed_strategy.target_rate_cents
    summer_deals = [
        (
            creators[0],
            81.0,
            "summer_glow_mia",
            "Collaboration offer for Summer Glow Launch.",
            "ACCEPT — excited to partner!",
            "Loving this vitamin C glow #ad hugo.link/demo-summer",
            "https://media.example/demo-summer-mia.jpg",
            "https://tiktok.com/@glowwithmia/video/demo",
            "tr_demo_summer_glow_mia",
            "demo-payout-summer-glow-mia",
            True,
        ),
        (
            creators[3],
            75.0,
            "summer_glow_sam",
            "Summer Glow Launch — SPF-friendly vitamin C routine slot.",
            "ACCEPT — perfect fit for my audience.",
            "Morning SPF + vitamin C combo #ad hugo.link/demo-summer",
            "https://media.example/demo-summer-sam.jpg",
            "https://tiktok.com/@spfwithsam/video/demo",
            "tr_demo_summer_glow_sam",
            "demo-payout-summer-glow-sam",
            False,
        ),
        (
            creators[7],
            71.0,
            "summer_glow_lumen",
            "Summer Glow Launch — glow routine creator offer.",
            "ACCEPT — let's do it!",
            "My glow routine with Lumina vitamin C #ad hugo.link/demo-summer",
            "https://media.example/demo-summer-lumen.jpg",
            "https://tiktok.com/@lumenglow/video/demo",
            "tr_demo_summer_glow_lumen",
            "demo-payout-summer-glow-lumen",
            False,
        ),
        (
            creators[9],
            77.0,
            "summer_glow_vibes",
            "Summer Glow Launch — creator collaboration offer.",
            "ACCEPT — rate works for me.",
            "Vitamin C summer glow check #ad hugo.link/demo-summer",
            "https://media.example/demo-summer-vibes.jpg",
            "https://tiktok.com/@vitamincvibes/video/demo",
            "tr_demo_summer_glow_vibes",
            "demo-payout-summer-glow-vibes",
            False,
        ),
    ]
    for (
        creator,
        fit_score,
        thread_key,
        offer_body,
        accept_body,
        caption,
        media_url,
        post_url,
        transfer_id,
        idempotency_key,
        include_failed_draft_qa,
    ) in summer_deals:
        _seed_transferred_deal(
            db,
            campaign=completed,
            creator=creator,
            rate_cents=summer_rate,
            fit_score=fit_score,
            thread_key=thread_key,
            offer_body=offer_body,
            accept_body=accept_body,
            caption=caption,
            media_url=media_url,
            post_url=post_url,
            transfer_id=transfer_id,
            idempotency_key=idempotency_key,
            include_failed_draft_qa=include_failed_draft_qa,
        )
    db.add_all(
        [
            LedgerEntry(
                campaign_id=completed.id,
                entry_type="funding",
                amount_cents=120_000,
                reference_id="ch_demo_summer_glow",
                metadata_json={**DEMO_META, "provider": "stripe"},
            ),
            LedgerEntry(
                campaign_id=completed.id,
                entry_type="service_spend",
                amount_cents=-100,
                reference_id="lsrq_demo_discovery",
                metadata_json={**DEMO_META, "provider": "influencers.club"},
            ),
        ]
    )
    db.add(
        LearningRun(
            campaign_id=completed.id,
            run_id=completed.run_id,
            status="completed",
            baseline_status="applied",
            database_updates={
                "strategy_prior": {
                    "niche": DEMO_NICHE,
                    "creator_tier": "micro",
                    "before": {
                        "observations": 7,
                        "mean_cost_per_result": 0.021,
                        "win_rate": 0.68,
                    },
                    "after": {
                        "observations": 8,
                        "mean_cost_per_result": 0.0194,
                        "win_rate": 0.72,
                    },
                },
                "creator_reputations": [],
            },
            patch_status="no_op",
            summary="Micro skincare creators on TikTok delivered the best cost per qualified view.",
            evidence={"views": 248_500, "engagements": 18_420},
            skill_version=2,
        )
    )
    emit(db, completed.id, "campaign.created", {"demo_seed": True})
    emit(db, completed.id, "campaign.funded", {"charge_id": "ch_demo_summer_glow"})
    emit(db, completed.id, "campaign_run_closed", {"views": 248_500})

    active = Campaign(
        brand_id=brand.id,
        name="Hydration Series (demo)",
        goal="Build awareness for the new hydration line",
        platform="tiktok",
        budget_cents=90_000,
        per_creator_cap_cents=15_000,
        payout_model="flat",
        compensation=_demo_compensation(10_000),
        compensation_source="hugo",
        operation_mode="full_autonomy",
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(active)
    db.flush()
    campaigns_created += 1
    db.add(
        CampaignStrategy(
            campaign_id=active.id,
            creator_tier="micro",
            target_creators=3,
            target_rate_cents=10_000,
            rationale="Three micro creators with hydration-focused content.",
            projected_cost_per_result=0.02,
            skill_version=2,
            approved=True,
        )
    )
    db.add(
        FundingPayment(
            campaign_id=active.id,
            amount_cents=90_000,
            status="succeeded",
            checkout_session_id="cs_demo_hydration",
            payment_intent_id="pi_demo_hydration",
            source_charge_id="ch_demo_hydration",
        )
    )
    db.add(
        LedgerEntry(
            campaign_id=active.id,
            entry_type="funding",
            amount_cents=90_000,
            reference_id="ch_demo_hydration",
            metadata_json={**DEMO_META, "provider": "stripe"},
        )
    )
    db.add(
        ServiceSpend(
            campaign_id=active.id,
            provider="influencers.club",
            amount_cents=100,
            status="pending_approval",
            context="Top up discovery credits for the next hydration creator batch",
        )
    )
    active_deals = [
        (creators[4], DealStatus.CONTACTED.value, 10_000, True, False),
        (creators[5], DealStatus.CONTRACTED.value, 11_000, True, True),
        (creators[6], DealStatus.DRAFT_QA.value, 12_000, True, True),
    ]
    for creator, status, rate, outbound, inbound in active_deals:
        deal = Deal(
            campaign_id=active.id,
            creator_id=creator.id,
            status=status,
            fit_score=76.0,
            agreed_rate_cents=rate,
            compensation=_demo_compensation(rate),
            terms_accepted=status != DealStatus.CONTACTED.value,
            draft_approved=status == DealStatus.DRAFT_QA.value,
        )
        db.add(deal)
        db.flush()
        if outbound:
            db.add(
                DealMessage(
                    deal_id=deal.id,
                    direction="outbound",
                    external_id=f"msg_demo_hydration_out_{creator.handle}",
                    provider_thread_id=f"thread_demo_hydration_{creator.handle}",
                    body="Hydration Series collaboration offer.",
                    intent="offer",
                )
            )
        if inbound:
            db.add(
                DealMessage(
                    deal_id=deal.id,
                    direction="inbound",
                    external_id=f"msg_demo_hydration_in_{creator.handle}",
                    provider_thread_id=f"thread_demo_hydration_{creator.handle}",
                    body="Happy to collaborate at the proposed rate.",
                    intent="accept",
                )
            )
        if status == DealStatus.DRAFT_QA.value:
            draft = Deliverable(
                deal_id=deal.id,
                caption="Morning hydration ritual with Lumina",
                media_url="https://media.example/demo-hydration.jpg",
                stage="draft",
                qa_status="pending",
            )
            db.add(draft)
    db.add(
        Deal(
            campaign_id=active.id,
            creator_id=creators[1].id,
            status=DealStatus.APPROVAL_PENDING.value,
            fit_score=80.0,
            agreed_rate_cents=10_000,
            compensation=_demo_compensation(10_000),
        )
    )
    emit(db, active.id, "campaign.launched", {"demo_seed": True})

    funding_pending = Campaign(
        brand_id=brand.id,
        name="SPF Campaign (demo)",
        goal="Educate audience on daily SPF habits",
        platform="instagram",
        budget_cents=75_000,
        per_creator_cap_cents=14_000,
        payout_model="flat",
        compensation=_demo_compensation(9_500),
        compensation_source="hugo",
        operation_mode="strategy_creators_payments",
        status=CampaignStatus.AWAITING_FUNDING.value,
    )
    db.add(funding_pending)
    db.flush()
    campaigns_created += 1
    db.add(
        CampaignStrategy(
            campaign_id=funding_pending.id,
            creator_tier="micro",
            target_creators=3,
            target_rate_cents=9_500,
            rationale="Instagram micro creators with sun-care education content.",
            projected_cost_per_result=0.021,
            skill_version=2,
            approved=True,
        )
    )
    emit(db, funding_pending.id, "strategy.approved", {"demo_seed": True})

    strategy_pending = Campaign(
        brand_id=brand.id,
        name="Fall Collection Teaser (demo)",
        goal="Tease the fall skincare collection",
        platform="tiktok",
        budget_cents=60_000,
        per_creator_cap_cents=12_000,
        payout_model="flat",
        compensation=_demo_compensation(),
        compensation_source="hugo",
        operation_mode="full_autonomy",
        status=CampaignStatus.STRATEGY_PENDING.value,
    )
    db.add(strategy_pending)
    db.flush()
    campaigns_created += 1
    db.add(
        HermesTask(
            campaign_id=strategy_pending.id,
            task_type="strategy",
            status="pending",
            payload={"demo_seed": True},
            dedupe_key=f"demo-strategy:{strategy_pending.id}",
        )
    )

    draft = Campaign(
        brand_id=brand.id,
        name="Holiday Gift Guide (demo)",
        goal="Holiday gift guide featuring Lumina bestsellers",
        platform="youtube",
        budget_cents=100_000,
        per_creator_cap_cents=20_000,
        payout_model="flat",
        compensation=_demo_compensation(15_000),
        compensation_source="user",
        operation_mode="strategy_creators",
        status=CampaignStatus.DRAFT.value,
    )
    db.add(draft)
    db.flush()
    campaigns_created += 1

    db.commit()
    return {
        "brands": 1,
        "campaigns": campaigns_created,
        "creators": len(creators),
    }


def _demo_campaign_ids(db: Session) -> set[str]:
    return {
        campaign.id
        for campaign in db.scalars(select(Campaign)).all()
        if (campaign.brand.policy or {}).get("demo_seed")
    }


def _demo_creator_ids(db: Session) -> set[str]:
    return {
        creator.id
        for creator in db.scalars(select(Creator)).all()
        if (creator.profile_data or {}).get("demo_seed")
    }


def _demo_brand_ids(db: Session) -> set[str]:
    return {
        brand.id
        for brand in db.scalars(select(Brand)).all()
        if (brand.policy or {}).get("demo_seed")
    }


def clear_demo_data(db: Session) -> dict[str, int]:
    """Remove all records tagged with demo_seed. Safe to call repeatedly."""
    demo_campaign_ids = _demo_campaign_ids(db)
    demo_creator_ids = _demo_creator_ids(db)
    demo_brand_ids = _demo_brand_ids(db)

    removed = {"campaigns": 0, "creators": 0, "brands": 0}

    if demo_campaign_ids:
        deal_ids = [
            row[0]
            for row in db.execute(select(Deal.id).where(Deal.campaign_id.in_(demo_campaign_ids)))
        ]
        deliverable_ids = (
            [
                row[0]
                for row in db.execute(
                    select(Deliverable.id).where(Deliverable.deal_id.in_(deal_ids))
                )
            ]
            if deal_ids
            else []
        )
        if deliverable_ids:
            db.execute(delete(QACheck).where(QACheck.deliverable_id.in_(deliverable_ids)))
        if deal_ids:
            db.execute(delete(DealMessage).where(DealMessage.deal_id.in_(deal_ids)))
            db.execute(delete(Deliverable).where(Deliverable.deal_id.in_(deal_ids)))
        db.execute(delete(HermesTask).where(HermesTask.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(Payout).where(Payout.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(LedgerEntry).where(LedgerEntry.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(ServiceSpend).where(ServiceSpend.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(FundingPayment).where(FundingPayment.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(LearningRun).where(LearningRun.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(Deal).where(Deal.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(CampaignStrategy).where(CampaignStrategy.campaign_id.in_(demo_campaign_ids)))
        db.execute(delete(DomainEvent).where(DomainEvent.campaign_id.in_(demo_campaign_ids)))
        result = db.execute(delete(Campaign).where(Campaign.id.in_(demo_campaign_ids)))
        removed["campaigns"] = result.rowcount or 0

    if demo_creator_ids:
        db.execute(
            delete(CreatorReputation).where(CreatorReputation.creator_id.in_(demo_creator_ids))
        )
        result = db.execute(delete(Creator).where(Creator.id.in_(demo_creator_ids)))
        removed["creators"] = result.rowcount or 0

    if demo_brand_ids:
        result = db.execute(delete(Brand).where(Brand.id.in_(demo_brand_ids)))
        removed["brands"] = result.rowcount or 0

    db.execute(delete(StrategyPrior).where(StrategyPrior.niche == DEMO_NICHE))
    db.execute(
        delete(SkillVersion).where(
            SkillVersion.skill_name == "hugo-strategy-engine",
            SkillVersion.content_hash == "demo_seed_hash",
        )
    )
    playbooks = db.scalars(select(AlgorithmPlaybook)).all()
    for playbook in playbooks:
        if any(source.get("demo_seed") for source in playbook.sources if isinstance(source, dict)):
            db.delete(playbook)

    db.commit()
    return removed
