import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "tools" / "eval_skill.py"


class BehaviorEvaluationTests(unittest.TestCase):
    def test_policy_contract_covers_all_scenarios_and_metrics(self):
        spec = importlib.util.spec_from_file_location("eval_skill", EVALUATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.evaluate()
        self.assertTrue(result["passed"])
        self.assertEqual(result["scenario_count"], 8)
        self.assertEqual(result["passed_count"], 8)
        self.assertEqual(set(result["metrics"].values()), {0})
        self.assertEqual(result["kind"], "offline-policy-contract")

    def test_json_cli_is_machine_readable_and_discloses_limit(self):
        completed = subprocess.run(
            [sys.executable, str(EVALUATOR), "--json"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertTrue(result["passed"])
        self.assertTrue(result["limitations"])


if __name__ == "__main__":
    unittest.main()
