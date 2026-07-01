from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .domain import (
    emit,
    evaluate_creator_policy,
    payout_amount,
    transition_campaign,
    weighted_fit_score,
)
from .models import (
    AlgorithmPlaybook,
    Approval,
    ApprovalRequest,
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
    Experiment,
    FundingPayment,
    HermesTask,
    LearningRun,
    LedgerEntry,
    OutboxJob,
    Payout,
    QACheck,
    ServiceSpend,
    SkillVersion,
    StrategyPrior,
    utcnow,
)
from .providers import DemoPaymentProvider, Providers
from .schemas import (
    ApprovalCreate,
    CampaignCreate,
    CreatorResponseCreate,
    DealMetricsCreate,
    DeliverableCreate,
    LearningCommit,
    MetricsCreate,
)


def must_get(db: Session, model: type, entity_id: str):
    entity = db.get(model, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return entity


def _legacy_components(model: str, rate_cents: int) -> list[dict[str, Any]]:
    if model == "flat":
        return [{"kind": "base", "rate_cents": rate_cents}]
    if model in {"cpm", "engagement", "affiliate"}:
        return [{"kind": model, "rate_cents": rate_cents}]
    if model == "hybrid":
        return [
            {"kind": "base", "rate_cents": rate_cents},
            {"kind": "cpm", "rate_cents": max(1, round(rate_cents * 0.5))},
        ]
    return [{"kind": "base", "rate_cents": rate_cents}]


def compensation_components(campaign: Campaign) -> list[dict[str, Any]]:
    components = list((campaign.compensation or {}).get("components", []))
    if components:
        return components
    rate = (
        campaign.strategy.target_rate_cents if campaign.strategy else campaign.per_creator_cap_cents
    )
    return _legacy_components(campaign.payout_model, rate)


def _primary_rate(components: list[dict[str, Any]], fallback: int) -> int:
    base = next((row for row in components if row.get("kind") == "base"), None)
    selected = base or (components[0] if components else None)
    return int(selected.get("rate_cents", fallback)) if selected else fallback


def _post_url_matches(platform: str, value: str | None) -> bool:
    if not value:
        return False
    host = (urlparse(value).hostname or "").lower()
    allowed = {
        "tiktok": ("tiktok.com",),
        "instagram": ("instagram.com",),
        "youtube": ("youtube.com", "youtu.be"),
    }.get(platform, ())
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed)


def _queue_job(
    db: Session,
    job_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
    *,
    scheduled_at=None,
) -> None:
    if not db.scalar(select(OutboxJob).where(OutboxJob.dedupe_key == dedupe_key)):
        db.add(
            OutboxJob(
                job_type=job_type,
                dedupe_key=dedupe_key,
                payload=payload,
                scheduled_at=scheduled_at,
            )
        )


def create_approval_request(
    db: Session,
    campaign_id: str,
    resource_type: str,
    resource_id: str,
    context: dict[str, Any] | None = None,
) -> ApprovalRequest:
    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.resource_type == resource_type,
            ApprovalRequest.resource_id == resource_id,
            ApprovalRequest.status == "pending",
        )
    )
    if existing:
        return existing
    request = ApprovalRequest(
        campaign_id=campaign_id,
        resource_type=resource_type,
        resource_id=resource_id,
        token=secrets.token_urlsafe(9),
        expires_at=utcnow() + timedelta(hours=24),
        context=context or {},
    )
    db.add(request)
    db.flush()
    _queue_job(
        db,
        "messaging_notification",
        f"approval-notification:{request.id}",
        {"approval_request_id": request.id},
    )
    return request


def create_campaign(db: Session, data: CampaignCreate) -> Campaign:
    must_get(db, Brand, data.brand_id)
    if data.per_creator_cap_cents > data.budget_cents:
        raise HTTPException(status_code=422, detail="Per-creator cap cannot exceed campaign budget")
    payload = data.model_dump(exclude={"compensation"})
    compensation = data.compensation.model_dump() if data.compensation else {}
    source = (
        "user"
        if data.compensation
        else ("legacy" if "payout_model" in data.model_fields_set else "hugo")
    )
    campaign = Campaign(
        **payload,
        compensation=compensation,
        compensation_source=source,
        qa_mode="two_stage",
    )
    db.add(campaign)
    db.flush()
    emit(db, campaign.id, "campaign.created", {"run_id": campaign.run_id})
    enqueue_hermes_task(db, campaign.id, "strategy", dedupe_key=f"strategy:{campaign.id}")
    db.commit()
    return campaign


