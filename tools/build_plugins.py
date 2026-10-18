"""Generate optional native plugin shells using the installed `worklog` CLI."""
import json
from pathlib import Path

from worklog import __version__
from worklog.hooks import config

ROOT = Path(__file__).resolve().parents[1]


def plugin_hooks(harness: str) -> dict:
    """The project-local hook config, calling the `worklog` console script found on PATH."""
    hooks = config(harness)
    for event, groups in hooks["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = f"worklog hook --harness {harness} --event {event}"
    return hooks


def main():
    skill = (ROOT / "src/worklog/templates/SKILL.md").read_bytes()
    for harness in ("claude", "codex"):
        base = ROOT / "plugins" / harness
        meta = base / (".claude-plugin" if harness == "claude" else ".codex-plugin")
        meta.mkdir(parents=True, exist_ok=True)
        manifest = {"name": "worklog", "version": __version__,
                    "description": "Current frontier and at most three global historical landmarks"}
        if harness == "codex":
            manifest["hooks"] = "./hooks/hooks.json"
        (meta / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (base / "hooks").mkdir(exist_ok=True)
        (base / "hooks/hooks.json").write_text(json.dumps(plugin_hooks(harness), indent=2) + "\n")
        (base / "skills/worklog").mkdir(parents=True, exist_ok=True)
        (base / "skills/worklog/SKILL.md").write_bytes(skill)


if __name__ == "__main__":
    main()
