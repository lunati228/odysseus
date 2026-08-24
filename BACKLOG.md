# Privacy Workspace active gates

This is the canonical next-work list. A completed feature belongs in
[PROGRESS.md](PROGRESS.md), not here. Until the security gates below close,
Privacy Workspace is not approved for sensitive research.

## Session wrap-up

No delivery item remains from the 2026-08-24 model/runtime work. The accepted
machine-local model profile and its measured evidence are stored only in an
ignored local handoff; public documentation records the behavioral contract,
not the operator's hardware or paths. The disposable audit account was reset
to first-run state. Only the privacy-acceptance gates below remain open.

## Privacy acceptance gates

| ID | Priority | Open condition | Exit evidence |
| --- | --- | --- | --- |
| PRV-003 | Critical | The Python policy/backstop does not prove complete OS-level containment of service, native, child-process, or loopback-forwarder egress. | Re-audit classifications and capture DNS/TCP behavior for startup, idle, chat, research, failure, and shutdown on the exact installed revision. |
| PRV-005 / PRV-012 | High | Source blocks analytics/cache and sanitizes logs, but current-source residue behavior is unproven. | Canary prompts/queries must be absent from logs, temp/crash paths, notifications, telemetry, Standard data, and automatic exports. |
| PRV-006 | High | Untrusted page framing and tool limits are source-tested, not adversarially accepted with live models. | Hostile-page tests prove no tool escalation or query-based exfiltration. |
| PRV-007 / PRV-008 | High | The vault is not independently encrypted and historical setup ACL cleanup remains to be verified. | An accepted encryption-at-rest/threat decision and tested recovery procedure; verify only the intended identities retain vault access. |
| PRV-009 | High | Loopback endpoint validation is implemented, but all model/provider/helper boundaries and observed traffic are not fully accepted. | Adversarial endpoint tests plus traffic capture prove only intended numeric loopback model traffic. |
| PRV-013 | Medium | Cross-profile browser storage/cookie/service-worker isolation is not live-accepted. | Browser proof that ports 7000/7001 do not share cookies, chats, storage, uploads, memory, or CSRF state. |
| PRV-014 / PRV-015 | Medium | Tor stream-isolation policy and the HTTPS-only exception policy need an explicit decision. | Documented policy and tests; credentials must never enter status or logs, and no silent HTTP downgrade is possible. |
| PRV-017 | Conditional | Attribution is required if reference code is copied later. | Update acknowledgments and relevant source headers before committing copied code. |
| PRV-020 | Critical | No completed independent security review exists. | Fresh-context review after the above evidence stabilizes; resolve findings and re-run tests. |

## Completed or archival items

PRV-001, PRV-002, PRV-004, PRV-010, PRV-011, PRV-016, PRV-021, and PRV-023
have recorded implementation or historical live evidence in PROGRESS.md.
They do not close the broader certification gate. The old detached-baseline
test issue (PRV-022) is archival: current fork-suite evidence should be
re-established whenever upstream is merged or the environment changes.

## Non-negotiable release rule

Do not describe the workspace as anonymous, fully private, or safe for
sensitive research until every applicable privacy gate is closed on the exact
installed revision. Route-level Tor success and local-model operation are
necessary evidence, not sufficient proof.