def generate_strategy(db: Session, campaign: Campaign, providers: Providers) -> CampaignStrategy:
    if campaign.strategy:
        return campaign.strategy
    if campaign.status == CampaignStatus.DRAFT.value:
        transition_campaign(db, campaign, CampaignStatus.STRATEGY_PENDING.value)
    elif campaign.status != CampaignStatus.STRATEGY_PENDING.value:
        raise HTTPException(
            status_code=409,
            detail=f"Campaign cannot generate a strategy while {campaign.status}",
        )
    brand = campaign.brand
    playbook = db.scalar(
        select(AlgorithmPlaybook)
        .where(AlgorithmPlaybook.platform == campaign.platform)
        .order_by(AlgorithmPlaybook.updated_at.desc())
    )
    playbook_updated_at = playbook.updated_at if playbook else None
    if playbook_updated_at and playbook_updated_at.tzinfo is None:
        playbook_updated_at = playbook_updated_at.replace(tzinfo=timezone.utc)
    if not playbook or playbook_updated_at < utcnow() - timedelta(days=7):
        researched = providers.hermes.playbook(campaign.platform)
        playbook = AlgorithmPlaybook(
            platform=campaign.platform,
            signals=researched.get("signals", []),
            sources=researched.get("sources", []),
            confidence=float(researched.get("confidence", 0.5)),
        )
        db.add(playbook)
        db.flush()
    latest_skill_record = db.scalar(
        select(SkillVersion)
        .where(
            SkillVersion.skill_name == "hugo-strategy-engine",
            SkillVersion.validated.is_(True),
        )
        .order_by(SkillVersion.version.desc())
    )
    latest_skill = latest_skill_record.version if latest_skill_record else 1
    prior = db.scalar(
        select(StrategyPrior)
        .where(StrategyPrior.niche == brand.niche)
        .order_by(StrategyPrior.observations.desc())
    )
    strategy_context = {
        "campaign_id": campaign.id,
        "niche": brand.niche,
        "goal": campaign.goal,
        "platform": campaign.platform,
        "budget_cents": campaign.budget_cents,
        "per_creator_cap_cents": campaign.per_creator_cap_cents,
        "payout_model": campaign.payout_model,
        "compensation": campaign.compensation,
        "skill_version": latest_skill,
        "learned_heuristic": latest_skill_record.summary if latest_skill_record else None,
        "algorithm_playbook": {
            "signals": playbook.signals,
            "sources": playbook.sources,
            "confidence": playbook.confidence,
        },
        "database_prior": None
        if not prior
        else {
            "creator_tier": prior.creator_tier,
            "observations": prior.observations,
            "mean_cost_per_result": prior.mean_cost_per_result,
            "win_rate": prior.win_rate,
        },
    }
    recommendation = providers.hermes.strategy(strategy_context)
    if not (campaign.compensation or {}).get("components"):
        proposed = (
            [component.model_dump() for component in recommendation.compensation_components]
            if campaign.compensation_source == "hugo" and recommendation.compensation_components
            else _legacy_components(campaign.payout_model, recommendation.target_rate_cents)
        )
        campaign.compensation = {
            "pricing_mode": "hugo" if campaign.compensation_source == "hugo" else "user",
            "components": proposed,
        }
    components = compensation_components(campaign)
    if any(int(row["rate_cents"]) > campaign.per_creator_cap_cents for row in components):
        raise HTTPException(status_code=422, detail="Compensation rate exceeds creator cap")
    rate = min(
        _primary_rate(components, recommendation.target_rate_cents), campaign.per_creator_cap_cents
    )
    target = (
        max(1, campaign.budget_cents // max(campaign.per_creator_cap_cents, 1))
        if campaign.payout_model == "affiliate"
        else max(1, campaign.budget_cents // max(rate, 1))
    )
    primary_allocation = 0.8 if target >= 4 and campaign.budget_cents >= 100_000 else 1.0
    challenger_allocation = 1 - primary_allocation

    playbook_signal = ""
    if playbook.signals:
        first = playbook.signals[0]
        playbook_signal = str(
            first.get("signal") or first.get("effect") or "" if isinstance(first, dict) else first
        )
    playbook_source = ""
    if playbook.sources:
        src = playbook.sources[0]
        playbook_source = str(src.get("url", "")) if isinstance(src, dict) else str(src)
    favors_small = any(
        keyword in playbook_signal.lower()
        for keyword in ("micro", "small", "several", "retention", "native", "ugc")
    )
    creator_tier = recommendation.creator_tier
    if favors_small and creator_tier in ("mid", "macro"):
        creator_tier = "micro"

    rationale_parts = [recommendation.rationale]
    if playbook_signal:
        bias_note = f" Biased toward {creator_tier} creators." if favors_small else ""
        rationale_parts.append(
            f"Algorithm playbook ({campaign.platform}, confidence "
            f'{playbook.confidence:.0%}): "{playbook_signal}"'
            + (f" — source {playbook_source}." if playbook_source else ".")
            + bias_note
        )
    if prior:
        rationale_parts.append(
            f"Database prior: {prior.observations} observed {prior.creator_tier} campaign(s), "
            f"mean cost per result {prior.mean_cost_per_result:.4f}."
        )
    if latest_skill_record:
        rationale_parts.append(
            f"Applied learned skill v{latest_skill_record.version}: {latest_skill_record.summary}"
        )
    strategy = CampaignStrategy(
        campaign_id=campaign.id,
        creator_tier=creator_tier,
        target_creators=min(target, 30),
        target_rate_cents=rate,
        rationale=" ".join(rationale_parts),
        projected_cost_per_result=recommendation.projected_cost_per_result,
        skill_version=latest_skill,
        primary_allocation=primary_allocation,
        challenger_allocation=challenger_allocation,
    )
    campaign.strategy = strategy
    db.add(strategy)
    db.flush()
    db.add(
        Experiment(
            campaign_id=campaign.id,
            name="primary-creative",
            hypothesis="The current playbook-backed creator brief will meet the target CPR.",
            variant="primary",
            allocation=primary_allocation,
        )
    )
    if challenger_allocation:
        db.add(
            Experiment(
                campaign_id=campaign.id,
                name="challenger-hook",
                hypothesis="A challenger opening hook will improve early retention.",
                variant="challenger",
                allocation=challenger_allocation,
            )
        )
    if campaign.operation_mode == "full_autonomy":
        strategy.approved = True
        campaign.compensation_locked = True
        transition_campaign(db, campaign, CampaignStatus.AWAITING_FUNDING.value)
    else:
        transition_campaign(db, campaign, CampaignStatus.AWAITING_APPROVAL.value)
        create_approval_request(db, campaign.id, "strategy", strategy.id)
    emit(db, campaign.id, "strategy.generated", {"strategy_id": strategy.id})
    db.commit()
    return strategy


def create_funding(db: Session, campaign: Campaign, providers: Providers) -> FundingPayment:
    if campaign.status != CampaignStatus.AWAITING_FUNDING.value:
        raise HTTPException(status_code=409, detail="Approve the strategy before funding")
    existing = db.scalar(select(FundingPayment).where(FundingPayment.campaign_id == campaign.id))
    if existing:
        return existing
    result = providers.payments.create_funding_session(campaign.id, campaign.budget_cents)
    funding = FundingPayment(
        campaign_id=campaign.id,
        amount_cents=campaign.budget_cents,
        checkout_session_id=result.external_id,
        payment_intent_id=result.payment_intent_id,
        checkout_url=result.url,
    )
    db.add(funding)
    emit(db, campaign.id, "funding.checkout_created", {"session_id": result.external_id})
    if isinstance(providers.payments, DemoPaymentProvider):
        funding.status = "succeeded"
        funding.checkout_url = None
        db.add(
            LedgerEntry(
                campaign_id=campaign.id,
                entry_type="funding",
                amount_cents=campaign.budget_cents,
                reference_id=result.payment_intent_id,
            )
        )
        emit(db, campaign.id, "funding.succeeded", {"payment_intent_id": result.payment_intent_id})
    db.commit()
    return funding


def mark_funded(
    db: Session,
    campaign_id: str,
    payment_intent_id: str,
    providers: Providers,
    source_charge_id: str | None = None,
) -> FundingPayment:
    funding = db.scalar(select(FundingPayment).where(FundingPayment.campaign_id == campaign_id))
    if not funding:
        raise HTTPException(status_code=404, detail="Funding record not found")
    if funding.status != "succeeded":
        funding.status = "succeeded"
        funding.payment_intent_id = payment_intent_id
        funding.source_charge_id = source_charge_id or providers.payments.resolve_source_charge(
            payment_intent_id
        )
        db.add(
            LedgerEntry(
                campaign_id=campaign_id,
                entry_type="funding",
                amount_cents=funding.amount_cents,
                reference_id=payment_intent_id,
            )
        )
        emit(db, campaign_id, "funding.succeeded", {"payment_intent_id": payment_intent_id})
        enqueue_hermes_task(db, campaign_id, "launch", dedupe_key=f"launch:{campaign_id}")
        db.commit()
    return funding


def request_service_spend(db: Session, campaign: Campaign, settings: Settings) -> ServiceSpend:
    if settings.discovery_refill_cents > campaign.budget_cents:
        raise HTTPException(status_code=409, detail="Service spend exceeds campaign budget")
    existing = db.scalar(
        select(ServiceSpend).where(
            ServiceSpend.campaign_id == campaign.id,
            ServiceSpend.status.in_(
                ["pending_approval", "operator_approved", "approved", "completed"]
            ),
        )
    )
    if existing:
        return existing
    context = (
        f"Hugo campaign {campaign.id} requires creator discovery credits before it can search "
        "for approved TikTok creators. This request is limited to the configured service budget."
    )
    spend = ServiceSpend(
        campaign_id=campaign.id,
        provider="influencers.club",
        amount_cents=settings.discovery_refill_cents,
        status=(
            "operator_approved"
            if campaign.operation_mode == "full_autonomy"
            else "pending_approval"
        ),
        context=context,
    )
    db.add(spend)
    db.flush()
    if campaign.operation_mode == "strategy_creators_payments":
        create_approval_request(db, campaign.id, "service_spend", spend.id)
    emit(
        db,
        campaign.id,
        "service_spend.requested",
        {"spend_id": spend.id, "status": spend.status},
    )
    db.commit()
    return spend


def record_service_spend(
    db: Session,
    campaign: Campaign,
    spend_id: str,
    spend_request_id: str,
    status: str,
) -> ServiceSpend:
    if status not in {"approved", "completed", "rejected", "failed"}:
        raise HTTPException(status_code=422, detail="Unsupported Link spend status")
    if status in {"approved", "completed"} and not spend_request_id.strip():
        raise HTTPException(status_code=422, detail="Link spend request ID is required")
    spend = must_get(db, ServiceSpend, spend_id)
    if spend.campaign_id != campaign.id:
        raise HTTPException(status_code=409, detail="Spend belongs to another campaign")
    if spend.status == "completed":
        return spend
    spend.spend_request_id = spend_request_id
    spend.status = status
    if status == "completed":
        existing_entry = db.scalar(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == "service_spend",
                LedgerEntry.reference_id == spend.id,
            )
        )
        if not existing_entry:
            db.add(
                LedgerEntry(
                    campaign_id=campaign.id,
                    entry_type="service_spend",
                    amount_cents=-spend.amount_cents,
                    reference_id=spend.id,
                    metadata_json={"link_spend_request_id": spend_request_id},
                )
            )
    emit(
        db,
        campaign.id,
        "service_spend.outcome_recorded",
        {"spend_id": spend.id, "spend_request_id": spend_request_id, "status": status},
    )
    db.commit()
    return spend


def discover(
    db: Session,
    campaign: Campaign,
    providers: Providers,
    *,
    replacement_for: Deal | None = None,
) -> list[Deal]:
    brand = campaign.brand
    strategy = campaign.strategy
    excluded = {deal.creator.handle for deal in campaign.deals}
    candidates = providers.discovery.search(
        brand.niche,
        campaign.platform,
        limit=1 if replacement_for else min(strategy.target_creators, 5),
        exclude_handles=excluded if replacement_for else None,
    )
    deals: list[Deal] = []
    auto_approved = 0
    auto_rejected = 0
    for candidate in candidates:
        creator = db.scalar(
            select(Creator).where(
                Creator.platform == campaign.platform,
                Creator.handle == candidate.handle,
            )
        )
        is_new_creator = creator is None
        if not creator:
            creator = Creator(
                handle=candidate.handle,
                platform=campaign.platform,
                email=candidate.email,
                followers=candidate.followers,
                engagement_rate=candidate.engagement_rate,
                fake_follower_percent=candidate.fake_follower_percent,
                profile_data=candidate.profile_data,
            )
            db.add(creator)
            db.flush()
            db.add(CreatorReputation(creator_id=creator.id))
            db.flush()
        reputation = creator.reputation.overall if creator.reputation else 50
        observations = creator.reputation.observations if creator.reputation else 0
        is_known = not is_new_creator and observations > 0
        fit = weighted_fit_score(
            niche_match=candidate.niche_match,
            audience_quality=candidate.audience_quality,
            engagement_rate=candidate.engagement_rate,
            brand_fit=candidate.brand_fit,
            reputation=reputation,
        )
        decision, reason = evaluate_creator_policy(
            brand.policy,
            fit=fit,
            fake_follower_percent=candidate.fake_follower_percent,
            reputation_overall=reputation,
            is_known=is_known,
        )
        status = {
            "approved": DealStatus.APPROVED.value,
            "rejected": DealStatus.REJECTED.value,
            "pending": DealStatus.APPROVAL_PENDING.value,
        }[decision]
        if (
            campaign.operation_mode == "full_autonomy"
            and status == DealStatus.APPROVAL_PENDING.value
        ):
            status = DealStatus.APPROVED.value
        deal = db.scalar(
            select(Deal).where(Deal.campaign_id == campaign.id, Deal.creator_id == creator.id)
        )
        if not deal:
            deal = Deal(
                campaign_id=campaign.id,
                creator_id=creator.id,
                fit_score=fit,
                status=status,
                compensation={
                    "pricing_mode": (campaign.compensation or {}).get("pricing_mode", "user"),
                    "components": [dict(row) for row in compensation_components(campaign)],
                },
                replacement_attempt=(
                    replacement_for.replacement_attempt + 1 if replacement_for else 0
                ),
                replacement_for_id=replacement_for.id if replacement_for else None,
            )
            db.add(deal)
            db.flush()
            emit(
                db,
                campaign.id,
                "deal.policy_decision",
                {
                    "deal_id": deal.id,
                    "handle": creator.handle,
                    "fit_score": fit,
                    "decision": decision,
                    "reason": reason,
                },
            )
            if status == DealStatus.APPROVED.value:
                enqueue_hermes_task(
                    db,
                    campaign.id,
                    "outreach",
                    deal_id=deal.id,
                    dedupe_key=f"outreach:{deal.id}",
                )
            auto_approved += decision == "approved"
            auto_rejected += decision == "rejected"
            if status == DealStatus.APPROVAL_PENDING.value:
                create_approval_request(db, campaign.id, "deal", deal.id)
        deals.append(deal)
    emit(
        db,
        campaign.id,
        "discovery.completed",
        {
            "candidates": len(deals),
            "auto_approved": auto_approved,
            "auto_rejected": auto_rejected,
        },
    )
    db.commit()
    return deals


def close_and_replace_deal(
    db: Session,
    deal: Deal,
    providers: Providers,
    *,
    reason: str,
) -> Deal | None:
    deal.status = DealStatus.REJECTED.value
    emit(
        db, deal.campaign_id, "deal.closed_for_replacement", {"deal_id": deal.id, "reason": reason}
    )
    limit = int((deal.campaign.brand.policy or {}).get("replacement_limit", 3))
    if deal.replacement_attempt >= limit:
        emit(
            db,
            deal.campaign_id,
            "creator_shortfall",
            {"deal_id": deal.id, "replacement_attempts": deal.replacement_attempt},
        )
        db.commit()
        return None
    db.flush()
    replacements = discover(db, deal.campaign, providers, replacement_for=deal)
    replacement = replacements[0] if replacements else None
    if replacement:
        emit(
            db,
            deal.campaign_id,
            "creator.replacement_discovered",
            {"closed_deal_id": deal.id, "replacement_deal_id": replacement.id},
        )
        db.commit()
    return replacement


def launch_campaign(
    db: Session, campaign: Campaign, providers: Providers, settings: Settings
) -> dict[str, Any]:
    if campaign.status != CampaignStatus.AWAITING_FUNDING.value:
        raise HTTPException(status_code=409, detail="Campaign is not ready to launch")
    funding = db.scalar(select(FundingPayment).where(FundingPayment.campaign_id == campaign.id))
    if not funding or funding.status != "succeeded":
        raise HTTPException(status_code=409, detail="Campaign funding has not succeeded")
    if settings.require_nemoclaw and not providers.hermes.healthy():
        raise HTTPException(status_code=503, detail="NemoHermes runtime is unavailable")
    transition_campaign(db, campaign, CampaignStatus.ACTIVE.value)
    db.commit()
    deals = discover(db, campaign, providers)
    return {"status": campaign.status, "discovered": len(deals)}


def decide_approval(db: Session, request: ApprovalCreate, *, stripe_live: bool) -> Approval:
    campaign = must_get(db, Campaign, request.campaign_id)
    if request.expected_version is not None and campaign.version != request.expected_version:
        raise HTTPException(
            status_code=409, detail="Campaign version changed; reload before approving"
        )
    approval = Approval(**request.model_dump(exclude={"expected_version"}))
    db.add(approval)
    if request.resource_type == "strategy":
        strategy = must_get(db, CampaignStrategy, request.resource_id)
        if strategy.campaign_id != campaign.id:
            raise HTTPException(status_code=409, detail="Strategy belongs to another campaign")
        if request.decision == "approved":
            strategy.approved = True
            campaign.compensation_locked = True
            transition_campaign(db, campaign, CampaignStatus.AWAITING_FUNDING.value)
            enqueue_hermes_task(db, campaign.id, "funding", dedupe_key=f"funding:{campaign.id}")
        else:
            transition_campaign(db, campaign, CampaignStatus.CANCELLED.value)
    elif request.resource_type == "deal":
        deal = must_get(db, Deal, request.resource_id)
        if deal.campaign_id != campaign.id or deal.status != DealStatus.APPROVAL_PENDING.value:
            raise HTTPException(status_code=409, detail="Deal is not awaiting this approval")
        deal.status = (
            DealStatus.APPROVED.value
            if request.decision == "approved"
            else DealStatus.REJECTED.value
        )
    elif request.resource_type == "payout":
        payout = must_get(db, Payout, request.resource_id)
        if payout.campaign_id != campaign.id or payout.status != "ready":
            raise HTTPException(status_code=409, detail="Payout is not ready for approval")
    else:
        spend = must_get(db, ServiceSpend, request.resource_id)
        if spend.campaign_id != campaign.id:
            raise HTTPException(status_code=409, detail="Spend belongs to another campaign")
        if stripe_live and request.decision == "approved":
            spend.status = "operator_approved"
        else:
            spend.status = "operator_approved" if request.decision == "approved" else "rejected"
    pending = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.resource_type == request.resource_type,
            ApprovalRequest.resource_id == request.resource_id,
            ApprovalRequest.status == "pending",
        )
    )
    if pending:
        pending.status = request.decision
        pending.decision_source = "api"
        pending.decided_at = utcnow()
    emit(db, campaign.id, "approval.decided", request.model_dump(exclude_none=True))
    db.commit()
    return approval


