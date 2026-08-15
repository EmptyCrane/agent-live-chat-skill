#!/usr/bin/env python3
"""Run the offline Live Chat behavior-policy contract evaluation.

This preflight verifies that every scenario has an explicit, testable policy in
the shipped Skill instructions. It does not claim to replace a real host/model
end-to-end evaluation.
"""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "live-chat"
DEFAULT_SCENARIOS = ROOT / "tests" / "behavior_scenarios.json"

ANCHORS = {
    "explicit_bypass": ("SKILL.md", 'Treat only explicit wording such as "start directly"'),
    "bounded_workflow": ("SKILL.md", "no more than three roles and three rounds by default"),
    "criteria_completion": ("SKILL.md", "Complete only when the goal is met"),
    "single_batch_questions": ("SKILL.md", "Ask one compact batch"),
    "approval_before_dispatch": ("SKILL.md", "Do not dispatch subagents until the user approves it"),
    "user_language": ("SKILL.md", "Show a concise proposal in the user's language"),
    "no_simulated_agents": ("SKILL.md", "Never generate several fictional agents"),
    "capability_fallback": ("SKILL.md", "offer replay/manual-push mode"),
    "model_fallback_decision": (
        "references/orchestration.md",
        "`fallback=ask`：派发前设置`waiting_user`",
    ),
    "resume_only_unfinished": ("SKILL.md", "dispatch only unfinished roles"),
    "persisted_decision": ("references/orchestration.md", "保存拒绝"),
    "reject_stops_dispatch": ("references/orchestration.md", "停止本场会话，不派发任何新任务"),
}

METRIC_INVARIANTS = {
    "dispatch_before_approval": "approval_before_dispatch",
    "duplicate_questions": "single_batch_questions",
    "simulated_agent_outputs": "no_simulated_agents",
    "duplicate_resume_dispatch": "resume_only_unfinished",
    "false_completion": "criteria_completion",
}


def evaluate(scenario_path=DEFAULT_SCENARIOS):
    payload = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    if payload.get("format") != "live-chat-behavior-eval/v1":
        raise ValueError("unsupported behavior evaluation format")
    documents = {
        name: (SKILL / name).read_text(encoding="utf-8")
        for name in {document for document, _ in ANCHORS.values()}
    }
    results = []
    covered = set()
    for scenario in payload.get("scenarios", []):
        missing = []
        for invariant in scenario.get("expects", []):
            covered.add(invariant)
            anchor = ANCHORS.get(invariant)
            if anchor is None or anchor[1] not in documents.get(anchor[0], ""):
                missing.append(invariant)
        results.append({
            "id": scenario.get("id", ""),
            "locale": scenario.get("locale", ""),
            "passed": not missing,
            "missing_invariants": missing,
        })
    missing_metrics = {
        metric: invariant
        for metric, invariant in METRIC_INVARIANTS.items()
        if invariant not in covered
        or ANCHORS[invariant][1] not in documents[ANCHORS[invariant][0]]
    }
    metrics = {
        metric: 0 if metric not in missing_metrics else 1
        for metric in METRIC_INVARIANTS
    }
    passed = bool(results) and all(result["passed"] for result in results) and not missing_metrics
    return {
        "format": payload["format"],
        "kind": "offline-policy-contract",
        "passed": passed,
        "scenario_count": len(results),
        "passed_count": sum(result["passed"] for result in results),
        "metrics": metrics,
        "results": results,
        "limitations": ["A real host/model end-to-end evaluation is still required."],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate(args.scenarios)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "Behavior policy evaluation: %d/%d scenarios passed"
            % (result["passed_count"], result["scenario_count"])
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
