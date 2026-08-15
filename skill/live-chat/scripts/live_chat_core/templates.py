"""Bundled, non-executable workflow template catalog."""

import json
import re
from copy import deepcopy
from functools import lru_cache

from .config import templates_asset_path
from .models import DEFAULT_SESSION
from .validation import (
    MAX_PARTICIPANTS,
    TEMPLATE_ID,
    ValidationError,
    require_mapping,
    validate_scene,
    validate_session,
)


LANGUAGES = ("en", "zh-CN")
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
CATEGORIES = {"productivity", "entertainment"}
STRATEGIES = {
    "parallel_panel",
    "sequential_pipeline",
    "critic_revise",
    "debate_judge",
}


def _catalog_error(message):
    raise RuntimeError("invalid bundled template catalog: %s" % message)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        _catalog_error("%s must be non-empty text" % label)
    return value.strip()


def _positive_integer(value, label, minimum=1, maximum=MAX_PARTICIPANTS):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        _catalog_error("%s must be an integer from %d to %d" % (label, minimum, maximum))
    return value


def _validate_locales(value, label, fields):
    if not isinstance(value, dict) or set(value) != set(LANGUAGES):
        _catalog_error("%s must contain en and zh-CN" % label)
    normalized = {}
    for language in LANGUAGES:
        localized = value[language]
        if not isinstance(localized, dict):
            _catalog_error("%s.%s must be an object" % (label, language))
        missing = set(fields) - set(localized)
        if missing:
            _catalog_error("%s.%s is missing %s" % (label, language, sorted(missing)))
        normalized[language] = deepcopy(localized)
    return normalized


def _validate_role(raw_role, seen, label):
    if not isinstance(raw_role, dict):
        _catalog_error("%s must be an object" % label)
    role_id = _text(raw_role.get("id"), "%s.id" % label)
    if not TEMPLATE_ID.fullmatch(role_id) or role_id in seen:
        _catalog_error("%s.id must be a unique lowercase underscore identifier" % label)
    seen.add(role_id)
    locales = _validate_locales(
        raw_role.get("locales"),
        "%s.locales" % label,
        {"name", "role", "focus", "instruction"},
    )
    for language in LANGUAGES:
        for field in ("name", "role", "focus", "instruction"):
            _text(locales[language].get(field), "%s.%s.%s" % (label, language, field))
    return {"id": role_id, "locales": locales}


