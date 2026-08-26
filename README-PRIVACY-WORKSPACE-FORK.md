# Privacy Workspace fork

## Status

This branch adds Privacy Workspace to Odysseus. It is a separate local profile for chat and web research with tighter data, tool, and network boundaries than Standard Workspace.

Privacy Workspace is not privacy-certified and is not approved for sensitive research. The open evidence requirements are in [BACKLOG-PRIVACY-WORKSPACE-FORK.md](BACKLOG-PRIVACY-WORKSPACE-FORK.md).

## What this fork changes

| Area | Privacy Workspace behavior |
| --- | --- |
| Process and browser origin | Runs as a separate process with its own origin, session cookie, and startup configuration. Switching a running process is not supported. |
| Storage | Uses a separate data root outside the repository. Chats, uploads, memory, caches, and sessions are not shared with Standard Workspace. |
| Web research | Sends search and page fetches through the configured local Tor SOCKS endpoint with remote DNS. A failed request does not retry through a direct client. |
| Browser recovery | After Tor fails, Deep Research may make one managed Brave plus Windscribe browser navigation. The browser path is checked again before use and has no direct-client fallback. |
| Research limits | The operator may remove the fixed agent and research count ceiling. Query, page, evidence, and tool limits still apply. |
| Model traffic | Accepts only explicit numeric loopback model endpoints. Cloud model fallbacks and hosted model services are unavailable in Privacy Workspace. |
| Tool authority | Allows only the narrow privacy tool set. Commands and file changes require a fresh, exact approval. |
| Local model control | Adds managed local-model start, stop, switch, readiness, context, and Qwen reasoning-level controls. |
| Attachments | Uses advertised local-model modalities for image input and refuses raw binary file reads. |
| Data residue | Disables private search analytics and page disk caches, and starts logging with privacy sanitization. |

## Design decisions

### Separate process, not runtime switch

Odysseus selects database, cache, configuration, provider, and startup state during import. Changing a setting after startup could leave Standard Workspace resources active while the interface says Privacy. Privacy Workspace starts with its restricted environment or refuses to start.

The separate origin helps browser isolation, but ports alone do not isolate cookies. The privacy profile therefore uses a distinct session cookie and does not migrate workspace state between profiles.

### Tor-first research path

The privacy transport uses `socks5h` so Tor resolves the target hostname. It requires HTTPS, validates every initial and redirected URL, rejects local and reserved destinations, limits redirects and response size, and does not inherit proxy settings from the environment. A Tor failure returns a failure result rather than falling back to direct HTTP.

The managed Brave plus Windscribe path is only a recovery path for one navigation after Tor fails. It is not general browser authority and it is not proof of browser-level isolation.

### Narrow authority

Privacy Workspace permits Tor-routed search and fetch, a numeric-loopback local model, private storage, and workspace-confined read-only coding tools. It denies cloud models, API-key search, hosted embeddings and speech, arbitrary network MCP, webhooks, account sync, downloads, extensions, background automation, telemetry, search analytics, and page disk caches.

An approved shell command can create network traffic outside the research route. Treat command approval as a separate security decision.

### Egress and endpoint controls

The privacy profile installs a Python egress guard that denies non-loopback socket and DNS traffic. This protects ordinary Python networking paths while keeping the local Tor proxy available. The model and paired workspace endpoints must use numeric `127.0.0.1` addresses with explicit ports.

The guard is a backstop, not OS-level containment. Native extensions, child processes, and a locally listening proxy can sit outside its coverage. See the backlog before making stronger claims.

## Implementation map

The main implementation lives in:

- `src/privacy_mode.py`, `src/privacy_policy.py`, `src/privacy_egress.py`, `src/privacy_logging.py`, and `src/privacy_routes.py`
- `services/search/privacy_transport.py`, `services/search/privacy_search.py`, and `services/search/privacy_browser.py`
- `src/local_model_lifecycle.py` and `src/qwen_reasoning.py`
- `static/js/privacyWorkspace.js` and the affected UI, route, agent, and research modules

Focused tests cover profile isolation, Tor remote DNS and fail-closed behavior, URL and redirect validation, egress denial, route and tool policy, storage and logging behavior, browser fallback, local-model endpoints and lifecycle, Qwen reasoning levels, timezone redaction, and workspace UI behavior.

## Recorded verification and milestones

These are development records, not a current certification. Re-run the relevant checks after upstream changes or environment changes.

| Date | Recorded result |
| --- | --- |
| 2026-07-30 | Implemented the initial profile boundary, Tor-only research transport, private manager lifecycle, and live Tor and failure checks. |
| 2026-08-13 | Added egress policy, route and storage guards, logging and cache controls, and deep-research limits. |
| 2026-08-23 to 2026-08-24 | Added local-model lifecycle and Qwen reasoning controls, model endpoint checks, and the managed browser fallback. |
| 2026-08-24 | Integrated the then-current upstream `dev` tree while retaining the privacy routing and policy changes. |
| 2026-08-25 | Added workspace-confined reads, exact approval for commands and file changes, vision attachment controls, live context display, timezone redaction, and the compact workspace control. |
| 2026-08-25 | Added on-demand startup and paired stop behavior for the local runtime. |

Recorded test evidence includes a 773-pass post-integration run with one intentional skip and 88 JavaScript subtests, a 525-pass run for fork-changed files, a 430-pass affected release slice, and 95 focused runtime and profile checks. The development record also includes live checks for local models, Tor failure behavior, model switching, and the managed browser fallback.

## Limits and claim boundary

Privacy Workspace does not establish whole-process or OS-level egress containment, encrypted-at-rest storage, protection from same-user malware, absence of browser or crash residue, independent security review, anonymity, or invisibility. A local model and Tor-routed research reduce some exposure; they do not make the complete application private.

Do not describe this fork as anonymous, fully private, or safe for sensitive research until the relevant items in [BACKLOG-PRIVACY-WORKSPACE-FORK.md](BACKLOG-PRIVACY-WORKSPACE-FORK.md) are closed on the installed revision.

## Maintaining this fork

Compare changes with `upstream/dev`, preserve the import-time profile boundary, run the affected privacy and regression tests, and update this overview and the backlog when the security boundary or its evidence changes.
