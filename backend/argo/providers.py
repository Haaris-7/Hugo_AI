from __future__ import annotations

import json
import logging
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .config import Settings
from .schemas import (
    DiscoveryCandidate,
    HermesLearning,
    HermesNegotiation,
    HermesStrategy,
    QAResult,
)

logger = logging.getLogger("argo.providers")


def extract_json(text: str) -> str:
    """Best-effort extraction of a JSON object from a model response.

    Live Nemotron/NIM responses sometimes wrap JSON in prose or markdown fences;
    this isolates the outermost ``{...}`` so structured parsing stays robust.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1]
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


@dataclass
class FundingResult:
    external_id: str
    url: str
    payment_intent_id: str | None = None


@dataclass
class TransferResult:
    external_id: str


@dataclass
class MetricResult:
    views: int
    engagements: int
    source_url: str
    evidence: dict[str, Any]


@dataclass
class MailSendResult:
    message_id: str
    thread_id: str


@dataclass
class MailReply:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    body: str


class HermesProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def healthy(self) -> bool:
        try:
            return self.probe()["ok"]
        except Exception:
            return False

    def probe(self) -> dict[str, Any]:
        base = self.settings.hermes_base_url.removesuffix("/v1")
        response = httpx.get(f"{base}/health", timeout=5)
        return {"ok": response.status_code == 200, "status_code": response.status_code}

    def strategy(self, context: dict[str, Any]) -> HermesStrategy:
        prompt = {
            "role": "user",
            "content": (
                "Return JSON only with creator_tier (nano|micro|mid), "
                "target_rate_cents, rationale, projected_cost_per_result, and "
                "compensation_components. Context: " + json.dumps(context)
            ),
        }
        return HermesStrategy.model_validate_json(extract_json(self._chat([prompt])))

    def playbook(self, platform: str) -> dict[str, Any]:
        result = self._chat(
            [
                {
                    "role": "user",
                    "content": (
                        f"Research current {platform} ranking signals. "
                        "Return JSON only with platform, signals, sources, and confidence."
                    ),
                }
            ]
        )
        return json.loads(extract_json(result))

    def learning(self, dossier: dict[str, Any]) -> HermesLearning:
        prompt = {
            "role": "user",
            "content": (
                "Analyze this closed campaign. Return JSON only with summary, change_type "
                "(patch|no_op), heuristic, "
                "no_op_reason, skill_name, evidence_ids, and governance. Dossier: "
                + json.dumps(dossier)
            ),
        }
        return HermesLearning.model_validate_json(extract_json(self._chat([prompt])))

    def outreach(self, context: dict[str, Any]) -> str:
        prompt = {
            "role": "user",
            "content": (
                "Draft the complete creator deal email. Include the "
                "deliverable, compensation, usage expectations, two-stage QA, disclosure and "
                "tracking requirements. Tell the creator to reply ACCEPT, propose a rate, or "
                "decline. Do not include or mention a web portal. Context: " + json.dumps(context)
            ),
        }
        text = self._chat([prompt]).strip()
        if not text:
            raise RuntimeError("Hermes returned an empty outreach email")
        return text

    def negotiate(self, context: dict[str, Any]) -> HermesNegotiation:
        result = self._chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Evaluate this creator reply without "
                        "exceeding the campaign cap. Return JSON only with intent "
                        "(accept|counter|decline), response, "
                        "and agreed_rate_cents. Keep the entire workflow in email. Context: "
                        + json.dumps(context)
                    ),
                }
            ]
        )
        return HermesNegotiation.model_validate_json(extract_json(result))

    def discover_creators(
        self, niche: str, platform: str, limit: int, exclude_handles: set[str]
    ) -> list[DiscoveryCandidate]:
        json_schema = (
            'Return JSON only as {"creators": [...]} with handle, email, followers, '
            "engagement_rate, fake_follower_percent, niche_match, audience_quality, "
            "brand_fit, and profile_data. "
            f"Niche={niche!r}; platform={platform!r}; limit={limit}; "
            f"exclude={sorted(exclude_handles)!r}."
        )

        def _parse_candidates(result: str) -> list[DiscoveryCandidate]:
            rows = json.loads(extract_json(result)).get("creators", [])
            candidates = [DiscoveryCandidate.model_validate(row) for row in rows]
            return [row for row in candidates if row.email and row.handle not in exclude_handles]

        api_key_context = ""
        if self.settings.influencers_club_api_key:
            api_key_context = (
                f"Use this influencers.club API key: {self.settings.influencers_club_api_key}. "
            )

        primary_prompt = (
            "Use the influencers.club agent tools to "
            + api_key_context
            + "provision or reuse API access, check credits, discover candidates, "
            "and enrich the selected handles for verified email addresses. "
            "Manage any supported credit refill through the approved Stripe agent "
            "payment flow. Never invent a creator or contact detail. "
            + json_schema
        )
        research_prompt = (
            "Research real creators on "
            f"{platform!r} in the {niche!r} niche using your web research and "
            "platform search skills. Find creators with verified public contact "
            "information (email in bio, linktree, or business email). "
            "Never invent contacts or metrics. "
            + json_schema
        )

        use_influencers_club = self.settings.discovery_mode == "influencers_club"

        candidates: list[DiscoveryCandidate] = []
        if use_influencers_club:
            try:
                candidates = _parse_candidates(
                    self._chat([{"role": "user", "content": primary_prompt}])
                )
            except Exception:
                candidates = []

        if not candidates:
            candidates = _parse_candidates(
                self._chat([{"role": "user", "content": research_prompt}])
            )
        return candidates

    def sample(self) -> dict[str, Any]:
        """One real round-trip used by the live-primitive proof surface."""
        content = self._chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Reply with a single short sentence confirming you are Hermes running "
                        "on Nemotron 3 Ultra inside NemoClaw."
                    ),
                }
            ]
        )
        return {"model": self.settings.hermes_model, "response": content[:280]}

    def _chat(self, messages: list[dict[str, str]]) -> str:
        headers = {"Authorization": f"Bearer {self.settings.hermes_api_key}"}
        response = httpx.post(
            f"{self.settings.hermes_base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": self.settings.hermes_model, "messages": messages},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


class DiscoveryProvider:
    def __init__(self, hermes: HermesProvider):
        self.hermes = hermes

    def search(
        self,
        niche: str,
        platform: str = "tiktok",
        limit: int = 5,
        exclude_handles: set[str] | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = self.hermes.discover_creators(niche, platform, limit, exclude_handles or set())
        if not candidates:
            raise RuntimeError(
                "Creator discovery returned no enriched creators with verified email"
            )
        return candidates


class VisionProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def verify(
        self,
        caption: str,
        media_url: str | None,
        *,
        platform: str = "tiktok",
        stage: str = "final",
    ) -> QAResult:
        if not self.settings.capability_configured("vision"):
            raise RuntimeError("NVIDIA NIM is not configured")
        if not media_url:
            raise ValueError("A media URL is required for NVIDIA vision QA")

        # Text-policy checks run alongside the live vision verdict.
        findings: list[dict[str, Any]] = []
        lowered = caption.lower()
        if "#ad" not in lowered and "sponsored" not in lowered:
            findings.append({"code": "missing_disclosure", "message": "FTC disclosure is missing."})
        if "argo.link/" not in lowered:
            findings.append(
                {"code": "missing_tracking_link", "message": "Tracking link is missing."}
            )

        content = [
            {
                "type": "text",
                "text": (
                    "Check whether this sponsored content is brand safe and clearly shows the "
                    f"advertised product for a {platform} {stage} submission. Return JSON only: "
                    '{"passed": boolean, "findings": [{"code": string, "message": string}]}. '
                    "Do not use markdown."
                ),
            },
            {"type": "image_url", "image_url": {"url": media_url}},
        ]
        response = httpx.post(
            f"{self.settings.nvidia_vision_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.nvidia_api_key}"},
            json={
                "model": self.settings.nvidia_vision_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
            },
            timeout=90,
        )
        response.raise_for_status()
        result = json.loads(extract_json(response.json()["choices"][0]["message"]["content"]))
        for finding in result.get("findings", []):
            if isinstance(finding, dict):
                findings.append(
                    {
                        "code": str(finding.get("code", "vision_noncompliance")),
                        "message": str(finding.get("message", "Visual QA failed"))[:500],
                    }
                )
        if result.get("passed") is not True and not result.get("findings"):
            findings.append({"code": "vision_noncompliance", "message": "Visual QA did not pass."})
        return QAResult(
            passed=not findings,
            severity="major" if findings else "none",
            findings=findings,
            model=self.settings.nvidia_vision_model,
        )

    def probe(self) -> dict[str, Any]:
        if not self.settings.capability_configured("vision"):
            raise RuntimeError("NVIDIA NIM is not configured")
        response = httpx.post(
            f"{self.settings.nvidia_vision_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.nvidia_api_key}"},
            json={
                "model": self.settings.nvidia_vision_model,
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 4,
                "temperature": 0,
            },
            timeout=5,
        )
        response.raise_for_status()
        return {
            "ok": True,
            "model": self.settings.nvidia_vision_model,
            "status_code": response.status_code,
        }


class PaymentProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create_funding_session(self, campaign_id: str, amount_cents: int) -> FundingResult:
        if not self.settings.capability_configured("stripe"):
            raise RuntimeError("Stripe is not configured")
        import stripe

        client = stripe.StripeClient(self.settings.stripe_secret_key)
        session = client.checkout.sessions.create(
            {
                "mode": "payment",
                "line_items": [
                    {
                        "price_data": {
                            "currency": "usd",
                            "unit_amount": amount_cents,
                            "product_data": {"name": f"Hugo campaign {campaign_id}"},
                        },
                        "quantity": 1,
                    }
                ],
                "success_url": self.settings.stripe_success_url.format(campaign_id=campaign_id),
                "cancel_url": self.settings.stripe_cancel_url.format(campaign_id=campaign_id),
                "metadata": {"campaign_id": campaign_id},
                "payment_intent_data": {"transfer_group": campaign_id},
            }
        )
        return FundingResult(external_id=session.id, url=session.url)

    def create_onboarding_link(self, creator_id: str, email: str | None) -> tuple[str, str, bool]:
        if not self.settings.capability_configured("stripe"):
            raise RuntimeError("Stripe is not configured")
        import stripe

        client = stripe.StripeClient(self.settings.stripe_secret_key)
        account = client.v1.accounts.create(
            {
                "country": "US",
                "email": email,
                "controller": {
                    "fees": {"payer": "application"},
                    "losses": {"payments": "application"},
                    "stripe_dashboard": {"type": "express"},
                },
                "capabilities": {"transfers": {"requested": True}},
                "metadata": {"argo_creator_id": creator_id},
            }
        )
        link = client.v1.account_links.create(
            {
                "account": account.id,
                "refresh_url": self.settings.stripe_connect_refresh_url,
                "return_url": self.settings.stripe_connect_return_url,
                "type": "account_onboarding",
            }
        )
        return account.id, link.url, False

    def resolve_source_charge(self, payment_intent_id: str) -> str:
        import stripe

        client = stripe.StripeClient(self.settings.stripe_secret_key)
        payment_intent = client.v1.payment_intents.retrieve(payment_intent_id)
        charge = getattr(payment_intent, "latest_charge", None)
        if not charge:
            raise RuntimeError("Stripe PaymentIntent has no settled source charge")
        return charge if isinstance(charge, str) else charge.id

    def transfer(
        self,
        *,
        payout_id: str,
        amount_cents: int,
        destination: str,
        source_transaction: str,
        campaign_id: str,
        idempotency_key: str,
    ) -> TransferResult:
        import stripe

        client = stripe.StripeClient(self.settings.stripe_secret_key)
        transfer = client.v1.transfers.create(
            {
                "amount": amount_cents,
                "currency": "usd",
                "destination": destination,
                "source_transaction": source_transaction,
                "transfer_group": campaign_id,
                "metadata": {"payout_id": payout_id},
            },
            options={"idempotency_key": idempotency_key},
        )
        return TransferResult(external_id=transfer.id)

    def probe(self) -> dict[str, Any]:
        if not self.settings.capability_configured("stripe"):
            raise RuntimeError("Stripe is not configured")
        import stripe

        stripe.api_key = self.settings.stripe_secret_key
        account = stripe.Account.retrieve()
        return {
            "ok": True,
            "account_id": account.id,
            "country": getattr(account, "country", None),
        }


class MailProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._renewed_access_token: str | None = None

    def _access_token(self) -> str:
        if self._renewed_access_token:
            return self._renewed_access_token
        if all(
            (
                self.settings.gmail_client_id,
                self.settings.gmail_client_secret,
                self.settings.gmail_refresh_token,
            )
        ):
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.settings.gmail_client_id,
                    "client_secret": self.settings.gmail_client_secret,
                    "refresh_token": self.settings.gmail_refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
            response.raise_for_status()
            self._renewed_access_token = str(response.json()["access_token"])
            return self._renewed_access_token
        if self.settings.gmail_access_token:
            return self.settings.gmail_access_token
        raise RuntimeError("Gmail OAuth is not configured")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        *,
        thread_id: str | None = None,
    ) -> MailSendResult:
        if not to:
            raise ValueError("Creator email is required for outreach")
        message = EmailMessage()
        message["To"] = to
        message["From"] = self.settings.gmail_sender
        message["Subject"] = subject
        message["X-Argo-Idempotency-Key"] = idempotency_key
        message.set_content(body)
        payload: dict[str, str] = {
            "raw": urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        }
        if thread_id:
            payload["threadId"] = thread_id
        response = httpx.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return MailSendResult(message_id=str(result["id"]), thread_id=str(result["threadId"]))

    @staticmethod
    def _decode_body(payload: dict[str, Any]) -> str:
        body = payload.get("body", {})
        encoded = body.get("data")
        if encoded and payload.get("mimeType") == "text/plain":
            padded = encoded + "=" * (-len(encoded) % 4)
            return urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            text = MailProvider._decode_body(part)
            if text:
                return text
        return ""

    def thread_messages(self, thread_ids: set[str]) -> list[MailReply]:
        replies: list[MailReply] = []
        sender_address = self.settings.gmail_sender.lower()
        for thread_id in sorted(thread_ids):
            response = httpx.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                headers=self._headers(),
                params={"format": "full"},
                timeout=30,
            )
            response.raise_for_status()
            for message in response.json().get("messages", []):
                headers = {
                    str(row.get("name", "")).lower(): str(row.get("value", ""))
                    for row in message.get("payload", {}).get("headers", [])
                }
                sender = parseaddr(headers.get("from", ""))[1].lower()
                if not sender or sender == sender_address:
                    continue
                body = self._decode_body(message.get("payload", {})).strip()
                if body:
                    replies.append(
                        MailReply(
                            message_id=str(message["id"]),
                            thread_id=str(message.get("threadId") or thread_id),
                            sender=sender,
                            subject=headers.get("subject", ""),
                            body=body,
                        )
                    )
        return replies

    def probe(self) -> dict[str, Any]:
        response = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers=self._headers(),
            timeout=5,
        )
        response.raise_for_status()
        profile = response.json()
        return {
            "ok": True,
            "email": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
        }


class MetricsProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(self, platform: str, post_url: str) -> MetricResult:
        if platform != "youtube":
            raise ValueError("Automatic metrics are currently available only for YouTube")
        parsed = urlparse(post_url)
        host = (parsed.hostname or "").lower()
        video_id = ""
        if host == "youtu.be" or host.endswith(".youtu.be"):
            video_id = parsed.path.strip("/").split("/")[0]
        elif host == "youtube.com" or host.endswith(".youtube.com"):
            if parsed.path.startswith("/shorts/"):
                video_id = parsed.path.split("/")[2]
            else:
                video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not video_id:
            raise ValueError("Unable to extract a YouTube video ID")
        if not self.settings.capability_configured("youtube"):
            raise RuntimeError("YouTube Data API is not configured")
        response = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "statistics",
                "id": video_id,
                "key": self.settings.youtube_api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            raise RuntimeError("YouTube video is unavailable or has no public statistics")
        statistics = items[0].get("statistics", {})
        views = int(statistics.get("viewCount", 0))
        likes = int(statistics.get("likeCount", 0))
        comments = int(statistics.get("commentCount", 0))
        return MetricResult(
            views=views,
            engagements=likes + comments,
            source_url=post_url,
            evidence={
                "provider": "youtube",
                "video_id": video_id,
                "like_count": likes,
                "comment_count": comments,
            },
        )


class TelegramProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _live(self) -> bool:
        return self.settings.should_attempt_live("telegram")

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._live():
            if method in ("getUpdates", "sendMessage", "answerCallbackQuery"):
                return {"ok": True, "result": []}
            raise RuntimeError("Telegram is not configured")
        response = httpx.post(
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}",
            json=payload or {},
            timeout=35,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description", "Telegram request failed")))
        return body

    def identity(self) -> dict[str, Any]:
        return self._call("getMe").get("result", {})

    def updates(self, offset: int) -> list[dict[str, Any]]:
        return self._call(
            "getUpdates",
            {"offset": offset, "timeout": 20, "allowed_updates": ["message", "callback_query"]},
        ).get("result", [])

    def send(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in buttons]
                ]
            }
        result = self._call("sendMessage", payload).get("result", {})
        return str(result["message_id"])

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


@dataclass
class Providers:
    hermes: HermesProvider
    discovery: DiscoveryProvider
    vision: VisionProvider
    payments: PaymentProvider
    mail: MailProvider
    metrics: MetricsProvider
    telegram: TelegramProvider


class DemoHermesProvider(HermesProvider):
    """Returns canned responses so the full lifecycle works without API keys."""

    def healthy(self) -> bool:
        return True

    def probe(self) -> dict[str, Any]:
        return {"ok": True, "status_code": 200, "demo": True}

    def strategy(self, context: dict[str, Any]) -> HermesStrategy:
        cap = context.get("per_creator_cap_cents", 15_000)
        rate = min(cap, 10_000)
        return HermesStrategy(
            creator_tier="micro",
            target_rate_cents=rate,
            rationale=(
                "Demo strategy: micro creators with authentic routine content "
                "deliver the best cost-per-result for skincare campaigns."
            ),
            projected_cost_per_result=0.019,
            compensation_components=[{"kind": "base", "rate_cents": rate}],
        )

    def playbook(self, platform: str) -> dict[str, Any]:
        return {
            "platform": platform,
            "signals": [{"signal": "Favor concise native creator video.", "weight": 0.8}],
            "sources": [{"url": f"https://example.test/{platform}-playbook"}],
            "confidence": 0.82,
        }

    def learning(self, dossier: dict[str, Any]) -> HermesLearning:
        return HermesLearning(
            summary="Demo learning: micro skincare creators on TikTok delivered the best CPV.",
            heuristic="Prefer micro creators with strong routine storytelling.",
            skill_name="argo-strategy-engine",
            evidence_ids=list(dossier.get("evidence_ids", ["demo-evidence"])),
            governance={"risk": "low", "generator": "demo", "demo": True},
        )

    def outreach(self, context: dict[str, Any]) -> str:
        name = context.get("campaign_name", "Campaign")
        rate = context.get("rate_cents", 10_000)
        return (
            f"Hi! We'd love to collaborate with you on {name}. "
            f"We're offering ${rate / 100:.2f} for a sponsored post. "
            "Please include #ad disclosure and our tracking link. "
            "Reply ACCEPT, propose a rate, or decline."
        )

    def negotiate(self, context: dict[str, Any]) -> HermesNegotiation:
        return HermesNegotiation(
            intent="accept",
            response="Great, the proposed rate works. Looking forward to collaborating!",
            agreed_rate_cents=context.get("rate_cents", 10_000),
        )

    def discover_creators(
        self, niche: str, platform: str, limit: int, exclude_handles: set[str]
    ) -> list[DiscoveryCandidate]:
        pool = [
            ("demo.creator.alpha", 42_000, 5.2, 8.0),
            ("demo.creator.beta", 67_000, 4.8, 5.5),
            ("demo.creator.gamma", 28_000, 6.1, 12.0),
            ("demo.creator.delta", 55_000, 5.5, 7.0),
            ("demo.creator.epsilon", 35_000, 5.9, 9.0),
        ]
        candidates = []
        for handle, followers, engagement, fake_pct in pool:
            if handle in exclude_handles or len(candidates) >= limit:
                break
            candidates.append(
                DiscoveryCandidate(
                    handle=handle,
                    email=f"{handle.replace('.', '-')}@example.test",
                    followers=followers,
                    engagement_rate=engagement,
                    fake_follower_percent=fake_pct,
                    niche_match=85,
                    audience_quality=80,
                    brand_fit=78,
                    profile_data={"niche": niche, "demo": True},
                )
            )
        return candidates

    def sample(self) -> dict[str, Any]:
        return {
            "model": "demo-mode",
            "response": "Demo mode active — no Hermes connection required.",
        }

    def _chat(self, messages: list[dict[str, str]]) -> str:
        return '{"demo": true}'


class DemoVisionProvider(VisionProvider):
    def verify(self, caption: str, media_url: str | None, **kwargs: Any) -> QAResult:
        findings: list[dict[str, Any]] = []
        lowered = (caption or "").lower()
        if "#ad" not in lowered and "sponsored" not in lowered:
            findings.append({"code": "missing_disclosure", "message": "FTC disclosure missing."})
        if "argo.link/" not in lowered:
            findings.append({"code": "missing_tracking_link", "message": "Tracking link missing."})
        return QAResult(
            passed=not findings,
            severity="major" if findings else "none",
            findings=findings,
            model="demo-vision",
        )

    def probe(self) -> dict[str, Any]:
        return {"ok": True, "model": "demo-vision", "demo": True}


class DemoPaymentProvider(PaymentProvider):
    def create_funding_session(self, campaign_id: str, amount_cents: int) -> FundingResult:
        return FundingResult(
            external_id=f"cs_demo_{campaign_id}",
            url=f"http://localhost:3000/campaigns/{campaign_id}?funding=demo",
            payment_intent_id=f"pi_demo_{campaign_id}",
        )

    def create_onboarding_link(self, creator_id: str, email: str | None) -> tuple[str, str, bool]:
        return (
            f"acct_demo_{creator_id}",
            "http://localhost:3000/system?onboarding=demo",
            True,
        )

    def resolve_source_charge(self, payment_intent_id: str) -> str:
        return payment_intent_id.replace("pi_", "ch_", 1)

    def transfer(self, **kwargs: Any) -> TransferResult:
        return TransferResult(external_id=f"tr_demo_{kwargs.get('payout_id', 'unknown')}")

    def probe(self) -> dict[str, Any]:
        return {"ok": True, "account_id": "acct_demo_platform", "demo": True}


class DemoMailProvider(MailProvider):
    def send(
        self, to: str, subject: str, body: str, idempotency_key: str, **kwargs: Any
    ) -> MailSendResult:
        return MailSendResult(
            message_id=f"msg_demo_{idempotency_key}",
            thread_id=kwargs.get("thread_id") or f"thread_demo_{idempotency_key}",
        )

    def thread_messages(self, thread_ids: set[str]) -> list[MailReply]:
        return []

    def probe(self) -> dict[str, Any]:
        return {"ok": True, "email": "demo@example.test", "messages_total": 0, "demo": True}


def build_providers(settings: Settings) -> Providers:
    if settings.demo_mode:
        hermes = DemoHermesProvider(settings)
        return Providers(
            hermes=hermes,
            discovery=DiscoveryProvider(hermes),
            vision=DemoVisionProvider(settings),
            payments=DemoPaymentProvider(settings),
            mail=DemoMailProvider(settings),
            metrics=MetricsProvider(settings),
            telegram=TelegramProvider(settings),
        )
    hermes = HermesProvider(settings)
    return Providers(
        hermes=hermes,
        discovery=DiscoveryProvider(hermes),
        vision=VisionProvider(settings),
        payments=PaymentProvider(settings),
        mail=MailProvider(settings),
        metrics=MetricsProvider(settings),
        telegram=TelegramProvider(settings),
    )
