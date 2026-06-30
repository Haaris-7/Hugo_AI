---
name: argo-performance-learning
description: Convert a closed Hugo campaign's evidence into validated memory and procedural skill improvements.
---

# Hugo Performance Learning

1. Call `argo_begin_learning` with the run ID.
2. Compare prediction, actual cost per result, revisions, negotiation, human overrides, and failures.
3. Update only generalized, anonymized knowledge. Never store credentials, creator PII, brand names,
   or transactional payment facts in memory or skills.
4. Patch only an `argo-*` skill or its `references/learned-heuristics.md` file. Prefer a no-op when
   evidence is weak or contradictory.
5. Call `argo_commit_learning` with the summary, heuristic, skill name, and exact evidence IDs.

After the learning cycle, mark the Hermes task complete with `argo_complete_task`,
or fail it with `argo_fail_task` if the learning raises an error.
