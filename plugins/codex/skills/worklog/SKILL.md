---
name: worklog
description: Maintain a bounded current frontier across sessions, tools and accounts. At most three GLOBAL historical landmarks; no session journal.
---

# Worklog Frontier v0.2

The authoritative data is the CURRENT frontier, not a log. Use the installed
`worklog` CLI. Never edit its storage JSON directly or create your own session
logs/archives/backups next to it.

## Read and continue

At session start or after compaction, read `worklog context` (a hook may already
provide it). It starts with a one-line index of every card. Under Claude Code's hook
size cap it may show only your own card in full; read another with
`worklog context --card ID`. This read does not write any semantic state. A frontier card is a
workline, NOT a session. Continue a relevant existing card. Do not create a card
for every user message/session/account switch. Do not create cards for unrelated
conversation that is not workspace work.

Use the session ID injected by the hook or WORKLOG_SESSION_ID; never guess another
session's ID. Add `--session ID` to your CLI writes (add and note too) when the
environment lacks it; it also keeps the hook from re-sending your own changes.
A launcher provides the identity: `worklog launch --harness claude --account a -- claude`.
An account is an alias; Worklog does not sign in or copy credentials.

Before code edits, `worklog claim CARD --expect CARD_REV --session ID --harness claude`.
For new work: `worklog add short-id --title '...' --current '...' --next '...'`, then
claim it. One cooperating writer per checkout. For parallel code edits, use
separate Git worktrees sharing this frontier. This is coordination, not a sandbox.
Do not steal an existing owner's work. A blocked claim names the owner and prints
the exact `takeover` command; run it only after confirming the old writer has
stopped, then inspect real code and external jobs. Ending a session releases its
card as `needs_check` (a launched session keeps it across /clear and resume). That
does not mean its jobs stopped: whoever claims a `needs_check` card checks them first.

## Replace current state at meaningful progress

`worklog show CARD` returns its fresh revision token. Then:

```
worklog update CARD --expect CARD_REV --session ID \
  --current 'What is true now; distinguish checked from unverified' \
  --next 'The concrete next action' \
  --caution 'Only a still-valid warning' \
  --reference '/path/to/existing/log-or-artifact'
```

Supplied fields REPLACE the previous field. `--caution` replaces the whole list;
`--clear-cautions` removes resolved warnings. References behave the same way.
Unspecified fields are retained, so explicitly clear stale items. Never append a
chronological list of attempts inside current/next/overview/cautions/references.
Do not store transcripts, full test output, secrets, every failed command, or copied
artifacts. Store only references to existing files. Worklog does not execute tests
or certify completion. Describe what was actually checked and what remains unknown.

On conflict, re-read and reconcile; don't just substitute the newest revision into
an old overwrite. Different cards have independent revisions. `--expect` uses the
full token; no guessed, partial or incremented tokens.

At handoff use `worklog handoff CARD --expect CARD_REV --session ID --current '...' --next '...'`.
It updates that same card and releases its owner; it creates no handoff history.
The receiving session claims the same card with its new revision and checks reality.
Checkpoint during long work too: abrupt termination can lose progress since the last
semantic update. Hooks can't infer what your unfinished work means.

## Exactly three optional GLOBAL past landmarks

`worklog status` shows the WORKSPACE revision and slots 1,2,3. Use:

```
worklog note 1 --expect WORKSPACE_REV --text 'Past mistake/decision and the reusable lesson'
```

The slot is replaced, not appended. To clear it:
`worklog note 1 --expect WORKSPACE_REV --clear`.

These are three slots for the ENTIRE workspace, not per card or per session. Keep
only the most consequential mistakes, discoveries, or decisions that still help
future work. When all slots are occupied, deliberately choose one to replace or
merge into a concise lesson. Do not create a fourth item hidden inside a slot or
rename historical backlog as "current". Do not invent a landmark just to fill a slot.
A presently unresolved problem stays in its current card; it need not consume a
historical slot. Each landmark is at most 1 KiB, each card at most 4 KiB, the entire
serialized frontier at most 32 KiB. There are at most 16 current cards. Exceeding a
limit is an explicit error, not permission to silently omit unfinished work.

## Finish and reminders

After reviewing outstanding cautions, referenced external jobs, and any dependent
work, preserve an essential result in the relevant still-active card or a landmark
slot when appropriate. Then `worklog finish CARD --expect CARD_REV --session ID --confirm`.
Finish removes the card. It does not archive it or imply independently verified tests.

A Stop hook may request an update after a potentially mutating tool. Save meaningful
changes. If there was no material change (for example a read-only shell command), run
`worklog ack --session ID`. Do not fabricate progress. Hooks stop after one reminder
continuation and never extract/store your transcript. They cannot guarantee an update
after an interrupt, crash, ignored instructions, or unsupported custom tools.

## Sandboxes

`status`, `show` and `context` work even where the store is read-only. A write there
fails with error `read_only`, naming the store path and the fix. Do not retry, `ack`,
edit the store files, or work around the sandbox; tell the user the store path, the fix,
and the update you would have saved. Codex keeps `.git` (and so `.git/worklog`)
read-only; `worklog install codex` adds `.codex/rules/worklog.rules`, which runs the
frontier writes outside the sandbox in a trusted project. Run them as plain
`worklog SUBCOMMAND ...` commands so that rule matches.
