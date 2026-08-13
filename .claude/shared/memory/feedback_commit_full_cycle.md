---
name: feedback_commit_full_cycle
description: "STRICT: 'commit'/'коммить' means the FULL release cycle, not just git commit; execute end-to-end without asking molecule-level confirmations"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7525bce9-2c09-4975-8992-6c434c6ffd84
  modified: 2026-07-21T07:29:53.700Z
---

When the operator says "коммить" / "commit" / "закоммить деплой", they mean the
WHOLE release cycle, not a bare `git commit`:

1. Deploy the artifacts (frontend scp dist, agent publish, service restart if needed).
2. `git commit` the source.
3. `git push` to the canonical remote.
4. "накати" — pull/apply on the hoster so its source tree matches.
5. Update federation documentation (the `onboarding` skill: pull canon, write the
   project snapshot, register with Klod, ensure the portal card).
6. Run the agent's own `/init` / onboarding refresh.
7. Project hygiene: `graphify update .` (CLAUDE.md rule after code changes), and any
   other standing post-change steps.

**Why:** the operator runs a live multi-box trading system; "commit" to them is the
act of making a change REAL and traceable everywhere, not one git verb. Leaving it
at a local commit is an unfinished job.

**How to apply:** do NOT ask permission five times "по молекуле" for each step. Batch
the independent steps, pick sensible defaults, execute the full cycle, and report what
was done at the end. Only stop to ask when a step is genuinely irreversible or
ambiguous in a way defaults cannot resolve (e.g. merging a feature branch to main when
that changes what production tracks). See [[feedback_deploy_vds_safety]]
[[feedback_secrets_protocol]] [[project_quik_agent_phase1]].
