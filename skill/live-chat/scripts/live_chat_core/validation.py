"""Request and persisted-state validation."""

import re
from collections.abc import Mapping

from .config import SCHEMA_VERSION
from .models import DEFAULT_SCENE, DEFAULT_SESSION, public_message


MAX_PARTICIPANTS = 100
MAX_ROLE_INSTRUCTIONS = 10
MODEL_FALLBACKS = {"ask", "inherit", "available"}
POLICY_REASONING_EFFORTS = {"inherit", "none", "low", "medium", "high", "xhigh", "max"}
ROLE_REASONING_EFFORTS = POLICY_REASONING_EFFORTS | {"default"}
SESSION_STATUSES = {
    "idle",
    "running",
    "paused",
    "waiting_user",
    "completed",
    "stopped",
    "partial_failure",
}
SESSION_PHASES = {"not_started", "independent", "challenge", "synthesis"}
WORKFLOW_STRATEGIES = {
    "parallel_panel",
    "sequential_pipeline",
    "critic_revise",
    "debate_judge",
}
WORKFLOW_APPROVALS = {"required", "approved", "bypassed", "rejected", "legacy"}
DECISION_KINDS = {"plan_approval", "clarification", "model_fallback", "checkpoint"}
DECISION_ACTIONS = {"approve", "edit", "reject", "respond"}
RUN_PARTICIPANT_STATUSES = {"pending", "running", "completed", "failed", "skipped"}
CRITERION_STATUSES = {"met", "partial", "unmet"}
DECISION_ID = re.compile(r"^[0-9a-f]{32}$")
OPTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ValidationError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def utf8_safe_text(value):
    """Replace lone UTF-16 surrogate code units with the Unicode replacement character."""
    return "".join("\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character for character in value)


def require_mapping(value, label="request body"):
    if not isinstance(value, Mapping):
        raise ValidationError("invalid_type", "%s must be a JSON object" % label)
    return value


def _text(value, field, minimum, maximum, strip=True):
    if not isinstance(value, str):
        raise ValidationError("invalid_%s" % field, "%s must be a string" % field)
    value = utf8_safe_text(value)
    result = value.strip() if strip else value
    if len(result) < minimum or len(result) > maximum:
        raise ValidationError(
            "invalid_%s" % field,
            "%s must contain %d-%d characters" % (field, minimum, maximum),
        )
    return result


def validate_scene(value):
    require_mapping(value, "scene")
    title = _text(value.get("title", ""), "title", 1, 200)
    subtitle = _text(value.get("subtitle", ""), "subtitle", 0, 500)
    return {"title": title, "subtitle": subtitle}


def _string_list(value, field, maximum_items, item_maximum, minimum_items=0):
    if not isinstance(value, list):
        raise ValidationError("invalid_%s" % field, "%s must be an array" % field)
    if len(value) < minimum_items or len(value) > maximum_items:
        raise ValidationError(
            "invalid_%s" % field,
            "%s must contain %d-%d items" % (field, minimum_items, maximum_items),
        )
    result = []
    seen = set()
    for raw_item in value:
        item = _text(raw_item, field, 1, item_maximum)
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    if len(result) < minimum_items:
        raise ValidationError(
            "invalid_%s" % field,
            "%s must contain at least %d unique items" % (field, minimum_items),
        )
    return result


def _enum(value, field, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(
            "invalid_%s" % field,
            "%s must be one of: %s" % (field, ", ".join(sorted(allowed))),
        )
    return value


def _validate_model_policy(value):
    require_mapping(value, "model_policy")
    return {
        "default": _text(value.get("default", "inherit"), "default_model", 1, 200),
        "reasoning_effort": _enum(
            value.get("reasoning_effort", "inherit"),
            "policy_reasoning_effort",
            POLICY_REASONING_EFFORTS,
        ),
        "fallback": _enum(
            value.get("fallback", "ask"), "model_fallback", MODEL_FALLBACKS
        ),
    }


def _validate_role_model(value):
    require_mapping(value, "role model")
    return {
        "requested": _text(value.get("requested", "default"), "requested_model", 1, 200),
        "effective": _text(value.get("effective", ""), "effective_model", 0, 200),
        "reasoning_effort": _enum(
            value.get("reasoning_effort", "default"),
            "role_reasoning_effort",
            ROLE_REASONING_EFFORTS,
        ),
        "fallback_reason": _text(
            value.get("fallback_reason", ""), "model_fallback_reason", 0, 500
        ),
    }


def _integer(value, field, minimum, maximum):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationError(
            "invalid_%s" % field,
            "%s must be an integer from %d to %d" % (field, minimum, maximum),
        )
    return value


def _timestamp(value, field):
    return _text(value, field, 0, 64)


def _validate_workflow(value, role_count, round_maximum):
    if value is None:
        value = {
            "strategy": "parallel_panel",
            "approval": "legacy",
            "limits": {
                "max_rounds": max(3, round_maximum),
                "max_participants": max(3, role_count),
                "max_retries": 1,
                "wall_time_seconds": None,
            },
        }
    require_mapping(value, "workflow")
    limits = require_mapping(value.get("limits", {}), "workflow limits")
    max_rounds = _integer(limits.get("max_rounds", 3), "max_rounds", 1, 99)
    max_participants = _integer(
        limits.get("max_participants", 3), "max_participants", 2, MAX_PARTICIPANTS
    )
    max_retries = _integer(limits.get("max_retries", 1), "max_retries", 0, 3)
    wall_time = limits.get("wall_time_seconds")
    if wall_time is not None:
        wall_time = _integer(wall_time, "wall_time_seconds", 1, 86400)
    if round_maximum > max_rounds:
        raise ValidationError("invalid_max_rounds", "round.max cannot exceed workflow max_rounds")
    if role_count > max_participants:
        raise ValidationError(
            "invalid_max_participants", "session roles cannot exceed workflow max_participants"
        )
    return {
        "strategy": _enum(
            value.get("strategy", "parallel_panel"), "workflow_strategy", WORKFLOW_STRATEGIES
        ),
        "approval": _enum(
            value.get("approval", "required"), "workflow_approval", WORKFLOW_APPROVALS
        ),
        "limits": {
            "max_rounds": max_rounds,
            "max_participants": max_participants,
            "max_retries": max_retries,
            "wall_time_seconds": wall_time,
        },
    }


def validate_pending_decision(value):
    if value is None:
        return None
    require_mapping(value, "pending_decision")
    decision_id = _text(value.get("id", ""), "decision_id", 32, 32)
    if not DECISION_ID.fullmatch(decision_id):
        raise ValidationError("invalid_decision_id", "decision id must be 32 lowercase hex characters")
    raw_options = value.get("options", [])
    if not isinstance(raw_options, list) or not 1 <= len(raw_options) <= 10:
        raise ValidationError("invalid_decision_options", "decision options must contain 1-10 items")
    options = []
    seen = set()
    for raw_option in raw_options:
        require_mapping(raw_option, "decision option")
        option_id = _text(raw_option.get("id", ""), "decision_option_id", 1, 32)
        if not OPTION_ID.fullmatch(option_id) or option_id in seen:
            raise ValidationError(
                "invalid_decision_option_id", "decision option ids must be unique lowercase identifiers"
            )
        seen.add(option_id)
        options.append({
            "id": option_id,
            "label": _text(raw_option.get("label", ""), "decision_option_label", 1, 64),
            "description": _text(
                raw_option.get("description", ""), "decision_option_description", 0, 300
            ),
        })
    return {
        "id": decision_id,
        "kind": _enum(value.get("kind", "clarification"), "decision_kind", DECISION_KINDS),
        "prompt": _text(value.get("prompt", ""), "decision_prompt", 1, 1000),
        "options": options,
        "created_at": _timestamp(value.get("created_at", ""), "decision_created_at"),
    }


def _validate_run(value, role_names, workflow):
    require_mapping(value, "run")
    raw_participants = value.get("participants", [])
    if (
        not isinstance(raw_participants, list)
        or len(raw_participants) > workflow["limits"]["max_participants"]
    ):
        raise ValidationError("invalid_run_participants", "run participants must be an array")
    participants = []
    seen = set()
    for raw_participant in raw_participants:
        require_mapping(raw_participant, "run participant")
        name = _text(raw_participant.get("name", ""), "run_participant_name", 1, 64)
        if name in seen or name not in role_names:
            raise ValidationError(
                "invalid_run_participant", "run participants must uniquely reference session roles"
            )
        seen.add(name)
        duration = raw_participant.get("duration_ms")
        if duration is not None:
            duration = _integer(duration, "duration_ms", 0, 86400000)
        attempt = _integer(raw_participant.get("attempt", 0), "attempt", 0, 4)
        if attempt > workflow["limits"]["max_retries"] + 1:
            raise ValidationError(
                "retry_budget_exceeded",
                "participant attempt exceeds workflow max_retries",
            )
        participants.append({
            "name": name,
            "status": _enum(
                raw_participant.get("status", "pending"),
                "run_participant_status",
                RUN_PARTICIPANT_STATUSES,
            ),
            "attempt": attempt,
            "started_at": _timestamp(raw_participant.get("started_at", ""), "started_at"),
            "ended_at": _timestamp(raw_participant.get("ended_at", ""), "ended_at"),
            "duration_ms": duration,
            "error_code": _text(raw_participant.get("error_code", ""), "error_code", 0, 64),
        })
    raw_summaries = value.get("round_summaries", [])
    if not isinstance(raw_summaries, list) or len(raw_summaries) > 99:
        raise ValidationError("invalid_round_summaries", "round summaries must be an array")
    summaries = []
    seen_rounds = set()
    for raw_summary in raw_summaries:
        require_mapping(raw_summary, "round summary")
        number = _integer(raw_summary.get("round", 0), "summary_round", 1, 99)
        if number > workflow["limits"]["max_rounds"]:
            raise ValidationError(
                "round_budget_exceeded",
                "round summary exceeds workflow max_rounds",
            )
        if number in seen_rounds:
            raise ValidationError("duplicate_round_summary", "round summaries must be unique")
        seen_rounds.add(number)
        summaries.append({
            "round": number,
            "consensus": _string_list(raw_summary.get("consensus", []), "consensus", 20, 1000),
            "disagreements": _string_list(
                raw_summary.get("disagreements", []), "disagreements", 20, 1000
            ),
            "evidence": _string_list(raw_summary.get("evidence", []), "evidence", 50, 1000),
            "open_questions": _string_list(
                raw_summary.get("open_questions", []), "open_questions", 20, 1000
            ),
        })
    return {
        "id": _text(value.get("id", ""), "run_id", 0, 64),
        "started_at": _timestamp(value.get("started_at", ""), "run_started_at"),
        "updated_at": _timestamp(value.get("updated_at", ""), "run_updated_at"),
        "participants": participants,
        "round_summaries": summaries,
    }


def _validate_result(value, session_criteria):
    if value is None:
        return None
    require_mapping(value, "result")
    raw_criteria = value.get("criteria", [])
    if not isinstance(raw_criteria, list) or len(raw_criteria) > 5:
        raise ValidationError("invalid_result_criteria", "result criteria must be an array")
    criteria = []
    seen = set()
    for raw_item in raw_criteria:
        require_mapping(raw_item, "result criterion")
        text = _text(raw_item.get("text", ""), "result_criterion", 1, 500)
        if text in seen or text not in session_criteria:
            raise ValidationError(
                "invalid_result_criterion", "result criteria must uniquely reference session criteria"
            )
        seen.add(text)
        criteria.append({
            "text": text,
            "status": _enum(
                raw_item.get("status", "unmet"), "criterion_status", CRITERION_STATUSES
            ),
            "evidence": _string_list(
                raw_item.get("evidence", []), "criterion_evidence", 20, 500
            ),
        })
    return {
        "summary": _text(value.get("summary", ""), "result_summary", 1, 4000),
        "criteria": criteria,
        "disagreements": _string_list(
            value.get("disagreements", []), "result_disagreements", 20, 1000
        ),
        "next_actions": _string_list(
            value.get("next_actions", []), "result_next_actions", 20, 1000
        ),
    }


def validate_session(value, participants=None, allow_legacy_workflow=False):
    require_mapping(value, "session")
    explicit_workflow = "workflow" in value
    status = value.get("status", "idle")
    if status not in SESSION_STATUSES:
        raise ValidationError("invalid_session_status", "unsupported session status")

    pending_decision = validate_pending_decision(value.get("pending_decision"))
    background = _text(value.get("background", ""), "background", 0, 4000)
    objective = _text(value.get("objective", ""), "objective", 0, 1000)
    deliverable = _text(value.get("deliverable", ""), "deliverable", 0, 1000)
    stop_reason = _text(value.get("stop_reason", ""), "stop_reason", 0, 1000)
    preplan_clarification = (
        status == "waiting_user"
        and pending_decision is not None
        and pending_decision["kind"] == "clarification"
        and not value.get("roles")
    )
    active = status != "idle" and not preplan_clarification
    criteria = _string_list(
        value.get("criteria", []), "criteria", 5, 500, minimum_items=1 if active else 0
    )
    model_policy = _validate_model_policy(
        value.get("model_policy", DEFAULT_SESSION["model_policy"])
    )

    raw_roles = value.get("roles", [])
    if not isinstance(raw_roles, list):
        raise ValidationError("invalid_roles", "roles must be an array")
    if len(raw_roles) > MAX_PARTICIPANTS:
        raise ValidationError("too_many_roles", "roles supports at most 100 entries", 413)
    roles = []
    role_names = set()
    for raw_role in raw_roles:
        require_mapping(raw_role, "role")
        name = _text(raw_role.get("name", ""), "role_name", 1, 64)
        if name in role_names:
            raise ValidationError("duplicate_role", "role names must be unique")
        role_names.add(name)
        roles.append({
            "name": name,
            "role": _text(raw_role.get("role", ""), "role", 1, 200),
            "focus": _text(raw_role.get("focus", ""), "focus", 1, 500),
            "tone": _text(raw_role.get("tone", ""), "tone", 0, 500),
            "style": _text(raw_role.get("style", ""), "style", 0, 500),
            "instructions": _string_list(
                raw_role.get("instructions", []),
                "role_instructions",
                MAX_ROLE_INSTRUCTIONS,
                500,
            ),
            "model": _validate_role_model(raw_role.get("model", {})),
        })

    if active:
        if not objective or not deliverable:
            raise ValidationError(
                "incomplete_session", "active sessions require objective and deliverable"
            )
        if len(roles) < 2:
            raise ValidationError("invalid_roles", "active sessions require at least two roles")

    raw_round = require_mapping(value.get("round", DEFAULT_SESSION["round"]), "round")
    current = raw_round.get("current", 0)
    maximum = raw_round.get("max", 3)
    phase = raw_round.get("phase", "not_started")
    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
        raise ValidationError("invalid_round", "round.current must be a non-negative integer")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 99:
        raise ValidationError("invalid_round", "round.max must be an integer from 1 to 99")
    if current > maximum:
        raise ValidationError("invalid_round", "round.current cannot exceed round.max")
    if active and current < 1:
        raise ValidationError("invalid_round", "active sessions require round.current >= 1")
    if status == "idle" and (current != 0 or phase != "not_started"):
        raise ValidationError("invalid_round", "idle sessions must use round 0 and not_started")
    if phase not in SESSION_PHASES:
        raise ValidationError("invalid_session_phase", "unsupported session phase")
    completed = _string_list(
        raw_round.get("completed_participants", []),
        "completed_participants",
        MAX_PARTICIPANTS,
        64,
    )
    unknown_completed = [name for name in completed if name not in role_names]
    if unknown_completed:
        raise ValidationError(
            "unknown_completed_participant",
            "completed participants must reference session roles",
        )
    if participants is not None:
        participant_names = set(validate_participants(participants))
        unknown_roles = [role["name"] for role in roles if role["name"] not in participant_names]
        if unknown_roles:
            raise ValidationError(
                "unknown_session_role", "session roles must exist in participants"
            )

    workflow = _validate_workflow(value.get("workflow"), len(roles), maximum)
    if (
        explicit_workflow
        and workflow["approval"] == "legacy"
        and not allow_legacy_workflow
    ):
        raise ValidationError(
            "invalid_workflow_approval",
            "legacy workflow approval is reserved for migrated sessions",
        )
    if pending_decision is not None and status != "waiting_user":
        raise ValidationError(
            "invalid_pending_decision", "a pending decision requires waiting_user status"
        )
    if (
        explicit_workflow
        and workflow["approval"] != "legacy"
        and status == "waiting_user"
        and pending_decision is None
    ):
        raise ValidationError(
            "missing_pending_decision",
            "a waiting v2 workflow requires a pending decision",
        )
    if explicit_workflow and status == "running" and workflow["approval"] not in {
        "approved",
        "bypassed",
        "legacy",
    }:
        raise ValidationError(
            "approval_required",
            "a v2 workflow must be approved or explicitly bypassed before running",
        )
    run = _validate_run(value.get("run", DEFAULT_SESSION["run"]), role_names, workflow)
    result = _validate_result(value.get("result"), criteria)
    if (
        explicit_workflow
        and workflow["approval"] != "legacy"
        and status == "completed"
    ):
        if result is None:
            raise ValidationError(
                "incomplete_result",
                "a completed v2 workflow requires a structured result",
            )
        criterion_results = {item["text"]: item["status"] for item in result["criteria"]}
        if set(criterion_results) != set(criteria) or any(
            criterion_results[item] != "met" for item in criteria
        ):
            raise ValidationError(
                "completion_criteria_unmet",
                "a completed v2 workflow requires every completion criterion to be met",
            )

    return {
        "status": status,
        "background": background,
        "objective": objective,
        "deliverable": deliverable,
        "criteria": criteria,
        "model_policy": model_policy,
        "roles": roles,
        "round": {
            "current": current,
            "max": maximum,
            "phase": phase,
            "completed_participants": completed,
        },
        "workflow": workflow,
        "pending_decision": pending_decision,
        "run": run,
        "result": result,
        "stop_reason": stop_reason,
    }


def validate_participants(value):
    if not isinstance(value, list):
        raise ValidationError("invalid_participants", "participants must be an array")
    result = []
    seen = set()
    for raw_name in value:
        name = _text(raw_name, "participant", 1, 64)
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
        if len(result) > MAX_PARTICIPANTS:
            raise ValidationError(
                "too_many_participants",
                "participants supports at most %d unique names" % MAX_PARTICIPANTS,
                413,
            )
    return result


def derive_participants(messages, typing):
    names = []
    for message in messages:
        if not message.get("sys") and message.get("sender"):
            names.append(message["sender"])
    names.extend(typing.keys())
    return validate_participants(names)


def validate_message(value, message_id=None):
    require_mapping(value, "message")
    system = value.get("sys", False)
    if not isinstance(system, bool):
        raise ValidationError("invalid_sys", "sys must be a boolean")
    sender = value.get("sender", "")
    if system:
        if not isinstance(sender, str):
            raise ValidationError("invalid_sender", "sender must be a string")
        sender = sender.strip()[:64]
    else:
        sender = _text(sender, "sender", 1, 64)
    text = _text(value.get("text", ""), "text", 1, 100000)
    timestamp = value.get("ts", "")
    if timestamp is None:
        timestamp = ""
    if not isinstance(timestamp, str) or len(timestamp) > 32:
        raise ValidationError("invalid_ts", "ts must be a string of at most 32 characters")
    result = {
        "id": message_id if message_id is not None else value.get("id", 0),
        "sender": sender,
        "text": text,
        "sys": system,
        "ts": timestamp,
    }
    return public_message(result)


def validate_typing(value):
    require_mapping(value)
    if value.get("clear") is True:
        if "sender" in value or "active" in value:
            raise ValidationError("invalid_typing", "clear cannot be combined with sender or active")
        return {"clear": True}
    sender = _text(value.get("sender", ""), "sender", 1, 64)
    active = value.get("active")
    if not isinstance(active, bool):
        raise ValidationError("invalid_active", "active must be a boolean")
    return {"sender": sender, "active": active, "clear": False}


def validate_seed(value):
    require_mapping(value)
    raw_messages = value.get("messages")
    if not isinstance(raw_messages, list):
        raise ValidationError("invalid_messages", "messages must be an array")
    if len(raw_messages) > 5000:
        raise ValidationError("too_many_messages", "seed supports at most 5000 messages", 413)
    messages = [validate_message(message, index + 1) for index, message in enumerate(raw_messages)]
    scene = None
    if value.get("scene") is not None:
        scene = validate_scene(value["scene"])
    participants = None
    if "participants" in value:
        participants = validate_participants(value["participants"])
    session = None
    if "session" in value:
        session = validate_session(value["session"])
    return {
        "scene": scene,
        "session": session,
        "participants": participants,
        "messages": messages,
    }


def validate_since(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError("invalid_since", "since must be a non-negative integer")
    if result < 0:
        raise ValidationError("invalid_since", "since must be a non-negative integer")
    return result


def validate_persisted_state(value):
    require_mapping(value, "persisted state")
    if value.get("schema_version") not in (1, SCHEMA_VERSION):
        raise ValidationError("unsupported_schema", "unsupported state schema", 409)
    epoch = value.get("epoch")
    revision = value.get("revision")
    if not isinstance(epoch, int) or epoch < 0:
        raise ValidationError("invalid_epoch", "epoch must be a non-negative integer")
    if not isinstance(revision, int) or revision < 0:
        raise ValidationError("invalid_revision", "revision must be a non-negative integer")
    event_seq = value.get("event_seq", 0)
    if not isinstance(event_seq, int) or event_seq < 0:
        raise ValidationError("invalid_event_seq", "event_seq must be a non-negative integer")
    scene = validate_scene(value.get("scene", DEFAULT_SCENE))
    raw_messages = value.get("messages")
    if not isinstance(raw_messages, list):
        raise ValidationError("invalid_messages", "messages must be an array")
    messages = [validate_message(message, index + 1) for index, message in enumerate(raw_messages)]
    raw_typing = value.get("typing")
    if not isinstance(raw_typing, Mapping):
        raise ValidationError("invalid_typing", "typing must be an object")
    typing = {}
    for sender, active in raw_typing.items():
        normalized = _text(sender, "sender", 1, 64)
        if active is True:
            typing[normalized] = True
        elif active is not False:
            raise ValidationError("invalid_active", "typing values must be booleans")
    if "participants" in value:
        participants = validate_participants(value["participants"])
    else:
        participants = derive_participants(messages, typing)
    session = validate_session(
        value.get("session", DEFAULT_SESSION),
        participants,
        allow_legacy_workflow=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "epoch": epoch,
        "revision": revision,
        "event_seq": event_seq,
        "scene": scene,
        "session": session,
        "participants": participants,
        "messages": messages,
        "typing": typing,
    }
