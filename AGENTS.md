# AGENTS.md — orientation for agents (LLM or human) working in this repo

This is the public repo for the **Stroompeil HA addon**, a Home Assistant custom
integration (HACS-installable, `custom_components/stroompeil_ha_addon`). It connects
a Home Assistant instance to the Stroompeil HA Fleet Manager server over a
persistent outbound WebSocket: a status snapshot every 60s, allowlisted commands
pushed back down the same socket. The HA instance is always the client; it needs
no inbound ports, only outbound internet.

See the parent repo `stroompeil/ha-fleet-manager` (where this repo is consumed as
the `ha-addon/` submodule) for the full architecture and ADRs.

## Conventions

- **Commit authorship**: commits must be authored as `stroompeil`
  (`user.name=stroompeil`, `user.email=bot@stroompeil.nl`), not a personal
  account, so the public history reads as coming from the project (ADR 0016).
- **Default development branch is `dev`**; autonomous agent sessions push a
  feature branch and open a PR against `dev`. Do not push to or merge `dev`
  directly — the PR review is the operator's inspection point.
- **Open PRs as ready-for-review (non-draft), not drafts.** Agents capable of
  creating non-draft PRs should do so immediately when delivering work; do not
  open a draft PR first and mark it ready later.
- **Comments**: minimal. Avoid standalone/inline comments; explain *why* in a
  commit message instead.
- **Commit messages**: short imperative subject, then a body.
- **Secrets**: never commit tokens or agent tokens; tests use throwaway tokens only.
- **Robustness**: never raise out of the integration or block the HA event loop;
  always reconnect with backoff on disconnect.
