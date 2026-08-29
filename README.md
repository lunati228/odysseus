<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="website/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Odysseus Privacy Workspace

This repository keeps the standard Odysseus workspace and adds a separate,
local-first Privacy Workspace for chat and web research. The goal is to preserve
more of the privacy benefit of local models when a task also needs the web:
running a model locally can keep the conversation on the machine, but ordinary
web searches still expose queries through the normal network path.

Privacy Workspace runs as its own process with a separate browser origin,
session cookie, startup configuration, and data root. Chats, uploads, memory,
caches, and sessions are not shared with Standard Workspace. Model traffic is
limited to explicit numeric loopback endpoints, and the workspace adds controls
for starting, stopping, and switching local models, viewing the active context,
and changing supported Qwen reasoning levels.

Built-in privacy search, page retrieval, and Deep Research are Tor-first. Tor
routes traffic through multiple relays before it reaches the destination,
making the original connection harder for the site to trace directly. The Tor
client uses the configured local SOCKS endpoint with remote DNS, validates the
initial URL and every redirect, bounds redirects and response sizes, and returns
a failure instead of retrying through a direct HTTP client. This fail-closed rule
does not cover approved shell commands or the managed browser path.

A separately configured managed Brave browser can provide a second path. The
intended deployment routes it through Windscribe; Deep Research revalidates that
role, proxy, isolation, and launch configuration before each call, while agent
MCP use relies on startup configuration:

- **Agent chat** makes the browser MCP tools available to the model after an
  agent Tor web-tool failure, or earlier when the request is classified as
  browser-oriented. The model must still decide to call the browser tool.
- **Deep Research** automatically attempts one managed-browser navigation for
  each Tor search without a usable result and each Tor fetch without readable
  content. Recovery happens per failed search or page, so one blocked site does
  not have to stop the entire research run.

The fork also narrows tool authority, isolates storage, reduces logging and disk
caching, and adds a Python egress guard around the privacy runtime. The result is
two workspaces that can run side by side: Standard Workspace for normal use and
Privacy Workspace for local-model workflows with a separate Tor- and
browser-based research stack.

Privacy Workspace is experimental, not privacy-certified, and not approved for
sensitive research. Agent-browser VPN routing is externally configured and is
not code-proven fail-closed, and the Python egress guard is not OS-level
containment. See the [technical fork overview](README-PRIVACY-WORKSPACE-FORK.md)
and [open verification gates](BACKLOG-PRIVACY-WORKSPACE-FORK.md) for the exact
boundary, implementation map, recorded milestones, and remaining work.

## Quick Start

### Privacy Workspace checkout

```bash
git clone --branch feature/privacy-workspace https://github.com/lunati228/odysseus.git
cd odysseus
cp .env.example .env
```

This checks out the fork; it does not activate the privacy boundary by itself.
A Privacy Workspace deployment must run as a separate process and configure a
separate data root, numeric-loopback model endpoint, and local Tor SOCKS
endpoint. The optional browser fallback requires its own managed configuration.
The repository does not currently provide a one-command installer for that local
runtime. Read the [technical fork overview](README-PRIVACY-WORKSPACE-FORK.md) and
its [open verification gates](BACKLOG-PRIVACY-WORKSPACE-FORK.md) before running
it.

### Standard Workspace

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/odysseus-dev/odysseus/tree/main) if you want the more curated branch.

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](website/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Demo

A full hover-to-play tour lives on the [Odysseus landing page](https://odysseus-dev.github.io/odysseus/). Its source lives under [`website/`](website/).

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.

Deployment details are in the [setup guide](website/setup.md#security-notes).

## Star History

<a href="https://star-history.dera.page/#odysseus-dev/odysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