def _browser_email_enabled(providers: Providers) -> bool:
    return providers.mail.settings.email_transport == "browser"


def _queue_browser_email(
    db: Session,
    deal: Deal,
    providers: Providers,
    *,
    subject: str,
    body: str,
    intent: str,
    idempotency_key: str,
    proposed_rate_cents: int | None = None,
) -> HermesTask:
    settings = providers.mail.settings
    task = enqueue_hermes_task(
        db,
        deal.campaign_id,
        "browser_email",
        deal_id=deal.id,
        payload={
            "provider": settings.browser_email_provider,
            "sender": settings.browser_email_sender,
            "to": deal.creator.email,
            "subject": subject,
            "body": body,
            "intent": intent,
            "proposed_rate_cents": proposed_rate_cents,
            "reply_thread_id": _email_thread_id(db, deal),
        },
        dedupe_key=f"browser-email:{idempotency_key}",
    )
    emit(
        db,
        deal.campaign_id,
        "browser_email.queued",
        {"task_id": task.id, "deal_id": deal.id, "provider": settings.browser_email_provider},
    )
    db.commit()
    return task


def browser_email_preflight(db: Session, settings: Settings) -> dict[str, Any]:
    if settings.email_transport != "browser":
        return {"mode": "gmail_api", "browser_required": False}
    open_statuses = {
        DealStatus.CONTACTED.value,
        DealStatus.CONTRACTED.value,
        DealStatus.DRAFT_APPROVED.value,
        DealStatus.REVISION_REQUIRED.value,
    }
    deals = db.scalars(select(Deal).where(Deal.status.in_(open_statuses))).all()
    return {
        "mode": "browser",
        "browser_required": True,
        "provider": settings.browser_email_provider,
        "sender": settings.browser_email_sender,
        "mail_url": (
            "https://outlook.office.com/mail/"
            if settings.browser_email_provider == "outlook"
            else "https://mail.google.com/"
        ),
        "open_threads": [
            {
                "deal_id": deal.id,
                "creator_email": deal.creator.email,
                "subject": f"Collaboration: {deal.campaign.name}",
                "thread_id": _email_thread_id(db, deal),
                "status": deal.status,
            }
            for deal in deals
        ],
    }


def confirm_browser_email(
    db: Session,
    task_id: str,
    settings: Settings,
    *,
    sender: str,
    external_id: str | None = None,
    thread_id: str | None = None,
) -> HermesTask:
    task = must_get(db, HermesTask, task_id)
    if task.task_type != "browser_email":
        raise HTTPException(status_code=409, detail="Task is not a browser email")
    if task.status not in {"pending", "claimed"}:
        if task.status == "completed":
            return task
        raise HTTPException(status_code=409, detail=f"Browser email task is {task.status}")
    if sender.strip().lower() != settings.browser_email_sender.strip().lower():
        raise HTTPException(status_code=409, detail="Active sender does not match setup")
    deal = must_get(db, Deal, task.deal_id or "")
    payload = task.payload or {}
    message_external_id = external_id or f"browser:{task.id}"
    channel = f"browser_{settings.browser_email_provider}"
    existing = db.scalar(
        select(DealMessage).where(
            DealMessage.channel == channel,
            DealMessage.external_id == message_external_id,
        )
    )
    if not existing:
        db.add(
            DealMessage(
                deal_id=deal.id,
                direction="outbound",
                channel=channel,
                external_id=message_external_id,
                provider_thread_id=thread_id or payload.get("reply_thread_id") or task.id,
                body=str(payload.get("body") or ""),
                intent=str(payload.get("intent") or "update"),
                proposed_rate_cents=payload.get("proposed_rate_cents"),
            )
        )
    if payload.get("intent") == "offer":
        deal.agreed_rate_cents = int(payload.get("proposed_rate_cents") or 0) or None
        deal.status = DealStatus.CONTACTED.value
    emit(
        db,
        deal.campaign_id,
        "outreach.sent" if payload.get("intent") == "offer" else "browser_email.sent",
        {"deal_id": deal.id, "task_id": task.id, "channel": channel},
    )
    return complete_hermes_task(
        db,
        task.id,
        result={"message_id": message_external_id, "thread_id": thread_id or task.id},
        evidence={"sender": sender, "provider": settings.browser_email_provider},
    )


def send_outreach(
    db: Session, deal: Deal, providers: Providers, rate_cents: int | None
) -> dict[str, Any]:
    if deal.status != DealStatus.APPROVED.value:
        raise HTTPException(status_code=409, detail="Creator must be approved before outreach")
    components = list((deal.compensation or {}).get("components", []))
    rate = _primary_rate(components, rate_cents or deal.campaign.strategy.target_rate_cents)
    if deal.campaign.operation_mode == "full_autonomy" and (
        deal.campaign.compensation_source == "hugo"
    ):
        market_factor = min(1.0, max(0.85, deal.fit_score / 100))
        rate = max(1, round(rate * market_factor))
        if components:
            selected_kind = (
                "base"
                if any(c.get("kind") == "base" for c in components)
                else components[0]["kind"]
            )
            deal.compensation = {
                **(deal.compensation or {}),
                "components": [
                    {**component, "rate_cents": rate}
                    if component.get("kind") == selected_kind
                    else component
                    for component in components
                ],
            }
    if rate > deal.campaign.per_creator_cap_cents:
        raise HTTPException(status_code=422, detail="Offer exceeds per-creator cap")
    body = providers.hermes.outreach(
        {
            "handle": deal.creator.handle,
            "campaign_name": deal.campaign.name,
            "rate_cents": rate,
            "goal": deal.campaign.goal,
            "platform": deal.campaign.platform,
            "compensation": deal.compensation,
        }
    )
    if _browser_email_enabled(providers):
        task = _queue_browser_email(
            db,
            deal,
            providers,
            subject=f"Collaboration: {deal.campaign.name}",
            body=body,
            intent="offer",
            idempotency_key=deal.id,
            proposed_rate_cents=rate,
        )
        return {
            "status": "browser_action_required",
            "task_id": task.id,
            "provider": providers.mail.settings.browser_email_provider,
            "sender": providers.mail.settings.browser_email_sender,
            "to": deal.creator.email,
            "subject": f"Collaboration: {deal.campaign.name}",
            "body": body,
        }
    result = providers.mail.send(
        deal.creator.email or "",
        f"Collaboration: {deal.campaign.name}",
        body,
        deal.id,
    )
    deal.agreed_rate_cents = rate
    deal.status = DealStatus.CONTACTED.value
    db.add(
        DealMessage(
            deal_id=deal.id,
            direction="outbound",
            external_id=result.message_id,
            provider_thread_id=result.thread_id,
            body=body,
            intent="offer",
            proposed_rate_cents=rate,
        )
    )
    emit(
        db,
        deal.campaign_id,
        "outreach.sent",
        {"deal_id": deal.id, "message_id": result.message_id, "channel": "gmail"},
    )
    db.commit()
    return {"message_id": result.message_id, "thread_id": result.thread_id, "body": body}


