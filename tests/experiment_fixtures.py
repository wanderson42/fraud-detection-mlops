"""Small comparison policies shared by evaluation and experiment scenarios."""

import json

from fraud_detection_mlops.modeling.experiments.terminal_feature_ablation import DEFAULT_POLICY


def tiny_policy():
    policy = json.loads(DEFAULT_POLICY.read_text())
    policy["data_policy"]["validation_days"] = 2
    return policy
