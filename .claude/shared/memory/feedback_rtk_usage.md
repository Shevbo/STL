---
name: feedback-rtk-usage
description: "RTK must be used for ALL shell commands — strict rule, no exceptions"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f41e1ea9-c79d-4c57-8e12-cbfce1e2195f
---

Always prefix shell commands with `rtk` — e.g. `rtk git status`, `rtk ls`, `rtk git commit`.

**Why:** RTK is a token-optimizing proxy (saves 60-90% tokens). User stated explicitly this is a STRICT RULE, not a suggestion.

**How to apply:** Every Bash tool call that runs git, ls, grep, or similar CLI commands must use `rtk <command>`. No exceptions. The hook may auto-rewrite, but use the prefix explicitly anyway.