def _validate_template(raw_template, seen_ids):
    if not isinstance(raw_template, dict):
        _catalog_error("template must be an object")
    template_id = _text(raw_template.get("id"), "template.id")
    if not TEMPLATE_ID.fullmatch(template_id) or template_id in seen_ids:
        _catalog_error("template ids must be unique lowercase underscore identifiers")
    seen_ids.add(template_id)
    version = _positive_integer(raw_template.get("version"), "%s.version" % template_id, 1, 9999)
    category = raw_template.get("category")
    strategy = raw_template.get("strategy")
    if category not in CATEGORIES:
        _catalog_error("%s.category is unsupported" % template_id)
    if strategy not in STRATEGIES:
        _catalog_error("%s.strategy is unsupported" % template_id)
    rounds = _positive_integer(raw_template.get("rounds"), "%s.rounds" % template_id, 1, 99)
    retries = _positive_integer(raw_template.get("retries"), "%s.retries" % template_id, 0, 3)
    policy = raw_template.get("role_policy")
    if not isinstance(policy, dict):
        _catalog_error("%s.role_policy must be an object" % template_id)
    minimum = _positive_integer(policy.get("min"), "%s.role_policy.min" % template_id, 2)
    recommended = _positive_integer(
        policy.get("recommended"), "%s.role_policy.recommended" % template_id, minimum
    )
    maximum = policy.get("max")
    if maximum is not None:
        maximum = _positive_integer(
            maximum, "%s.role_policy.max" % template_id, recommended
        )
    if category == "productivity" and maximum is None:
        _catalog_error("%s productivity templates require a maximum" % template_id)
    if category == "entertainment" and maximum is not None:
        _catalog_error("%s entertainment templates cannot define a business maximum" % template_id)
    threshold = policy.get("confirmation_threshold")
    if threshold is not None:
        threshold = _positive_integer(
            threshold, "%s.role_policy.confirmation_threshold" % template_id, recommended
        )
    if category == "entertainment" and threshold != 8:
        _catalog_error("%s entertainment confirmation threshold must be 8" % template_id)
    locales = _validate_locales(
        raw_template.get("locales"),
        "%s.locales" % template_id,
        {"name", "description", "deliverable_hint", "criteria_hints", "tone", "style"},
    )
    for language in LANGUAGES:
        localized = locales[language]
        for field in ("name", "description", "deliverable_hint", "tone", "style"):
            _text(localized.get(field), "%s.%s.%s" % (template_id, language, field))
        hints = localized.get("criteria_hints")
        if not isinstance(hints, list) or not 1 <= len(hints) <= 5:
            _catalog_error("%s.%s.criteria_hints must contain 1-5 items" % (template_id, language))
        for hint in hints:
            _text(hint, "%s.%s.criteria_hints" % (template_id, language))
    seen_roles = set()
    core = [
        _validate_role(role, seen_roles, "%s.core_roles" % template_id)
        for role in raw_template.get("core_roles", [])
    ]
    optional = [
        _validate_role(role, seen_roles, "%s.optional_roles" % template_id)
        for role in raw_template.get("optional_roles", [])
    ]
    if len(core) != minimum or len(core) + len(optional) < recommended:
        _catalog_error("%s role blueprints do not satisfy its role policy" % template_id)
    if maximum is not None and len(core) + len(optional) != maximum:
        _catalog_error("%s bounded template blueprints must fill its maximum" % template_id)
    archetypes = raw_template.get("role_archetypes")
    if not isinstance(archetypes, dict) or set(archetypes) != set(LANGUAGES):
        _catalog_error("%s.role_archetypes must contain en and zh-CN" % template_id)
    for language in LANGUAGES:
        if not isinstance(archetypes[language], list):
            _catalog_error("%s.role_archetypes.%s must be an array" % (template_id, language))
        for archetype in archetypes[language]:
            _text(archetype, "%s.role_archetypes.%s" % (template_id, language))
    return {
        "id": template_id,
        "version": version,
        "category": category,
        "strategy": strategy,
        "rounds": rounds,
        "retries": retries,
        "role_policy": {
            "min": minimum,
            "recommended": recommended,
            "max": maximum,
            "confirmation_threshold": threshold,
        },
        "locales": locales,
        "core_roles": core,
        "optional_roles": optional,
        "role_archetypes": deepcopy(archetypes),
    }


@lru_cache(maxsize=1)
def load_catalog():
    try:
        with templates_asset_path().open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeError("cannot load bundled template catalog: %s" % exc) from exc
    if not isinstance(raw, dict) or raw.get("catalog_version") != 1:
        _catalog_error("catalog_version must be 1")
    templates = raw.get("templates")
    if not isinstance(templates, list) or len(templates) != 10:
        _catalog_error("catalog must contain exactly 10 templates")
    seen = set()
    return {
        "catalog_version": 1,
        "templates": tuple(_validate_template(item, seen) for item in templates),
    }


def _language(value):
    if value not in LANGUAGES:
        raise ValidationError("invalid_template_language", "language must be en or zh-CN")
    return value


def _localized_role(raw_role, template_locale, template_localized):
    localized = raw_role["locales"][template_locale]
    return {
        "name": localized["name"],
        "role": localized["role"],
        "focus": localized["focus"],
        "tone": template_localized["tone"],
        "style": template_localized["style"],
        "instructions": [localized["instruction"]],
        "model": {
            "requested": "default",
            "effective": "",
            "reasoning_effort": "default",
            "fallback_reason": "",
        },
    }


