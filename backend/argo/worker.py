import time

from sqlalchemy import or_, select

from .config import get_settings
from .db import SessionLocal
from .messaging import process_telegram_updates, send_approval_notification, send_operator_message
from .models import ApprovalRequest, Campaign, OutboxJob, utcnow
from .providers import build_providers
from .services import (
    advance_autonomous_campaigns,
    collect_campaign_metrics,
    process_creator_email_updates,
    process_learning,
)


def run_once() -> bool:
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        job = db.scalar(
            select(OutboxJob)
            .where(
                OutboxJob.status == "pending",
                or_(OutboxJob.scheduled_at.is_(None), OutboxJob.scheduled_at <= utcnow()),
            )
            .order_by(OutboxJob.created_at)
            .with_for_update(skip_locked=True)
        )
        if not job:
            activity = process_creator_email_updates(db, providers)
            if not settings.hermes_cron_active:
                activity += advance_autonomous_campaigns(db, providers, settings)
            activity += process_telegram_updates(db, providers, settings)
            return bool(activity)
        job.status = "processing"
        job.attempts += 1
        db.commit()
        try:
            if job.job_type == "campaign_learning":
                process_learning(db, job.payload["campaign_id"], providers)
            elif job.job_type == "collect_metrics":
                collect_campaign_metrics(
                    db,
                    db.get(Campaign, job.payload["campaign_id"]),
                    providers,
                    settings,
                    conversions=int(job.payload.get("conversions", 0)),
                )
            elif job.job_type == "messaging_notification":
                request = db.get(ApprovalRequest, job.payload["approval_request_id"])
                if request:
                    send_approval_notification(db, request, providers, settings)
            elif job.job_type == "operator_message":
                send_operator_message(db, str(job.payload.get("text", "")), providers)
            else:
                raise ValueError(f"Unknown job type: {job.job_type}")
            job.status = "completed"
            db.commit()
        except Exception as exc:
            job.status = "failed" if job.attempts >= 3 else "pending"
            job.last_error = str(exc)[:1000]
            db.commit()
        return True


def main() -> None:
    while True:
        settings = get_settings()
        try:
            settings.validate_runtime()
            if not run_once():
                time.sleep(max(2, settings.automation_poll_seconds))
        except RuntimeError:
            # The worker starts with the stack and waits until the setup wizard
            # has persisted a complete configuration to the shared .env file.
            get_settings.cache_clear()
            time.sleep(5)


if __name__ == "__main__":
    main()
