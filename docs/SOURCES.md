# Hook interface sources

Checked on 2026-10-16 against primary vendor documentation:

- Claude Code hook reference: https://code.claude.com/docs/en/hooks
  SessionStart/UserPromptSubmit context, command-hook configuration, PreToolUse,
  Stop `decision: block` and `stop_hook_active`, SessionEnd. Rechecked 2026-10-17:
  exit 2 blocks PreToolUse/UserPromptSubmit and overrides Stop; matcher syntax
  (exact-name list, otherwise unanchored regex); `systemMessage`.
- Codex hooks: https://developers.openai.com/codex/hooks
  (redirects to https://learn.chatgpt.com/docs/hooks)
  Project `.codex/hooks.json`, hook trust, additionalContextLimit, Stop continuation,
  SessionStart after compaction, and SessionEnd timing/advisory behavior. Rechecked
  2026-10-17: regex matcher on canonical tool names (`Bash`, `apply_patch`), exit 2
  semantics, `systemMessage`, and Stop requiring JSON on stdout.

These references inform adapter wire shapes. They do not establish that any live
logged-in Claude Code/Codex session was tested. No private transcript formats are
parsed. No model API or provider-account integration is required by the core.
