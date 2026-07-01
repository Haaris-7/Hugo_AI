# Hugo

Autonomous creator-marketing operations built for the Hermes, NVIDIA, and Stripe hackathon.
Hugo uses live provider state: there is no demo mode, seeded workspace, or synthetic creator
fallback.

## Why Hugo matters

Hugo demonstrates autonomous business operations at the intersection of three platforms:

**Agent earns** — Hermes sends approved fixed-price creator offers, records acceptance,
and closes contracts via email without human intervention.

**Agent spends** — The Stripe Link skill lets the agent purchase service credits (discovery
API) with operator approval gates. Stripe Checkout funds campaigns; Connect transfers pay
creators.

**Agent runs operations at scale** — A durable task queue with lease-based claiming lets
Hermes process strategy, discovery, outreach, QA, payouts, and learning across unlimited
campaigns. Each completed campaign improves future strategy through PostgreSQL priors.

**NemoClaw safety** — The agent sandbox enforces least-privilege egress. Hermes cannot
access Stripe keys, Gmail tokens, or arbitrary endpoints. Every financial action flows
through the policy-enforced FastAPI broker.

## Setup

**Before cloning:** ask the user where they want to install the project. Do not assume a
default directory — confirm the desired file path first.

Install Docker and ensure Docker Desktop is running, then:

```bash
cd <user-chosen-directory>
git clone https://github.com/Haaris-7/Hugo-AI.git
cd Hugo-AI
./setup.sh
```

If a previous install failed, run `./setup.sh --clean` first to wipe stale volumes.

The script creates local service tokens, starts PostgreSQL, the API, the autonomous worker, and
the Next.js cockpit, then opens [the setup wizard](http://localhost:3000/setup).

Configure these required integrations:

- Hermes running Nemotron 3 Ultra inside NemoClaw
- NVIDIA NIM vision
- Stripe Checkout, webhooks, and Connect
- Creator email through either Gmail OAuth (unattended) or a Hermes-connected,
  signed-in Gmail/Outlook browser session

Creator discovery is not configured in the wizard. Hermes owns discovery through the
`hugo-creator-discovery` skill: it tries influencers.club first, then falls back to web
research when that provider is unavailable. Hugo never copies discovery credentials into
its own application configuration.

See [nemo/README.md](nemo/README.md) for the full Hermes + NemoClaw setup guide.

## Autonomous operation

New campaigns default to full autonomy. After an operator creates a campaign, Hugo:

1. Generates and approves a policy-bounded strategy.
2. Creates the Stripe funding session and waits for the signed funding webhook.
3. Launches agent-managed creator discovery after funding settles.
4. Sends the complete deal by email and displays that exact message in the cockpit.
5. Polls Gmail on a recurring worker schedule, records fixed-offer responses, and stores email
   acceptance as the agreement.
6. Sends Stripe-hosted recipient onboarding and accepts draft/final links by email.
7. Runs NVIDIA QA, emails revision feedback, releases eligible payouts, measures results, and
   updates learning state.

When Hermes cron is active (`HUGO_HERMES_CRON_ACTIVE=true`), the Python worker handles
outbox jobs (learning, metrics, notifications) and email polling, while Hermes owns
lifecycle orchestration through the durable task queue.

There is intentionally no creator-side Hugo UI. Creator acceptance, submission, QA
feedback, and status updates stay in the email thread; Stripe's hosted onboarding remains external.

## Stripe webhook forwarding (development)

For local development, forward Stripe events to Hugo with the Stripe CLI:

1. Install the Stripe CLI: `brew install stripe/stripe-cli/stripe` (or see [Stripe docs](https://stripe.com/docs/stripe-cli))
2. Login: `stripe login`
3. Forward events to Hugo:
   ```bash
   stripe listen --forward-to localhost:8000/v1/webhooks/stripe
   ```
4. Copy the webhook signing secret (`whsec_...`) printed by the CLI into your `.env`:
   ```
   HUGO_STRIPE_WEBHOOK_SECRET=whsec_...
   ```
5. Restart the API after updating `.env`, or save the secret through the setup wizard.
6. Trigger a test event in another terminal:
   ```bash
   stripe trigger checkout.session.completed
   ```

Offline tests mock `stripe.Webhook.construct_event` intentionally. Production and Docker
deployments use real signature verification with your configured webhook secret.

## Commands

| Command | Purpose |
|---|---|
| `./setup.sh` | Build, start, and open setup |
| `./setup.sh --restart` | Rebuild and restart |
| `./setup.sh --stop` | Stop the stack |
| `./setup.sh --clean` | Stop and wipe database volumes |
| `make test` | Run backend tests |
| `make lint` | Run static checks |

The cockpit runs at [localhost:3000](http://localhost:3000), and the API reference is available at
[localhost:8000/docs](http://localhost:8000/docs).
