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

## Define and approve the session

Use a host-neutral plan-first intake. Do not attempt to switch the host's collaboration mode. If the host is already in Plan Mode, use its question surface; otherwise follow the same intake in the current mode.

Extract background, objective, deliverable, one to five completion criteria, language, constraints, requested roles, model policy, time budget, and explicit approval bypass from the request. Reuse information already present. Ask one compact batch containing only missing choices that materially change the session.

Read `references/templates.md`, recommend one bundled template when it materially fits, and give one short reason. Let the user choose another template or a blank custom plan. Templates accelerate role and workflow design; they never replace missing critical intake or user approval.

Choose a workflow strategy, roles, round limit, retry limit, dispatch concurrency, and completion test. Show a concise proposal in the user's language. Do not dispatch subagents until the user approves it. Treat only explicit wording such as "start directly" or "无需确认" as approval bypass; urgency alone is not a bypass. On approval record `workflow.approval=approved`; on an explicit bypass record `bypassed`.

Before dispatching real subagents, read `references/orchestration.md` for role selection, prompt fields, model resolution, round transitions, completion, and recovery. Keep responsibilities and behavior separate from model requests. Do not infer host availability from a vendor catalog: probe the active host surface, and never guess an effective model.

Tell the user which roles and strategy will participate, the approved roster size, dispatch concurrency, estimated waves, round and retry limits, and that they can pause, continue, or stop at any time. Use each productivity template's bounded role policy. For entertainment templates use the recommended roster unless distinct responsibilities justify more roles; never expand merely to make the chat busier.

## Start the local display

For a new installation or a reported failure, run `python <skill>/scripts/live_chat.py --json doctor` first and act on failed checks. Warnings do not block startup.

Run:

```text
python <skill>/scripts/live_chat.py --json start
```

Parse the returned `url`. If a built-in browser tool is available, open that URL once for the session without setting panel size, viewport, zoom, or global layout. Otherwise return a clickable URL. Never invoke the system default browser.

In Codex, actively inspect the current callable tools, including delayed tools, for the exact name `codex_app__open_in_codex`; do not rely only on the initial tool summary. Follow the exact call and fallback sequence in `references/hosts.md`. Do not consider the chat display started until the built-in browser call succeeds or you explicitly report why it is unavailable or failed and provide the clickable URL. Headless browser QA does not satisfy this host-opening step.

Create a new persistent conversation when the request is unrelated to the active one:

```text
python <skill>/scripts/live_chat.py --json sessions create --title "Topic"
```

Keep the returned `session_id`. Existing commands write to the active conversation. Never reset or seed another conversation to start a new task.

Inspect or apply the selected template through the bundled interface:

```text
python <skill>/scripts/live_chat.py --json templates show <template-id> --lang <en|zh-CN>
python <skill>/scripts/live_chat.py --json templates apply <template-id> --lang <en|zh-CN> --stdin
```

Pass the complete intake, actual role roster, workflow limits, dispatch metadata, and a fresh request ID. Template apply only saves a proposal; it never dispatches. For an entertainment roster above eight roles, resolve the returned `checkpoint` with the `continue` option, then apply again with a new request ID and the resolved checkpoint ID. Do not silently add roles after approval.

For a proposal that requires confirmation, register the proposed roles and submit one `plan_approval` decision containing the full draft session through `decision request --stdin`. Wait for the host user's `approve`, `edit`, `reject`, or `respond` answer, then persist it with `decision resolve`. An approval leaves the session paused; set it to running only immediately before real dispatch. If the user edits the proposal, issue a new decision with a new ID after applying the edit.

Reset the scene, register every role in display order, then set a complete session document:

```text
python <skill>/scripts/live_chat.py reset "Topic" "Subtitle"
python <skill>/scripts/live_chat.py participants set "Architect" "Critic" "Operator"
python <skill>/scripts/live_chat.py session set --stdin
```

An active session must contain objective, deliverable, one to five criteria, a model policy, at least two registered roles, and current round state. Read `references/protocol.md` before constructing or changing session JSON.

