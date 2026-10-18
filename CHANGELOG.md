# Changelog

## Unreleased

Hooks fail open: `worklog hook` never exits 2 (which hosts read as "block"); store,
input and internal errors become `{}` plus a stderr line, with a `systemMessage` at
SessionStart/UserPromptSubmit. `stop_hook_active` and non-mutating PreToolUse calls
return before any store access; payloads over 1 MiB are drained and ignored. Generated
hook commands use `python -P`, and PreToolUse gets a matcher for potentially mutating
tools. The Stop reminder now fires once per dirty episode. `worklog init` writes
`.worklog/.gitignore` for plain-directory stores. Rerun `worklog install` and `worklog init`
(also after `worklog migrate --apply`).

Context starts with the header, session, claim instruction and a one-line index of every
card. Claude Code caps hook context at 10,000 characters (2,000-character preview beyond
it), so when the full render exceeds ~9,500 characters the Claude hook keeps the index,
overview and landmarks but only your own card in full, pointing to `worklog context --card ID`;
Codex and `worklog context` keep the full render. A CLI write with `--session` (now also
accepted by `add`, `note`, `overview`) marks its own result as seen unless another session
wrote first, so the next prompt does not re-send it; receipts gain
`previous_workspace_revision`. SessionEnd and the end of `worklog launch` release the
session's card (`working` becomes `needs_check`), except for a pinned identity ending with
`clear`/`resume`. Claim conflicts name the blocking card and owner and print the exact
takeover command; a separate worktree is suggested only inside Git.

Packaging: SPDX `license = "MIT"` with `license-files` (build needs setuptools>=77),
Python 3.11–3.13 classifiers, and the version single-sourced from `worklog.__version__`.
CI also gates coverage (subprocesses counted), runs a short soak and smoke-tests the built
wheel. Generated 0.2.0 evidence moved to `docs/evidence/0.2.0/` and is no longer in the sdist.

## 0.2.0 — Frontier (breaking)

Replace the event-sourced worklog with one bounded current snapshot. Add GLOBAL
historical slots 1/2/3, card-level CAS, in-place updates, explicit removal on finish,
fixed temp publication, bounded disposable Hook flags, and offline destructive
migration. Remove event replay from normal operations; remove session/attempt/
checkpoint/handoff/evidence archives, artifacts, verifier, job registry, leases,
heartbeat launcher, and idempotency history. Context reads no longer write events,
and context no longer loses active cards via tail truncation. Reinstall adapters.

## 0.1.0

Initial immutable-ledger implementation. Superseded for the frontier-only use case;
not included as a runtime compatibility mode.