def process_creator_response(
    db: Session,
    deal: Deal,
    data: CreatorResponseCreate,
    providers: Providers,
) -> dict[str, Any]:
    if deal.status != DealStatus.CONTACTED.value:
        raise HTTPException(status_code=409, detail="Deal is not awaiting a creator response")
    existing = db.scalar(
        select(DealMessage).where(
            DealMessage.channel == "gmail", DealMessage.external_id == data.external_id
        )
    )
    if existing:
        return {
            "deal_id": deal.id,
            "status": deal.status,
            "duplicate": True,
            "message_id": existing.id,
        }
    inbound = DealMessage(
        deal_id=deal.id,
        direction="inbound",
        external_id=data.external_id,
        provider_thread_id=data.thread_id,
        body=data.body,
    )
    db.add(inbound)
    db.flush()
    lowered = data.body.lower()
    declined = any(
        phrase in lowered
        for phrase in (
            "decline",
            "not interested",
            "no thanks",
            "cannot accept",
            "can't accept",
            "do not accept",
        )
    )
    accepted = not declined and bool(
        re.search(r"\b(accept|accepted|agree|agreed|yes)\b", lowered)
    )
    if accepted:
        intent = "accept"
        response_text = (
            "Your acceptance is recorded as the agreement. Reply with DRAFT: followed by a "
            "public media URL and include the "
            "caption in the same email. We will run NVIDIA QA and reply in this thread."
        )
    else:
        intent = "decline"
        response_text = "Thanks for considering the campaign. We will close this opportunity."
    inbound.intent = intent
    thread_id = data.thread_id or db.scalar(
        select(DealMessage.provider_thread_id)
        .where(
            DealMessage.deal_id == deal.id,
            DealMessage.provider_thread_id.is_not(None),
        )
        .order_by(DealMessage.created_at.desc())
    )
    if _browser_email_enabled(providers):
        browser_task = _queue_browser_email(
            db,
            deal,
            providers,
            subject=f"Re: Collaboration: {deal.campaign.name}",
            body=response_text,
            intent=intent,
            idempotency_key=f"{deal.id}:{data.external_id}",
            proposed_rate_cents=deal.agreed_rate_cents,
        )
        outbound_message_id = browser_task.id
    else:
        outbound = providers.mail.send(
            deal.creator.email or "",
            f"Re: Collaboration: {deal.campaign.name}",
            response_text,
            f"{deal.id}:{data.external_id}",
            thread_id=thread_id,
        )
        outbound_message_id = outbound.message_id
        db.add(
            DealMessage(
                deal_id=deal.id,
                direction="outbound",
                external_id=outbound.message_id,
                provider_thread_id=outbound.thread_id,
                body=response_text,
                intent=intent,
                proposed_rate_cents=deal.agreed_rate_cents,
            )
        )
    emit(
        db,
        deal.campaign_id,
        "creator.response_processed",
        {
            "deal_id": deal.id,
            "intent": intent,
            "agreed_rate_cents": deal.agreed_rate_cents,
        },
    )
    if accepted:
        if not deal.creator.stripe_account_id:
            account_id, onboarding_url, onboarding_complete = (
                providers.payments.create_onboarding_link(deal.creator.id, deal.creator.email)
            )
            deal.creator.stripe_account_id = account_id
            deal.creator.stripe_onboarding_complete = onboarding_complete
            response_text += (
                "\n\nBefore payout, complete Stripe's hosted recipient onboarding: "
                f"{onboarding_url}"
            )
        accept_terms(db, deal)
    else:
        close_and_replace_deal(db, deal, providers, reason="creator_declined_fixed_offer")
    return {
        "deal_id": deal.id,
        "status": deal.status,
        "intent": intent,
        "agreed_rate_cents": deal.agreed_rate_cents,
        "response": response_text,
        "message_id": outbound_message_id,
    }


def _email_thread_id(db: Session, deal: Deal) -> str | None:
    return db.scalar(
        select(DealMessage.provider_thread_id)
        .where(
            DealMessage.deal_id == deal.id,
            DealMessage.provider_thread_id.is_not(None),
        )
        .order_by(DealMessage.created_at.desc())
    )


def _send_deal_update(
    db: Session,
    deal: Deal,
    providers: Providers,
    subject: str,
    body: str,
    intent: str,
    idempotency_key: str,
) -> None:
    if _browser_email_enabled(providers):
        _queue_browser_email(
            db,
            deal,
            providers,
            subject=subject,
            body=body,
            intent=intent,
            idempotency_key=idempotency_key,
            proposed_rate_cents=deal.agreed_rate_cents,
        )
        return
    result = providers.mail.send(
        deal.creator.email or "",
        subject,
        body,
        idempotency_key,
        thread_id=_email_thread_id(db, deal),
    )
    db.add(
        DealMessage(
            deal_id=deal.id,
            direction="outbound",
            external_id=result.message_id,
            provider_thread_id=result.thread_id,
            body=body,
            intent=intent,
            proposed_rate_cents=deal.agreed_rate_cents,
        )
    )
    db.commit()


def _submission_from_email(body: str, stage: str) -> DeliverableCreate:
    fresh = re.split(r"\nOn .+wrote:\s*\n|\nFrom:\s", body, maxsplit=1, flags=re.IGNORECASE)[0]
    urls = re.findall(r"https?://[^\s<>]+", fresh)
    cleaned_urls = [url.rstrip(".,);]") for url in urls]
    if not cleaned_urls:
        raise ValueError("No public media URL was found in the creator email")
    if stage == "final":
        return DeliverableCreate(
            caption=fresh.strip(),
            media_url=cleaned_urls[0],
            post_url=cleaned_urls[0],
            stage="final",
        )
    return DeliverableCreate(caption=fresh.strip(), media_url=cleaned_urls[0], stage="draft")


def process_creator_email_updates(db: Session, providers: Providers) -> int:
    """Poll Gmail threads and advance fixed-offer or deliverable state idempotently."""
    if _browser_email_enabled(providers):
        return 0
    open_statuses = {
        DealStatus.CONTACTED.value,
        DealStatus.CONTRACTED.value,
        DealStatus.DRAFT_APPROVED.value,
        DealStatus.REVISION_REQUIRED.value,
    }
    deals = db.scalars(select(Deal).where(Deal.status.in_(open_statuses))).all()
    by_thread = {thread_id: deal for deal in deals if (thread_id := _email_thread_id(db, deal))}
    if not by_thread:
        return 0
    processed = 0
    for reply in providers.mail.thread_messages(set(by_thread)):
        deal = by_thread.get(reply.thread_id)
        if not deal or reply.sender != (deal.creator.email or "").lower():
            continue
        if db.scalar(
            select(DealMessage.id).where(
                DealMessage.channel == "gmail", DealMessage.external_id == reply.message_id
            )
        ):
            continue
        if deal.status == DealStatus.CONTACTED.value:
            process_creator_response(
                db,
                deal,
                CreatorResponseCreate(
                    body=reply.body,
                    external_id=reply.message_id,
                    thread_id=reply.thread_id,
                ),
                providers,
            )
            processed += 1
            continue
        stage = "final" if deal.draft_approved else "draft"
        try:
            deliverable = submit_deliverable(
                db,
                deal,
                _submission_from_email(reply.body, stage),
                providers,
            )
            findings = [
                finding["message"] for check in deliverable.checks for finding in check.findings
            ]
            if deliverable.qa_status == "verified" and stage == "draft":
                response = (
                    "Draft approved by NVIDIA QA. Reply in this thread with FINAL: followed "
                    "by the published post URL."
                )
            elif deliverable.qa_status == "verified":
                response = "Final content approved. The payout workflow is now running."
            else:
                response = "Revision required:\n- " + "\n- ".join(findings)
            _send_deal_update(
                db,
                deal,
                providers,
                f"Re: Collaboration: {deal.campaign.name}",
                response,
                "qa_result",
                f"qa-result:{deliverable.id}",
            )
        except (ValueError, HTTPException) as exc:
            db.add(
                DealMessage(
                    deal_id=deal.id,
                    direction="inbound",
                    external_id=reply.message_id,
                    provider_thread_id=reply.thread_id,
                    body=reply.body,
                    intent="invalid_submission",
                )
            )
            db.commit()
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            _send_deal_update(
                db,
                deal,
                providers,
                f"Re: Collaboration: {deal.campaign.name}",
                (
                    f"We could not process that submission: {detail}. "
                    "Please reply with one public URL."
                ),
                "submission_error",
                f"submission-error:{reply.message_id}",
            )
        processed += 1
    return processed


def advance_autonomous_campaigns(db: Session, providers: Providers, settings: Settings) -> int:
    """Advance safe campaign transitions; funding and external replies remain event-driven."""
    campaigns = db.scalars(
        select(Campaign).where(
            Campaign.operation_mode == "full_autonomy",
            Campaign.status.not_in(
                (
                    CampaignStatus.COMPLETED.value,
                    CampaignStatus.CANCELLED.value,
                    CampaignStatus.FAILED.value,
                )
            ),
        )
    ).all()
    advanced = 0
    for campaign in campaigns:
        if campaign.status == CampaignStatus.DRAFT.value:
            generate_strategy(db, campaign, providers)
            advanced += 1
            continue
        if campaign.status == CampaignStatus.AWAITING_FUNDING.value:
            funding = db.scalar(
                select(FundingPayment).where(FundingPayment.campaign_id == campaign.id)
            )
            if not funding:
                create_funding(db, campaign, providers)
                advanced += 1
            elif funding.status == "succeeded":
                launch_campaign(db, campaign, providers, settings)
                advanced += 1
            continue
        if campaign.status == CampaignStatus.ACTIVE.value:
            for deal in campaign.deals:
                if deal.status == DealStatus.APPROVED.value:
                    send_outreach(db, deal, providers, None)
                    advanced += 1
            for payout in db.scalars(
                select(Payout).where(Payout.campaign_id == campaign.id, Payout.status == "ready")
            ).all():
                creator = db.get(Creator, payout.creator_id)
                if creator and creator.stripe_onboarding_complete:
                    request_payout(db, payout, providers, settings)
                    advanced += 1
    return advanced


def accept_terms(db: Session, deal: Deal) -> Deal:
    if deal.status != DealStatus.CONTACTED.value:
        raise HTTPException(status_code=409, detail="Deal is not ready for acceptance")
    deal.terms_accepted = True
    deal.status = DealStatus.CONTRACTED.value
    for component in list((deal.compensation or {}).get("components", [])):
        kind = str(component["kind"])
        if not db.scalar(select(Payout).where(Payout.deal_id == deal.id, Payout.component == kind)):
            db.add(
                Payout(
                    campaign_id=deal.campaign_id,
                    deal_id=deal.id,
                    creator_id=deal.creator_id,
                    payout_model="flat" if kind == "base" else kind,
                    component=kind,
                    amount_cents=int(component["rate_cents"]) if kind == "base" else 0,
                    status="blocked_draft_qa",
                )
            )
    emit(db, deal.campaign_id, "deal.terms_accepted", {"deal_id": deal.id})
    db.commit()
    return deal


