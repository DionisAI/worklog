# Frontier architecture

## Authority and boundedness

`frontier.json` is the only semantic authority. Its envelope contains a SHA-256
checksum and a schema-validated current state: workspace identity, a fixed-width
revision, current overview, current work cards, and slots 1/2/3 for landmarks.
The envelope is capped at 32768 UTF-8 bytes. It contains no event cursor or log.
An update takes `worklog.lock`, reads at most that bounded file, checks the card's
revision, replaces supplied fields, validates all count/byte/ownership constraints,
then publishes via a fixed staging file and atomic rename. Identical updates do
not write or advance revision. Reads never mutate semantic or runtime state. They
take a shared lock through a read-only descriptor of `worklog.lock`, so they work in a
store this process cannot write (`.git` is read-only inside Codex's workspace-write
sandbox); a write there fails with a `read_only` error naming the store. `worklog install
codex` adds a Codex rule that runs the frontier writes outside that sandbox.

Cards have independent CAS tokens. Workspace-level overview/landmark selection uses
a workspace CAS token. Tokens are random fixed-width UUID4 values, not growing
integers; deleting and recreating the same card ID cannot accept an old token.
At most 16 cards (4KiB each) coexist, additionally subject to the 32KiB total cap.
Errors occur before publication; there is no truncation, active-card LRU or silent
warning eviction. A full frontier is a signal to consolidate/resolve work explicitly.

Finish removes the current card. Landmark replacement removes the replaced text.
No deleted item is written elsewhere. There is no authoritative audit/event chain,
artifact cache, embedding store, native transcript reader, Markdown shadow state,
or rolling sequence of recovery snapshots. On-disk corruption fails closed instead
of fabricating an empty project. This intentionally trades historical recovery for
a smaller current-state-only product.

## Disposable hooks

`runtime.json` is a schema-validated optional cache bounded at 16384 bytes and 32
session entries. Each contains only `seen` (last injected frontier revision),
`dirty` (possible unsaved work), and `at` (cache age). Expiry is 24h; LRU eviction
limits abrupt-session leftovers. SessionEnd removes entries. This cache is neither
proof that an agent is alive nor authority to acquire/release ownership. It can be
explicitly cleared without changing a single frontier card. Each frontier transaction
also reports the revision it started from; after a CLI write with `--session`, seen
moves to the new revision only if it equaled that pre-write revision. A session's own
write is therefore not re-sent, while an unseen write by another session still is.

PreToolUse sets dirty before a known potentially-mutating command, so the CLI's
own update or ack can clear it before the tool returns. We do not parse arbitrary
shell text as proof that it is read-only or exempt compound commands based on the
presence of the word `worklog`. A read-only Bash command may cause one conservative
Stop reminder. Stop clears dirty when it sends a reminder, so it fires once per dirty
episode; tools used during that continuation flag the next turn's Stop. A
`stop_hook_active` Stop returns before any store access, so it never extends a
continuation. Generated configs give PreToolUse a matcher for the same tool set, so
other tools never start the hook. Unknown tools and interruptions have no universal
semantic checkpoint guarantee.

Hooks fail open. Both hosts treat exit 2 as a block (prompt rejected, tool refused,
stop overridden), so the hook entrypoint maps every error to exit 0 with `{}` and a
stderr line, plus a user-visible `systemMessage` at SessionStart/UserPromptSubmit.
Stop loads the frontier before reminding, so a corrupt store is skipped rather than
turned into a block. Only normal CLI commands keep exit 2 for structured errors.

SessionStart loads the frontier. UserPromptSubmit compares the current revision
against seen and injects only on change. A render starts with the header, identity,
claim instruction and a one-line index of every card, sized to fit within the 2,000
characters Claude Code previews of an over-cap hook context. Card bodies are shown
whole or not at all. Claude Code caps hook context at 10,000 characters, so a Claude
render over 9,500 drops the other cards' bodies, then its own, always keeping the
index, overview and landmarks and pointing to `worklog context --card ID`. Codex
config raises its additionalContextLimit above the bounded output and gets the full
render. Host-side transport/trust/context behavior is still controlled by the host agent.

## Files and cost

Normal core files: workspace.json (<4KiB), frontier.json (<=32KiB), worklog.lock
(empty), optional runtime.json (<=16KiB). A plain-directory `.worklog/` also holds a
fixed 2-byte `.gitignore`, written only by init. File count and retained bytes do not depend
on historical sessions or updates. Writes use a fixed `.<name>.tmp`, not unique
orphan paths, and fsync both file and parent directory. Current and staging bytes
can temporarily coexist. Hashing/parsing/checking scale with this bounded current
state, not the number of past sessions. Filesystem latency itself is not guaranteed.

A Python package install may contain ordinary fixed module caches. Agent-native
transcripts, external logs, user exports, and Git snapshots are outside this storage
contract. Project Hook configurations/Skill are fixed installation artifacts, not
per-session output and not a frontier history.

## Cooperation and failure boundary

Owner information lives only on a current card: session, harness, account alias,
checkout ID. Card ownership and one-writer-per-checkout are atomically checked.
Independent Git worktrees use distinct checkout IDs and a shared common-dir store.
There is no OS isolation, distributed lease service, credential management, process
supervisor or external-execution idempotency guarantee. An external editor can
bypass this protocol; a cached old reader must reconcile with code reality.

SessionEnd, and the end of `worklog launch`, release the ended session's cards in this
checkout in one transaction: the owner is cleared and working becomes needs_check,
because descendants may persist and the next claimer must check them. A pinned
launcher identity outlives Claude's `clear`/`resume` session ends, so its card is
kept then. Handoff explicitly releases the owner. A crashed session runs no hook, so
takeover remains: it requires fresh CAS and operator confirmation of old writer
termination, and a blocked claim names the blocking card and owner and prints the
exact takeover command. No clock expiry silently steals a checkout. No native session
history is needed to continue the current card.

After a write was published but its reply was lost, callers must reread. This
release does not retain an unbounded idempotency-receipt table. Same-value writes
are no-ops only with a matching current CAS token. Creation uses a caller-chosen
stable card ID and rejects duplicates. Finish has an explicit confirmation flag.

## Offline migration

The isolated migration module is the only code that replays a legacy v0.1 ledger.
Plan validates event hashes, selects current unfinished work and unsettled-job
cautions/references, and produces a review proposal. It guesses no past landmarks.
Apply checks byte/count bounds, source digest and explicit omission acknowledgements;
a bounded migration marker makes interrupted publication/cleanup resumable. It
publishes schema 2, removes old event/artifact/view directories and removes the
marker. Normal operations refuse a pending migration. All v0.1 agents must be stopped:
the old protocol cannot coordinate safely with a live schema replacement.
