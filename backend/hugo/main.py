from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .domain import emit
from .messaging import confirm_telegram_pairing, start_telegram_pairing
from .models import (
    AlgorithmPlaybook,
    Brand,
    Campaign,
    CampaignStatus,
    CampaignStrategy,
    Creator,
    Deal,
    DealStatus,
    Deliverable,
    DomainEvent,
    FundingPayment,
    HermesTask,
    LearningRun,
    LedgerEntry,
    OutboxJob,
    Payout,
    ServiceSpend,
    SkillVersion,
    StrategyPrior,
    WebhookReceipt,
)
from .providers import Providers, build_providers
from .schemas import (
    AgentActionRequest,
    ApprovalCreate,
    BrandCreate,
    BrandRead,
    CampaignCreate,
    CampaignRead,
    CampaignUpdate,
    CompensationPlan,
    CreatorResponseCreate,
    DeliverableCreate,
    LearningCommit,
    MetricsCreate,
    OutreachCreate,
    StrategyRead,
)
from .security import require_agent_token, require_api_token
from .services import (
    _queue_job,
    browser_email_preflight,
    build_learning_dossier,
    campaign_state,
    claim_hermes_tasks,
    collect_campaign_metrics,
    commit_learning_change,
    complete_hermes_task,
    confirm_browser_email,
    create_campaign,
    create_funding,
    decide_approval,
    discover,
    fail_hermes_task,
    generate_strategy,
    hermes_task_projection,
    launch_campaign,
    learning_projection,
    mark_funded,
    must_get,
    process_creator_email_updates,
    process_creator_response,
    process_learning,
    record_metrics_and_close,
    record_service_spend,
    request_payout,
    request_service_spend,
    retry_hermes_task,
    run_qa_check,
    send_outreach,
    submit_deliverable,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Hugo", version="0.1.0", lifespan=lifespan)


async def settings_dep() -> Settings:
    return get_settings()


async def providers_dep(settings: Settings = Depends(settings_dep)) -> Providers:
    return build_providers(settings)


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])
internal = APIRouter(prefix="/internal/agent", dependencies=[Depends(require_agent_token)])


def _campaign_summary(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "run_id": campaign.run_id,
        "brand_id": campaign.brand_id,
        "brand_name": campaign.brand.name,
        "name": campaign.name,
        "goal": campaign.goal,
        "platform": campaign.platform,
        "budget_cents": campaign.budget_cents,
        "per_creator_cap_cents": campaign.per_creator_cap_cents,
        "payout_model": campaign.payout_model,
        "compensation": campaign.compensation,
        "compensation_source": campaign.compensation_source,
        "compensation_locked": campaign.compensation_locked,
        "operation_mode": campaign.operation_mode,
        "measurement_window_hours": campaign.measurement_window_hours,
        "learning_mode": campaign.learning_mode,
        "status": campaign.status,
        "version": campaign.version,
        "views": campaign.actual_views,
        "engagements": campaign.actual_engagements,
        "conversions": campaign.actual_conversions,
        "metrics_recorded": campaign.metrics_recorded,
        "created_at": campaign.created_at.isoformat(),
        "updated_at": campaign.updated_at.isoformat(),
        "is_demo": bool((campaign.brand.policy or {}).get("demo_seed")),
    }


def _action_queue_items(db: Session) -> list[dict]:
    items: list[dict] = []
    for campaign in db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.AWAITING_APPROVAL.value)
    ).all():
        if campaign.strategy:
            items.append(
                {
                    "id": campaign.strategy.id,
                    "type": "strategy",
                    "campaign_id": campaign.id,
                    "campaign_name": campaign.name,
                    "title": "Approve campaign strategy",
                    "detail": campaign.strategy.rationale,
                    "amount_cents": campaign.budget_cents,
                    "expected_version": campaign.version,
                    "created_at": campaign.strategy.created_at.isoformat(),
                }
            )
    for deal in db.scalars(
        select(Deal).where(Deal.status == DealStatus.APPROVAL_PENDING.value)
    ).all():
        items.append(
            {
                "id": deal.id,
                "type": "deal",
                "campaign_id": deal.campaign_id,
                "campaign_name": deal.campaign.name,
                "title": f"Approve @{deal.creator.handle}",
                "detail": f"Fit {deal.fit_score:.0f} · Reputation "
                f"{deal.creator.reputation.overall if deal.creator.reputation else 50:.0f}",
                "amount_cents": deal.campaign.strategy.target_rate_cents,
                "expected_version": deal.campaign.version,
                "created_at": deal.created_at.isoformat(),
            }
        )
    for spend in db.scalars(
        select(ServiceSpend).where(ServiceSpend.status == "pending_approval")
    ).all():
        campaign = db.get(Campaign, spend.campaign_id)
        items.append(
            {
                "id": spend.id,
                "type": "service_spend",
                "campaign_id": spend.campaign_id,
                "campaign_name": campaign.name,
                "title": f"Approve {spend.provider} credits",
                "detail": spend.context,
                "amount_cents": spend.amount_cents,
                "expected_version": campaign.version,
                "created_at": spend.created_at.isoformat(),
            }
        )
    for payout in db.scalars(select(Payout).where(Payout.status == "ready")).all():
        campaign = db.get(Campaign, payout.campaign_id)
        items.append(
            {
                "id": payout.id,
                "type": "payout",
                "campaign_id": payout.campaign_id,
                "campaign_name": campaign.name,
                "title": "Release creator payout",
                "detail": "Terms accepted and NVIDIA QA verified",
                "amount_cents": payout.amount_cents,
                "expected_version": campaign.version,
                "created_at": payout.created_at.isoformat(),
            }
        )
    return sorted(items, key=lambda item: item["created_at"], reverse=True)


