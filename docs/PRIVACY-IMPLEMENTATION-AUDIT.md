# Historical Privacy Workspace manager audit

Status: superseded as a current specification, retained for provenance.

This was the pre-implementation audit of the private PowerShell manager. Its
durable conclusions are now implemented and summarized in the current fork
documents:

- manager actions must be allowlisted and never expose secret paths, hashes, or
  raw subprocess output;
- lifecycle operations must verify executable identity, process start time, and
  the intended loopback listener before stopping a process;
- Tor readiness needs bootstrap and route proof, not only a listening port;
- private status responses and logs must be redacted; and
- private data/log paths must stay below the private vault.

The source and upstream baseline changed substantially after this audit. Use
[README-FORK.md](../README-FORK.md) and
[docs/PRIVACY-MODE-RESEARCH.md](PRIVACY-MODE-RESEARCH.md) for the current
contract, [PROGRESS.md](../PROGRESS.md) for observed evidence, and
[BACKLOG.md](../BACKLOG.md) for remaining gates.