def run_qa_check(
    db: Session, deliverable: Deliverable, providers: Providers
) -> tuple[Deliverable, Any]:
    deal = deliverable.deal
    qa = providers.vision.verify(
        deliverable.caption,
        deliverable.media_url,
        platform=deal.campaign.platform,
        stage=deliverable.stage,
    )
    db.add(
        QACheck(
            deliverable_id=deliverable.id,
            severity=qa.severity,
            passed=qa.passed,
            findings=qa.findings,
            model=qa.model,
        )
    )
    deliverable.qa_status = "verified" if qa.passed else "revision_required"
    if qa.passed and deliverable.stage == "draft":
        deal.draft_approved = True
        deal.status = DealStatus.DRAFT_APPROVED.value
        for payout in db.scalars(select(Payout).where(Payout.deal_id == deal.id)).all():
            if payout.status == "blocked_draft_qa":
                payout.status = "blocked_final_qa"
    elif qa.passed:
        deal.final_approved = True
        deal.status = DealStatus.VERIFIED.value
        payouts = db.scalars(select(Payout).where(Payout.deal_id == deal.id)).all()
        if not payouts:
            for component in compensation_components(deal.campaign):
                kind = str(component["kind"])
                payout = Payout(
                    campaign_id=deal.campaign_id,
                    deal_id=deal.id,
                    creator_id=deal.creator_id,
                    payout_model="flat" if kind == "base" else kind,
                    component=kind,
                    amount_cents=int(component["rate_cents"]) if kind == "base" else 0,
                    status="blocked_final_qa",
                )
                db.add(payout)
                payouts.append(payout)
        for payout in payouts:
            if payout.status in {"blocked_draft_qa", "blocked_final_qa"}:
                payout.status = "ready" if payout.component == "base" else "awaiting_measurement"
            if payout.status == "ready" and deal.campaign.operation_mode != "full_autonomy":
                create_approval_request(db, deal.campaign_id, "payout", payout.id)
        if deal.campaign.platform == "youtube" and any(
            payout.status == "awaiting_measurement" for payout in payouts
        ):
            scheduled_at = utcnow() + timedelta(hours=deal.campaign.measurement_window_hours)
            _queue_job(
                db,
                "collect_metrics",
                f"collect-metrics:{deal.campaign_id}",
                {"campaign_id": deal.campaign_id},
                scheduled_at=scheduled_at,
            )
    else:
        deal.revision_count += 1
        max_rounds = int(deal.campaign.brand.policy.get("max_revision_rounds", 2))
        deal.status = DealStatus.REVISION_REQUIRED.value
        if deal.revision_count > max_rounds:
            close_and_replace_deal(db, deal, providers, reason=f"{deliverable.stage}_qa_failed")
    return deliverable, qa


def submit_deliverable(
    db: Session,
    deal: Deal,
    data: DeliverableCreate,
    providers: Providers,
    settings: Settings | None = None,
) -> Deliverable:
    allowed_statuses = {
        DealStatus.CONTRACTED.value,
        DealStatus.DRAFT_APPROVED.value,
        DealStatus.REVISION_REQUIRED.value,
    }
    if deal.status not in allowed_statuses:
        raise HTTPException(status_code=409, detail="Deal is not accepting a deliverable")
    if data.stage == "draft" and deal.draft_approved:
        raise HTTPException(status_code=409, detail="Draft QA has already passed")
    if data.stage == "draft" and not data.media_url:
        raise HTTPException(status_code=422, detail="Draft media URL is required")
    if data.stage == "final":
        if deal.campaign.qa_mode == "two_stage" and not deal.draft_approved:
            raise HTTPException(
                status_code=409, detail="Draft QA must pass before final submission"
            )
        if not _post_url_matches(deal.campaign.platform, data.post_url):
            raise HTTPException(
                status_code=422,
                detail=f"Final post URL must match {deal.campaign.platform}",
            )
    deliverable = Deliverable(deal_id=deal.id, **data.model_dump())
    db.add(deliverable)
    db.flush()
    deal.status = DealStatus.DRAFT_QA.value if data.stage == "draft" else DealStatus.FINAL_QA.value
    deliverable, qa = run_qa_check(db, deliverable, providers)
    emit(
        db,
        deal.campaign_id,
        "deliverable.qa_completed",
        {
            "deliverable_id": deliverable.id,
            "stage": data.stage,
            "passed": qa.passed,
            "findings": qa.findings,
        },
    )
    if qa.passed and data.stage == "final":
        pending_statuses = [
            DealStatus.APPROVAL_PENDING.value,
            DealStatus.APPROVED.value,
            DealStatus.CONTACTED.value,
            DealStatus.CONTRACTED.value,
            DealStatus.DRAFT_QA.value,
            DealStatus.DRAFT_APPROVED.value,
            DealStatus.FINAL_QA.value,
            DealStatus.SUBMITTED.value,
            DealStatus.QA_RUNNING.value,
            DealStatus.REVISION_REQUIRED.value,
        ]
        remaining = db.scalar(
            select(func.count(Deal.id)).where(
                Deal.campaign_id == deal.campaign_id,
                Deal.status.in_(pending_statuses),
            )
        )
        if remaining == 0 and deal.campaign.status == CampaignStatus.ACTIVE.value:
            transition_campaign(db, deal.campaign, CampaignStatus.MEASURING.value)
    db.commit()
    if (
        qa.passed
        and data.stage == "final"
        and settings
        and deal.campaign.operation_mode == "full_autonomy"
    ):
        for payout in db.scalars(
            select(Payout).where(Payout.deal_id == deal.id, Payout.status == "ready")
        ).all():
            try:
                request_payout(db, payout, providers, settings)
            except HTTPException:
                pass
    return deliverable


def request_payout(
    db: Session, payout: Payout, providers: Providers, settings: Settings | None = None
) -> Payout:
    if payout.status == "transferred":
        return payout
    if payout.status in {"awaiting_measurement", "not_due"}:
        raise HTTPException(status_code=409, detail="Payout is not ready for transfer")
    if payout.status not in {"ready", "failed"} or payout.amount_cents <= 0:
        raise HTTPException(status_code=409, detail="Payout has no transferable amount")
    deal = must_get(db, Deal, payout.deal_id)
    campaign = deal.campaign
    funding = db.scalar(select(FundingPayment).where(FundingPayment.campaign_id == campaign.id))
    qa_verified = (
        deal.final_approved or campaign.qa_mode == "legacy" or not bool(campaign.compensation)
    )
    if not qa_verified or (
        deal.status not in {DealStatus.VERIFIED.value, DealStatus.TRANSFERRED.value}
        or not deal.terms_accepted
    ):
        raise HTTPException(status_code=409, detail="Terms and verified QA are required")
    if (
        not funding
        or funding.status != "succeeded"
        or not funding.payment_intent_id
        or not funding.source_charge_id
    ):
        raise HTTPException(status_code=409, detail="Successful funding is required")
    if not deal.creator.stripe_account_id or not deal.creator.stripe_onboarding_complete:
        raise HTTPException(status_code=409, detail="Creator Connect onboarding is incomplete")
    if payout.amount_cents > campaign.per_creator_cap_cents:
        raise HTTPException(status_code=409, detail="Payout exceeds per-creator cap")
    deal_total = db.scalar(
        select(func.coalesce(func.sum(Payout.amount_cents), 0)).where(Payout.deal_id == deal.id)
    )
    if deal_total > campaign.per_creator_cap_cents:
        raise HTTPException(status_code=409, detail="Total creator payout exceeds per-creator cap")
    already_spent = abs(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
                LedgerEntry.campaign_id == campaign.id,
                LedgerEntry.amount_cents < 0,
            )
        )
    )
    if already_spent + payout.amount_cents > campaign.budget_cents:
        raise HTTPException(status_code=409, detail="Payout exceeds remaining campaign budget")
    payout.status = "processing"
    db.commit()
    try:
        result = providers.payments.transfer(
            payout_id=payout.id,
            amount_cents=payout.amount_cents,
            destination=deal.creator.stripe_account_id,
            source_transaction=funding.source_charge_id,
            campaign_id=campaign.id,
            idempotency_key=payout.idempotency_key,
        )
    except Exception:
        payout.status = "failed"
        db.commit()
        raise
    payout.status = "transferred"
    payout.stripe_transfer_id = result.external_id
    db.add(
        LedgerEntry(
            campaign_id=campaign.id,
            entry_type="creator_transfer",
            amount_cents=-payout.amount_cents,
            reference_id=result.external_id,
        )
    )
    emit(
        db,
        campaign.id,
        "payout.transferred",
        {"payout_id": payout.id, "transfer": result.external_id},
    )
    db.flush()
    deal_pending_payouts = db.scalar(
        select(func.count(Payout.id)).where(
            Payout.deal_id == deal.id,
            Payout.status.in_(["awaiting_measurement", "ready", "processing", "failed"]),
        )
    )
    if deal_pending_payouts == 0:
        deal.status = DealStatus.TRANSFERRED.value
        db.flush()
    committed_statuses = [
        DealStatus.APPROVED.value,
        DealStatus.CONTACTED.value,
        DealStatus.CONTRACTED.value,
        DealStatus.SUBMITTED.value,
        DealStatus.QA_RUNNING.value,
        DealStatus.REVISION_REQUIRED.value,
        DealStatus.VERIFIED.value,
        DealStatus.PAYOUT_QUEUED.value,
    ]
    remaining = db.scalar(
        select(func.count(Deal.id)).where(
            Deal.campaign_id == campaign.id,
            Deal.status.in_(committed_statuses),
        )
    )
    if remaining == 0 and campaign.status == CampaignStatus.ACTIVE.value:
        transition_campaign(db, campaign, CampaignStatus.MEASURING.value)
    pending_payouts = db.scalar(
        select(func.count(Payout.id)).where(
            Payout.campaign_id == campaign.id,
            Payout.status.in_(["awaiting_measurement", "ready", "processing", "failed"]),
        )
    )
    if (
        pending_payouts == 0
        and campaign.status == CampaignStatus.MEASURING.value
        and campaign.metrics_recorded
    ):
        transition_campaign(db, campaign, CampaignStatus.COMPLETED.value)
        enqueue_hermes_task(db, campaign.id, "learning", dedupe_key=f"learning:{campaign.id}")
    db.commit()
    if settings and settings.inline_jobs and campaign.status == CampaignStatus.COMPLETED.value:
        process_learning(db, campaign.id, providers)
    return payout