@app.get("/health")
async def health(providers: Providers = Depends(providers_dep)) -> dict:
    return {
        "status": "ok",
        "service": "hugo-api",
        "hermes": "ok" if providers.hermes.healthy() else "unavailable",
        "model": get_settings().hermes_model,
        "nemoclaw_required": get_settings().require_nemoclaw,
    }


@v1.post("/brands", response_model=BrandRead, status_code=201)
async def create_brand(data: BrandCreate, db: Session = Depends(get_db)) -> Brand:
    brand = Brand(**data.model_dump())
    db.add(brand)
    db.commit()
    return brand


@v1.get("/brands")
async def list_brands(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Brand).order_by(Brand.created_at.desc())).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "website": row.website,
                "niche": row.niche,
                "policy": row.policy,
                "campaign_count": len(row.campaigns),
            }
            for row in rows
        ],
        "total": len(rows),
    }


@v1.put("/brands/{brand_id}/policy", response_model=BrandRead)
async def update_brand_policy(brand_id: str, policy: dict, db: Session = Depends(get_db)) -> Brand:
    brand = must_get(db, Brand, brand_id)
    brand.policy = {**brand.policy, **policy}
    db.commit()
    return brand


@v1.post("/campaigns", response_model=CampaignRead, status_code=201)
async def campaigns_create(
    data: CampaignCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> Campaign:
    if "operation_mode" not in data.model_fields_set:
        data = data.model_copy(update={"operation_mode": settings.telegram_approval_mode})
    return create_campaign(db, data)


@v1.patch("/campaigns/{campaign_id}/compensation")
async def campaign_compensation_update(
    campaign_id: str,
    data: CompensationPlan,
    db: Session = Depends(get_db),
) -> dict:
    campaign = must_get(db, Campaign, campaign_id)
    if campaign.compensation_locked or campaign.status not in {
        CampaignStatus.DRAFT.value,
        CampaignStatus.AWAITING_APPROVAL.value,
    }:
        raise HTTPException(status_code=409, detail="Compensation is already locked")
    if any(component.rate_cents > campaign.per_creator_cap_cents for component in data.components):
        raise HTTPException(status_code=422, detail="Compensation rate exceeds creator cap")
    campaign.compensation = data.model_dump()
    campaign.compensation_source = data.pricing_mode
    campaign.version += 1
    db.commit()
    return {"campaign_id": campaign.id, "compensation": campaign.compensation}


@v1.get("/campaigns")
async def campaigns_list(
    status: str | None = None,
    brand_id: str | None = None,
    search: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    query = select(Campaign)
    count_query = select(func.count(Campaign.id))
    if status:
        query = query.where(Campaign.status == status)
        count_query = count_query.where(Campaign.status == status)
    if brand_id:
        query = query.where(Campaign.brand_id == brand_id)
        count_query = count_query.where(Campaign.brand_id == brand_id)
    if search:
        query = query.where(Campaign.name.ilike(f"%{search}%"))
        count_query = count_query.where(Campaign.name.ilike(f"%{search}%"))
    rows = db.scalars(query.order_by(Campaign.updated_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_campaign_summary(row) for row in rows], "total": db.scalar(count_query) or 0}


@v1.patch("/campaigns/{campaign_id}", response_model=CampaignRead)
async def campaign_update(
    campaign_id: str,
    data: CampaignUpdate,
    db: Session = Depends(get_db),
) -> Campaign:
    campaign = must_get(db, Campaign, campaign_id)
    if campaign.status != CampaignStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Learning mode locks when strategy begins")
    campaign.learning_mode = data.learning_mode
    campaign.version += 1
    db.commit()
    return campaign


@v1.get("/overview")
async def overview(db: Session = Depends(get_db)) -> dict:
    campaigns = db.scalars(select(Campaign).order_by(Campaign.updated_at.desc())).all()
    events = db.scalars(select(DomainEvent).order_by(DomainEvent.created_at.desc()).limit(12)).all()
    funded = db.scalar(
        select(func.coalesce(func.sum(FundingPayment.amount_cents), 0)).where(
            FundingPayment.status == "succeeded"
        )
    )
    transferred = db.scalar(
        select(func.coalesce(func.sum(Payout.amount_cents), 0)).where(
            Payout.status == "transferred"
        )
    )
    actions = _action_queue_items(db)
    return {
        "campaigns_total": len(campaigns),
        "campaigns_active": sum(
            row.status not in {"completed", "failed", "cancelled"} for row in campaigns
        ),
        "funded_cents": funded or 0,
        "transferred_cents": transferred or 0,
        "pending_actions": len(actions),
        "campaigns": [_campaign_summary(row) for row in campaigns[:8]],
        "actions": actions[:6],
        "events": [
            {
                "campaign_id": event.campaign_id,
                "campaign_name": db.get(Campaign, event.campaign_id).name,
                "type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


@v1.get("/action-queue")
async def action_queue(
    type: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    items = _action_queue_items(db)
    if type:
        items = [item for item in items if item["type"] == type]
    return {"items": items[offset : offset + limit], "total": len(items)}


@v1.get("/ledger")
async def ledger_list(
    campaign_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = select(LedgerEntry)
    count_query = select(func.count(LedgerEntry.id))
    if campaign_id:
        query = query.where(LedgerEntry.campaign_id == campaign_id)
        count_query = count_query.where(LedgerEntry.campaign_id == campaign_id)
    rows = db.scalars(
        query.order_by(LedgerEntry.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "campaign_id": row.campaign_id,
                "campaign_name": db.get(Campaign, row.campaign_id).name,
                "entry_type": row.entry_type,
                "amount_cents": row.amount_cents,
                "reference_id": row.reference_id,
                "metadata": row.metadata_json,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "total": db.scalar(count_query) or 0,
    }


@v1.get("/learning-runs")
async def learning_runs(
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    query = select(LearningRun).order_by(LearningRun.updated_at.desc())
    if status:
        query = query.where(LearningRun.status == status)
    rows = db.scalars(query).all()
    return {
        "items": [learning_projection(row, db.get(Campaign, row.campaign_id)) for row in rows],
        "total": len(rows),
        "strategy_priors": [
            {
                "id": prior.id,
                "niche": prior.niche,
                "creator_tier": prior.creator_tier,
                "observations": prior.observations,
                "mean_cost_per_result": prior.mean_cost_per_result,
                "win_rate": prior.win_rate,
                "updated_at": prior.updated_at.isoformat(),
            }
            for prior in db.scalars(
                select(StrategyPrior).order_by(StrategyPrior.updated_at.desc())
            ).all()
        ],
    }


@v1.get("/playbook")
async def algorithm_playbook(
    platform: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    query = select(AlgorithmPlaybook).order_by(AlgorithmPlaybook.updated_at.desc())
    if platform:
        query = query.where(AlgorithmPlaybook.platform == platform)
    latest_by_platform: dict[str, AlgorithmPlaybook] = {}
    for row in db.scalars(query).all():
        latest_by_platform.setdefault(row.platform, row)
    items = [
        {
            "id": row.id,
            "platform": row.platform,
            "signals": row.signals,
            "sources": row.sources,
            "confidence": row.confidence,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in latest_by_platform.values()
    ]
    return {"items": items, "total": len(items)}


@v1.get("/system/status")
async def system_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    providers: Providers = Depends(providers_dep),
) -> dict:
    db.execute(text("SELECT 1"))
    pending_jobs = (
        db.scalar(select(func.count(OutboxJob.id)).where(OutboxJob.status == "pending")) or 0
    )
    failed_jobs = (
        db.scalar(select(func.count(OutboxJob.id)).where(OutboxJob.status == "failed")) or 0
    )
    modes = settings.capability_modes()

    def capability_status(capability: str, *, healthy: bool = True) -> str:
        if modes[capability]["resolved"] == "missing":
            return "unavailable"
        return "ok" if healthy else "degraded"

    hermes_healthy = providers.hermes.healthy()
    real_in_demo = (
        [s.strip() for s in settings.demo_real_providers.split(",") if s.strip()]
        if settings.demo_mode
        else []
    )
    return {
        "environment": settings.env,
        "demo_mode": settings.demo_mode,
        "demo_real_providers": real_in_demo,
        "capabilities": modes,
        "services": [
            {"name": "PostgreSQL", "status": "ok", "detail": "Authoritative state"},
            {
                "name": "Hermes / NemoClaw",
                "status": capability_status("hermes", healthy=hermes_healthy),
                "detail": f"{settings.hermes_model} · {modes['hermes']['resolved']}",
            },
            {
                "name": "NVIDIA NIM",
                "status": capability_status("vision"),
                "detail": f"{settings.nvidia_vision_model} · {modes['vision']['resolved']}",
            },
            {
                "name": "Stripe",
                "status": capability_status("stripe"),
                "detail": f"Checkout, Link and Connect · {modes['stripe']['resolved']}",
            },
            {
                "name": "Creator email",
                "status": capability_status("gmail"),
                "detail": (
                    f"Hermes browser · {settings.browser_email_provider} · "
                    f"{settings.browser_email_sender or 'sender missing'}"
                    if settings.email_transport == "browser"
                    else f"Gmail API · {modes['gmail']['resolved']}"
                ),
            },
            {
                "name": "Learning worker",
                "status": "attention" if failed_jobs else "ok",
                "detail": f"{pending_jobs} pending · {failed_jobs} failed",
            },
            {
                "name": "Hermes cron",
                "status": "ok"
                if not (
                    db.scalar(
                        select(func.count(HermesTask.id)).where(HermesTask.status == "failed")
                    )
                    or 0
                )
                else "attention",
                "detail": "{} pending · {} claimed · {} failed".format(
                    db.scalar(
                        select(func.count(HermesTask.id)).where(HermesTask.status == "pending")
                    )
                    or 0,
                    db.scalar(
                        select(func.count(HermesTask.id)).where(HermesTask.status == "claimed")
                    )
                    or 0,
                    db.scalar(
                        select(func.count(HermesTask.id)).where(HermesTask.status == "failed")
                    )
                    or 0,
                ),
            },
        ],
    }


@v1.post("/system/live-probe")
async def live_probe(
    settings: Settings = Depends(settings_dep),
    providers: Providers = Depends(providers_dep),
) -> dict:
    """Run explicit live connectivity checks for each integration."""
    import time

    modes = settings.capability_modes()
    result: dict[str, dict] = {}

    hermes_resolved = modes["hermes"]["resolved"]
    result["hermes"] = {"resolved": hermes_resolved}
    if hermes_resolved == "ready":
        start = time.perf_counter()
        try:
            sample = providers.hermes.sample()
            result["hermes"].update(
                {
                    "ok": True,
                    "model": sample["model"],
                    "excerpt": sample["response"],
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface the failure to the operator
            result["hermes"].update({"ok": False, "error": str(exc)[:200]})
    else:
        result["hermes"]["note"] = "Hermes is not configured. Complete the setup wizard."

    for capability in ("vision", "stripe", "gmail"):
        entry = {
            "resolved": modes[capability]["resolved"],
            "credentials_present": modes[capability]["credentials_present"],
        }
        if capability == "vision":
            entry["model"] = settings.nvidia_vision_model
        if entry["resolved"] == "ready":
            if capability == "gmail" and settings.email_transport == "browser":
                entry.update(
                    {
                        "ok": False,
                        "note": "Browser mode is verified by Hermes against the signed-in account.",
                        "provider": settings.browser_email_provider,
                        "sender": settings.browser_email_sender,
                    }
                )
                result[capability] = entry
                continue
            start = time.perf_counter()
            try:
                provider = {
                    "vision": providers.vision,
                    "stripe": providers.payments,
                    "gmail": providers.mail,
                }[capability]
                probe_result = provider.probe()
                entry.update(
                    {
                        "ok": True,
                        "latency_ms": round((time.perf_counter() - start) * 1000),
                        **probe_result,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                entry.update({"ok": False, "error": str(exc)[:200]})
        result[capability] = entry
    return result


@v1.get("/system/setup")
async def system_setup_get() -> dict:
    """Masked configuration snapshot + per-capability readiness for the setup wizard."""
    from .setup import summary

    return summary()


@v1.post("/system/setup")
async def system_setup_save(payload: dict) -> dict:
    """Persist setup-wizard values to .env (server-side) and return refreshed status."""
    from .setup import apply_updates

    updates = payload.get("updates", payload)
    if not isinstance(updates, dict):
        raise HTTPException(status_code=422, detail="Expected an object of env updates")
    try:
        return apply_updates({str(k): str(v) for k, v in updates.items()})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@v1.post("/system/setup/test")
async def system_setup_test(
    settings: Settings = Depends(settings_dep),
    providers: Providers = Depends(providers_dep),
) -> dict:
    """Probe each capability's reachability with the currently saved configuration."""
    modes = settings.capability_modes()
    results: dict[str, dict] = {}
    for capability, info in modes.items():
        entry = {"resolved": info["resolved"], "credentials_present": info["credentials_present"]}
        if info["resolved"] == "ready":
            try:
                if capability == "hermes":
                    entry["reachable"] = providers.hermes.healthy()
                elif capability == "vision":
                    entry["reachable"] = providers.vision.probe()["ok"]
                elif capability == "stripe":
                    entry["reachable"] = providers.payments.probe()["ok"]
                elif capability == "gmail":
                    if settings.email_transport == "browser":
                        entry["reachable"] = False
                        entry["error"] = "Connect and verify the signed-in browser through Hermes."
                    else:
                        entry["reachable"] = providers.mail.probe()["ok"]
                else:
                    entry["reachable"] = info["credentials_present"]
            except Exception as exc:  # noqa: BLE001 - report, don't raise
                entry["reachable"] = False
                entry["error"] = str(exc)[:160]
        elif info["resolved"] == "agent_managed":
            entry["reachable"] = providers.hermes.healthy()
        results[capability] = entry
    return {"capabilities": results}


@v1.post("/system/telegram/pair")
async def telegram_pair_start(
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    return start_telegram_pairing(db, providers)


@v1.post("/system/telegram/pair/confirm")
async def telegram_pair_confirm(
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    return confirm_telegram_pairing(db, providers)


@v1.get("/hermes/tasks/preflight")
async def hermes_tasks_preflight(db: Session = Depends(get_db)) -> dict:
    pending = (
        db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "pending")) or 0
    )
    claimed = (
        db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "claimed")) or 0
    )
    failed = db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "failed")) or 0
    return {
        "pending": pending,
        "claimed": claimed,
        "failed": failed,
        "should_claim": pending > 0,
    }


@v1.get("/hermes/tasks")
async def hermes_tasks_list(
    status: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = select(HermesTask).order_by(HermesTask.created_at.desc())
    if status:
        query = query.where(HermesTask.status == status)
    if campaign_id:
        query = query.where(HermesTask.campaign_id == campaign_id)
    tasks = db.scalars(query.limit(limit)).all()
    return {"tasks": [hermes_task_projection(task) for task in tasks], "total": len(tasks)}


@v1.post("/hermes/tasks/{task_id}/retry")
async def hermes_task_retry(task_id: str, db: Session = Depends(get_db)) -> dict:
    task = retry_hermes_task(db, task_id)
    return hermes_task_projection(task)


@v1.post("/campaigns/{campaign_id}/service-spend")
async def campaign_service_spend(
    campaign_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    spend = request_service_spend(db, must_get(db, Campaign, campaign_id), settings)
    return {
        "id": spend.id,
        "status": spend.status,
        "amount_cents": spend.amount_cents,
        "context": spend.context,
        "spend_request_id": spend.spend_request_id,
    }


@v1.post("/payouts/{payout_id}/release")
async def payout_release(
    payout_id: str,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    payout = request_payout(db, must_get(db, Payout, payout_id), providers, settings)
    return {"id": payout.id, "status": payout.status, "transfer_id": payout.stripe_transfer_id}


@v1.post("/campaigns/{campaign_id}/strategy", response_model=StrategyRead)
async def campaigns_strategy(
    campaign_id: str,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> CampaignStrategy:
    return generate_strategy(db, must_get(db, Campaign, campaign_id), providers)


@v1.post("/campaigns/{campaign_id}/funding-session")
async def campaigns_funding(
    campaign_id: str,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    funding = create_funding(db, must_get(db, Campaign, campaign_id), providers)
    return {
        "id": funding.id,
        "status": funding.status,
        "checkout_session_id": funding.checkout_session_id,
        "checkout_url": funding.checkout_url,
    }


@v1.post("/campaigns/{campaign_id}/launch")
async def campaigns_launch(
    campaign_id: str,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    return launch_campaign(db, must_get(db, Campaign, campaign_id), providers, settings)


@v1.post("/approvals", status_code=201)
async def approvals_create(
    data: ApprovalCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    providers: Providers = Depends(providers_dep),
) -> dict:
    approval = decide_approval(db, data, stripe_live=settings.capability_configured("stripe"))
    campaign = must_get(db, Campaign, data.campaign_id)
    if settings.inline_jobs and campaign.status in {
        CampaignStatus.COMPLETED.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.CANCELLED.value,
    }:
        process_learning(db, campaign.id, providers)
    return {"id": approval.id, "decision": approval.decision, "resource_id": approval.resource_id}


@v1.post("/deals/{deal_id}/outreach")
async def deals_outreach(
    deal_id: str,
    data: OutreachCreate,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    return send_outreach(db, must_get(db, Deal, deal_id), providers, data.proposed_rate_cents)


@v1.post("/deals/{deal_id}/response")
async def deals_creator_response(
    deal_id: str,
    data: CreatorResponseCreate,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    return process_creator_response(db, must_get(db, Deal, deal_id), data, providers)


@v1.post("/deals/{deal_id}/deliverables", status_code=201)
async def deals_deliverable(
    deal_id: str,
    data: DeliverableCreate,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    deliverable = submit_deliverable(db, must_get(db, Deal, deal_id), data, providers, settings)
    return {
        "id": deliverable.id,
        "deal_id": deliverable.deal_id,
        "qa_status": deliverable.qa_status,
        "stage": deliverable.stage,
        "checks": [
            {"passed": c.passed, "severity": c.severity, "findings": c.findings, "model": c.model}
            for c in deliverable.checks
        ],
    }


@v1.post("/creators/{creator_id}/connect-onboarding-link")
async def creator_connect(
    creator_id: str,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
) -> dict:
    creator = must_get(db, Creator, creator_id)
    if creator.stripe_account_id:
        return {"account_id": creator.stripe_account_id, "already_created": True}
    account_id, url, onboarding_complete = providers.payments.create_onboarding_link(
        creator.id, creator.email
    )
    creator.stripe_account_id = account_id
    creator.stripe_onboarding_complete = onboarding_complete
    db.commit()
    return {
        "account_id": account_id,
        "url": url,
        "onboarding_complete": onboarding_complete,
    }


@v1.post("/campaigns/{campaign_id}/metrics")
async def campaign_metrics(
    campaign_id: str,
    data: MetricsCreate,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    campaign = record_metrics_and_close(
        db, must_get(db, Campaign, campaign_id), data, settings, providers
    )
    return {"campaign_id": campaign.id, "status": campaign.status}


@v1.post("/campaigns/{campaign_id}/metrics/collect")
async def campaign_metrics_collect(
    campaign_id: str,
    conversions: int = 0,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    campaign = collect_campaign_metrics(
        db,
        must_get(db, Campaign, campaign_id),
        providers,
        settings,
        conversions=conversions,
    )
    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "views": campaign.actual_views,
        "engagements": campaign.actual_engagements,
        "conversions": campaign.actual_conversions,
    }


@v1.get("/campaigns/{campaign_id}/state")
async def campaign_get_state(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    return campaign_state(db, must_get(db, Campaign, campaign_id))


@v1.get("/campaigns/{campaign_id}/learning")
async def campaign_learning(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign_id))
    if not learning:
        raise HTTPException(status_code=404, detail="Learning has not run")
    return learning_projection(learning, must_get(db, Campaign, campaign_id))


@v1.get("/hermes/skills/versions")
async def skill_versions(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(SkillVersion).order_by(SkillVersion.skill_name, SkillVersion.version)
    ).all()
    return [
        {
            "skill_name": row.skill_name,
            "version": row.version,
            "content_hash": row.content_hash,
            "summary": row.summary,
            "governance": row.governance,
            "previous_version": row.previous_version,
            "validated": row.validated,
        }
        for row in rows
    ]


@app.post("/v1/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    providers: Providers = Depends(providers_dep),
) -> dict:
    raw = await request.body()
    import stripe

    try:
        event = stripe.Webhook.construct_event(
            raw, stripe_signature, settings.stripe_webhook_secret
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook") from exc
    event_id = event["id"]
    if db.get(WebhookReceipt, event_id):
        return {"received": True, "duplicate": True}
    event_type = event["type"]
    body = event["data"]["object"]
    db.add(WebhookReceipt(id=event_id, event_type=event_type, payload=event))
    if event_type in {"checkout.session.completed", "payment_intent.succeeded"}:
        campaign_id = body.get("metadata", {}).get("campaign_id")
        if not campaign_id and event_type == "checkout.session.completed":
            funding = db.scalar(
                select(FundingPayment).where(FundingPayment.checkout_session_id == body.get("id"))
            )
            campaign_id = funding.campaign_id if funding else None
        if campaign_id:
            payment_intent_id = body.get("payment_intent") or body.get("id")
            source_charge_id = body.get("latest_charge")
            if not source_charge_id:
                charges = body.get("charges", {}).get("data", [])
                source_charge_id = charges[0].get("id") if charges else None
            mark_funded(
                db,
                campaign_id,
                payment_intent_id,
                providers,
                source_charge_id=source_charge_id,
            )
    elif event_type == "payment_intent.payment_failed":
        campaign_id = body.get("metadata", {}).get("campaign_id")
        if campaign_id:
            funding = db.scalar(
                select(FundingPayment).where(FundingPayment.campaign_id == campaign_id)
            )
            if funding and funding.status != "succeeded":
                funding.status = "failed"
                emit(
                    db,
                    campaign_id,
                    "funding.failed",
                    {"payment_intent_id": body.get("id")},
                )
    elif event_type == "transfer.failed":
        payout_id = body.get("metadata", {}).get("payout_id")
        if payout_id:
            payout = db.get(Payout, payout_id)
            if payout:
                payout.status = "failed"
    elif event_type == "account.updated":
        creator = db.scalar(select(Creator).where(Creator.stripe_account_id == body.get("id")))
        if creator:
            creator.stripe_onboarding_complete = bool(
                body.get("details_submitted") and body.get("payouts_enabled")
            )
    db.commit()
    return {"received": True}


@internal.get("/campaigns/{campaign_id}")
async def agent_get_campaign(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    return campaign_state(db, must_get(db, Campaign, campaign_id))


@internal.post("/actions/{action}")
async def agent_action(
    action: str,
    data: AgentActionRequest,
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    if action == "discovery":
        campaign = must_get(db, Campaign, data.campaign_id or "")
        return {"deals": [deal.id for deal in discover(db, campaign, providers)]}
    if action == "service-spend":
        campaign = must_get(db, Campaign, data.campaign_id or "")
        if data.arguments.get("status"):
            spend = record_service_spend(
                db,
                campaign,
                str(data.arguments.get("spend_id") or ""),
                str(data.arguments.get("spend_request_id") or ""),
                str(data.arguments["status"]),
            )
            return {
                "id": spend.id,
                "status": spend.status,
                "spend_request_id": spend.spend_request_id,
            }
        spend = request_service_spend(db, campaign, settings)
        return {
            "id": spend.id,
            "status": spend.status,
            "amount_cents": spend.amount_cents,
            "context": spend.context,
            "spend_request_id": spend.spend_request_id,
            "next_action": (
                "Use the installed official/payments/stripe-link-cli Hermes skill. "
                "After Link approval, call this tool again with spend_id, "
                "spend_request_id, and status."
            ),
        }
    if action == "outreach":
        deal = must_get(db, Deal, data.resource_id or "")
        return send_outreach(db, deal, providers, int(data.arguments.get("rate_cents") or 0))
    if action == "email-preflight":
        return browser_email_preflight(db, settings)
    if action == "browser-email-sent":
        task = confirm_browser_email(
            db,
            data.resource_id or "",
            settings,
            sender=str(data.arguments.get("sender") or ""),
            external_id=(
                str(data.arguments["external_id"]) if data.arguments.get("external_id") else None
            ),
            thread_id=(
                str(data.arguments["thread_id"]) if data.arguments.get("thread_id") else None
            ),
        )
        return hermes_task_projection(task)
    if action == "creator-response":
        deal = must_get(db, Deal, data.resource_id or "")
        reply = CreatorResponseCreate(
            body=str(data.arguments.get("body") or ""),
            external_id=str(data.arguments.get("external_id") or ""),
        )
        return process_creator_response(db, deal, reply, providers)
    if action == "qa":
        deliverable = must_get(db, Deliverable, data.resource_id or "")
        run_qa_check(db, deliverable, providers)
        db.commit()
        db.refresh(deliverable)
        return {
            "id": deliverable.id,
            "qa_status": deliverable.qa_status,
            "checks": [
                {"passed": c.passed, "severity": c.severity, "findings": c.findings}
                for c in deliverable.checks
            ],
        }
    if action == "payout":
        payout = request_payout(
            db,
            must_get(db, Payout, data.resource_id or ""),
            providers,
            settings,
        )
        return {"id": payout.id, "status": payout.status, "transfer_id": payout.stripe_transfer_id}
    if action == "strategy":
        campaign = must_get(db, Campaign, data.campaign_id or "")
        strategy = generate_strategy(db, campaign, providers)
        return {
            "id": strategy.id,
            "creator_tier": strategy.creator_tier,
            "target_creators": strategy.target_creators,
            "target_rate_cents": strategy.target_rate_cents,
            "rationale": strategy.rationale,
            "approved": strategy.approved,
        }
    if action == "launch":
        campaign = must_get(db, Campaign, data.campaign_id or "")
        return launch_campaign(db, campaign, providers, settings)
    if action == "funding":
        campaign = must_get(db, Campaign, data.campaign_id or "")
        funding = create_funding(db, campaign, providers)
        return {
            "id": funding.id,
            "status": funding.status,
            "amount_cents": funding.amount_cents,
            "checkout_url": funding.checkout_url,
        }
    if action == "notify":
        text_value = str(data.arguments.get("text") or "").strip()
        if not text_value:
            raise HTTPException(status_code=422, detail="Notification text is required")
        dedupe_key = str(data.arguments.get("dedupe_key") or f"agent-notify:{hash(text_value)}")
        _queue_job(
            db,
            "operator_message",
            dedupe_key,
            {"text": text_value[:3500]},
        )
        db.commit()
        return {"status": "queued", "dedupe_key": dedupe_key}
    raise HTTPException(status_code=404, detail=f"Unknown agent action: {action}")


@internal.post("/learning/{run_id}/begin")
async def agent_learning_begin(run_id: str, db: Session = Depends(get_db)) -> dict:
    campaign = db.scalar(select(Campaign).where(Campaign.run_id == run_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="Run not found")
    learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign.id))
    versions = db.scalars(select(SkillVersion).order_by(SkillVersion.version.desc())).all()
    return {
        "run_id": run_id,
        "campaign_id": campaign.id,
        "learning_mode": campaign.learning_mode,
        "evidence": (learning.evidence if learning else build_learning_dossier(db, campaign)),
        "skill_snapshot": [
            {"skill": v.skill_name, "version": v.version, "hash": v.content_hash} for v in versions
        ],
    }


@internal.post("/learning/{run_id}/commit")
async def agent_learning_commit(
    run_id: str, data: LearningCommit, db: Session = Depends(get_db)
) -> dict:
    campaign = db.scalar(select(Campaign).where(Campaign.run_id == run_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="Run not found")
    learning = db.scalar(select(LearningRun).where(LearningRun.campaign_id == campaign.id))
    if not learning or learning.baseline_status != "applied":
        raise HTTPException(status_code=409, detail="Database learning must commit first")
    if campaign.learning_mode != "database_and_skill_patch":
        raise HTTPException(status_code=409, detail="Skill patching is disabled for this run")
    try:
        skill = commit_learning_change(db, campaign, learning, data)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "accepted": True,
        "run_id": run_id,
        "change_type": data.change_type,
        "skill_name": data.skill_name,
        "skill_version": skill.version if skill else learning.skill_version,
        "patch_status": learning.patch_status,
    }


# ---------------------------------------------------------------------------
# Hermes task endpoints (durable cron orchestration)
# ---------------------------------------------------------------------------


@internal.post("/poll_emails")
async def agent_poll_emails(
    db: Session = Depends(get_db),
    providers: Providers = Depends(providers_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    if settings.email_transport == "browser":
        return {"processed": 0, **browser_email_preflight(db, settings)}
    processed = process_creator_email_updates(db, providers)
    return {"processed": processed}


@internal.post("/tasks/claim")
async def tasks_claim(
    limit: int = Query(default=5, ge=1, le=10),
    db: Session = Depends(get_db),
) -> dict:
    tasks = claim_hermes_tasks(db, limit=limit)
    return {"tasks": [hermes_task_projection(task) for task in tasks], "claimed": len(tasks)}


@internal.post("/tasks/{task_id}/complete")
async def tasks_complete(
    task_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> dict:
    body = body or {}
    task = complete_hermes_task(
        db, task_id, result=body.get("result"), evidence=body.get("evidence")
    )
    return hermes_task_projection(task)


@internal.post("/tasks/{task_id}/fail")
async def tasks_fail(
    task_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> dict:
    body = body or {}
    error = body.get("error", "Unknown error")
    task = fail_hermes_task(db, task_id, error=error, evidence=body.get("evidence"))
    return hermes_task_projection(task)


@internal.get("/tasks")
async def tasks_list(
    status: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = select(HermesTask).order_by(HermesTask.created_at.desc())
    if status:
        query = query.where(HermesTask.status == status)
    if campaign_id:
        query = query.where(HermesTask.campaign_id == campaign_id)
    tasks = db.scalars(query.limit(limit)).all()
    return {"tasks": [hermes_task_projection(task) for task in tasks], "total": len(tasks)}


@internal.get("/tasks/preflight")
async def tasks_preflight(db: Session = Depends(get_db)) -> dict:
    """Lightweight check for the Hermes cron: pending count and system health."""
    pending = (
        db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "pending")) or 0
    )
    claimed = (
        db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "claimed")) or 0
    )
    failed = db.scalar(select(func.count(HermesTask.id)).where(HermesTask.status == "failed")) or 0
    return {
        "pending": pending,
        "claimed": claimed,
        "failed": failed,
        "should_claim": pending > 0,
    }


app.include_router(v1)
app.include_router(internal)
