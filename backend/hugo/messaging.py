from __future__ import annotations

import re
import secrets
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    ApprovalRequest,
    Campaign,
    Deal,
    MessagingChannel,
    MessagingReceipt,
    Payout,
    ServiceSpend,
    utcnow,
)
from .providers import Providers
from .schemas import ApprovalCreate
from .services import close_and_replace_deal, decide_approval, request_payout


def _aware(value):
    if value and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def start_telegram_pairing(db: Session, providers: Providers) -> dict[str, Any]:
    identity = providers.telegram.identity()
    username = identity.get("username")
    if not username:
        raise HTTPException(status_code=503, detail="Telegram bot identity is unavailable")
    channel = db.scalar(select(MessagingChannel).where(MessagingChannel.provider == "telegram"))
    if not channel:
        channel = MessagingChannel(provider="telegram")
        db.add(channel)
    channel.pairing_nonce = secrets.token_urlsafe(12)
    channel.pairing_expires_at = utcnow() + timedelta(minutes=10)
    channel.username = str(username)
    channel.enabled = False
    db.commit()
    return {
        "provider": "telegram",
        "username": username,
        "pairing_url": f"https://t.me/{username}?start={channel.pairing_nonce}",
        "expires_at": channel.pairing_expires_at.isoformat(),
    }


def confirm_telegram_pairing(db: Session, providers: Providers) -> dict[str, Any]:
    channel = db.scalar(select(MessagingChannel).where(MessagingChannel.provider == "telegram"))
    if not channel or not channel.pairing_nonce:
        raise HTTPException(status_code=409, detail="Start Telegram pairing first")
    if _aware(channel.pairing_expires_at) < utcnow():
        raise HTTPException(status_code=409, detail="Telegram pairing code expired")
    updates = providers.telegram.updates(channel.last_update_id + 1)
    expected = f"/start {channel.pairing_nonce}"
    matched = None
    for update in updates:
        channel.last_update_id = max(channel.last_update_id, int(update.get("update_id", 0)))
        message = update.get("message") or {}
        if str(message.get("text", "")).strip() == expected:
            matched = message
    if not matched:
        db.commit()
        raise HTTPException(status_code=409, detail="Send the pairing link to the bot, then retry")
    chat = matched.get("chat") or {}
    sender = matched.get("from") or {}
    if chat.get("type") != "private":
        raise HTTPException(status_code=422, detail="Telegram pairing requires a private chat")
    channel.chat_id = str(chat.get("id"))
    channel.user_id = str(sender.get("id"))
    channel.enabled = True
    channel.pairing_nonce = None
    channel.pairing_expires_at = None
    db.commit()
    providers.telegram.send(channel.chat_id, "Hugo approvals are connected to this account.")
    return {
        "provider": "telegram",
        "enabled": True,
        "username": channel.username,
        "chat_id": channel.chat_id,
        "user_id": channel.user_id,
    }


def _approval_text(db: Session, request: ApprovalRequest) -> str:
    campaign = db.get(Campaign, request.campaign_id)
    detail = ""
    if request.resource_type == "payout":
        payout = db.get(Payout, request.resource_id)
        detail = f"\nAmount ${payout.amount_cents / 100:.2f}." if payout else ""
    elif request.resource_type == "service_spend":
        spend = db.get(ServiceSpend, request.resource_id)
        detail = (
            f"\nAuthorization ${spend.amount_cents / 100:.2f}; Link approval still required."
            if spend
            else ""
        )
    return (
        f"Hugo approval · {request.resource_type.replace('_', ' ')}\n"
        f"Campaign: {campaign.name if campaign else request.campaign_id}{detail}\n"
        f"Reply approve {request.token} or reject {request.token}."
    )


