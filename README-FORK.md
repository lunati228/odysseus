# Odysseus Privacy Workspace fork

- Branch: feature/privacy-workspace
- Upstream integration baseline: the current upstream/dev tree
- Canonical documents: this overview, [progress](PROGRESS.md), [active gates](BACKLOG.md), and the [security design](docs/PRIVACY-MODE-RESEARCH.md)

## Scope and current boundary

This fork adds a separate Privacy Workspace for local chat and web research. It
keeps Standard Workspace behavior intact while placing Privacy Workspace in its
own process, origin, data root, session namespace, and Tor-routed research
path.

It is **not privacy-certified** and is not approved for sensitive research.
Local inference, Tor routing for the research path, and a separate vault do not
prove whole-application containment, anonymity, encrypted-at-rest storage, or
protection from same-user malware.

## What is currently implemented

| Area | Current evidence | Important boundary |
| --- | --- | --- |
| Separate profiles | Standard and Privacy are selected before application imports; Privacy data and SQLite paths must be absolute, off-repository, and confined below the private root. | Switching a running process cannot safely change profile. |
| Workspace switch | Both login and app UI expose a fail-closed Standard/Privacy switch. Navigation carries no chat or session state. | Restart an old process after an update if the switch reports an unavailable counterpart. |
| Research transport | Privacy search and page fetch use the local Tor SOCKS endpoint with remote DNS and no direct retry. | The wider application still has unproven egress surfaces; see PRV-003. |
| Privacy authority | The profile allows Tor search/fetch, a numeric-loopback local model, and private storage; cloud providers, network MCP, shell, webhooks, sync, downloads, and background automation are denied. | Browser fallback is a narrow exception, not general browser authority. |
| Local model control | The picker switches only registered local models through the external manager, verifies loopback readiness, and redacts paths and hashes. | Qwen is the model under test, never a delegated coding/research agent unless the owner explicitly changes that rule. |
| Browser fallback | Built-in Brave + Windscribe fallback is offered after Tor failure and requires an exact-action approval. | It remains subject to the browser/isolation acceptance gate. |

The broad post-upstream-merge regression run recorded 773 passes, 1 intentional
skip, 1 inherited SQLAlchemy warning, and 88 JavaScript subtests. The final
fork-changed-file run recorded 525 passes with the same skip/warning and all 88
JavaScript subtests. Live installed checks covered both local models, Tor,
Brave + Windscribe fallback, model switching, and every Qwen reasoning level.
These results are feature evidence, not a privacy-certification claim.

## Private local-model runtime

The external manager owns model files, context/cache sizing, accelerator
placement, multimodal projection, sampling, warmup, and lifecycle safeguards.
Those machine-specific paths, hardware details, memory snapshots, and
benchmarks are intentionally kept in an ignored local handoff rather than this
public repository.

The durable public contract is:

- registered local models are reached only through the numeric-loopback model
  endpoint;
- Qwen reasoning levels are low, medium, and xhigh;
- the manager may leave prediction length uncapped, while generation still
  ends at EOS or the remaining total context;
- paths come from `ODYSSEUS_PRIVATE_HOME`, the manager-owned data root, or the
  two explicit local-model path environment variables; and
- changing quantization, context, device placement, or private model files is
  an operator-local decision and must not be copied into public documentation.

## Operating facts

The dedicated PowerShell manager is the process owner:

~~~powershell
$manager = Join-Path $env:ODYSSEUS_PRIVATE_HOME "bin\Odysseus-Private.ps1"
& $manager -Action start-private
& $manager -Action status
~~~

Use the manager's allowlisted model actions or the UI picker; do not start
llama.cpp manually over a managed instance. It owns safe stop/start and
listener identity checks.

| Service | Expected local endpoint |
| --- | --- |
| Standard Workspace | http://127.0.0.1:7000 |
| Privacy Workspace | http://127.0.0.1:7001 |
| Local llama.cpp | http://127.0.0.1:18085/v1 |
| Tor SOCKS | socks5h://127.0.0.1:19050 |

Privacy-mode paired-workspace and model endpoints must be numeric 127.0.0.1
with an explicit port. Do not bind Privacy Workspace, its model server, or Tor
to LAN/wildcard addresses. Private data belongs under the installed
privacy-vault, never in this repository, its logs, or a committed environment
file.

## Durable security invariants

- Keep Standard and Privacy as separate processes. Profile-sensitive database,
  cache, provider, and startup state is chosen at import time.
- Keep private web search/fetch on socks5h Tor with remote DNS, HTTPS, strict
  URL and redirect validation, size/time bounds, and no direct fallback.
- Treat retrieved pages as untrusted evidence. They cannot grant tools; query
  length and research-tool calls are bounded.
- Keep private local-model traffic on numeric loopback only. Do not re-enable
  cloud fallback, remote embeddings/speech, webhooks, account sync, downloads,
  shell, or network MCP in Privacy Workspace without a new security review.
- Preserve fail-closed behavior. A malformed status, missing Tor, invalid
  endpoint, or unavailable fallback should stop the operation rather than guess.
- Never put credentials, query text, prompts, private installed-model paths,
  vault data, or raw manager output in Git or documentation. Public integrity
  hashes belong only in audited supply-chain configuration/tests, never status
  output.

## Handoff map

| Need | Use |
| --- | --- |
| Current implementation and operational contract | This file |
| Dated evidence and completed milestones | [PROGRESS.md](PROGRESS.md) |
| Open security and session-wrap-up work | [BACKLOG.md](BACKLOG.md) |
| Threat model, invariants, and acceptance design | [docs/PRIVACY-MODE-RESEARCH.md](docs/PRIVACY-MODE-RESEARCH.md) |
| Historical 2026-08-13 checkpoint | [docs/ODYSSEY-COMPLETION-CHECKPOINT-2026-08-13.md](docs/ODYSSEY-COMPLETION-CHECKPOINT-2026-08-13.md) |
| Historical pre-implementation manager audit | [docs/PRIVACY-IMPLEMENTATION-AUDIT.md](docs/PRIVACY-IMPLEMENTATION-AUDIT.md) |

Before changing the fork, compare with upstream/dev, preserve the profile
boundary, run the privacy/local-model tests plus affected regressions, and
update the evidence and gate documents in the same change.
