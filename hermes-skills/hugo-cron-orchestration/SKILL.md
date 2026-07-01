---
name: hugo-cron-orchestration
description: Hermes cron loop that claims and executes durable work tasks every minute.
---

# Hugo Cron Orchestration

This skill defines the Hermes cron loop. It runs every minute and executes durable
work tasks using the lease-based claim pattern.

## Loop structure

1. Call `hugo_preflight` to check pending/claimed/failed counts.
2. If `should_claim` is false, exit early.
3. Call `hugo_poll_emails` to advance any open email threads before claiming new work.
4. Call `hugo_claim_tasks` with a limit of 5. Each claimed task has a 180-second lease.
5. For each claimed task, dispatch by `task_type`:

| task_type   | Tool to call                     | Skill reference                |
|-------------|----------------------------------|--------------------------------|
| strategy    | `hugo_generate_strategy`         | hugo-strategy-engine           |
| discovery   | `hugo_request_discovery`         | hugo-creator-discovery         |
| outreach    | `hugo_request_outreach`          | hugo-outreach                  |
| browser_email | Browser tools + `hugo_confirm_browser_email` | hugo-browser-email |
| funding     | `hugo_create_funding`            | (direct lifecycle tool)        |
| launch      | `hugo_launch_campaign`           | (direct lifecycle tool)        |
| qa          | `hugo_request_qa`                | (direct lifecycle tool)        |
| payout      | `hugo_request_payout`            | (direct lifecycle tool)        |
| learning    | `hugo_begin_learning` + `hugo_commit_learning` | hugo-performance-learning |
| notify      | `hugo_notify_operator`           | (direct lifecycle tool)        |

6. After each task executes, call `hugo_complete_task` with the result, or
   `hugo_fail_task` with the error message.

For `browser_email`, the confirmation tool completes the task after the browser send succeeds;
do not call `hugo_complete_task` a second time.

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

- If a tool call raises an error, call `hugo_fail_task` with the error message.
- Failed tasks are visible in the cockpit and preflight counts.
- Do not retry failed tasks in the same cron cycle; they will be addressed in
  subsequent runs or by operator intervention.
