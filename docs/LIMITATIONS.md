# Scope and limitations

- This is a current frontier, not an audit log or complete history. Replaced text,
  completed cards, previous snapshots and the fourth potential landmark are not
  archived. There is intentionally no history-based disaster recovery.
- At most 3 structured landmark entries can exist globally. The code cannot prove
  that an agent did not stuff many historical facts inside one text field; the
  Skill forbids that, and field/total byte limits still prevent unbounded growth.
- Semantic freshness requires the agent to update current/next/cautions. A Hook
  cannot infer accurate work state from nothing. Crashes/interrupts can lose
  progress since the last update. The Stop reminder is conservative, one-shot,
  and not a proof of compliance. External/custom tools may not be recognized.
- Python 3.11+ is targeted; POSIX/macOS/Linux are supported by design. Actual test
  environments and integration limitations are in TEST_SUMMARY.md (English) and
  TEST_REPORT.md (Chinese). Windows,
  network filesystems, cross-host synchronization and hostile agents are not
  validated. Do not put this live store in bidirectional file sync.
- File locks protect cooperating local Worklog calls, not arbitrary filesystem
  edits or outside processes. Accounts are aliases; provider credentials remain
  elsewhere. State is plaintext in private local files, not encrypted storage.
- External jobs are only described by current cautions/references; Worklog never
  starts, restarts, kills, observes or certifies them. `finish --confirm` means an
  operator chose to remove current work, not that an independent verifier passed.
- Only Worklog-owned runtime files are bounded. Provider transcripts, Git history,
  manually retained exports, project configs, referenced logs, and unrelated user
  files are outside the cap. Unknown files are reported, never silently deleted.
- Current+staging data may temporarily coexist during atomic writes. Fixed staging
  names bound repeated crash residue. Fsync/rename does not replace real hardware
  power-loss validation, and checksum is not authentication against malicious peers.
- Offline migration deliberately discards old history/artifacts after an explicit
  review and flags. Stop all old Worklog processes first. A fixed migration marker
  resumes incomplete cleanup; a proposal isn't permission to overwrite newer work.
- 0.1's task/verify/job/events API is removed. 0.2 commands and expectations differ;
  reinstall the skills/hooks and disable old native plugins. Duplicate host hook
  registrations in other scopes cannot be removed by a project-local installer.
- The Codex rule (`.codex/rules/worklog.rules`) uses Codex's experimental rules
  feature. It was checked with `codex execpolicy check` and the store grant with
  `codex sandbox`, not in a live Codex agent session. Allowed commands run outside
  the sandbox, so the `worklog` on PATH must not be writable from inside it.
