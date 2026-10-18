# Hook interface sources

Checked on 2026-10-16 against primary vendor documentation:

- Claude Code hook reference: https://code.claude.com/docs/en/hooks
  SessionStart/UserPromptSubmit context, command-hook configuration, PreToolUse,
  Stop `decision: block` and `stop_hook_active`, SessionEnd.
- Codex hooks: https://developers.openai.com/codex/hooks
  (redirects to https://learn.chatgpt.com/docs/hooks)
  Project `.codex/hooks.json`, hook trust, additionalContextLimit, Stop continuation,
  SessionStart after compaction, and SessionEnd timing/advisory behavior.

These references inform adapter wire shapes. They do not establish that any live
logged-in Claude Code/Codex session was tested. No private transcript formats are
parsed. No model API or provider-account integration is required by the core.
