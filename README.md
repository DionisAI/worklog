# Worklog Frontier 0.2.0

**Current state, next action, still-valid cautions — plus at most THREE historical
landmarks for the entire workspace. Nothing else becomes a growing work history.**

This is a deliberately smaller replacement for Worklog 0.1, not log compaction
bolted onto its event ledger. Local-first Python 3.11+, standard library only.
macOS/Linux design; CI runs on both with Python 3.11–3.13 (see the
[test summary](docs/TEST_SUMMARY.md)).

## Quick start

```sh
uv tool install --force .          # or: python -m pip install .
cd /path/to/project
worklog init
worklog install claude             # and/or: worklog install codex; then restart the agent
worklog add login --title 'Fix login redirect' \
  --current 'Reproduced the loop; cause unknown' --next 'Read the auth middleware'
worklog context                    # what a new session is shown at SessionStart
```

Agents then claim, update, hand off and finish that card through the skill that
`worklog install` adds; the sections below cover each step.

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
Project-local hook files are untracked: Codex needs `worklog install codex` (and folder
trust) in each Git worktree, while Claude Code 2.1.211+ uses the main checkout's hooks for
linked worktrees; plugin shells apply everywhere.

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
silently evicted, and no text is chopped off the tail. Every context render starts
with the header, your session, the claim-before-editing instruction and a one-line
index of every card (`id | status | owner | title`). `worklog context` and Codex hook
context then include the admitted frontier in full. Claude Code caps hook context at
10,000 characters (beyond that it shows a 2,000-character preview), so when the full
render would exceed ~9,500 characters the Claude hook keeps the index, overview and
landmarks but only your own card in full, and points to `worklog context --card ID`
for the rest. `worklog context --card ID` focuses detail on one card while keeping
the index and all three landmarks.

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
A checkout is identified by the workspace plus which checkout it is (main, which a plain
directory also counts as, or a linked worktree's admin name), so moving or renaming it, or
running `git init` in a plain directory, keeps the checkout part of its owners valid. Hook
session IDs still include the path, so after a move only a pinned `WORKLOG_SESSION_ID`
(`worklog launch`) keeps writing its card; others take it over. Subagents share their
parent session's identity.
These rules do not prevent arbitrary shell/editor writes outside Worklog.
Reads take a shared lock through a read-only descriptor, so `status`, `show` and
`context` also work where the store is read-only, e.g. `.git/worklog` inside Codex's
workspace-write sandbox; a write there fails with a `read_only` error naming the store.

An ended session releases its card: the `SessionEnd` hook and the end of
`worklog launch` clear its owner in this checkout and turn `working` into
`needs_check`, because its subprocesses may still be running and the next claimer
must check them. A pinned launcher identity (`WORKLOG_SESSION_ID`) keeps its card
across `/clear` and resume. A crashed session runs no hook, so its card stays owned:
a blocked claim names the card and owner and prints the exact
`worklog takeover CARD --expect REV --session YOURS --confirm-old-writer-stopped`
command (and suggests a separate worktree only inside Git). Run it only after
checking the old writer really stopped; Worklog does not itself prove that.
Inspect actual code and external jobs.

Current state is atomically replaced with file/directory fsync. A fixed staging
filename bounds leftovers even after repeated abrupt process exits. There is no
old snapshot backup. Corruption is reported, never silently converted into an empty workspace.
If the response is lost or fsync reports an error after publication, re-read state
before retrying. This is not an exactly-once external execution service.

## Hooks

`SessionStart` loads current state; `UserPromptSubmit` reinjects it only when it has
changed since the session last saw it. A CLI write that passes `--session` (claim,
update, handoff, finish, add, note, overview) counts as seen unless another session
wrote first, so a session's own write is not sent back to it. `PreToolUse` runs only for known potentially mutating tool classes
(its `matcher`) and marks a disposable dirty boolean. A `Stop` hook sends one reminder
per stretch of such tool use and then clears the flag. Potentially mutating tools used
during that continuation (including the Bash call that reads `worklog status`) flag the
next turn's Stop. A successful CLI update/handoff/finish clears that reminder;
`worklog ack --session ID` clears it when nothing materially changed.
`SessionEnd` deletes the sidecar entry and releases (never finishes) the session's
card as described above. No per-tool command/output
history is stored, and no heartbeat is necessary.

Hooks fail open. Claude Code and Codex read exit code 2 as "block", so `worklog hook`
always exits 0: a corrupt or un-migrated store, malformed input, or an internal error
is reported on stderr (and as a user-visible `systemMessage` at SessionStart and
UserPromptSubmit) instead of blocking prompts, tools, or stopping. Payloads over 1 MiB
are drained and ignored, so such a tool call is not marked dirty. Generated commands run
`python -P`: the session directory is not implicitly on `sys.path`, so a project's own
`worklog.py` or `json.py` is not imported as hook code (entries you add to PYTHONPATH,
even an empty one, still apply). Rerun `worklog install` to update existing hook configs.

Semantic updates still require the agent to use the skill/CLI. Hooks cannot invent
accurate next actions, guarantee cooperation, or capture uncheckpointed thinking
after a crash. A read-only shell command can conservatively trigger a reminder;
unknown custom mutating tools may not trigger one. The current frontier remains
usable without hooks and without the disposable sidecar.

## Codex sandbox

Codex's workspace-write sandbox (its `:workspace` profile) keeps `<repo>/.git` read-only,
and a Git store lives in `<git-common-dir>/worklog`; from a linked worktree that is
outside the writable workspace altogether. Reads (`status`, `show`, `context`) still work.
`worklog install codex` also writes `.codex/rules/worklog.rules`, a Codex rule that runs
the frontier writes (`add`, `update`, `checkpoint`, `handoff`, `claim`, `start`, `resume`,
`takeover`, `finish`, `note`, `overview`, `ack`) outside the sandbox without a prompt.
`launch`, `install`, `init`, `migrate`, `clear-runtime` and `--root` forms keep Codex's
normal approval. Codex loads project rules only in a trusted project, like
`.codex/hooks.json`, and calls rules experimental. An allowed command runs the first
`worklog` on PATH unsandboxed, as the hooks do: install Worklog outside the project (not
editable from it, not in a virtualenv inside it), or an agent could change that code and
leave the sandbox.

Without the rule (an untrusted project, a managed policy, or the native plugin shell,
which ships no rules file), a write fails with `read_only`, and the Stop reminder tells
the agent to report the store path and the update it would have saved instead of
retrying. The fallback is to make the store writable: get its absolute path from
`worklog stats` (`"store"`; for a linked worktree it is in the main checkout's `.git`)
and grant it in `~/.codex/config.toml`. With a permissions profile, only that directory
becomes writable and the rest of `.git` stays read-only (checked with codex-cli 0.163.0):

```toml
default_permissions = "worklog"

[permissions.worklog]
extends = ":workspace"

[permissions.worklog.filesystem]
"/path/to/repo/.git/worklog" = "write"
```

If any config layer sets `sandbox_mode`, Codex ignores `default_permissions` and uses its
older settings: `writable_roots = ["/path/to/repo/.git/worklog"]` under
`[sandbox_workspace_write]`, or `codex --add-dir /path/to/repo/.git/worklog` for one
session. Codex documents both; they were not exercised here.

## Switching accounts or agents

The frontier lives in the project's store, not in any agent's conversation history, so
work started under one Claude account can continue under another account, in Codex, or
in another agent. Worklog does not log in, switch accounts or detect an exhausted quota;
it carries the current card (position, next action, cautions) across the switch. Use
only your own accounts, within each plan's terms.

**Before switching.**

- The next session starts from the card's last `update` or `checkpoint`. A session out of
  quota cannot save anything more, and the turn that hit the limit gets no Stop reminder:
  Claude Code runs `StopFailure`, not `Stop`, when a turn ends on an API error. Edits made
  since the last update exist only in the working tree, so compare `git status` and
  `git diff` with the card.
- End the old session normally (`/exit`; in the Codex app quit or archive the
  conversation, because switching conversations does not end it). Its SessionEnd hook, or
  the end of `worklog launch`, releases its card, and a `working` card becomes
  `needs_check`.
- If it is still open, or was killed before SessionEnd ran, it still owns the card. Once
  you have checked it is not mid-turn and nothing it started is still writing, the new
  session runs `worklog takeover CARD --expect REV --session NEW --confirm-old-writer-stopped`
  (a refused `claim` prints this command filled in). The old session's later writes to
  the card are then refused.
- The CLI needs no model, so once the card is free you can record where things stand
  from a terminal: `worklog update CARD --expect REV --current '...' --next '...'`.

**Another Claude account.** `/login` inside a session switches it to another account and
keeps the conversation, but that config directory then holds only the new login. To stay
signed in to both, give each account its own config directory: each has its own login,
settings, plugins and session history, so `--continue` and the `--resume` picker do not
list the other account's conversations. The frontier carries over either way. Unset any
exported `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN`, which
outrank the login in every directory.

```sh
alias claude-b='CLAUDE_CONFIG_DIR=~/.claude-b claude'   # the first run logs in account B
```

The project hooks and skill from `worklog install claude` apply under every account once
that account accepts the folder's trust dialog. Native plugin shells are installed per
config directory; use one kind per project, or both run.

**Codex.** Run `worklog install codex`, trust the project in Codex, restart it, and trust
the Worklog hooks in `/hooks`. Codex skips new or changed hooks until you do, so repeat
that after a reinstall changes their command. Its hooks inject the same frontier, and the
rule in [Codex sandbox](#codex-sandbox) lets its frontier writes leave the sandbox. The
Codex session claims the card the Claude session released and releases it the same way.

**Any other agent.** An agent that can run shell commands can use the CLI without hooks,
but nothing then injects the frontier or reminds it to save. Give it the skill: only
`worklog install claude` and `worklog install codex` write one (to
`.claude/skills/worklog/SKILL.md` and `.agents/skills/worklog/SKILL.md`, along with that
harness's hooks), so run one of them and point the agent's instructions file at it. For
many agents that file is `AGENTS.md`; Gemini CLI reads `GEMINI.md` unless its
`context.fileName` setting lists `AGENTS.md`. Then start the agent through `launch`:

```sh
worklog launch --harness gemini --account personal -- gemini
```

Inside, the agent runs `worklog context`, claims the card with its revision and updates it
as it works, leaving out `--session`, `--harness` and `--account`: they default to the
launched identity, while the skill's examples show `--harness claude`. Ctrl-C goes to the
agent, not the launcher; when the agent exits or its terminal closes, `launch` releases its
card.

**Git worktrees.** Project hook files are untracked. Codex loads them per checkout, so each
linked worktree needs its own `worklog install codex` and project trust. Claude Code
2.1.211+ uses the main checkout's `.claude/settings.local.json` for a linked worktree, and
2.1.277+ its untracked `.claude/skills` as well.

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
worklog init   # adds .worklog/.gitignore to a plain-directory store
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
worklog doctor   # store, runtime cache and this checkout's hooks, each problem with a fix
PYTHONPATH=src python examples/demo.py
python -m pip install -e '.[dev]'
python -m pytest -q --cov        # coverage also counts CLI/hook subprocesses
PYTHONPATH=src python tools/soak.py --iterations 10000
```

Git repositories store state in `<git-common-dir>/worklog/`; plain directories
use `.worklog/`, which `worklog init` marks with a fixed `.gitignore` (`*`) so a later
`git init` never commits it (rerun `worklog init` to add it to an existing store).
Core files are `workspace.json`, `frontier.json`, `worklog.lock`,
and the optional `runtime.json`. Context reads do not modify file bytes or mtimes.
No default on-disk Markdown projection duplicates old frontier content.
If `frontier.json` becomes unreadable (for example a merge conflict), `worklog doctor`
names it and the fix: restore a good copy, or run
`worklog reset --discard-current-state --confirm`, which replaces only a missing or
unreadable frontier with an empty one in the same workspace (its cards and landmarks are lost).

The bounds cover **Worklog-owned runtime storage**. Provider-native transcripts,
Git history, user-exported files, and referenced external test/job logs are not
controlled by this tool. Do not commit generated frontier snapshots on every update.

See [Chinese guide](docs/USAGE.zh-CN.md), [architecture](docs/ARCHITECTURE.md),
[test summary](docs/TEST_SUMMARY.md) (full report in Chinese: [TEST_REPORT.md](docs/TEST_REPORT.md)),
[limits](docs/LIMITATIONS.md), and
[hook sources](docs/SOURCES.md). This is a breaking 0.2 release, not a compatible
replacement for 0.1 event/verify/job commands.
