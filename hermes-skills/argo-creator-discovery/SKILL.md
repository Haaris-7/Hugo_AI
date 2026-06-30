---
name: argo-creator-discovery
description: Provision and operate creator discovery for a live Hugo campaign.
---

# Creator discovery

Call `argo_request_discovery` with the campaign ID. The backend applies policy
scoring and returns deal IDs for discovered creators.

## Primary path: influencers.club

1. Check service-spend credits before discovery. If the provider requires a refill,
   use `argo_request_service_spend` within the campaign's approved service-spend policy.
2. Use influencers.club agent tools to discover candidates using the campaign niche
   and platform, excluding previously discovered handles.
3. Enrich selected handles for verified email addresses.

## Fallback path: web research

If influencers.club is unavailable, credits are exhausted, or the operator declines
to purchase access, the backend automatically retries discovery using Hermes web
research skills. In that case:

1. Search the target platform for creators in the campaign niche.
2. Find verified public contact information (email in bio, linktree, or business email).
3. Return only real, verifiable creator profiles.

## Rules (both paths)

- Candidates without a verified email are not outreach-ready and are filtered by the backend.
- Only provider-backed or research-verified metrics are stored.
- `profile_data` preserves the source response.
- Do not use example.com contacts, synthetic metrics, fallback rosters, or guessed handles.

After discovery, mark the Hermes task complete with `argo_complete_task` including
the deal IDs in the result, or fail it with `argo_fail_task`.
