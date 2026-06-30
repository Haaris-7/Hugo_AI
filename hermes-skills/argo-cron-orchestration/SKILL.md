---
name: argo-cron-orchestration
description: Hermes cron loop that claims and executes durable work tasks every minute.
---

# Hugo Cron Orchestration

This skill defines the Hermes cron loop. It runs every minute and executes durable
work tasks using the lease-based claim pattern.

## Loop structure

1. Call `argo_preflight` to check pending/claimed/failed counts.
2. If `should_claim` is false, exit early.
3. Call `argo_poll_emails` to advance any open email threads before claiming new work.
4. Call `argo_claim_tasks` with a limit of 5. Each claimed task has a 180-second lease.
5. For each claimed task, dispatch by `task_type`:

| task_type   | Tool to call                     | Skill reference                |
|-------------|----------------------------------|--------------------------------|
| strategy    | `argo_generate_strategy`         | argo-strategy-engine           |
| discovery   | `argo_request_discovery`         | argo-creator-discovery         |
| outreach    | `argo_request_outreach`          | argo-outreach-negotiation      |
| negotiation | `argo_process_creator_reply`     | argo-outreach-negotiation      |
| funding     | `argo_create_funding`            | (direct lifecycle tool)        |
| launch      | `argo_launch_campaign`           | (direct lifecycle tool)        |
| qa          | `argo_request_qa`                | (direct lifecycle tool)        |
| payout      | `argo_request_payout`            | (direct lifecycle tool)        |
| learning    | `argo_begin_learning` + `argo_commit_learning` | argo-performance-learning |
| notify      | `argo_notify_operator`           | (direct lifecycle tool)        |

6. After each task executes, call `argo_complete_task` with the result, or
   `argo_fail_task` with the error message.

## Lease semantics

- Each claimed task gets a 180-second lease.
- If the lease expires before completion, the task reverts to pending and is
  retried on the next cron cycle.
- The `attempt` counter increments on every claim; use it to detect stuck tasks.

## Dedupe keys

Tasks are created with dedupe keys to prevent duplicate pending/claimed entries.
The cron loop does not need to check for duplicates; the backend enforces this
during `enqueue_hermes_task`.

## Error handling

- If a tool call raises an error, call `argo_fail_task` with the error message.
- Failed tasks are visible in the cockpit and preflight counts.
- Do not retry failed tasks in the same cron cycle; they will be addressed in
  subsequent runs or by operator intervention.
