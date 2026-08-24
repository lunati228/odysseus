# Privacy Workspace security design

This is the fork's durable design contract, not a research transcript. Current
feature evidence is in [PROGRESS.md](../PROGRESS.md); open acceptance work is
in [BACKLOG.md](../BACKLOG.md).

## Decision

Privacy Workspace is a separate process, not a runtime toggle. Odysseus binds
database, cache, configuration, provider, and startup state during import.
Changing an environment variable or browser control after startup could leave
standard resources alive while the UI says Privacy. A private process either
starts with its full restricted environment or refuses to start.

| Concern | Standard Workspace | Privacy Workspace |
| --- | --- | --- |
| Origin | http://127.0.0.1:7000 | http://127.0.0.1:7001 |
| Data | standard data root | separate off-repository privacy-vault |
| Session | standard cookie name | distinct privacy cookie name |
| Web research | normal product policy | Tor SOCKS, remote DNS, fail closed |
| Model traffic | normal product policy | explicit numeric-loopback endpoint only |

The separate port gives browser origin isolation, but cookies are not isolated
by port alone; the distinct cookie name is mandatory. Switching does not
migrate chats, accounts, uploads, memory, sessions, or storage.

## Privacy-mode authority

The allowed authority is intentionally small:

- Tor-routed search and bounded page fetch;
- a local model at numeric 127.0.0.1 with an explicit port; and
- storage confined below the private vault.

Cloud models and fallbacks, API-key search, hosted embeddings/speech, network
MCP, shell, webhooks, account sync, cookbook/download activity, extensions,
background automation, telemetry, plaintext search analytics, and disk page
caches are denied in Privacy Workspace.

A built-in Brave + Windscribe browser fallback may be used only after Tor
failure and an exact-action approval. It is a recovery mechanism for a
specific request, not a grant of general browser/network authority.

## Tor research transport

Privacy search and fetch must:

- use only the configured socks5h://127.0.0.1 endpoint with remote target DNS
  and trust_env disabled;
- require Tor bootstrap/readiness, then fail closed if Tor is missing,
  misconfigured, or fails mid-request;
- use HTTPS, validate every target and redirect before connecting, refuse
  localhost/private/reserved/IP-literal/credentialed targets, and bound
  redirects, content type, bytes, and time;
- never retry a failed private request through a direct client; and
- treat returned pages as untrusted evidence, never as instructions or a source
  of additional tool authority.

The canonical offline proof includes a fake SOCKS server that sees a hostname
request and a guard showing zero local target DNS. A listening SOCKS port alone
is not a Tor proof.

When a full-tunnel VPN is enabled, the expected route is:

~~~text
Odysseus -> local Tor SOCKS -> VPN tunnel -> Tor network -> HTTPS destination
~~~

This reduces casual IP-to-query linkage. It does not hide timing, defeat a
global observer, make rare queries anonymous, or stop search providers/sites
from logging their own interaction.

## Model, logs, and data invariants

- Model and paired-workspace URLs must be numeric 127.0.0.1 with an explicit
  port; hostname, LAN, cloud, credentialed, query-bearing, and fragment-bearing
  URLs are invalid.
- The private data root must be absolute, outside the Git repository, and
  contain configured database/cache paths. Privacy mode must not read the
  repository environment file.
- Logging starts with a privacy sanitizer; private search analytics and page
  disk cache fail closed. This is not a completed residue audit.
- The manager owns Tor/app/model processes and must verify executable, start
  time, and intended loopback listener before stopping one.
- Use verified/pinned supply-chain inputs. Re-check publisher, license,
  signatures, hashes, and the existing installation before an update; do not
  execute reference repositories merely to inspect them.

## Explicit non-claims

The design does not currently establish:

- whole-process or OS-level egress containment;
- encrypted-at-rest vault contents or protection from same-user malware;
- live browser-state isolation or absence of temp/crash/notification residue;
- independent security review; or
- anonymity, invisibility, or a safe basis for sensitive research.

## Acceptance contract

Before a stronger privacy claim, the exact installed revision must pass:

1. loopback listener and model endpoint checks;
2. fake-SOCKS/remote-DNS, Tor-bootstrap, Tor-kill, redirect, and direct-fallback
   tests;
3. live DNS/TCP capture across startup, idle, chat, research, failure, and
   shutdown;
4. canary searches for data in logs, crash/temp paths, telemetry, and Standard
   storage;
5. cross-profile browser/data isolation and hostile-page/tool-containment tests;
6. storage/ACL/encryption recovery checks; and
7. an independent security review after implementation and evidence stabilize.

Re-run the relevant acceptance evidence after upstream merges or any change to
providers, extensions, browser behavior, speech/embedding, model management,
or tool authority.