## Run goal-driven strategies

Select one bounded strategy in `references/orchestration.md`: `parallel_panel`, `sequential_pipeline`, `critic_revise`, or `debate_judge`. Follow its three-phase soft guardrail. Use the template's approved roster and no more than three rounds by default:

1. `independent`: collect independent views without cross-anchoring.
2. `challenge`: challenge disagreements, evidence, and risk.
3. `synthesis`: form the conclusion, residual objections, and next actions.

Respect a user-specified limit. End early when the objective is met.

Treat roster size and dispatch concurrency separately. Use a numeric host limit when the active host exposes one; honor a lower user limit; otherwise use three with `source=conservative_default` without claiming it is the host maximum. Dispatch at most that many unfinished roles at once and continue in waves. If the host rejects work because capacity is full, reduce concurrency, requeue the role, and do not consume its retry budget. Never fabricate a missing participant.

At each round start, atomically set `status=running`, round number, phase, and an empty `completed_participants`, then push a system separator. Turn typing on before dispatching each participant.

Record run and participant lifecycle metadata without hidden reasoning: run ID, pending/running/completed/failed/skipped status, attempt number, timestamps, duration, and stable error code. After each round, persist a compact summary of consensus, disagreements, evidence, and open questions. Give the next round only the background and summary it needs, not the complete transcript.

For every real reply, in actual completion order:

1. Turn that participant's typing off in a `finally`-equivalent path.
2. Push the unmodified reply through stdin.
3. Add the participant to `completed_participants` only after a successful push.

After each round, evaluate the deliverable, every completion criterion, material disagreements, and choices only the user can make. Persist a result with each criterion marked `met`, `partial`, or `unmet` and linked to visible evidence. Complete only when the goal is met; otherwise continue, request a checkpoint decision for a material user choice, set `waiting_user` at the limit, or set `partial_failure` when a necessary role fails. Do not report completion merely because the round limit was reached.

## Pause, resume, stop, and failure

On pause, stop new dispatches, preserve received replies, clear typing, record completed participants, and set `paused`. Interrupt running subagents only when the host supports it; otherwise state that in-flight work cannot be force-cancelled and ignore late results until continuation. On continue, restore the persisted session and dispatch only unfinished roles, following the recovery rules in `references/orchestration.md` without silent model substitution.

On stop, clear typing and set `stopped` with a reason. Keep the page, messages, objective, and roster available.

Use structured decisions only for plan approval, missing critical information, model fallback, material disagreement, exhausted budget, or an external side effect that needs authority. Never expose hidden chain-of-thought as a trace; record only visible messages, summaries, decisions, lifecycle metadata, and errors.

## Preserve and replay history

Use `sessions list --archived` before selecting or archiving history. Do not archive the active conversation; select or create another one first. Never delete runtime files to remove a conversation.

Use `export <session-id> --format snapshot --file <path>` for a compact handoff. Use `--format events` when ordering, typing, or event provenance matters. Replay exports with `replay --file <path> --speed 0`; replay always creates a new conversation and never overwrites the source.

Use `events emit --stdin` only for a host integration that can construct the normalized event envelope. Read `references/protocol.md` first. Keep real agent dispatch in the host; the adapter layer records capabilities and provenance but does not call a vendor SDK.

For an empty reply, request output once; on a second empty result, close typing and show a system explanation. Never invent missing dialogue. Retry a failed display push once and report any reply that could not be displayed.

## Load references only when needed

- Read `references/hosts.md` for host capability and installation differences.
- Read `references/orchestration.md` for role selection, rounds, completion, pause, and recovery.
- Read `references/templates.md` when recommending, customizing, or applying a bundled template.
- Read `references/protocol.md` for HTTP, CLI, session, and seed structures.
- Read `references/ui.md` when modifying or validating the page.
- Read `references/troubleshooting.md` for service, state, or browser failures.

The service must remain bound to `127.0.0.1`. Never expose it to the public network.
