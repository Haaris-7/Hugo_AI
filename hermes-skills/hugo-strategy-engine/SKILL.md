---
name: hugo-strategy-engine
description: Design a budget-safe creator campaign using Hugo playbooks, priors, and reputation evidence.
---

# Hugo Strategy Engine

Call `hugo_generate_strategy` with the campaign ID. The backend fetches the latest
algorithm playbook, strategy priors, and learned heuristics, then returns a
budget-constrained strategy with creator tier, target rate, rationale, and
experiment allocation.

Read the campaign through `hugo_get_campaign` first to understand context.
Recommend only `nano`, `micro`, or `mid` creator tiers. Treat all model output as
a proposal: creator count multiplied by target rate must fit the campaign budget and
each rate must respect the per-creator cap.

Prefer an 80/20 primary/challenger experiment only when at least four creators and
$1,000 of budget are available. Cite playbook and strategy-prior evidence in the
rationale. Never invent creator metrics or payment state.

After strategy generation, use `hugo_complete_task` if this was a Hermes cron task,
or `hugo_fail_task` if the generation raised an error.

See [learned heuristics](references/learned-heuristics.md) before choosing a strategy.
