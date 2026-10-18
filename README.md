# Worklog Frontier 0.2.0

**Current state, next action, still-valid cautions — plus at most THREE historical
landmarks for the entire workspace. Nothing else becomes a growing work history.**

This is a deliberately smaller replacement for Worklog 0.1, not log compaction
bolted onto its event ledger. Local-first Python 3.11+, standard library only.
macOS/Linux design; see the test report for actually exercised environments.

## Install

From this source directory:

```sh
uv tool install --force .
# Or use pip in your own isolated virtual environment:
python -m pip install .
worklog --version
```

In a project that has not used Worklog before:

```sh
cd /path/to/project
worklog init
worklog install claude
worklog install codex
worklog launch --harness claude --account account-a -- claude
```

The account flag is an identity label, **not an authentication/account switch**.
Hooks still need to be enabled/trusted in the host agent; restart it after installing.
The installer merges only Worklog handlers and removes obsolete Worklog 0.1 handlers.
It does not rewrite provider credentials, copy transcripts, or create backup histories.
Use either project-local hooks or the optional native plugin shells, not both.

An existing 0.1 ledger needs the explicit offline migration below. `init` refuses
to overwrite it. Do not simply delete the old event files.

## The data model

- **Frontier cards:** one per ongoing workline, NOT one per session. Replace the
  current position, next action, cautions and external references as work changes.
  Completed work is removed. Multiple cards permit parallel independent worklines.
- **Historical landmarks:** optional GLOBAL slots `1`, `2`, `3`. An important past
  mistake, discovery or decision can occupy a slot. Deliberately select which slot
  to replace. There is no slot 4, per-task history, automatic archive, or hidden
  collection of prior versions. Empty slots need not be filled.
- **Current owner:** an optional session/harness/account/checkout identity on a card.
  No persistent session or attempt tables. No task verifier, job supervisor,
  scheduler, lease history, heartbeat daemon, or model API calls.

A currently unresolved hazard stays in its card's cautions. A historical lesson
uses one of the three slots. Do not smuggle a chronological backlog into a field
called "current". Admission limits bound bytes; selecting meaningful current
information remains the agent's responsibility.

## Hard bounds (not output truncation)

| Item | Limit / behavior |
|---|---|
| Entire authoritative `frontier.json` | 32 KiB UTF-8 bytes, including envelope |
| Current cards | At most 16; at most 4 KiB per card |
| Historical landmarks | At most 3 **for the whole workspace**; 1 KiB text per slot |
| Workspace overview | 2 KiB current text |
| Per card lists | At most 8 cautions + 8 references; 384 bytes per item |
| Hook sidecar `runtime.json` | At most 16 KiB; at most 32 disposable session flag entries |
| Runtime flags | Expire after 24h; removed at SessionEnd or bounded LRU eviction |
| Events / transcript copies / artifact copies / old frontier backups | **None** |

Oversized updates fail before publication. Unfinished cards and cautions are NOT
silently evicted. Model context includes the admitted frontier in full, rather
than chopping off the tail. `worklog context --card ID` focuses detail while
retaining the other current card/owner index and all three landmarks.

## CLI workflow

```sh
worklog add replay --title 'Fix duplicate counting' \
  --current 'Located a duplicate-event branch; fix not verified' \
  --next 'Add a regression case'
worklog show replay
```

Use the actual full `revision` from that card:

```sh
worklog claim replay --expect CARD_REV --session agent-a --harness claude
worklog show replay
worklog update replay --expect NEW_CARD_REV --session agent-a \
  --current 'Fix written; full regression still pending' \
  --next 'Check the already-running replay' \
  --caution 'Do not restart the existing replay job' \
  --reference 'tmux:replay:0'
```

The `--caution` and `--reference` options **replace their entire lists**, rather
than append. Use `--clear-cautions` / `--clear-references` to remove resolved items.
Unspecified fields remain unchanged. `checkpoint` is an alias of `update`, not a
new historical checkpoint record.

```sh
worklog show replay
worklog handoff replay --expect CARD_REV --session agent-a \
  --current 'Implementation reviewed; real tests not yet checked' \
  --next 'Inspect current test results, then continue'
# Agent B reads the same current card, verifies reality, and claims it:
worklog show replay
worklog resume replay --expect NEW_CARD_REV --session agent-b --harness codex
```

`handoff` updates the same card and releases its owner. It saves no handoff object
or log. `claim`, `start`, and `resume` all claim the current card with a fresh CAS
revision. No original conversation/native agent state is imported.

For a landmark, use the **workspace** revision from `worklog status`:

```sh
worklog status
worklog note 1 --expect WORKSPACE_REV --text 'Timestamp alone is not a safe dedup key.'
# Later: replace or clear the SAME slot, never add slot 4.
worklog note 1 --expect NEW_WORKSPACE_REV --clear
```

After reviewing outstanding cautions/jobs/dependencies and preserving an essential
result in a still-active card or a landmark when necessary:

```sh
worklog finish replay --expect CARD_REV --session agent-b --confirm
```

`finish` deletes the card. It neither archives it nor certifies that tests passed.
References point to existing files/jobs; Worklog does not start, kill, restart or
copy them. Test outcomes must be recorded truthfully by the working agent.

## Concurrency and crash behavior

A stable POSIX lock serializes each read-modify-publish operation. Card-level CAS
prevents lost updates; unrelated cards can update independently. Revision tokens
are fixed-width random values to avoid delete/recreate ABA and counter growth.
One cooperating writer is allowed per checkout; parallel code writers should use
separate Git worktrees, which share the same frontier by Git common directory.
These rules do not prevent arbitrary shell/editor writes outside Worklog.

A stopped session does not automatically release its card: its subprocesses may
still be running. After checking the old writer really stopped, a new session may
use `takeover --confirm-old-writer-stopped` with the current card revision. Worklog
does not itself prove the process stopped. Inspect actual code and external jobs.

Current state is atomically replaced with file/directory fsync. A fixed staging
filename bounds leftovers even after repeated abrupt process exits. There is no
old snapshot backup. Corruption is reported, not converted into an empty workspace.
If the response is lost or fsync reports an error after publication, re-read state
before retrying. This is not an exactly-once external execution service.

## Hooks

`SessionStart` loads current state; `UserPromptSubmit` reinjects it only when its
revision changed. `PreToolUse` marks a disposable dirty boolean for known potentially
mutating tool classes. A `Stop` hook gives at most one reminder continuation to
update the frontier. A successful CLI update/handoff/finish clears that reminder;
`worklog ack --session ID` clears it when nothing materially changed. `SessionEnd`
deletes the sidecar entry, not the work card. No per-tool command/output history is
stored, and no heartbeat is necessary.

Semantic updates still require the agent to use the skill/CLI. Hooks cannot invent
accurate next actions, guarantee cooperation, or capture uncheckpointed thinking
after a crash. A read-only shell command can conservatively trigger a reminder;
unknown custom mutating tools may not trigger one. The current frontier remains
usable without hooks and without the disposable sidecar.

## Migrating 0.1 (explicit, offline, destructive to old history)

Stop all 0.1 agent/launcher processes first. Existing external jobs may continue;
inspect them and ensure no old Worklog writer is still running.

```sh
worklog migrate --plan > /tmp/worklog-frontier-review.json
# Review/condense current cards; choose up to 3 landmarks in frontier.notes.
# Do not remove unresolved work silently. Reviewed merged/resolved old IDs must
# be explicitly listed in acknowledged_omissions if their old cards are omitted.
worklog migrate --apply /tmp/worklog-frontier-review.json \
  --discard-history --confirm-agents-stopped
worklog install claude
worklog install codex
worklog doctor
```

Planning leaves the old ledger intact. Applying validates the reviewed bounded
state and source digest, publishes frontier mode, then deletes old events,
artifacts, sessions embedded in those files, and derived views. **No archive is
created.** Interrupted migration blocks normal use until the same proposal is
successfully reapplied. A completed migration cannot overwrite newer frontier work.
Remove your temporary review export when no longer needed; it is not a runtime file.
Disable an old native plugin if you switch to project-local installation.

## Inspect / develop

```sh
worklog status
worklog context
worklog stats
worklog doctor
PYTHONPATH=src python examples/demo.py
python -m pytest -q
PYTHONPATH=src python tools/soak.py --iterations 10000 --output docs/soak-result.json
```

Git repositories store state in `<git-common-dir>/worklog/`; plain directories
use `.worklog/`. Core files are `workspace.json`, `frontier.json`, `worklog.lock`,
and the optional `runtime.json`. Context reads do not modify file bytes or mtimes.
No default on-disk Markdown projection duplicates old frontier content.

The bounds cover **Worklog-owned runtime storage**. Provider-native transcripts,
Git history, user-exported files, and referenced external test/job logs are not
controlled by this tool. Do not commit generated frontier snapshots on every update.

See [Chinese guide](docs/USAGE.zh-CN.md), [architecture](docs/ARCHITECTURE.md),
[test report](docs/TEST_REPORT.md), [limits](docs/LIMITATIONS.md), and
[hook sources](docs/SOURCES.md). This is a breaking 0.2 release, not a compatible
replacement for 0.1 event/verify/job commands.
