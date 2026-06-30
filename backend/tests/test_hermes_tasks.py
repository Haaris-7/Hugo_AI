"""Tests for the Hermes durable task / lease system and agent action endpoints."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from argo.config import get_settings
from argo.db import SessionLocal
from argo.models import (
    Brand,
    Campaign,
    CampaignStatus,
    Creator,
    CreatorReputation,
    Deal,
    DealStatus,
    Deliverable,
    FundingPayment,
    HermesTask,
    utcnow,
)
from argo.providers import build_providers
from argo.schemas import CampaignCreate
from argo.services import (
    claim_hermes_tasks,
    complete_hermes_task,
    create_campaign,
    enqueue_hermes_task,
    fail_hermes_task,
    mark_funded,
    retry_hermes_task,
)


def _active_campaign(db) -> Campaign:
    brand = Brand(name="Task brand", niche="tech")
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name="Task campaign",
        goal="Test hermes tasks",
        budget_cents=50_000,
        per_creator_cap_cents=15_000,
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(campaign)
    db.commit()
    return campaign


def test_enqueue_and_claim_lifecycle():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(
            db, campaign.id, "strategy", dedupe_key=f"strategy:{campaign.id}"
        )
        db.commit()
        assert task.status == "pending"
        assert task.task_type == "strategy"

        claimed = claim_hermes_tasks(db, limit=5)
        assert len(claimed) == 1
        assert claimed[0].id == task.id
        assert claimed[0].status == "claimed"
        assert claimed[0].lease_expires_at is not None
        assert claimed[0].attempt == 1


def test_dedupe_prevents_duplicate_pending_tasks():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        t1 = enqueue_hermes_task(db, campaign.id, "discovery", dedupe_key="disc:1")
        db.commit()
        t2 = enqueue_hermes_task(db, campaign.id, "discovery", dedupe_key="disc:1")
        db.commit()
        assert t1.id == t2.id


def test_complete_task():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "outreach")
        db.commit()
        claim_hermes_tasks(db, limit=1)

        completed = complete_hermes_task(
            db, task.id, result={"email_sent": True}, evidence={"provider": "gmail"}
        )
        assert completed.status == "completed"
        assert completed.result == {"email_sent": True}
        assert completed.evidence["provider"] == "gmail"


def test_fail_task():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "qa")
        db.commit()
        claim_hermes_tasks(db, limit=1)

        failed = fail_hermes_task(db, task.id, "NVIDIA API timeout")
        assert failed.status == "failed"
        assert "timeout" in failed.error


def test_retry_failed_task():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "outreach")
        db.commit()
        claim_hermes_tasks(db, limit=1)
        fail_hermes_task(db, task.id, "Temporary failure")

        retried = retry_hermes_task(db, task.id)
        assert retried.status == "pending"
        assert retried.error is None


@pytest.mark.anyio
async def test_hermes_task_retry_api(client, api_headers):
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "strategy")
        db.commit()
        claim_hermes_tasks(db, limit=1)
        fail_hermes_task(db, task.id, "Probe failure")
        task_id = task.id

    resp = await client.post(f"/v1/hermes/tasks/{task_id}/retry", headers=api_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_expired_lease_reverts_to_pending():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "payout")
        db.commit()
        claim_hermes_tasks(db, limit=1)

        db.refresh(task)
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

        reclaimed = claim_hermes_tasks(db, limit=5)
        assert len(reclaimed) == 1
        assert reclaimed[0].id == task.id
        assert reclaimed[0].attempt == 3


def test_cannot_complete_already_completed_task():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        task = enqueue_hermes_task(db, campaign.id, "learning")
        db.commit()
        claim_hermes_tasks(db, limit=1)
        complete_hermes_task(db, task.id)

        with pytest.raises(HTTPException) as exc:
            complete_hermes_task(db, task.id)
        assert exc.value.status_code == 409


def test_claim_respects_limit():
    with SessionLocal() as db:
        campaign = _active_campaign(db)
        for i in range(7):
            enqueue_hermes_task(db, campaign.id, f"task_{i}")
        db.commit()

        claimed = claim_hermes_tasks(db, limit=3)
        assert len(claimed) == 3

        remaining = db.scalars(
            select(HermesTask).where(HermesTask.status == "pending")
        ).all()
        assert len(remaining) == 4


@pytest.mark.anyio
async def test_task_api_endpoints(client, agent_headers):
    api = client
    h = {"Authorization": "Bearer test-api-token"}

    brand = await api.post("/v1/brands", json={"name": "API brand", "niche": "tech"}, headers=h)
    brand_id = brand.json()["id"]
    campaign = await api.post(
        "/v1/campaigns",
        json={
            "brand_id": brand_id,
            "name": "API campaign",
            "goal": "Test task API",
            "budget_cents": 50_000,
            "per_creator_cap_cents": 15_000,
        },
        headers=h,
    )
    campaign_id = campaign.json()["id"]

    preflight = await api.get("/internal/agent/tasks/preflight", headers=agent_headers)
    assert preflight.status_code == 200
    assert preflight.json()["pending"] == 1
    assert preflight.json()["should_claim"] is True

    claim_resp = await api.post(
        "/internal/agent/tasks/claim?limit=5", headers=agent_headers
    )
    assert claim_resp.status_code == 200
    tasks = claim_resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "strategy"
    task_id = tasks[0]["id"]

    complete_resp = await api.post(
        f"/internal/agent/tasks/{task_id}/complete",
        json={"result": {"ok": True}},
        headers=agent_headers,
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["status"] == "completed"

    list_resp = await api.get(
        f"/internal/agent/tasks?campaign_id={campaign_id}", headers=agent_headers
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1


@pytest.mark.anyio
async def test_agent_strategy_action(client, agent_headers):
    api = client
    h = {"Authorization": "Bearer test-api-token"}
    brand = await api.post("/v1/brands", json={"name": "Strat brand", "niche": "tech"}, headers=h)
    campaign = await api.post(
        "/v1/campaigns",
        json={
            "brand_id": brand.json()["id"],
            "name": "Strat campaign",
            "goal": "Test strategy action",
            "budget_cents": 100_000,
            "per_creator_cap_cents": 20_000,
        },
        headers=h,
    )
    campaign_id = campaign.json()["id"]

    resp = await api.post(
        "/internal/agent/actions/strategy",
        json={"campaign_id": campaign_id, "arguments": {}},
        headers=agent_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["creator_tier"] in ("nano", "micro", "mid")
    assert data["target_rate_cents"] > 0
    assert data["rationale"]


@pytest.mark.anyio
async def test_agent_funding_and_launch_actions(client, agent_headers):
    api = client
    h = {"Authorization": "Bearer test-api-token"}
    brand = await api.post(
        "/v1/brands",
        json={"name": "Launch brand", "niche": "beauty"},
        headers=h,
    )
    campaign = await api.post(
        "/v1/campaigns",
        json={
            "brand_id": brand.json()["id"],
            "name": "Launch campaign",
            "goal": "Test funding and launch",
            "budget_cents": 80_000,
            "per_creator_cap_cents": 20_000,
            "operation_mode": "full_autonomy",
        },
        headers=h,
    )
    campaign_id = campaign.json()["id"]

    await api.post(
        "/internal/agent/actions/strategy",
        json={"campaign_id": campaign_id, "arguments": {}},
        headers=agent_headers,
    )

    fund_resp = await api.post(
        "/internal/agent/actions/funding",
        json={"campaign_id": campaign_id, "arguments": {}},
        headers=agent_headers,
    )
    assert fund_resp.status_code == 200
    assert fund_resp.json()["checkout_url"]

    import json as _json
    webhook_payload = _json.dumps({
        "id": f"evt_test_{campaign_id}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"campaign_id": campaign_id},
                "payment_intent": f"pi_test_{campaign_id}",
            }
        },
    })
    await api.post(
        "/v1/webhooks/stripe",
        content=webhook_payload,
        headers={"stripe-signature": "t=1,v1=sig"},
    )

    launch_resp = await api.post(
        "/internal/agent/actions/launch",
        json={"campaign_id": campaign_id, "arguments": {}},
        headers=agent_headers,
    )
    assert launch_resp.status_code == 200
    assert launch_resp.json()["status"] == "active"


@pytest.mark.anyio
async def test_agent_poll_emails(client, agent_headers):
    resp = await client.post("/internal/agent/poll_emails", headers=agent_headers)
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0


def test_campaign_creation_enqueues_strategy_task():
    with SessionLocal() as db:
        brand = Brand(name="Enqueue brand", niche="tech")
        db.add(brand)
        db.flush()
        campaign = create_campaign(
            db,
            CampaignCreate(
                brand_id=brand.id,
                name="Enqueue campaign",
                goal="Verify strategy task enqueue",
                budget_cents=50_000,
                per_creator_cap_cents=15_000,
            ),
        )
        task = db.scalar(
            select(HermesTask).where(
                HermesTask.campaign_id == campaign.id,
                HermesTask.task_type == "strategy",
            )
        )
        assert task is not None
        assert task.dedupe_key == f"strategy:{campaign.id}"
        assert task.status == "pending"


def test_mark_funded_enqueues_launch_task():
    with SessionLocal() as db:
        brand = Brand(name="Launch brand", niche="tech")
        db.add(brand)
        db.flush()
        campaign = Campaign(
            brand_id=brand.id,
            name="Launch campaign",
            goal="Verify launch task enqueue",
            budget_cents=50_000,
            per_creator_cap_cents=15_000,
            status=CampaignStatus.AWAITING_FUNDING.value,
        )
        db.add(campaign)
        db.flush()
        db.add(
            FundingPayment(
                campaign_id=campaign.id,
                amount_cents=campaign.budget_cents,
                status="pending",
                payment_intent_id="pi_launch_test",
            )
        )
        db.commit()

        providers = build_providers(get_settings())
        mark_funded(db, campaign.id, "pi_launch_test", providers)
        task = db.scalar(
            select(HermesTask).where(
                HermesTask.campaign_id == campaign.id,
                HermesTask.task_type == "launch",
            )
        )
        assert task is not None
        assert task.dedupe_key == f"launch:{campaign.id}"


@pytest.mark.anyio
async def test_webhook_payment_failed_marks_funding(client, api_headers):
    brand = await client.post(
        "/v1/brands", json={"name": "Fail brand", "niche": "tech"}, headers=api_headers
    )
    campaign = await client.post(
        "/v1/campaigns",
        json={
            "brand_id": brand.json()["id"],
            "name": "Fail campaign",
            "goal": "Test failed funding webhook",
            "budget_cents": 10_000,
            "per_creator_cap_cents": 5_000,
            "operation_mode": "strategy_creators",
        },
        headers=api_headers,
    )
    campaign_id = campaign.json()["id"]
    strategy = await client.post(
        f"/v1/campaigns/{campaign_id}/strategy", json={}, headers=api_headers
    )
    await client.post(
        "/v1/approvals",
        json={
            "campaign_id": campaign_id,
            "resource_type": "strategy",
            "resource_id": strategy.json()["id"],
            "decision": "approved",
        },
        headers=api_headers,
    )
    funding = await client.post(
        f"/v1/campaigns/{campaign_id}/funding-session", json={}, headers=api_headers
    )
    assert funding.status_code == 200

    event = {
        "id": "evt_payment_failed_1",
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": "pi_failed_test",
                "metadata": {"campaign_id": campaign_id},
            }
        },
    }
    resp = await client.post(
        "/v1/webhooks/stripe",
        json=event,
        headers={"Stripe-Signature": "test-signature"},
    )
    assert resp.status_code == 200

    with SessionLocal() as db:
        funding_row = db.scalar(
            select(FundingPayment).where(FundingPayment.campaign_id == campaign_id)
        )
        assert funding_row.status == "failed"


@pytest.mark.anyio
async def test_qa_action_runs_vision_qa(client, agent_headers, api_headers):
    brand = await client.post(
        "/v1/brands", json={"name": "QA brand", "niche": "tech"}, headers=api_headers
    )
    campaign = await client.post(
        "/v1/campaigns",
        json={
            "brand_id": brand.json()["id"],
            "name": "QA campaign",
            "goal": "Test QA action",
            "budget_cents": 50_000,
            "per_creator_cap_cents": 15_000,
        },
        headers=api_headers,
    )
    campaign_id = campaign.json()["id"]

    with SessionLocal() as db:
        creator = Creator(handle="qa.creator")
        db.add(creator)
        db.flush()
        db.add(CreatorReputation(creator_id=creator.id))
        deal = Deal(
            campaign_id=campaign_id,
            creator_id=creator.id,
            status=DealStatus.DRAFT_QA.value,
        )
        db.add(deal)
        db.flush()
        deliverable = Deliverable(
            deal_id=deal.id,
            caption="Try the product",
            media_url="https://media.test/good.jpg",
            stage="draft",
            qa_status="pending",
        )
        db.add(deliverable)
        db.commit()
        deliverable_id = deliverable.id

    resp = await client.post(
        "/internal/agent/actions/qa",
        json={"resource_id": deliverable_id},
        headers=agent_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["qa_status"] == "revision_required"
    assert len(data["checks"]) == 1
    assert data["checks"][0]["passed"] is False
    assert {f["code"] for f in data["checks"][0]["findings"]} == {
        "missing_disclosure",
        "missing_tracking_link",
    }
