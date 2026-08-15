"""Host capability and installation metadata shared by runtime tools."""

import os
from pathlib import Path


HOST_CAPABILITIES_TO_PROBE = (
    "subagents",
    "interrupt",
    "browser_open",
    "model_override",
    "reasoning_override",
)


HOST_ADAPTERS = {
    "codex": {
        "id": "codex",
        "display_name": "OpenAI Codex",
        "user_root": "$CODEX_HOME/skills",
        "user_fallback": ".codex/skills",
        "project_root": ".agents/skills",
        "capabilities": HOST_CAPABILITIES_TO_PROBE,
    },
    "agents": {
        "id": "agents",
        "display_name": "Open Agent Skills",
        "user_root": ".agents/skills",
        "project_root": ".agents/skills",
        "capabilities": [],
    },
    "claude": {
        "id": "claude",
        "display_name": "Claude Code",
        "user_root": ".claude/skills",
        "project_root": ".claude/skills",
        "capabilities": HOST_CAPABILITIES_TO_PROBE,
    },
    "copilot": {
        "id": "copilot",
        "display_name": "GitHub Copilot",
        "user_root": ".copilot/skills",
        "project_root": ".github/skills",
        "capabilities": HOST_CAPABILITIES_TO_PROBE,
    },
    "generic": {
        "id": "generic",
        "display_name": "Generic Agent Host",
        "user_root": ".agents/skills",
        "project_root": ".agents/skills",
        "capabilities": [],
    },
}

EVENT_HOSTS = frozenset(HOST_ADAPTERS) | {"manual", "legacy"}


def adapter(name):
    """Return a copy of one adapter descriptor."""
    if name not in HOST_ADAPTERS:
        raise ValueError("unknown host adapter: %s" % name)
    value = HOST_ADAPTERS[name]
    return dict(value, capabilities=list(value["capabilities"]))


def skill_root_for(name, scope, home, project_root, environment=None):
    """Resolve the host Skill root without creating it."""
    value = adapter(name)
    environment = os.environ if environment is None else environment
    home = Path(home).expanduser().resolve()
    project_root = Path(project_root).expanduser().resolve()
    if scope == "project":
        return project_root / Path(value["project_root"])
    if scope != "user":
        raise ValueError("scope must be user or project")
    if name == "codex":
        codex_home = environment.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home).expanduser().resolve() / "skills"
        return home / Path(value["user_fallback"])
    return home / Path(value["user_root"])


def public_adapter(name, scope=None, home=None, project_root=None, environment=None):
    """Return JSON-safe adapter metadata, optionally with a resolved root."""
    value = adapter(name)
    if scope is not None:
        value["scope"] = scope
        value["resolved_root"] = str(
            skill_root_for(
                name,
                scope,
                Path.home() if home is None else home,
                Path.cwd() if project_root is None else project_root,
                environment,
            )
        )
    return value