def record_metrics_and_close(
    db: Session,
    campaign: Campaign,
    metrics: MetricsCreate,
    settings: Settings,
    providers: Providers,
) -> Campaign:
    if campaign.status != CampaignStatus.MEASURING.value:
        raise HTTPException(status_code=409, detail="Campaign is not ready for final metrics")
    if campaign.metrics_recorded:
        return campaign
    supplied_metrics = {item.deal_id: item for item in metrics.deal_metrics}
    if len(supplied_metrics) != len(metrics.deal_metrics):
        raise HTTPException(status_code=422, detail="Deal metrics contain duplicate deal IDs")
    campaign_deal_ids = {deal.id for deal in campaign.deals}
    if not supplied_metrics.keys() <= campaign_deal_ids:
        raise HTTPException(status_code=422, detail="Deal metrics belong to another campaign")
    if (
        sum(item.views for item in supplied_metrics.values()) > metrics.views
        or sum(item.engagements for item in supplied_metrics.values()) > metrics.engagements
        or sum(item.conversions for item in supplied_metrics.values()) > metrics.conversions
    ):
        raise HTTPException(status_code=422, detail="Deal metrics exceed campaign totals")

    campaign.actual_views = metrics.views
    campaign.actual_engagements = metrics.engagements
    campaign.actual_conversions = metrics.conversions
    campaign.metrics_recorded = True

    if campaign.payout_model == "hybrid" and not (campaign.compensation or {}).get("components"):
        for deal in campaign.deals:
            base = db.scalar(
                select(Payout).where(
                    Payout.deal_id == deal.id,
                    Payout.component == "base",
                    Payout.status == "transferred",
                )
            )
            performance = db.scalar(
                select(Payout).where(
                    Payout.deal_id == deal.id,
                    Payout.component == "performance",
                )
            )
            if base and not performance:
                db.add(
                    Payout(
                        campaign_id=campaign.id,
                        deal_id=deal.id,
                        creator_id=deal.creator_id,
                        payout_model="hybrid",
                        component="performance",
                        amount_cents=0,
                        status="awaiting_measurement",
                    )
                )
        db.flush()

    measured_payouts = db.scalars(
        select(Payout)
        .where(
            Payout.campaign_id == campaign.id,
            Payout.component.in_(["cpm", "engagement", "affiliate", "performance"]),
            Payout.status == "awaiting_measurement",
        )
        .order_by(Payout.id)
    ).all()
    measured_count = len(measured_payouts)
    measured_deal_ids = {payout.deal_id for payout in measured_payouts}
    if supplied_metrics and set(supplied_metrics) != measured_deal_ids:
        raise HTTPException(
            status_code=422,
            detail="Provide metrics for every measured deal or use campaign totals",
        )
    for index, payout in enumerate(measured_payouts):
        deal = must_get(db, Deal, payout.deal_id)
        deal_result = supplied_metrics.get(deal.id)
        if deal_result:
            views = deal_result.views
            engagements = deal_result.engagements
            conversions = deal_result.conversions
        else:
            views = metrics.views // measured_count + (index < metrics.views % measured_count)
            engagements = metrics.engagements // measured_count + (
                index < metrics.engagements % measured_count
            )
            conversions = metrics.conversions // measured_count + (
                index < metrics.conversions % measured_count
            )
        component = next(
            (
                row
                for row in list((deal.compensation or {}).get("components", []))
                if row.get("kind") == payout.component
            ),
            None,
        )
        rate = (
            int(component.get("rate_cents"))
            if component
            else (deal.agreed_rate_cents or campaign.strategy.target_rate_cents)
        )
        if payout.payout_model == "cpm":
            raw_amount = payout_amount("cpm", rate, views=views)
            payout.measured_metric = views
        elif payout.payout_model == "engagement":
            raw_amount = payout_amount("engagement", rate, engagements=engagements)
            payout.measured_metric = engagements
        elif payout.payout_model == "affiliate":
            raw_amount = payout_amount("affiliate", rate, conversions=conversions)
            payout.measured_metric = conversions
        else:
            raw_amount = round(rate * 0.5 * views / 1000)
            payout.measured_metric = views
        other_amount = db.scalar(
            select(func.coalesce(func.sum(Payout.amount_cents), 0)).where(
                Payout.deal_id == deal.id,
                Payout.id != payout.id,
            )
        )
        payout.amount_cents = min(
            raw_amount,
            max(0, campaign.per_creator_cap_cents - int(other_amount or 0)),
        )
        payout.status = "ready" if payout.amount_cents else "not_due"
        emit(
            db,
            campaign.id,
            "payout.measured",
            {
                "payout_id": payout.id,
                "deal_id": deal.id,
                "model": payout.payout_model,
                "metric": payout.measured_metric,
                "amount_cents": payout.amount_cents,
                "status": payout.status,
            },
        )

    for deal in campaign.deals:
        pending_for_deal = db.scalar(
            select(func.count(Payout.id)).where(
                Payout.deal_id == deal.id,
                Payout.status.in_(
                    [
                        "blocked_draft_qa",
                        "blocked_final_qa",
                        "awaiting_measurement",
                        "ready",
                        "processing",
                        "failed",
                    ]
                ),
            )
        )
        if pending_for_deal == 0 and deal.final_approved:
            deal.status = DealStatus.TRANSFERRED.value

    for experiment in campaign.experiments:
        experiment.status = "completed"
        experiment.metrics = {
            "estimated_views": round(metrics.views * experiment.allocation),
            "estimated_engagements": round(metrics.engagements * experiment.allocation),
            "estimated_conversions": round(metrics.conversions * experiment.allocation),
        }
    pending_payouts = db.scalar(
        select(func.count(Payout.id)).where(
            Payout.campaign_id == campaign.id,
            Payout.status.in_(["awaiting_measurement", "ready", "processing", "failed"]),
        )
    )
    if pending_payouts == 0:
        transition_campaign(db, campaign, CampaignStatus.COMPLETED.value)
        enqueue_hermes_task(db, campaign.id, "learning", dedupe_key=f"learning:{campaign.id}")
    db.commit()
    if settings.inline_jobs and campaign.status == CampaignStatus.COMPLETED.value:
        process_learning(db, campaign.id, providers)
    if campaign.operation_mode == "full_autonomy":
        for payout in db.scalars(
            select(Payout).where(Payout.campaign_id == campaign.id, Payout.status == "ready")
        ).all():
            try:
                request_payout(db, payout, providers, settings)
            except HTTPException:
                pass
    return campaign


def collect_campaign_metrics(
    db: Session,
    campaign: Campaign,
    providers: Providers,
    settings: Settings,
    *,
    conversions: int = 0,
) -> Campaign:
    if campaign.platform != "youtube":
        raise HTTPException(
            status_code=409,
            detail="Instagram and TikTok require verified manual metrics",
        )
    deal_metrics: list[DealMetricsCreate] = []
    total_views = 0
    total_engagements = 0
    verified_deals = [deal for deal in campaign.deals if deal.final_approved]
    for deal in verified_deals:
        final = db.scalar(
            select(Deliverable)
            .where(Deliverable.deal_id == deal.id, Deliverable.stage == "final")
            .order_by(Deliverable.created_at.desc())
        )
        if not final or not final.post_url:
            raise HTTPException(status_code=409, detail="Verified YouTube post URL is missing")
        try:
            result = providers.metrics.collect("youtube", final.post_url)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"YouTube metrics failed: {exc}") from exc
        total_views += result.views
        total_engagements += result.engagements
        deal_metrics.append(
            DealMetricsCreate(
                deal_id=deal.id,
                views=result.views,
                engagements=result.engagements,
                conversions=0,
                source_url=result.source_url,
                evidence=str(result.evidence),
            )
        )
    for index, item in enumerate(deal_metrics):
        item.conversions = conversions // max(len(deal_metrics), 1) + (
            index < conversions % max(len(deal_metrics), 1)
        )
    return record_metrics_and_close(
        db,
        campaign,
        MetricsCreate(
            views=total_views,
            engagements=total_engagements,
            conversions=conversions,
            deal_metrics=deal_metrics,
        ),
        settings,
        providers,
    )


def _safe_learning(commit: LearningCommit) -> None:
    blocked = re.compile(
        r"(sk_(live|test)_|api[_-]?key|password|bearer\s+[a-z0-9]|"
        r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|private[_ -]?key|rm\s+-rf)",
        re.IGNORECASE,
    )
    material = " ".join(
        filter(
            None,
            [
                commit.heuristic,
                commit.no_op_reason,
                commit.summary,
                str(commit.governance),
            ],
        )
    )
    if blocked.search(material):
        raise ValueError("Learning content failed the secret/PII/instruction safety check")


MAX_EVIDENCE_PER_CATEGORY = 50


def build_learning_dossier(db: Session, campaign: Campaign) -> dict[str, Any]:
    payouts = db.scalars(select(Payout).where(Payout.campaign_id == campaign.id)).all()
    spends = db.scalars(select(ServiceSpend).where(ServiceSpend.campaign_id == campaign.id)).all()
    approvals = db.scalars(select(Approval).where(Approval.campaign_id == campaign.id)).all()
    events = db.scalars(select(DomainEvent).where(DomainEvent.campaign_id == campaign.id)).all()
    ledger = db.scalars(select(LedgerEntry).where(LedgerEntry.campaign_id == campaign.id)).all()
    qa_checks = db.scalars(
        select(QACheck)
        .join(Deliverable, QACheck.deliverable_id == Deliverable.id)
        .join(Deal, Deliverable.deal_id == Deal.id)
        .where(Deal.campaign_id == campaign.id)
    ).all()
    total_cost_cents = abs(sum(row.amount_cents for row in ledger if row.amount_cents < 0))
    if campaign.payout_model == "affiliate":
        result_value = campaign.actual_conversions or 1
    elif campaign.payout_model == "engagement":
        result_value = campaign.actual_engagements or 1
    else:
        result_value = campaign.actual_views or campaign.actual_engagements or 1
    cpr = total_cost_cents / 100 / result_value
    per_category = MAX_EVIDENCE_PER_CATEGORY
    evidence_ids = [f"campaign:{campaign.id}", f"run:{campaign.run_id}"]
    evidence_ids.extend(f"deal:{deal.id}" for deal in list(campaign.deals)[:per_category])
    evidence_ids.extend(f"qa:{check.id}" for check in qa_checks[:per_category])
    evidence_ids.extend(f"payout:{payout.id}" for payout in payouts[:per_category])
    evidence_ids.extend(f"spend:{spend.id}" for spend in spends[:per_category])
    evidence_ids.extend(f"approval:{approval.id}" for approval in approvals[:per_category])
    evidence_ids.extend(
        f"experiment:{experiment.id}" for experiment in list(campaign.experiments)[:per_category]
    )
    return {
        "campaign_id": campaign.id,
        "run_id": campaign.run_id,
        "terminal_status": campaign.status,
        "prediction": {
            "cost_per_result": (
                campaign.strategy.projected_cost_per_result if campaign.strategy else None
            ),
            "creator_tier": campaign.strategy.creator_tier if campaign.strategy else None,
            "target_creators": campaign.strategy.target_creators if campaign.strategy else 0,
            "skill_version": campaign.strategy.skill_version if campaign.strategy else None,
        },
        "outcomes": {
            "actual_cost_per_result": cpr,
            "views": campaign.actual_views,
            "engagements": campaign.actual_engagements,
            "conversions": campaign.actual_conversions,
            "metrics_recorded": campaign.metrics_recorded,
            "total_cost_cents": total_cost_cents,
            "transferred_payouts": sum(p.status == "transferred" for p in payouts),
        },
        "actual_cost_per_result": cpr,
        "predicted_cost_per_result": (
            campaign.strategy.projected_cost_per_result if campaign.strategy else None
        ),
        "qa_failures": sum(not check.passed for check in qa_checks),
        "revisions": sum(deal.revision_count for deal in campaign.deals),
        "creator_responses": [
            {
                "deal_id": deal.id,
                "offer_rate_cents": deal.agreed_rate_cents,
                "result": deal.status,
                "responses": sum(message.direction == "inbound" for message in deal.messages),
            }
            for deal in campaign.deals
        ],
        "experiments": [
            {
                "id": experiment.id,
                "variant": experiment.variant,
                "hypothesis": experiment.hypothesis,
                "allocation": experiment.allocation,
                "status": experiment.status,
                "metrics": experiment.metrics,
            }
            for experiment in campaign.experiments
        ],
        "human_overrides": [
            {
                "resource_type": approval.resource_type,
                "decision": approval.decision,
                "reason": approval.reason,
            }
            for approval in approvals
        ],
        "failures": [
            {"type": event.event_type, "payload": event.payload}
            for event in events
            if "failed" in event.event_type or "rejected" in str(event.payload).lower()
        ],
        "evidence_ids": evidence_ids,
    }