def send_approval_notification(
    db: Session,
    request: ApprovalRequest,
    providers: Providers,
    settings: Settings,
) -> str | None:
    channel = db.scalar(
        select(MessagingChannel).where(
            MessagingChannel.provider == "telegram", MessagingChannel.enabled.is_(True)
        )
    )
    if not channel or not channel.chat_id:
        return None
    payment_types = {"payout", "service_spend"}
    allowed = request.resource_type not in payment_types or settings.telegram_approval_mode in {
        "strategy_creators_payments",
        "full_autonomy",
    }
    buttons = None
    if allowed and settings.telegram_approval_mode != "full_autonomy":
        buttons = [
            ("Approve", f"approve:{request.token}"),
            ("Reject", f"reject:{request.token}"),
        ]
    return providers.telegram.send(channel.chat_id, _approval_text(db, request), buttons)


def send_operator_message(db: Session, text: str, providers: Providers) -> str | None:
    channel = db.scalar(
        select(MessagingChannel).where(
            MessagingChannel.provider == "telegram", MessagingChannel.enabled.is_(True)
        )
    )
    if not channel or not channel.chat_id:
        return None
    return providers.telegram.send(channel.chat_id, text[:3500])


def resolve_approval_request(
    db: Session,
    request: ApprovalRequest,
    decision: str,
    providers: Providers,
    settings: Settings,
    *,
    source: str,
) -> dict[str, Any]:
    if request.status != "pending":
        return {"status": request.status, "duplicate": True}
    if _aware(request.expires_at) < utcnow():
        request.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="Approval request expired")
    approval = ApprovalCreate(
        campaign_id=request.campaign_id,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        decision=decision,
    )
    decide_approval(
        db,
        approval,
        stripe_live=settings.capability_configured("stripe"),
    )
    request = db.get(ApprovalRequest, request.id)
    request.status = decision
    request.decision_source = source
    request.decided_at = utcnow()
    if request.resource_type == "payout" and decision == "approved":
        request_payout(db, db.get(Payout, request.resource_id), providers, settings)
    elif request.resource_type == "deal" and decision == "rejected":
        deal = db.get(Deal, request.resource_id)
        close_and_replace_deal(db, deal, providers, reason="operator_rejected_creator")
    db.commit()
    return {"status": decision, "resource_type": request.resource_type}


def process_telegram_updates(
    db: Session,
    providers: Providers,
    settings: Settings,
) -> int:
    channel = db.scalar(
        select(MessagingChannel).where(
            MessagingChannel.provider == "telegram", MessagingChannel.enabled.is_(True)
        )
    )
    if not channel or not channel.chat_id or not channel.user_id:
        return 0
    handled = 0
    updates = providers.telegram.updates(channel.last_update_id + 1)
    for update in updates:
        update_id = int(update.get("update_id", 0))
        channel.last_update_id = max(channel.last_update_id, update_id)
        receipt_id = f"telegram:{update_id}"
        if db.get(MessagingReceipt, receipt_id):
            continue
        callback = update.get("callback_query") or {}
        message = callback.get("message") or update.get("message") or {}
        sender = callback.get("from") or message.get("from") or {}
        chat = message.get("chat") or {}
        receipt = MessagingReceipt(id=receipt_id, payload=update)
        db.add(receipt)
        if str(chat.get("id")) != channel.chat_id or str(sender.get("id")) != channel.user_id:
            receipt.status = "rejected_identity"
            continue
        data = str(callback.get("data") or "")
        text = str(message.get("text") or "").strip()
        match = re.fullmatch(r"(approve|reject)[: ]([A-Za-z0-9_-]+)", data or text, re.I)
        if not match:
            receipt.status = "ignored"
            continue
        decision = "approved" if match.group(1).lower() == "approve" else "rejected"
        request = db.scalar(select(ApprovalRequest).where(ApprovalRequest.token == match.group(2)))
        if not request:
            receipt.status = "unknown_token"
            continue
        try:
            resolve_approval_request(db, request, decision, providers, settings, source="telegram")
            receipt.status = decision
            handled += 1
            if callback.get("id"):
                providers.telegram.answer_callback(str(callback["id"]), decision.title())
        except HTTPException as exc:
            receipt.status = f"error_{exc.status_code}"
    db.commit()
    return handled
