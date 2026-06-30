---
name: argo-outreach-negotiation
description: Draft concise creator outreach and negotiate within explicit Hugo campaign caps.
---

# Hugo Outreach and Negotiation

## Outreach
Call `argo_request_outreach` with the deal ID and rate in cents. The backend drafts
the outreach email using the approved brief, creator handle, rate cap, and reputation
evidence, then sends it via Gmail.

Never promise a rate above the cap or imply payment before content passes QA.

## Negotiation
Call `argo_process_creator_reply` with the deal ID, reply body, and external message
ID when a creator responds. The backend classifies the reply (accept, counter, decline)
and responds within policy caps automatically.

After processing, mark the Hermes task complete with `argo_complete_task` including
the negotiation outcome, or fail it with `argo_fail_task`.

## Email polling
Call `argo_poll_emails` periodically to poll Gmail threads and advance negotiation
and deliverable state idempotently. This handles all open deals with active threads.
