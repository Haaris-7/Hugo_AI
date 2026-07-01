from types import SimpleNamespace

import pytest
from hugo.db import SessionLocal
from hugo.models import Brand, Campaign, CampaignStatus, LearningRun
from hugo.services import process_learning


class FailingHermes:
    def __init__(self) -> None:
        self.calls = 0

    def learning(self, dossier):
        self.calls += 1
        raise RuntimeError("simulated Hermes patch failure")


def _closed_campaign(db, learning_mode: str) -> Campaign:
    brand = Brand(name="Learning test", niche="fitness")
    db.add(brand)
    db.flush()
    campaign = Campaign(
        brand_id=brand.id,
        name="Closed run",
        goal="Test isolated learning modes",
        budget_cents=10_000,
        per_creator_cap_cents=5_000,
        learning_mode=learning_mode,
        status=CampaignStatus.COMPLETED.value,
        actual_views=1_000,
    )
    db.add(campaign)
    db.commit()
    return campaign


def test_database_learning_is_default_and_does_not_call_hermes():
    hermes = FailingHermes()
    with SessionLocal() as db:
        campaign = _closed_campaign(db, "database")
        learning = process_learning(db, campaign.id, SimpleNamespace(hermes=hermes))

        assert learning.baseline_status == "applied"
        assert learning.patch_status == "disabled"
        assert learning.skill_version is None
        assert hermes.calls == 0
        assert campaign.status == CampaignStatus.COMPLETED.value


def test_optional_patch_failure_cannot_undo_database_learning_or_completion():
    hermes = FailingHermes()
    with SessionLocal() as db:
        campaign = _closed_campaign(db, "database_and_skill_patch")
        learning = process_learning(db, campaign.id, SimpleNamespace(hermes=hermes))

        assert learning.baseline_status == "applied"
        assert learning.patch_status == "failed"
        assert "simulated Hermes patch failure" in learning.patch_error
        assert hermes.calls == 1
        assert campaign.status == CampaignStatus.COMPLETED.value


@pytest.mark.anyio
async def test_hermes_commit_supports_evidence_bounded_no_op(client, agent_headers):
    with SessionLocal() as db:
        campaign = _closed_campaign(db, "database_and_skill_patch")
        evidence_id = f"campaign:{campaign.id}"
        db.add(
            LearningRun(
                campaign_id=campaign.id,
                run_id=campaign.run_id,
                status="applied",
                baseline_status="applied",
                patch_status="hermes_running",
                evidence={"evidence_ids": [evidence_id]},
            )
        )
        db.commit()
        run_id = campaign.run_id

    response = await client.post(
        f"/internal/agent/learning/{run_id}/commit",
        json={
            "summary": "The evidence is insufficient for a generalized skill change.",
            "change_type": "no_op",
            "no_op_reason": "Only one terminal run exists and it has no creator outcomes.",
            "skill_name": "hugo-strategy-engine",
            "evidence_ids": [evidence_id],
            "governance": {"generator": "nvidia/skill-card-generator"},
        },
        headers=agent_headers,
    )
    assert response.status_code == 200
    assert response.json()["patch_status"] == "no_op"
