---
name: hugo-browser-email
description: Send and inspect Hugo creator email through a user-authorized Gmail or Outlook browser session.
---

# Hugo browser email

Use this skill only when `hugo_email_preflight` returns `mode: browser`.

## Prerequisite

Browser automation must be enabled in Hermes and connected to the user's existing Chrome,
Edge, Brave, or Chromium session. During setup, run `hermes tools`, enable Browser, choose
local Chromium/CDP (or the Nous managed browser gateway), then use `/browser connect` for
the browser in which the user is already signed in. Never ask for or store an email password.

## Sender safety gate

1. Call `hugo_email_preflight` and read `provider`, `sender`, and `mail_url`.
2. Open `mail_url` with browser tools.
3. Inspect the active account. It must exactly match `sender`, case-insensitively.
4. If the account is signed out or differs, stop and fail the task. Tell the operator which
   configured sender is required. Never switch accounts or send from another address.

## Send a queued email

For a claimed `browser_email` task, use its payload as authoritative:

1. Open a new compose window, or the matching thread when `reply_thread_id` is present.
2. Set the recipient and subject exactly as supplied.
3. Insert the supplied body verbatim. Do not add signatures, promises, or changed rates.
4. Review recipient, sender, subject, and body once, then click Send.
5. Call `hugo_confirm_browser_email` with the task ID and verified sender. Include a provider
   message/thread ID when the UI exposes one; otherwise Hugo uses the task ID idempotently.

Do not confirm before the UI visibly reports that the message was sent.

## Inspect replies

Use the `open_threads` returned by `hugo_email_preflight`. Search by creator email and exact
subject, open only unread messages newer than Hugo's last recorded thread, and pass the reply
body to `hugo_process_creator_response` with a stable external ID. Do not process drafts, sent
messages, spam, or a message from an address other than the campaign creator.
