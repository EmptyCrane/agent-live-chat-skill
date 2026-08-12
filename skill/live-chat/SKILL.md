---
name: live-chat
description: Stream, broadcast, visualize, diagnose, export, or replay real multi-agent conversations in a local browser with persistent sessions, visible participants, typing, goals, rounds, and completion state. Use for multi-agent live chat, browser-visible debates, reviews, diagnosis, negotiation, role-play, conversation history, 群聊直播, 多智能体辩论, 会诊, 谈判, 对话回放, or whenever the user wants to watch several AI agents talk. Supports capability-aware fallback when subagents or built-in browser tools are unavailable.
---

# live-chat

Show actual agent replies in completion order through a local read-only chat page. Keep the host agent responsible for orchestration and progress decisions; let subagents provide their own dialogue; let the bundled service persist display state.

## Check host capabilities

Before starting, determine whether the host provides:

- Python 3.9+ and a shell for the bundled CLI.
- Real subagent dispatch and, separately, interruption.
- A built-in browser-opening tool.

Read `references/hosts.md` for host-specific discovery, browser behavior, and installation paths.

If Python or shell execution is unavailable, explain that the local service cannot run and stop. If subagents are unavailable, offer replay/manual-push mode and state clearly that live multi-agent orchestration is unavailable. Never generate several fictional agents with one model while describing them as real subagents.

## Define the session with minimal questions

Extract background, objective, deliverable, one to five completion criteria, and constraints from the request. Treat requested models, reasoning effort, tone, style, and persona rules as constraints. Start immediately when objective and deliverable are sufficient. Otherwise ask once for only the missing critical information.

Use two roles for a simple comparison or short adversarial exchange. Use three complementary roles for review, diagnosis, open decisions, or creative convergence. Prefer user-specified roles. Use responsibility names such as Architect, Critic, and Operator; do not use model names as participant personas unless the user explicitly requests that name.

Keep each role's responsibility, focus, tone, response style, behavioral instructions, and model request separate. Include tone, style, and instructions in that subagent's task prompt; do not treat a model name as a personality.

Before dispatch, inspect the current subagent tool or host surface for model and reasoning overrides. Resolve each role's `requested` model and reasoning effort against those actual capabilities. Do not infer host availability from a vendor model catalog. If an exact requested model is unavailable and fallback is `ask`, set `waiting_user` and ask before dispatch. Apply `inherit` or another available model only when the session policy permits it, and record the actual model plus fallback reason. If the host does not reveal the effective model, record `host-managed` rather than guessing.

Tell the user which roles will participate, that the default is at most three rounds, and that they can pause, continue, or stop at any time.

## Start the local display

For a new installation or a reported failure, run `python <skill>/scripts/live_chat.py --json doctor` first and act on failed checks. Warnings do not block startup.

Run:

```text
python <skill>/scripts/live_chat.py --json start
```

Parse the returned `url`. If a built-in browser tool is available, open that URL once for the session without setting panel size, viewport, zoom, or global layout. Otherwise return a clickable URL. Never invoke the system default browser.

Create a new persistent conversation when the request is unrelated to the active one:

```text
python <skill>/scripts/live_chat.py --json sessions create --title "Topic"
```

Keep the returned `session_id`. Existing commands write to the active conversation. Never reset or seed another conversation to start a new task.

Reset the scene, register every role in display order, then set a complete session document:

```text
python <skill>/scripts/live_chat.py reset "Topic" "Subtitle"
python <skill>/scripts/live_chat.py participants set "Architect" "Critic" "Operator"
python <skill>/scripts/live_chat.py session set --stdin
```

An active session must contain objective, deliverable, one to five criteria, a model policy, at least two registered roles, and current round state. Read `references/protocol.md` before constructing or changing session JSON.

## Run goal-driven rounds

Use no more than three rounds by default:

1. `independent`: collect independent views without cross-anchoring.
2. `challenge`: challenge disagreements, evidence, and risk.
3. `synthesis`: form the conclusion, residual objections, and next actions.

Respect a user-specified limit. End early when the objective is met.

At each round start, atomically set `status=running`, round number, phase, and an empty `completed_participants`, then push a system separator. Turn typing on before dispatching each participant.

For every real reply, in actual completion order:

1. Turn that participant's typing off in a `finally`-equivalent path.
2. Push the unmodified reply through stdin.
3. Add the participant to `completed_participants` only after a successful push.

After each round, evaluate the deliverable, every completion criterion, material disagreements, and choices only the user can make:

- Set `completed` and record the satisfied criteria when the goal is met.
- Continue when progress remains possible and the round limit is not reached.
- Set `waiting_user` at the limit when the goal is not met; offer another round, role changes, objective changes, or a partial result.
- Set `partial_failure` when a necessary role fails and only a partial result is possible.

Do not report completion merely because the round limit was reached.

## Pause, resume, stop, and failure

On pause, stop new dispatches, preserve received replies, clear all typing, record completed participants, and set `paused`. If interruption exists, interrupt running subagents. If it does not, state that in-flight work cannot be force-cancelled and ignore late results until the user continues.

On continue, restore the persisted session and dispatch only unfinished roles in the current round. Recreate unavailable agents with the same responsibility, behavior settings, requested model, resolved reasoning effort, and a concise conversation summary. Re-check host availability; never silently substitute a now-unavailable model.

On stop, clear typing and set `stopped` with a reason. Keep the page, messages, objective, and roster available.

## Preserve and replay history

Use `sessions list --archived` before selecting or archiving history. Do not archive the active conversation; select or create another one first. Never delete runtime files to remove a conversation.

Use `export <session-id> --format snapshot --file <path>` for a compact handoff. Use `--format events` when ordering, typing, or event provenance matters. Replay exports with `replay --file <path> --speed 0`; replay always creates a new conversation and never overwrites the source.

Use `events emit --stdin` only for a host integration that can construct the normalized event envelope. Read `references/protocol.md` first. Keep real agent dispatch in the host; the adapter layer records capabilities and provenance but does not call a vendor SDK.

For an empty reply, request output once; on a second empty result, close typing and show a system explanation. Never invent missing dialogue. Retry a failed display push once and report any reply that could not be displayed.

## Load references only when needed

- Read `references/hosts.md` for host capability and installation differences.
- Read `references/orchestration.md` for role selection, rounds, completion, pause, and recovery.
- Read `references/protocol.md` for HTTP, CLI, session, and seed structures.
- Read `references/ui.md` when modifying or validating the page.
- Read `references/troubleshooting.md` for service, state, or browser failures.

The service must remain bound to `127.0.0.1`. Never expose it to the public network.
