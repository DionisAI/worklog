# Test summary

English summary of [TEST_REPORT.md](TEST_REPORT.md), the full report (in Chinese).

## Continuous integration (since 2026-10-18)

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request on
ubuntu-latest and macos-latest with Python 3.11, 3.12 and 3.13. Each job runs:

- pytest with coverage that also counts CLI and hook subprocesses
  (`[tool.coverage.run] patch = ["subprocess"]`), failing below 90%;
- `examples/demo.py`;
- a 1,000-round `tools/soak.py`;
- a wheel build, then `tools/smoke_release.py --wheel`, which installs that wheel into a
  fresh virtual environment and drives the installed CLI and the generated hook commands.

The first run on `main` (`cdf6c44`, 2026-10-18) passed in all six jobs (197 tests and the
demo; the coverage gate, soak and wheel steps were added after it). On macOS (Apple
Silicon), repeated runs on Python 3.11, 3.12 and 3.13 measured 91.29%–91.38% statement
coverage of 1,160 statements; `cli.py` is 89%, and about 65% when subprocesses are not counted.

## 0.3.0 release verification (2026-11-08)

- CI on `main` (`b09a7ca`) passed in all six jobs: ubuntu-latest and macos-latest with
  Python 3.11, 3.12 and 3.13.
- On macOS (Apple Silicon), the release branch passed 236 tests on Python 3.11, 3.12 and
  3.13, with 92.67% statement coverage of 1,378 statements (subprocesses counted); the
  demo, a 1,000-round soak, and `tools/smoke_release.py` on the built 0.3.0 wheel passed.
- Live Claude Code 2.1.280 (macOS), in a real session in this repository: SessionStart and
  UserPromptSubmit injected the frontier, and after a Bash call the Stop hook blocked once
  with the reminder, which `worklog ack` cleared.
- Live Codex (codex-cli 0.163.0, `codex exec`, workspace-write sandbox): with the rule from
  `worklog install codex`, `worklog update` wrote the card while a `--root` form, which the
  rule does not match, failed with `read_only` (PR #1).

## 0.2.0 release verification (2026-10-16, Linux, Python 3.13.5)

- 131 tests passed; 83.67% statement coverage, not counting subprocesses.
- 10,000-round soak (20,000 semantic writes, 10,000 distinct hook sessions): the frontier
  stayed at 576 bytes and hook state at 3,538 bytes in 4 files; 1,000 context reads and
  1,000 state reads changed no bytes or mtimes. Plain-directory stores now also hold a
  fixed `.gitignore`, so the current soak counts 5 files.
- The wheel, installed into a fresh virtual environment without PYTHONPATH, ran the
  claim → handoff → resume → landmark → finish flow, the demo, and the SessionStart
  command generated for Claude and for Codex.
- A real v0.1.0 wheel produced a 14-event ledger; the 0.2 wheel migrated it to 3 core
  files and kept the latest semantic progress, and the old version refuses the new schema.

Raw output from that run is in [evidence/0.2.0/](evidence/0.2.0/). Wheels are not
byte-reproducible (the zip records build-time file timestamps), so no wheel hash is published.

## Not tested

Windows, network filesystems, real power loss, an interactive Codex session's hooks, an
end-to-end handoff between two real Claude accounts or from Claude to Codex (the steps run
with simulated hook input), and marketplace distribution of the native plugins (the
repository has no marketplace manifest).