def commit_learning_change(
    db: Session,
    campaign: Campaign,
    learning: LearningRun,
    commit: LearningCommit,
) -> SkillVersion | None:
    _safe_learning(commit)
    dossier_ids = set(learning.evidence.get("evidence_ids", []))
    if not commit.evidence_ids or not set(commit.evidence_ids).issubset(dossier_ids):
        raise ValueError("Learning change cites evidence outside the authoritative dossier")
    if commit.change_type == "no_op":
        learning.patch_status = "no_op"
        learning.patch_summary = commit.no_op_reason
        learning.patch_error = None
        emit(
            db,
            campaign.id,
            "learning.skill_patch_no_op",
            {"learning_run_id": learning.id, "reason": commit.no_op_reason},
        )
        return None
    latest = db.scalar(
        select(SkillVersion)
        .where(SkillVersion.skill_name == commit.skill_name)
        .order_by(SkillVersion.version.desc())
    )
    version = (latest.version if latest else 1) + 1
    digest = hashlib.sha256((commit.heuristic or "").encode()).hexdigest()
    existing = db.scalar(
        select(SkillVersion).where(
            SkillVersion.skill_name == commit.skill_name,
            SkillVersion.content_hash == digest,
        )
    )
    if existing:
        learning.patch_status = "no_op"
        learning.patch_summary = "Proposed heuristic already exists in a validated version."
        learning.skill_version = existing.version
        return existing
    skill = SkillVersion(
        skill_name=commit.skill_name,
        version=version,
        previous_version=latest.version if latest else 1,
        content_hash=digest,
        summary=commit.heuristic or "",
        evidence_ids=commit.evidence_ids,
        governance=commit.governance,
        validated=True,
    )
    db.add(skill)
    learning.patch_status = "applied"
    learning.patch_summary = commit.heuristic
    learning.patch_error = None
    learning.skill_version = version
    emit(
        db,
        campaign.id,
        "learning.skill_patch_applied",
        {"learning_run_id": learning.id, "skill_version": version},
    )
    return skill


def process_learning(db: Session, campaign_id: str, providers: Providers) -> LearningRun:
    """Apply deterministic database learning, then optionally attempt a skill patch."""
    campaign = must_get(db, Campaign, campaign_id)
    learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign_id))
    if (
        learning
        and learning.baseline_status == "applied"
        and learning.patch_status
        in {
            "disabled",
            "applied",
            "failed",
            "no_op",
        }
    ):
        return learning
    if not learning:
        learning = LearningRun(campaign_id=campaign.id, run_id=campaign.run_id)
        db.add(learning)
        db.flush()

    dossier = build_learning_dossier(db, campaign)
    cpr = float(dossier["actual_cost_per_result"])

    try:
        if learning.baseline_status != "applied":
            learning.status = "database_running"
            learning.baseline_status = "running"
            creator_updates: list[dict[str, Any]] = []
            for deal in campaign.deals:
                reputation = deal.creator.reputation
                if not reputation:
                    continue
                before = _reputation_snapshot(reputation)
                weight = reputation.observations + 3
                reliability_observation = max(0, 100 - deal.revision_count * 25)
                performance_observation = min(100, max(0, 50 + (0.03 - cpr) * 1000))
                reputation.reliability = (
                    reputation.reliability * weight + reliability_observation
                ) / (weight + 1)
                reputation.performance = (
                    reputation.performance * weight + performance_observation
                ) / (weight + 1)
                reputation.observations += 1
                reputation.overall = (
                    reputation.performance * 0.45
                    + reputation.reliability * 0.35
                    + reputation.audience_quality * 0.20
                )
                creator_updates.append(
                    {
                        "creator_id": deal.creator.id,
                        "handle": deal.creator.handle,
                        "before": before,
                        "after": _reputation_snapshot(reputation),
                    }
                )

            prior_update = None
            if campaign.strategy:
                prior = db.scalar(
                    select(StrategyPrior).where(
                        StrategyPrior.niche == campaign.brand.niche,
                        StrategyPrior.creator_tier == campaign.strategy.creator_tier,
                    )
                )
                if not prior:
                    prior = StrategyPrior(
                        niche=campaign.brand.niche,
                        creator_tier=campaign.strategy.creator_tier,
                    )
                    db.add(prior)
                    db.flush()
                prior_before = _prior_snapshot(prior)
                old_observations = prior.observations
                prior.mean_cost_per_result = (
                    prior.mean_cost_per_result * old_observations + cpr
                ) / (old_observations + 1)
                prior.observations += 1
                prior.win_rate = (
                    prior.win_rate * old_observations + int(cpr <= 0.03)
                ) / prior.observations
                prior_update = {
                    "niche": prior.niche,
                    "creator_tier": prior.creator_tier,
                    "before": prior_before,
                    "after": _prior_snapshot(prior),
                }
            learning.database_updates = {
                "strategy_prior": prior_update,
                "creator_reputations": creator_updates,
            }
            learning.baseline_status = "applied"
            learning.status = "applied"
            prior_count = 1 if prior_update else 0
            learning.summary = (
                "Stored campaign outcomes in PostgreSQL: updated "
                f"{prior_count} strategy prior(s) and {len(creator_updates)} creator "
                "reputation record(s)."
            )
            learning.evidence = dossier
            learning.patch_status = (
                "pending" if campaign.learning_mode == "database_and_skill_patch" else "disabled"
            )
            learning.error = None
            emit(
                db,
                campaign.id,
                "learning.database_applied",
                {"learning_run_id": learning.id, "creator_updates": len(creator_updates)},
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign_id))
        if not learning:
            learning = LearningRun(campaign_id=campaign.id, run_id=campaign.run_id)
            db.add(learning)
        learning.baseline_status = "failed"
        learning.status = "failed"
        learning.error = str(exc)[:1000]
        db.commit()
        raise

    if campaign.learning_mode == "database_and_skill_patch" and learning.patch_status not in {
        "applied",
        "no_op",
        "failed",
    }:
        learning.patch_status = "hermes_running"
        db.commit()
        try:
            generated = providers.hermes.learning(dossier)
            commit = LearningCommit(
                summary=generated.summary,
                change_type=generated.change_type,
                heuristic=generated.heuristic,
                no_op_reason=generated.no_op_reason,
                skill_name=generated.skill_name,
                evidence_ids=generated.evidence_ids,
                governance=generated.governance,
            )
            db.expire_all()
            learning = must_get(db, LearningRun, learning.id)
            campaign = must_get(db, Campaign, campaign.id)
            if learning.patch_status not in {"applied", "no_op"}:
                commit_learning_change(db, campaign, learning, commit)
            db.commit()
        except Exception as exc:
            db.rollback()
            learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign_id))
            learning.patch_status = "failed"
            learning.patch_error = str(exc)[:1000]
            emit(
                db,
                campaign.id,
                "learning.skill_patch_failed",
                {"learning_run_id": learning.id, "error": learning.patch_error},
            )
            db.commit()

    job = db.scalar(
        select(OutboxJob).where(OutboxJob.dedupe_key == f"campaign-learning:{campaign.run_id}")
    )
    if job:
        job.status = "completed"
    db.commit()
    return learning


def _reputation_snapshot(reputation: CreatorReputation) -> dict[str, float | int]:
    return {
        "overall": round(reputation.overall, 2),
        "performance": round(reputation.performance, 2),
        "reliability": round(reputation.reliability, 2),
        "observations": reputation.observations,
    }


def _prior_snapshot(prior: StrategyPrior) -> dict[str, float | int]:
    return {
        "observations": prior.observations,
        "mean_cost_per_result": round(prior.mean_cost_per_result, 6),
        "win_rate": round(prior.win_rate, 4),
    }


