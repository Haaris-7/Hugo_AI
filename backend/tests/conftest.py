import json
import os
from pathlib import Path

TEST_DB = Path("/tmp/argo-ai-test.db")
TEST_DB.unlink(missing_ok=True)

os.environ.update({
    "ARGO_ENV": "test",
    "ARGO_DATABASE_URL": f"sqlite:///{TEST_DB}",
    "ARGO_INLINE_JOBS": "true",
    "ARGO_API_TOKEN": "test-api-token",
    "ARGO_AGENT_TOKEN": "test-agent-token",
    "ARGO_HERMES_API_KEY": "test-hermes-token",
    "ARGO_NVIDIA_API_KEY": "test-nvidia-token",
    "ARGO_STRIPE_SECRET_KEY": "sk_test_local",
    "ARGO_STRIPE_WEBHOOK_SECRET": "whsec_test_local",
    "ARGO_GMAIL_ACCESS_TOKEN": "test-gmail-token",
    "ARGO_GMAIL_SENDER": "operator@example.test",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
from argo.db import Base, engine  # noqa: E402
from argo.main import app  # noqa: E402
from argo.providers import (  # noqa: E402
    FundingResult,
    HermesProvider,
    MailProvider,
    MailSendResult,
    PaymentProvider,
    TransferResult,
    VisionProvider,
)
from argo.schemas import (  # noqa: E402
    DiscoveryCandidate,
    HermesLearning,
    HermesStrategy,
    QAResult,
)


@pytest.fixture(autouse=True)
def isolated_provider_contracts(monkeypatch):
    """Keep tests offline with explicit fakes; production providers remain live-only."""
    monkeypatch.setattr(HermesProvider, "healthy", lambda self: True)
    monkeypatch.setattr(HermesProvider, "probe", lambda self: {"ok": True, "status_code": 200})
    monkeypatch.setattr(
        HermesProvider,
        "playbook",
        lambda self, platform: {
            "platform": platform,
            "signals": [{"signal": "Favor concise native creator video."}],
            "sources": [{"url": "https://example.test/platform"}],
            "confidence": 0.8,
        },
    )

    def strategy(self, context):
        affiliate = context.get("payout_model") == "affiliate"
        rate = min(context["per_creator_cap_cents"], 2_500 if affiliate else 15_000)
        return HermesStrategy(
            creator_tier="micro",
            target_rate_cents=rate,
            rationale="Test contract uses budget-safe micro creators.",
            projected_cost_per_result=0.025,
            compensation_components=[{
                "kind": "affiliate" if affiliate else "base",
                "rate_cents": rate,
            }],
        )

    monkeypatch.setattr(HermesProvider, "strategy", strategy)
    monkeypatch.setattr(
        HermesProvider,
        "learning",
        lambda self, dossier: HermesLearning(
            summary="Stored evidence-backed learning.",
            heuristic="Prefer reliable creators.",
            skill_name="argo-strategy-engine",
            evidence_ids=list(dossier.get("evidence_ids", [])),
            governance={"risk": "low", "generator": "nvidia/skill-card-generator"},
        ),
    )
    monkeypatch.setattr(
        HermesProvider,
        "outreach",
        lambda self, context: (
            f"Deal for {context['campaign_name']}: ${context['rate_cents'] / 100:.2f}. "
            "Reply ACCEPT, counter with a rate, or decline."
        ),
    )
    monkeypatch.setattr(
        HermesProvider,
        "discover_creators",
        lambda self, niche, platform, limit, exclude_handles: [
            DiscoveryCandidate(
                handle=f"verified.creator.{index}",
                email=f"creator{index}@example.test",
                followers=25_000 + index * 1_000,
                engagement_rate=4.5,
                fake_follower_percent=3.0,
                niche_match=90,
                audience_quality=88,
                brand_fit=86,
                profile_data={"provider": "test-contract", "niche": niche},
            )
            for index in range(limit)
            if f"verified.creator.{index}" not in exclude_handles
        ],
    )

    def verify(self, caption, media_url, **kwargs):
        findings = []
        if "#ad" not in caption.lower() and "sponsored" not in caption.lower():
            findings.append({"code": "missing_disclosure", "message": "Disclosure missing"})
        if "argo.link/" not in caption.lower():
            findings.append({"code": "missing_tracking_link", "message": "Tracking link missing"})
        if media_url and "wrong-product" in media_url:
            findings.append({"code": "wrong_product", "message": "Wrong product"})
        return QAResult(
            passed=not findings,
            severity="major" if findings else "none",
            findings=findings,
            model="test-vision-contract",
        )

    monkeypatch.setattr(VisionProvider, "verify", verify)
    monkeypatch.setattr(VisionProvider, "probe", lambda self: {"ok": True, "model": "test-vision-contract"})
    monkeypatch.setattr(
        PaymentProvider,
        "create_funding_session",
        lambda self, campaign_id, amount_cents: FundingResult(
            external_id=f"cs_test_{campaign_id}",
            url=f"https://checkout.example.test/{campaign_id}",
            payment_intent_id=f"pi_test_{campaign_id}",
        ),
    )
    monkeypatch.setattr(
        PaymentProvider,
        "create_onboarding_link",
        lambda self, creator_id, email: (
            f"acct_test_{creator_id}",
            f"https://connect.example.test/{creator_id}",
            True,
        ),
    )
    monkeypatch.setattr(
        PaymentProvider,
        "resolve_source_charge",
        lambda self, payment_intent_id: payment_intent_id.replace("pi_", "ch_", 1),
    )
    monkeypatch.setattr(
        PaymentProvider,
        "transfer",
        lambda self, **kwargs: TransferResult(external_id=f"tr_test_{kwargs['payout_id']}"),
    )
    monkeypatch.setattr(
        PaymentProvider,
        "probe",
        lambda self: {"ok": True, "account_id": "acct_test_platform"},
    )
    monkeypatch.setattr(
        MailProvider,
        "send",
        lambda self, to, subject, body, idempotency_key, thread_id=None: MailSendResult(
            message_id=f"msg_{idempotency_key}",
            thread_id=thread_id or f"thread_{idempotency_key}",
        ),
    )
    monkeypatch.setattr(MailProvider, "thread_messages", lambda self, thread_ids: [])
    monkeypatch.setattr(
        MailProvider,
        "probe",
        lambda self: {"ok": True, "email": "operator@example.test", "messages_total": 1},
    )

    import stripe

    monkeypatch.setattr(
        stripe.Webhook,
        "construct_event",
        lambda payload, signature, secret: json.loads(payload),
    )

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api_headers():
    return {"Authorization": "Bearer test-api-token"}


@pytest.fixture
def agent_headers():
    return {"Authorization": "Bearer test-agent-token"}
