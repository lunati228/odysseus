# Privacy Workspace progress

This is the compact evidence ledger. [README-FORK.md](README-FORK.md) is the
current operating reference; [BACKLOG.md](BACKLOG.md) is the only active
next-work list. Historical evidence is retained only where it changes a future
decision.

## Major milestones

| Date | Revision or evidence | Durable result |
| --- | --- | --- |
| 2026-07-30 | initial installed-build run | Implemented profile isolation, Tor-only research transport, private manager lifecycle, and initial live Tor/failure checks without sensitive data. |
| 2026-08-13 | policy-hardening checkpoint | Added the egress policy/backstop, route and storage guards, privacy logging/cache controls, and deep-research tool limits. The focused checkpoint recorded 218 passes and 1 inherited SQLAlchemy warning. |
| 2026-08-21 | evidence-boundary review | Reclassified historical versus current-source evidence so installed-build results were not mistaken for deployment or certification. |
| 2026-08-23–24 | local-model integration | Added Qwen reasoning control, safe installed-model picker/lifecycle, active-model prompt identity, built-in browser fallback, bounded fallback retries, and manager/lifecycle guards. |
| 2026-08-24 | upstream integration | Merged the current upstream/dev tree and preserved the fork's privacy routing and policy changes. |
| 2026-08-24 | installed-runtime acceptance | Accepted a machine-local Qwen profile with xhigh reasoning and GPU-resident execution. Exact model, cache, context, device, memory, and benchmark details are retained only in an ignored local handoff. |
| 2026-08-24 | live workflow acceptance | Gemma and Qwen each passed identity, manager/UI lifecycle, Tor search, and exact-approved Brave + Windscribe navigation. Qwen also passed low/medium/xhigh persistence and restart checks. |

## Verification boundary at this handoff

- The broad post-merge regression run recorded 773 passes, 1 intentional skip,
  1 inherited SQLAlchemy warning, and 88 JavaScript subtests in 42.28 seconds.
  The final run restricted to files changed by the fork recorded 525 passes,
  the same skip/warning, and all 88 JavaScript subtests in 8.45 seconds.
- Focused runtime/profile coverage separately recorded 95 passes. The exact
  installed-profile assertion passed after the local profile was persisted.
- Tor was directly attested; a dead-Tor check failed closed without direct
  retry. Brave's publisher signature and the Windscribe proxy, DNS, WebRTC,
  and isolation launch flags were checked before live use.
- The temporary audit account was archived, its plaintext credential removed,
  and Privacy Workspace restarted with `configured=false` and
  `authenticated=false`. The original pre-audit backup remains untouched.

No source-test result establishes every egress surface, browser isolation,
storage residue, cryptographic protection, or privacy certification.

## Local model acceptance boundary

- Machine-specific model paths, quantization, context/cache sizing, device
  placement, memory headroom, and throughput are private operational data and
  are not recorded in tracked files.
- Qwen xhigh execution completed both short and long-context acceptance checks
  without a manager-imposed output-token ceiling.
- Final Qwen agent workflows passed Tor-only `web_search` and exact-approved
  Brave navigation to Example Domain with no terminal errors.

Do not use Qwen 3.8 as a delegated agent. It is the installed user model; use
another owner-authorized provider for delegated engineering or research work.

## Evidence rules

- Record the commit, test/run scope, and observed outcome; do not promote agent
  reports or historical installs into current runtime evidence.
- A feature test closes only that feature's claim. Privacy certification needs
  the acceptance gates in BACKLOG.md on the exact installed revision.
- Keep secrets, account data, prompts, query content, private model paths, and
  raw manager output out of the ledger. Public integrity hashes belong only in
  audited supply-chain configuration/tests.

The policy-hardening checkpoint and the prior manager audit are preserved as
short historical pointers, not as a second status system.
