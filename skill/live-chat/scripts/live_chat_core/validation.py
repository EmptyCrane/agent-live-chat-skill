"""Request and persisted-state validation."""

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


class ValidationError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def require_mapping(value, label="request body"):
    if not isinstance(value, Mapping):
        raise ValidationError("invalid_type", "%s must be a JSON object" % label)
    return value


def _text(value, field, minimum, maximum, strip=True):
    if not isinstance(value, str):
        raise ValidationError("invalid_%s" % field, "%s must be a string" % field)
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


def validate_session(value, participants=None):
    require_mapping(value, "session")
    status = value.get("status", "idle")
    if status not in SESSION_STATUSES:
        raise ValidationError("invalid_session_status", "unsupported session status")

    background = _text(value.get("background", ""), "background", 0, 4000)
    objective = _text(value.get("objective", ""), "objective", 0, 1000)
    deliverable = _text(value.get("deliverable", ""), "deliverable", 0, 1000)
    stop_reason = _text(value.get("stop_reason", ""), "stop_reason", 0, 1000)
    active = status != "idle"
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
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("unsupported_schema", "unsupported state schema", 409)
    epoch = value.get("epoch")
    revision = value.get("revision")
    if not isinstance(epoch, int) or epoch < 0:
        raise ValidationError("invalid_epoch", "epoch must be a non-negative integer")
    if not isinstance(revision, int) or revision < 0:
        raise ValidationError("invalid_revision", "revision must be a non-negative integer")
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
    session = validate_session(value.get("session", DEFAULT_SESSION), participants)
    return {
        "schema_version": SCHEMA_VERSION,
        "epoch": epoch,
        "revision": revision,
        "scene": scene,
        "session": session,
        "participants": participants,
        "messages": messages,
        "typing": typing,
    }