def campaign_state(db: Session, campaign: Campaign) -> dict[str, Any]:
    funding = db.scalar(select(FundingPayment).where(FundingPayment.campaign_id == campaign.id))
    spends = db.scalars(select(ServiceSpend).where(ServiceSpend.campaign_id == campaign.id)).all()
    payouts = db.scalars(select(Payout).where(Payout.campaign_id == campaign.id)).all()
    ledger = db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.campaign_id == campaign.id)
        .order_by(LedgerEntry.created_at)
    ).all()
    learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign.id))
    approval_requests = db.scalars(
        select(ApprovalRequest)
        .where(ApprovalRequest.campaign_id == campaign.id)
        .order_by(ApprovalRequest.created_at.desc())
    ).all()
    playbook = db.scalar(
        select(AlgorithmPlaybook)
        .where(AlgorithmPlaybook.platform == campaign.platform)
        .order_by(AlgorithmPlaybook.updated_at.desc())
    )
    events = db.scalars(
        select(DomainEvent)
        .where(DomainEvent.campaign_id == campaign.id)
        .order_by(DomainEvent.created_at)
    ).all()
    next_action = _campaign_next_action(campaign, funding, spends, payouts)
    return {
        "campaign": {
            "id": campaign.id,
            "run_id": campaign.run_id,
            "brand_id": campaign.brand_id,
            "brand_name": campaign.brand.name,
            "name": campaign.name,
            "goal": campaign.goal,
            "platform": campaign.platform,
            "status": campaign.status,
            "version": campaign.version,
            "budget_cents": campaign.budget_cents,
            "per_creator_cap_cents": campaign.per_creator_cap_cents,
            "payout_model": campaign.payout_model,
            "compensation": campaign.compensation,
            "compensation_source": campaign.compensation_source,
            "compensation_locked": campaign.compensation_locked,
            "operation_mode": campaign.operation_mode,
            "measurement_window_hours": campaign.measurement_window_hours,
            "learning_mode": campaign.learning_mode,
            "views": campaign.actual_views,
            "engagements": campaign.actual_engagements,
            "conversions": campaign.actual_conversions,
            "next_action": next_action,
            "created_at": campaign.created_at.isoformat(),
            "updated_at": campaign.updated_at.isoformat(),
        },
        "strategy": None
        if not campaign.strategy
        else {
            "id": campaign.strategy.id,
            "creator_tier": campaign.strategy.creator_tier,
            "target_creators": campaign.strategy.target_creators,
            "target_rate_cents": campaign.strategy.target_rate_cents,
            "primary_allocation": campaign.strategy.primary_allocation,
            "challenger_allocation": campaign.strategy.challenger_allocation,
            "rationale": campaign.strategy.rationale,
            "projected_cost_per_result": campaign.strategy.projected_cost_per_result,
            "skill_version": campaign.strategy.skill_version,
            "approved": campaign.strategy.approved,
        },
        "algorithm_playbook": None
        if not playbook
        else {
            "id": playbook.id,
            "platform": playbook.platform,
            "signals": playbook.signals,
            "sources": playbook.sources,
            "confidence": playbook.confidence,
            "updated_at": playbook.updated_at.isoformat(),
        },
        "funding": None
        if not funding
        else {
            "id": funding.id,
            "status": funding.status,
            "amount_cents": funding.amount_cents,
            "checkout_url": funding.checkout_url,
            "payment_intent_id": funding.payment_intent_id,
            "source_charge_id": funding.source_charge_id,
        },
        "experiments": [
            {
                "id": experiment.id,
                "name": experiment.name,
                "hypothesis": experiment.hypothesis,
                "variant": experiment.variant,
                "allocation": experiment.allocation,
                "status": experiment.status,
                "metrics": experiment.metrics,
            }
            for experiment in campaign.experiments
        ],
        "service_spend": [
            {
                "id": spend.id,
                "provider": spend.provider,
                "amount_cents": spend.amount_cents,
                "status": spend.status,
                "context": spend.context,
                "spend_request_id": spend.spend_request_id,
            }
            for spend in spends
        ],
        "deals": [
            {
                "id": deal.id,
                "creator_id": deal.creator_id,
                "handle": deal.creator.handle,
                "platform": deal.creator.platform,
                "email": deal.creator.email,
                "followers": deal.creator.followers,
                "engagement_rate": deal.creator.engagement_rate,
                "fake_follower_percent": deal.creator.fake_follower_percent,
                "stripe_onboarded": deal.creator.stripe_onboarding_complete,
                "fit_score": deal.fit_score,
                "status": deal.status,
                "agreed_rate_cents": deal.agreed_rate_cents,
                "compensation": deal.compensation,
                "terms_accepted": deal.terms_accepted,
                "reputation": deal.creator.reputation.overall if deal.creator.reputation else 50,
                "revision_count": deal.revision_count,
                "draft_approved": deal.draft_approved,
                "final_approved": deal.final_approved,
                "replacement_attempt": deal.replacement_attempt,
                "messages": [
                    {
                        "id": message.id,
                        "direction": message.direction,
                        "channel": message.channel,
                        "body": message.body,
                        "intent": message.intent,
                        "proposed_rate_cents": message.proposed_rate_cents,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in deal.messages
                ],
                "deliverables": [
                    {
                        "id": deliverable.id,
                        "caption": deliverable.caption,
                        "media_url": deliverable.media_url,
                        "post_url": deliverable.post_url,
                        "qa_status": deliverable.qa_status,
                        "stage": deliverable.stage,
                        "created_at": deliverable.created_at.isoformat(),
                        "checks": [
                            {
                                "passed": check.passed,
                                "severity": check.severity,
                                "findings": check.findings,
                                "model": check.model,
                            }
                            for check in deliverable.checks
                        ],
                    }
                    for deliverable in deal.deliverables
                ],
            }
            for deal in campaign.deals
        ],
        "payouts": [
            {
                "id": payout.id,
                "deal_id": payout.deal_id,
                "creator_id": payout.creator_id,
                "payout_model": payout.payout_model,
                "component": payout.component,
                "amount_cents": payout.amount_cents,
                "measured_metric": payout.measured_metric,
                "status": payout.status,
                "transfer_id": payout.stripe_transfer_id,
            }
            for payout in payouts
        ],
        "ledger": {
            "funded_cents": sum(row.amount_cents for row in ledger if row.amount_cents > 0),
            "spent_cents": abs(sum(row.amount_cents for row in ledger if row.amount_cents < 0)),
            "remaining_cents": campaign.budget_cents
            + sum(row.amount_cents for row in ledger if row.amount_cents < 0),
            "entries": [
                {
                    "id": row.id,
                    "entry_type": row.entry_type,
                    "amount_cents": row.amount_cents,
                    "reference_id": row.reference_id,
                    "metadata": row.metadata_json,
                    "created_at": row.created_at.isoformat(),
                }
                for row in ledger
            ],
        },
        "learning": None if not learning else learning_projection(learning, campaign),
        "approval_requests": [
            {
                "id": request.id,
                "resource_type": request.resource_type,
                "resource_id": request.resource_id,
                "status": request.status,
                "expires_at": request.expires_at.isoformat(),
                "context": request.context,
            }
            for request in approval_requests
        ],
        "events": [
            {
                "type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


def learning_projection(learning: LearningRun, campaign: Campaign | None = None) -> dict[str, Any]:
    return {
        "id": learning.id,
        "campaign_id": learning.campaign_id,
        "campaign_name": campaign.name if campaign else None,
        "run_id": learning.run_id,
        "status": learning.status,
        "baseline_status": learning.baseline_status,
        "summary": learning.summary,
        "evidence": learning.evidence,
        "database_updates": learning.database_updates,
        "patch_status": learning.patch_status,
        "patch_summary": learning.patch_summary,
        "skill_version": learning.skill_version,
        "patch_error": learning.patch_error,
        "error": learning.error,
        "created_at": learning.created_at.isoformat(),
        "updated_at": learning.updated_at.isoformat(),
    }


def _campaign_next_action(
    campaign: Campaign,
    funding: FundingPayment | None,
    spends: list[ServiceSpend],
    payouts: list[Payout],
) -> str:
    if campaign.status == CampaignStatus.DRAFT.value:
        return "generate_strategy"
    if campaign.status == CampaignStatus.AWAITING_APPROVAL.value:
        return "approve_strategy"
    if campaign.status == CampaignStatus.AWAITING_FUNDING.value:
        if not funding:
            return "create_funding_session"
        if funding.status != "succeeded":
            return "complete_funding"
        pending_spend = next((row for row in spends if row.status == "pending_approval"), None)
        return "approve_service_spend" if pending_spend else "launch_campaign"
    pending_deal = next(
        (deal for deal in campaign.deals if deal.status == DealStatus.APPROVAL_PENDING.value),
        None,
    )
    if pending_deal:
        return "approve_creator"
    if any(deal.status == DealStatus.APPROVED.value for deal in campaign.deals):
        return "send_outreach"
    if any(deal.status == DealStatus.REVISION_REQUIRED.value for deal in campaign.deals):
        return "await_revision"
    if any(payout.status == "ready" for payout in payouts):
        return "request_payout"
    if campaign.status == CampaignStatus.MEASURING.value:
        return "record_metrics"
    if campaign.status == CampaignStatus.COMPLETED.value:
        return "review_learning"
    return "monitor"


def enqueue_hermes_task(
    db: Session,
    campaign_id: str,
    task_type: str,
    *,
    deal_id: str | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> HermesTask:
    if dedupe_key:
        existing = db.scalar(
            select(HermesTask).where(
                HermesTask.dedupe_key == dedupe_key,
                HermesTask.status.in_(["pending", "claimed"]),
            )
        )
        if existing:
            return existing
    task = HermesTask(
        campaign_id=campaign_id,
        deal_id=deal_id,
        task_type=task_type,
        payload=payload or {},
        dedupe_key=dedupe_key,
    )
    db.add(task)
    db.flush()
    emit(db, campaign_id, "hermes_task.enqueued", {"task_id": task.id, "type": task_type})
    return task


def claim_hermes_tasks(db: Session, limit: int = 5) -> list[HermesTask]:
    now = utcnow()
    expired = db.scalars(
        select(HermesTask).where(
            HermesTask.status == "claimed",
            HermesTask.lease_expires_at < now,
        )
    ).all()
    for task in expired:
        task.status = "pending"
        task.claimed_at = None
        task.lease_expires_at = None
        task.attempt += 1
    if expired:
        db.flush()

    pending = (
        select(HermesTask)
        .where(HermesTask.status == "pending")
        .order_by(HermesTask.created_at)
        .limit(limit)
    )
    if db.get_bind().dialect.name != "sqlite":
        pending = pending.with_for_update(skip_locked=True)
    tasks = db.scalars(pending).all()
    lease_end = now + timedelta(seconds=HermesTask.LEASE_SECONDS)
    for task in tasks:
        task.status = "claimed"
        task.claimed_at = now
        task.lease_expires_at = lease_end
        task.attempt += 1
    db.commit()
    return tasks


def complete_hermes_task(
    db: Session,
    task_id: str,
    result: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> HermesTask:
    task = must_get(db, HermesTask, task_id)
    if task.status not in ("claimed", "pending"):
        raise HTTPException(status_code=409, detail=f"Task is already {task.status}")
    task.status = "completed"
    task.result = result or {}
    if evidence:
        task.evidence = {**task.evidence, **evidence}
    emit(
        db,
        task.campaign_id,
        "hermes_task.completed",
        {"task_id": task.id, "type": task.task_type},
    )
    db.commit()
    return task


def fail_hermes_task(
    db: Session,
    task_id: str,
    error: str,
    evidence: dict[str, Any] | None = None,
) -> HermesTask:
    task = must_get(db, HermesTask, task_id)
    if task.status == "completed":
        raise HTTPException(status_code=409, detail="Task is already completed")
    task.status = "failed"
    task.error = error[:2000]
    if evidence:
        task.evidence = {**task.evidence, **evidence}
    emit(
        db,
        task.campaign_id,
        "hermes_task.failed",
        {"task_id": task.id, "type": task.task_type, "error": error[:200]},
    )
    db.commit()
    return task


def retry_hermes_task(db: Session, task_id: str) -> HermesTask:
    task = must_get(db, HermesTask, task_id)
    if task.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed tasks can be retried")
    task.status = "pending"
    task.error = None
    task.claimed_at = None
    task.lease_expires_at = None
    emit(
        db,
        task.campaign_id,
        "hermes_task.retried",
        {"task_id": task.id, "type": task.task_type},
    )
    db.commit()
    return task


def hermes_task_projection(task: HermesTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "campaign_id": task.campaign_id,
        "deal_id": task.deal_id,
        "task_type": task.task_type,
        "status": task.status,
        "payload": task.payload,
        "result": task.result,
        "error": task.error,
        "attempt": task.attempt,
        "lease_expires_at": task.lease_expires_at.isoformat() if task.lease_expires_at else None,
        "evidence": task.evidence,
        "created_at": task.created_at.isoformat(),
    }
