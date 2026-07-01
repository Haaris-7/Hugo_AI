from . import tools

__all__ = ["register"]

_TASK_ID = {
    "task_id": {
        "type": "string",
        "description": "Hermes cron task ID when invoked from the task loop",
    }
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _with_task_id(properties: dict) -> dict:
    return {**properties, **_TASK_ID}


def register(ctx) -> None:
    specs = [
        (
            "hugo_get_campaign",
            "Read the authoritative state for a Hugo campaign.",
            _with_task_id({"campaign_id": {"type": "string"}}),
            ["campaign_id"],
            tools.hugo_get_campaign,
        ),
        (
            "hugo_request_discovery",
            "Request policy-metered creator discovery for a campaign.",
            _with_task_id({"campaign_id": {"type": "string"}}),
            ["campaign_id"],
            tools.hugo_request_discovery,
        ),
        (
            "hugo_request_service_spend",
            (
                "Create a capped service-spend authorization, or record the outcome after "
                "the official Stripe Link CLI skill receives operator approval."
            ),
            _with_task_id(
                {
                    "campaign_id": {"type": "string"},
                    "spend_id": {"type": "string"},
                    "spend_request_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["approved", "completed", "rejected", "failed"],
                    },
                }
            ),
            ["campaign_id"],
            tools.hugo_request_service_spend,
        ),
        (
            "hugo_request_outreach",
            "Request outreach after creator approval and budget validation.",
            _with_task_id(
                {
                    "deal_id": {"type": "string"},
                    "rate_cents": {"type": "integer", "minimum": 1},
                }
            ),
            ["deal_id", "rate_cents"],
            tools.hugo_request_outreach,
        ),
        (
            "hugo_email_preflight",
            "Read the configured email transport, sender, provider, and browser inbox work.",
            _with_task_id({}),
            [],
            tools.hugo_email_preflight,
        ),
        (
            "hugo_confirm_browser_email",
            "Confirm that a queued browser email was sent from the configured account.",
            {
                "task_id": {"type": "string"},
                "sender": {"type": "string"},
                "external_id": {"type": "string"},
                "thread_id": {"type": "string"},
            },
            ["task_id", "sender"],
            tools.hugo_confirm_browser_email,
        ),
        (
            "hugo_request_qa",
            "Request deterministic and NVIDIA NIM content QA.",
            _with_task_id({"deliverable_id": {"type": "string"}}),
            ["deliverable_id"],
            tools.hugo_request_qa,
        ),
        (
            "hugo_process_creator_response",
            "Record a creator's acceptance or decline of a fixed campaign offer.",
            _with_task_id(
                {
                    "deal_id": {"type": "string"},
                    "body": {"type": "string"},
                    "external_id": {"type": "string"},
                }
            ),
            ["deal_id", "body", "external_id"],
            tools.hugo_process_creator_response,
        ),
        (
            "hugo_request_payout",
            "Request a creator payout; the backend enforces every money gate.",
            _with_task_id({"payout_id": {"type": "string"}}),
            ["payout_id"],
            tools.hugo_request_payout,
        ),
        (
            "hugo_notify_operator",
            "Queue a concise operator update through the configured Telegram channel.",
            _with_task_id(
                {
                    "text": {"type": "string"},
                    "dedupe_key": {"type": "string"},
                }
            ),
            ["text", "dedupe_key"],
            tools.hugo_notify_operator,
        ),
        (
            "hugo_begin_learning",
            "Snapshot Hugo skill versions before a campaign learning cycle.",
            _with_task_id({"run_id": {"type": "string"}}),
            ["run_id"],
            tools.hugo_begin_learning,
        ),
        (
            "hugo_commit_learning",
            "Record the evidence-backed result after updating a hugo-* skill.",
            _with_task_id(
                {
                    "run_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "change_type": {"type": "string", "enum": ["patch", "no_op"]},
                    "heuristic": {"type": "string"},
                    "no_op_reason": {"type": "string"},
                    "skill_name": {"type": "string", "pattern": "^hugo-[a-z0-9-]+$"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "governance": {"type": "object"},
                }
            ),
            ["run_id", "summary", "skill_name", "evidence_ids"],
            tools.hugo_commit_learning,
        ),
        (
            "hugo_generate_strategy",
            "Generate a budget-safe creator strategy for a campaign using playbooks and priors.",
            _with_task_id({"campaign_id": {"type": "string"}}),
            ["campaign_id"],
            tools.hugo_generate_strategy,
        ),
        (
            "hugo_launch_campaign",
            "Launch a funded campaign into active discovery and outreach.",
            _with_task_id({"campaign_id": {"type": "string"}}),
            ["campaign_id"],
            tools.hugo_launch_campaign,
        ),
        (
            "hugo_create_funding",
            "Create a Stripe checkout session for campaign funding.",
            _with_task_id({"campaign_id": {"type": "string"}}),
            ["campaign_id"],
            tools.hugo_create_funding,
        ),
        (
            "hugo_poll_emails",
            "Poll Gmail threads for creator replies and advance deal state idempotently.",
            _with_task_id({}),
            [],
            tools.hugo_poll_emails,
        ),
        (
            "hugo_preflight",
            "Lightweight cron check: pending/claimed/failed task counts and should_claim flag.",
            _with_task_id({}),
            [],
            tools.hugo_preflight,
        ),
        (
            "hugo_claim_tasks",
            "Claim pending Hermes work tasks with a 180-second lease for execution.",
            _with_task_id(
                {"limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}
            ),
            [],
            tools.hugo_claim_tasks,
        ),
        (
            "hugo_complete_task",
            "Mark a claimed Hermes task as completed with optional result and evidence.",
            {
                "task_id": {"type": "string"},
                "result": {"type": "object"},
                "evidence": {"type": "object"},
            },
            ["task_id"],
            tools.hugo_complete_task,
        ),
        (
            "hugo_fail_task",
            "Mark a Hermes task as failed with an error message and optional evidence.",
            {
                "task_id": {"type": "string"},
                "error": {"type": "string"},
                "evidence": {"type": "object"},
            },
            ["task_id", "error"],
            tools.hugo_fail_task,
        ),
    ]
    for name, description, properties, required, handler in specs:
        ctx.register_tool(
            name=name,
            toolset="hugo_ops",
            schema=_schema(name, description, properties, required),
            handler=handler,
            description=description,
        )
