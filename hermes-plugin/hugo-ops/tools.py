from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> str:
    base = os.environ["HUGO_INTERNAL_API_URL"].rstrip("/")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['HUGO_AGENT_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:1000]
        return json.dumps({"ok": False, "status": exc.code, "detail": detail})


def hugo_get_campaign(campaign_id: str, task_id: str | None = None) -> str:
    return _request("GET", f"/internal/agent/campaigns/{campaign_id}")


def _action(
    action: str, campaign_id: str | None, resource_id: str | None, arguments: dict | None
) -> str:
    return _request(
        "POST",
        f"/internal/agent/actions/{action}",
        {"campaign_id": campaign_id, "resource_id": resource_id, "arguments": arguments or {}},
    )


def hugo_request_discovery(campaign_id: str, task_id: str | None = None) -> str:
    return _action("discovery", campaign_id, None, None)


def hugo_request_service_spend(
    campaign_id: str,
    spend_id: str | None = None,
    spend_request_id: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
) -> str:
    arguments = {
        key: value
        for key, value in {
            "spend_id": spend_id,
            "spend_request_id": spend_request_id,
            "status": status,
        }.items()
        if value is not None
    }
    return _action("service-spend", campaign_id, None, arguments)


def hugo_request_outreach(deal_id: str, rate_cents: int, task_id: str | None = None) -> str:
    return _action("outreach", None, deal_id, {"rate_cents": rate_cents})


def hugo_email_preflight(task_id: str | None = None) -> str:
    return _action("email-preflight", None, None, None)


def hugo_confirm_browser_email(
    task_id: str,
    sender: str,
    external_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    arguments = {
        key: value
        for key, value in {
            "sender": sender,
            "external_id": external_id,
            "thread_id": thread_id,
        }.items()
        if value is not None
    }
    return _action("browser-email-sent", None, task_id, arguments)


def hugo_process_creator_response(
    deal_id: str,
    body: str,
    external_id: str,
    task_id: str | None = None,
) -> str:
    return _action(
        "creator-response",
        None,
        deal_id,
        {"body": body, "external_id": external_id},
    )


def hugo_request_qa(deliverable_id: str, task_id: str | None = None) -> str:
    return _action("qa", None, deliverable_id, None)


def hugo_request_payout(payout_id: str, task_id: str | None = None) -> str:
    return _action("payout", None, payout_id, None)


def hugo_notify_operator(
    text: str,
    dedupe_key: str,
    task_id: str | None = None,
) -> str:
    return _action("notify", None, None, {"text": text, "dedupe_key": dedupe_key})


def hugo_begin_learning(run_id: str, task_id: str | None = None) -> str:
    return _request("POST", f"/internal/agent/learning/{run_id}/begin", {})


def hugo_commit_learning(
    run_id: str,
    summary: str,
    skill_name: str,
    evidence_ids: list[str],
    change_type: str = "patch",
    heuristic: str | None = None,
    no_op_reason: str | None = None,
    governance: dict | None = None,
    task_id: str | None = None,
) -> str:
    return _request(
        "POST",
        f"/internal/agent/learning/{run_id}/commit",
        {
            "summary": summary,
            "change_type": change_type,
            "heuristic": heuristic,
            "no_op_reason": no_op_reason,
            "skill_name": skill_name,
            "evidence_ids": evidence_ids,
            "governance": governance or {},
        },
    )


def hugo_generate_strategy(campaign_id: str, task_id: str | None = None) -> str:
    return _action("strategy", campaign_id, None, None)


def hugo_launch_campaign(campaign_id: str, task_id: str | None = None) -> str:
    return _action("launch", campaign_id, None, None)


def hugo_create_funding(campaign_id: str, task_id: str | None = None) -> str:
    return _action("funding", campaign_id, None, None)


def hugo_poll_emails(task_id: str | None = None) -> str:
    return _request("POST", "/internal/agent/poll_emails", {})


def hugo_preflight(task_id: str | None = None) -> str:
    return _request("GET", "/internal/agent/tasks/preflight")


def hugo_claim_tasks(limit: int = 5, task_id: str | None = None) -> str:
    return _request("POST", f"/internal/agent/tasks/claim?limit={limit}")


def hugo_complete_task(
    task_id: str,
    result: dict | None = None,
    evidence: dict | None = None,
) -> str:
    return _request(
        "POST",
        f"/internal/agent/tasks/{task_id}/complete",
        {"result": result or {}, "evidence": evidence or {}},
    )


def hugo_fail_task(
    task_id: str,
    error: str,
    evidence: dict | None = None,
) -> str:
    return _request(
        "POST",
        f"/internal/agent/tasks/{task_id}/fail",
        {"error": error, "evidence": evidence or {}},
    )
