import pytest


async def _post(client, path, body, headers):
    response = await client.post(path, json=body, headers=headers)
    assert response.status_code < 300, response.text
    return response.json()


@pytest.mark.anyio
async def test_complete_campaign_runs_automatic_learning(client, api_headers, agent_headers):
    brand = await _post(
        client,
        "/v1/brands",
        {"name": "Fitness Brand", "niche": "fitness", "website": "https://example.com"},
        api_headers,
    )
    campaign = await _post(
        client,
        "/v1/campaigns",
        {
            "brand_id": brand["id"],
            "name": "Summer challenge",
            "goal": "Drive qualified TikTok views",
            "budget_cents": 60_000,
            "per_creator_cap_cents": 15_000,
            "payout_model": "flat",
            "operation_mode": "strategy_creators",
        },
        api_headers,
    )
    strategy = await _post(client, f"/v1/campaigns/{campaign['id']}/strategy", {}, api_headers)
    await _post(
        client,
        "/v1/approvals",
        {
            "campaign_id": campaign["id"],
            "resource_type": "strategy",
            "resource_id": strategy["id"],
            "decision": "approved",
        },
        api_headers,
    )
    funding = await _post(
        client, f"/v1/campaigns/{campaign['id']}/funding-session", {}, api_headers
    )

    event = {
        "id": "evt_funding_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": funding["checkout_session_id"],
                "payment_intent": "pi_funding_1",
                "metadata": {"campaign_id": campaign["id"]},
            }
        },
    }
    webhook = await client.post(
        "/v1/webhooks/stripe",
        json=event,
        headers={"Stripe-Signature": "test-signature"},
    )
    assert webhook.status_code == 200

    launch = await _post(client, f"/v1/campaigns/{campaign['id']}/launch", {}, api_headers)
    assert launch["status"] == "active"
    state = (await client.get(f"/v1/campaigns/{campaign['id']}/state", headers=api_headers)).json()
    deal = state["deals"][0]
    await _post(
        client,
        "/v1/approvals",
        {
            "campaign_id": campaign["id"],
            "resource_type": "deal",
            "resource_id": deal["id"],
            "decision": "approved",
        },
        api_headers,
    )
    _outreach = await _post(
        client,
        f"/v1/deals/{deal['id']}/outreach",
        {"proposed_rate_cents": 15_000},
        api_headers,
    )
    response = await _post(
        client,
        f"/v1/deals/{deal['id']}/response",
        {"body": "ACCEPT", "external_id": "gmail_reply_1"},
        api_headers,
    )
    assert response["intent"] == "accept"
    assert response["agreed_rate_cents"] == 15_000
    assert response["status"] == "contracted"

    connect = await _post(
        client,
        f"/v1/creators/{deal['creator_id']}/connect-onboarding-link",
        {},
        api_headers,
    )
    assert connect["account_id"].startswith("acct_test_")

    failed_qa = await _post(
        client,
        f"/v1/deals/{deal['id']}/deliverables",
        {
            "caption": "Try the product",
            "media_url": "https://media.test/good.jpg",
            "stage": "draft",
        },
        api_headers,
    )
    assert failed_qa["qa_status"] == "revision_required"
    assert {f["code"] for f in failed_qa["checks"][0]["findings"]} == {
        "missing_disclosure",
        "missing_tracking_link",
    }

    passed_qa = await _post(
        client,
        f"/v1/deals/{deal['id']}/deliverables",
        {
            "caption": "Sponsored #ad — learn more at hugo.link/summer",
            "media_url": "https://media.test/good.jpg",
            "post_url": "https://tiktok.com/@creator/video/1",
            "stage": "draft",
        },
        api_headers,
    )
    assert passed_qa["qa_status"] == "verified"
    final_qa = await _post(
        client,
        f"/v1/deals/{deal['id']}/deliverables",
        {
            "caption": "Sponsored #ad — learn more at hugo.link/summer",
            "media_url": "https://media.test/good.jpg",
            "post_url": "https://tiktok.com/@creator/video/1",
            "stage": "final",
        },
        api_headers,
    )
    assert final_qa["qa_status"] == "verified"

    state = (await client.get(f"/v1/campaigns/{campaign['id']}/state", headers=api_headers)).json()
    assert state["experiments"][0]["variant"] == "primary"
    assert len(state["deals"][0]["messages"]) == 3
    payout = state["payouts"][0]
    transfer = await _post(
        client,
        "/internal/agent/actions/payout",
        {"resource_id": payout["id"]},
        agent_headers,
    )
    assert transfer["status"] == "transferred"

    metrics = await _post(
        client,
        f"/v1/campaigns/{campaign['id']}/metrics",
        {"views": 25_000, "engagements": 2_100},
        api_headers,
    )
    assert metrics["status"] == "completed"
    learning = await client.get(f"/v1/campaigns/{campaign['id']}/learning", headers=api_headers)
    assert learning.status_code == 200
    assert learning.json()["status"] == "applied"
    assert learning.json()["baseline_status"] == "applied"
    assert learning.json()["patch_status"] == "disabled"
    assert learning.json()["skill_version"] is None
    assert learning.json()["database_updates"]["strategy_prior"]["after"]["observations"] == 1

    next_campaign = await _post(
        client,
        "/v1/campaigns",
        {
            "brand_id": brand["id"],
            "name": "Learned campaign",
            "goal": "Apply the previous campaign learning",
            "budget_cents": 30_000,
            "per_creator_cap_cents": 15_000,
        },
        api_headers,
    )
    next_strategy = await _post(
        client, f"/v1/campaigns/{next_campaign['id']}/strategy", {}, api_headers
    )
    assert next_strategy["skill_version"] == 1
    assert "Database prior: 1 observed" in next_strategy["rationale"]

@pytest.mark.anyio
async def test_webhook_is_idempotent(client, api_headers):
    brand = await _post(client, "/v1/brands", {"name": "B", "niche": "tech"}, api_headers)
    campaign = await _post(
        client,
        "/v1/campaigns",
        {
            "brand_id": brand["id"],
            "name": "C",
            "goal": "Generate awareness",
            "budget_cents": 1_000,
            "per_creator_cap_cents": 1_000,
            "operation_mode": "strategy_creators",
        },
        api_headers,
    )
    strategy = await _post(client, f"/v1/campaigns/{campaign['id']}/strategy", {}, api_headers)
    await _post(
        client,
        "/v1/approvals",
        {
            "campaign_id": campaign["id"],
            "resource_type": "strategy",
            "resource_id": strategy["id"],
            "decision": "approved",
        },
        api_headers,
    )
    funding = await _post(
        client, f"/v1/campaigns/{campaign['id']}/funding-session", {}, api_headers
    )
    event = {
        "id": "evt_same",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": funding["checkout_session_id"],
                "payment_intent": "pi_same",
                "metadata": {"campaign_id": campaign["id"]},
            }
        },
    }
    headers = {"Stripe-Signature": "test-signature"}
    assert (await client.post("/v1/webhooks/stripe", json=event, headers=headers)).json()[
        "received"
    ]
    duplicate = (await client.post("/v1/webhooks/stripe", json=event, headers=headers)).json()
    assert duplicate == {"received": True, "duplicate": True}
