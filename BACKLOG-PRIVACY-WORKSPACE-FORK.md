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
| PRV-016 | High | Automatic browser recovery exists only in Deep Research. Chat context prefetch stops after the Tor path; the agent loop only exposes a browser MCP tool after an agent Tor web-tool error, or for route-classified browser intent. | Make an explicit product decision. If automatic recovery is required in those modes, add a per-mode runtime handoff and end-to-end tests for Tor-first order, browser invocation, and no direct retry. |
| PRV-017 | Conditional | Attribution is required if reference code is copied later. | Add the required acknowledgments and source headers before committing copied material. |
| PRV-018 | Critical | Agent browser MCP calls bypass Deep Research's per-call managed-browser validation. The in-repository startup path does not require a proxy; its launcher permits an unproxied browser when the proxy-required setting is absent. | Enforce role, mandatory proxy, isolation, and validated launch configuration in the privacy startup and agent-dispatch paths; prove misconfiguration cannot start browser egress, then capture a real browser route. |
| PRV-019 | Medium | Browser-URL cold start and multiple open-tab accounting are accepted, but the final installed streaming build has not completed a real close-tab and close-browser shutdown run. | Keep one Privacy tab open in the background and prove the runtime stays up; close one of multiple tabs and prove it stays up; close the last tab and the whole browser separately and prove only manager-owned app, model, and Tor processes stop within five minutes while the tiny wake helper re-arms. |
| PRV-020 | Critical | No independent security review has been completed. | Obtain a fresh review after the evidence above stabilizes, resolve findings, and rerun the affected checks. |

## Change rule

Do not close a gate because a unit test passes. Each gate needs the listed evidence on the installed revision. Reopen affected items after changes to providers, extensions, browser behavior, speech or embedding services, model management, tool authority, or upstream integration.
