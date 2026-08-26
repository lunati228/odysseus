# Privacy Workspace backlog

These items block stronger privacy claims. Privacy Workspace is not approved for sensitive research until the applicable items are closed on the installed revision.

| ID | Priority | Missing evidence | Exit condition |
| --- | --- | --- | --- |
| PRV-003 | Critical | The Python egress guard does not prove complete OS-level containment for service, native, child-process, or loopback-forwarder traffic. | Re-audit destinations and capture DNS and TCP behavior during startup, idle, chat, research, failure, and shutdown. |
| PRV-005 / PRV-012 | High | Source controls logs and caches, but current residue behavior is unproven. | Use canary data to confirm that logs, temporary and crash paths, notifications, telemetry, Standard storage, and exports contain no private content. |
| PRV-006 | High | Untrusted-page framing and tool limits are source-tested, not accepted against hostile pages with live models. | Show that hostile pages cannot escalate tools or exfiltrate query-derived data. |
| PRV-007 / PRV-008 | High | The private data root has no accepted encryption-at-rest decision or verified access-control recovery procedure. | Document the storage decision, verify intended access, and test recovery. |
| PRV-009 | High | Endpoint validation exists, but traffic at every model, provider, and helper boundary has not been fully accepted. | Run adversarial endpoint tests and capture traffic to prove only intended numeric-loopback model traffic. |
| PRV-013 | Medium | Browser cookies, storage, service workers, and data separation have not been live-accepted across the two profiles. | Show that the two origins do not share cookies, chats, storage, uploads, memory, or CSRF state. |
| PRV-014 / PRV-015 | Medium | Tor stream isolation and the HTTPS-only exception policy need explicit decisions. | Document the policy and add tests that prevent credential exposure and silent HTTP downgrade. |
| PRV-017 | Conditional | Attribution is required if reference code is copied later. | Add the required acknowledgments and source headers before committing copied material. |
| PRV-020 | Critical | No independent security review has been completed. | Obtain a fresh review after the evidence above stabilizes, resolve findings, and rerun the affected checks. |

## Change rule

Do not close a gate because a unit test passes. Each gate needs the listed evidence on the installed revision. Reopen affected items after changes to providers, extensions, browser behavior, speech or embedding services, model management, tool authority, or upstream integration.
