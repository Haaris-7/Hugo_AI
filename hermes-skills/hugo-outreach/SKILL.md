---
name: hugo-outreach
description: Draft concise creator outreach with fixed, approved compensation terms.
---

# Hugo Outreach

## Outreach
Call `hugo_request_outreach` with the deal ID and rate in cents. The backend drafts
the outreach email using the approved brief, creator handle, rate cap, and reputation
evidence. Gmail API mode sends immediately. Browser mode queues a `browser_email`
task that must be completed with the `hugo-browser-email` skill.

The compensation is final. Never invite rate changes, promise a rate above the cap, or imply
payment before content passes QA. Ask the creator to reply ACCEPT or DECLINE.

## Creator response
Call `hugo_process_creator_response` with the deal ID, reply body, and external message
ID when a creator responds. The backend records acceptance when the reply affirmatively accepts
the fixed offer; every other response closes the offer and starts replacement discovery.

After processing, mark the Hermes task complete with `hugo_complete_task` including
the response outcome, or fail it with `hugo_fail_task`.

## Email polling
Call `hugo_poll_emails` periodically. Gmail API mode polls automatically. Browser mode
returns the configured provider, sender, and open threads; use `hugo-browser-email` to
inspect the signed-in inbox and submit new replies through `hugo_process_creator_response`.
