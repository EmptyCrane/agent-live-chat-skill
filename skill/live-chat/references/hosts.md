# Host capability guide

The core Skill follows the open Agent Skills directory format. Host features are capabilities, not guaranteed side effects.

## OpenAI Codex

- The repository installer's isolated Codex target is `$CODEX_HOME/skills/live-chat`, falling back to `~/.codex/skills/live-chat`.
- Use the explicit `agents` installer target for the open shared path `~/.agents/skills/live-chat`.
- Repository Skill path: `.agents/skills/live-chat`.
- Run `doctor --host codex` after installation. If both Codex and `.agents` copies are discovered, disable one exact `SKILL.md` path in Codex configuration rather than deleting another host's copy.
- Use real subagent tools when present.
- Treat the callable subagent tool schema as authoritative for model and reasoning overrides. Pass them only when accepted by the current tool. A model listed in the OpenAI API catalog is not automatically available in the current Codex surface.
- After `start` returns a localhost URL, actively inspect all currently callable tools, including delayed tools that were absent from the initial tool summary. Search for the exact name `codex_app__open_in_codex`.
- When the tool exists, call it exactly once for the session:

  ```text
  codex_app__open_in_codex({
    target: {type: "browser", url: "<start returned URL>"},
    placement: "right"
  })
  ```

- Do not pass width, height, viewport, zoom, or global layout arguments.
- Do not infer that the tool is unavailable merely because it was not initially summarized. Only fall back after the callable-tool check proves it absent or after the call fails.
- On fallback, return the clickable URL and state the exact absence or failure. Do not claim the chat display is started until the tool call succeeds or this explicit fallback is complete.
- Never invoke the system default browser. Headless Chrome or Playwright may supplement visual QA but never replaces Codex built-in-browser acceptance.
- `agents/openai.yaml` is optional Codex UI metadata and is ignored by other hosts.

## Claude Code

- Personal Skill path: `~/.claude/skills/live-chat`.
- Repository Skill path: `.claude/skills/live-chat`.
- Use actual subagents and interruption only when exposed by the current Claude Code surface.
- Detect model selection and effort controls from the active Claude Code surface; do not translate OpenAI model names into Claude model names.
- Treat browser opening as unavailable unless the host exposes an explicit browser or open-URL tool; otherwise return the URL.

## GitHub Copilot

- Personal Skill path: `~/.copilot/skills/live-chat` or `~/.agents/skills/live-chat` where supported.
- Repository Skill path: `.github/skills/live-chat`; `.agents/skills/live-chat` is also recognized by supported Copilot surfaces.
- Subagent and browser capabilities differ among cloud agent, CLI, IDE, review, and app surfaces. Detect them independently.
- Model override and reasoning controls also vary by surface. Preserve opaque host model identifiers and never assume cross-vendor equivalence.
- When either capability is absent, use the corresponding fallback rather than assuming another Copilot surface's behavior.

## Generic or community hosts

- Require recognition of a directory containing `SKILL.md` and access to Python plus shell execution.
- Treat subagent dispatch, interruption, model override, reasoning override, and browser opening as independent optional capabilities.
- With no subagents, allow service startup, history replay, and manual CLI pushes only. Never label single-agent role simulation as a real multi-agent session.
- With no interruption, pause by stopping new dispatch and ignoring late results; tell the user in-flight work continues outside the Skill's control.
- With no browser tool, provide the localhost URL without opening a system browser.

## Adapter contract

Run `adapter show <host>` to obtain the canonical host name, install roots, and capability names. Treat every listed capability as something to probe, not as guaranteed availability. Include the canonical host in event `source.host`; keep host run identifiers opaque and never include credentials.