def localize_template(raw_template, language):
    language = _language(language)
    localized = raw_template["locales"][language]
    result = {
        field: deepcopy(raw_template[field])
        for field in ("id", "version", "category", "strategy", "rounds", "retries", "role_policy")
    }
    result.update(deepcopy(localized))
    result["core_roles"] = [
        dict(_localized_role(role, language, localized), slot_id=role["id"])
        for role in raw_template["core_roles"]
    ]
    result["optional_roles"] = [
        dict(_localized_role(role, language, localized), slot_id=role["id"])
        for role in raw_template["optional_roles"]
    ]
    result["role_archetypes"] = deepcopy(raw_template["role_archetypes"][language])
    return result


def template_catalog(language="en"):
    language = _language(language)
    catalog = load_catalog()
    return {
        "catalog_version": catalog["catalog_version"],
        "language": language,
        "templates": [localize_template(item, language) for item in catalog["templates"]],
    }


def template_by_id(template_id, language="en"):
    if not isinstance(template_id, str) or not TEMPLATE_ID.fullmatch(template_id):
        raise ValidationError("unknown_template", "template does not exist", 404)
    for item in load_catalog()["templates"]:
        if item["id"] == template_id:
            return localize_template(item, _language(language))
    raise ValidationError("unknown_template", "template does not exist", 404)


def _request_id(value):
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise ValidationError(
            "invalid_request_id", "request_id must be 32 lowercase hexadecimal characters"
        )
    return value


def _application_roles(value, template):
    raw_roles = value.get("roles")
    if raw_roles is None:
        candidates = template["core_roles"] + template["optional_roles"]
        raw_roles = [
            {field: deepcopy(role[field]) for field in (
                "name", "role", "focus", "tone", "style", "instructions", "model"
            )}
            for role in candidates[:template["role_policy"]["recommended"]]
        ]
    if not isinstance(raw_roles, list):
        raise ValidationError("invalid_roles", "roles must be an array")
    count = len(raw_roles)
    policy = template["role_policy"]
    if count < policy["min"]:
        raise ValidationError(
            "template_role_minimum",
            "template requires at least %d roles" % policy["min"],
        )
    if policy["max"] is not None and count > policy["max"]:
        raise ValidationError(
            "template_role_limit",
            "template supports at most %d roles; use a custom plan for a larger roster"
            % policy["max"],
            409,
        )
    if count > MAX_PARTICIPANTS:
        raise ValidationError("too_many_roles", "roles supports at most 100 entries", 413)
    signatures = set()
    for role in raw_roles:
        if not isinstance(role, dict):
            raise ValidationError("invalid_roles", "roles must contain objects")
        signature = (
            str(role.get("role", "")).strip().casefold(),
            str(role.get("focus", "")).strip().casefold(),
        )
        if signature in signatures:
            raise ValidationError(
                "duplicate_role_responsibility",
                "roles must have distinct responsibilities and focus",
            )
        signatures.add(signature)
    return deepcopy(raw_roles)


def _workflow_input(value):
    workflow = value.get("workflow", {})
    if not isinstance(workflow, dict):
        raise ValidationError("invalid_workflow", "workflow must be an object")
    limits = workflow.get("limits", {})
    dispatch = workflow.get("dispatch", {})
    if not isinstance(limits, dict):
        raise ValidationError("invalid_workflow", "workflow limits must be an object")
    if not isinstance(dispatch, dict):
        raise ValidationError("invalid_workflow", "workflow dispatch must be an object")
    return workflow, limits, dispatch


