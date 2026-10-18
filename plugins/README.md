# Optional native plugin shells

`claude/` and `codex/` contain native plugin manifests, hook configs and the same
Worklog Frontier Skill. Install the Python CLI first so `worklog` is on the PATH
seen by your agent, then load the matching directory through that agent's plugin
workflow. These are package shells, not uploaded marketplace listings.

Use native plugin hooks OR `worklog install claude/codex`, not both. Disable the
old 0.1 plugin when upgrading. Hook trust/enablement still belongs to the host.
The project-local installer is the directly tested setup path; live provider UI
plugin installation has not been run here.

Regenerate with `PYTHONPATH=src python tools/build_plugins.py`.
