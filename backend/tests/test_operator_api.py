import pytest
from argo.db import SessionLocal
from argo.models import Campaign, CampaignStatus, CampaignStrategy


@pytest.mark.anyio
async def test_learning_defaults_to_database_and_patch_is_explicit(client, api_headers):
    brand_response = await client.post(
        "/v1/brands", json={"name": "Baseline", "niche": "fitness"}, headers=api_headers
    )
    brand_id = brand_response.json()["id"]
    default_response = await client.post(
        "/v1/campaigns",
        json={
            "brand_id": brand_id,
            "name": "Default learning",
            "goal": "Verify database learning is default",
            "budget_cents": 10_000,
            "per_creator_cap_cents": 5_000,
        },
        headers=api_headers,
    )
    assert default_response.status_code == 201
    assert default_response.json()["learning_mode"] == "database"

    patch_response = await client.post(
        "/v1/campaigns",
        json={
            "brand_id": brand_id,
            "name": "Patch learning",
            "goal": "Exercise the optional patch extension",
            "budget_cents": 10_000,
            "per_creator_cap_cents": 5_000,
            "learning_mode": "database_and_skill_patch",
        },
        headers=api_headers,
    )
    campaign_id = patch_response.json()["id"]
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        campaign.status = CampaignStatus.MEASURING.value
        db.add(
            CampaignStrategy(
                campaign_id=campaign.id,
                creator_tier="micro",
                target_creators=2,
                target_rate_cents=5_000,
                rationale="Test strategy",
                projected_cost_per_result=0.03,
                skill_version=1,
                approved=True,
            )
        )
        db.commit()
    metrics = await client.post(
        f"/v1/campaigns/{campaign_id}/metrics",
        json={"views": 5_000, "engagements": 400},
        headers=api_headers,
    )
    assert metrics.status_code == 200
    assert metrics.json()["status"] == "completed"
    learning = (
        await client.get(f"/v1/campaigns/{campaign_id}/learning", headers=api_headers)
    ).json()
    assert learning["baseline_status"] == "applied"
    assert learning["patch_status"] == "applied"
    assert learning["skill_version"] == 2
    versions = (await client.get("/v1/hermes/skills/versions", headers=api_headers)).json()
    assert versions[0]["governance"]["generator"] == "nvidia/skill-card-generator"

    next_campaign = (
        await client.post(
            "/v1/campaigns",
            json={
                "brand_id": brand_id,
                "name": "Uses learned skill",
                "goal": "Prove the next run loads the learned heuristic",
                "budget_cents": 10_000,
                "per_creator_cap_cents": 5_000,
                "operation_mode": "strategy_creators",
            },
            headers=api_headers,
        )
    ).json()
    next_strategy = (
        await client.post(
            f"/v1/campaigns/{next_campaign['id']}/strategy",
            json={},
            headers=api_headers,
        )
    ).json()
    assert next_strategy["skill_version"] == 2
    assert "Applied learned skill v2" in next_strategy["rationale"]


@pytest.mark.anyio
async def test_terminal_cancellation_runs_database_learning(client, api_headers):
    brand = (
        await client.post(
            "/v1/brands", json={"name": "Cancellation", "niche": "tech"}, headers=api_headers
        )
    ).json()
    campaign = (
        await client.post(
            "/v1/campaigns",
            json={
                "brand_id": brand["id"],
                "name": "Cancelled run",
                "goal": "Learn from a rejected strategy",
                "budget_cents": 10_000,
                "per_creator_cap_cents": 5_000,
            },
            headers=api_headers,
        )
    ).json()
    strategy = (
        await client.post(f"/v1/campaigns/{campaign['id']}/strategy", json={}, headers=api_headers)
    ).json()
    response = await client.post(
        "/v1/approvals",
        json={
            "campaign_id": campaign["id"],
            "resource_type": "strategy",
            "resource_id": strategy["id"],
            "decision": "rejected",
            "reason": "Budget priorities changed",
        },
        headers=api_headers,
    )
    assert response.status_code == 201
    state = (await client.get(f"/v1/campaigns/{campaign['id']}/state", headers=api_headers)).json()
    assert state["campaign"]["status"] == "cancelled"
    assert state["learning"]["baseline_status"] == "applied"
    assert state["learning"]["patch_status"] == "disabled"
    assert any(event["type"] == "campaign_run_closed" for event in state["events"])


@pytest.mark.anyio
async def test_removed_demo_routes_are_not_exposed(client, api_headers):
    assert (await client.post("/v1/demo/reset", json={}, headers=api_headers)).status_code == 404
    assert (
        await client.post("/v1/demo/acceptance-run", json={}, headers=api_headers)
    ).status_code == 404
    system = (await client.get("/v1/system/status", headers=api_headers)).json()
    assert system["demo_mode"] is False


@pytest.mark.anyio
async def test_playbook_returns_latest_platform_intelligence(client, api_headers):
    brand = (
        await client.post(
            "/v1/brands",
            json={"name": "Intelligence", "niche": "fitness"},
            headers=api_headers,
        )
    ).json()
    campaign = (
        await client.post(
            "/v1/campaigns",
            json={
                "brand_id": brand["id"],
                "name": "Playbook campaign",
                "goal": "Expose current algorithm signals",
                "budget_cents": 20_000,
                "per_creator_cap_cents": 10_000,
            },
            headers=api_headers,
        )
    ).json()
    strategy = await client.post(
        f"/v1/campaigns/{campaign['id']}/strategy",
        json={},
        headers=api_headers,
    )
    assert strategy.status_code == 200

    response = await client.get("/v1/playbook?platform=tiktok", headers=api_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["platform"] == "tiktok"
    assert payload["items"][0]["signals"]
    assert payload["items"][0]["sources"]


@pytest.mark.anyio
async def test_affiliate_strategy_uses_per_conversion_rate_and_creator_caps(client, api_headers):
    brand = (
        await client.post(
            "/v1/brands",
            json={"name": "Affiliate", "niche": "fitness"},
            headers=api_headers,
        )
    ).json()
    campaign = (
        await client.post(
            "/v1/campaigns",
            json={
                "brand_id": brand["id"],
                "name": "CPA campaign",
                "goal": "Pay for tracked conversions",
                "budget_cents": 60_000,
                "per_creator_cap_cents": 15_000,
                "payout_model": "affiliate",
            },
            headers=api_headers,
        )
    ).json()

    response = await client.post(
        f"/v1/campaigns/{campaign['id']}/strategy",
        json={},
        headers=api_headers,
    )
    assert response.status_code == 200
    strategy = response.json()
    assert strategy["target_rate_cents"] == 2_500
    assert strategy["target_creators"] == 4


@pytest.mark.anyio
async def test_public_creator_portal_is_not_exposed(client, api_headers):
    missing = await client.get("/public/deals/not-a-real-token")
    assert missing.status_code == 404