def prepare_application(value, created_at=""):
    require_mapping(value, "template application")
    template_id = value.get("template_id")
    language = _language(value.get("language", "en"))
    template = template_by_id(template_id, language)
    version = value.get("template_version")
    if version != template["version"]:
        raise ValidationError(
            "template_version_conflict", "template version does not match the bundled catalog", 409
        )
    request_id = _request_id(value.get("request_id"))
    roles = _application_roles(value, template)
    workflow_input, limits_input, dispatch_input = _workflow_input(value)
    approval = value.get("approval", "required")
    if approval not in {"required", "bypassed"}:
        raise ValidationError("invalid_template_approval", "approval must be required or bypassed")
    max_concurrent = dispatch_input.get("max_concurrent", 3)
    source = dispatch_input.get("source", "conservative_default")
    strategy = workflow_input.get("strategy", template["strategy"])
    maximum_rounds = limits_input.get("max_rounds", template["rounds"])
    session = deepcopy(DEFAULT_SESSION)
    session.update({
        "status": "waiting_user" if approval == "required" else "paused",
        "background": value.get("background", ""),
        "objective": value.get("objective", ""),
        "deliverable": value.get("deliverable", ""),
        "criteria": value.get("criteria", []),
        "model_policy": value.get("model_policy", DEFAULT_SESSION["model_policy"]),
        "roles": roles,
        "round": {
            "current": 1,
            "max": maximum_rounds,
            "phase": "independent",
            "completed_participants": [],
        },
        "workflow": {
            "strategy": strategy,
            "approval": approval,
            "template": {"id": template["id"], "version": template["version"]},
            "dispatch": {
                "max_concurrent": max_concurrent,
                "source": source,
                "mode": dispatch_input.get("mode", "waves"),
            },
            "limits": {
                "max_rounds": maximum_rounds,
                "max_participants": len(roles),
                "max_retries": limits_input.get("max_retries", template["retries"]),
                "wall_time_seconds": limits_input.get("wall_time_seconds"),
            },
        },
    })
    names = [str(role.get("name", "")) for role in roles]
    waves = (len(roles) + max(1, int(max_concurrent)) - 1) // max(1, int(max_concurrent)) \
        if isinstance(max_concurrent, int) and not isinstance(max_concurrent, bool) else 0
    decision = None
    if approval == "required":
        localized_prompt = (
            "Approve the %s plan with %d roles in approximately %d waves?"
            if language == "en"
            else "是否批准“%s”方案：%d 个角色，预计 %d 个并发批次？"
        )
        decision = {
            "id": request_id,
            "kind": "plan_approval",
            "prompt": localized_prompt % (template["name"], len(roles), waves),
            "options": [
                {"id": "approve", "label": "Approve" if language == "en" else "批准", "description": "Accept this plan." if language == "en" else "接受当前方案。"},
                {"id": "edit", "label": "Edit" if language == "en" else "修改", "description": "Request changes before dispatch." if language == "en" else "派发前修改方案。"},
                {"id": "reject", "label": "Reject" if language == "en" else "拒绝", "description": "Stop without dispatching." if language == "en" else "停止且不派发。"},
            ],
            "created_at": created_at,
        }
        session["pending_decision"] = decision
    participants = list(names)
    session = validate_session(session, participants)
    scene = value.get("scene")
    scene = validate_scene(scene) if scene is not None else None
    return {
        "request_id": request_id,
        "language": language,
        "template": template,
        "participants": participants,
        "session": session,
        "scene": scene,
        "decision": decision,
        "waves": waves,
        "large_cast_decision_id": value.get("large_cast_decision_id"),
    }


def large_cast_decision(application, created_at=""):
    count = len(application["participants"])
    waves = application["waves"]
    template = application["template"]
    language = application["language"]
    prompt = (
        "%s uses %d roles and approximately %d waves. Continue with the large cast?"
        if language == "en"
        else "“%s”将使用 %d 个角色，预计 %d 个并发批次。是否继续使用大型阵容？"
    ) % (template["name"], count, waves)
    return {
        "id": application["request_id"],
        "kind": "checkpoint",
        "prompt": prompt,
        "options": [
            {"id": "continue", "label": "Continue" if language == "en" else "继续", "description": "Keep the proposed roster." if language == "en" else "保留当前角色阵容。"},
            {"id": "reduce", "label": "Reduce roster" if language == "en" else "精简阵容", "description": "Return with fewer roles." if language == "en" else "返回并减少角色数量。"},
        ],
        "created_at": created_at,
    }
