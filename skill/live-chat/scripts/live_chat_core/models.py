"""State model helpers."""

from copy import deepcopy

from .config import SCHEMA_VERSION

DEFAULT_SCENE = {
    "title": "群聊直播",
    "subtitle": "等待主持人开场…",
}

DEFAULT_SESSION = {
    "status": "idle",
    "background": "",
    "objective": "",
    "deliverable": "",
    "criteria": [],
    "model_policy": {
        "default": "inherit",
        "reasoning_effort": "inherit",
        "fallback": "ask",
    },
    "roles": [],
    "round": {
        "current": 0,
        "max": 3,
        "phase": "not_started",
        "completed_participants": [],
    },
    "workflow": {
        "strategy": "parallel_panel",
        "approval": "required",
        "template": None,
        "dispatch": {
            "max_concurrent": 3,
            "source": "conservative_default",
            "mode": "waves",
        },
        "limits": {
            "max_rounds": 3,
            "max_participants": 3,
            "max_retries": 1,
            "wall_time_seconds": None,
        },
    },
    "pending_decision": None,
    "run": {
        "id": "",
        "started_at": "",
        "updated_at": "",
        "participants": [],
        "round_summaries": [],
    },
    "result": None,
    "stop_reason": "",
}


def initial_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "epoch": 0,
        "revision": 0,
        "event_seq": 0,
        "scene": deepcopy(DEFAULT_SCENE),
        "session": deepcopy(DEFAULT_SESSION),
        "participants": [],
        "messages": [],
        "typing": {},
    }


def public_message(message):
    return {
        "id": int(message["id"]),
        "sender": str(message.get("sender", "")),
        "text": str(message["text"]),
        "sys": bool(message.get("sys", False)),
        "ts": str(message.get("ts", "")),
    }
