# Existing commands, grouped by purpose; execution stays in the original modules.
## Extract the full Bronze snapshot
.PHONY: data
data:
	$(POETRY) run python -m fraud_detection_mlops.data.ingestion.handbook_download extract

## Generate descriptive EDA from the training window of verified Silver
.PHONY: eda
eda:
	$(POETRY) run python -m fraud_detection_mlops.data.quality.training_eda build

## Build causal Gold features and temporal modeling splits
.PHONY: gold
gold:
	$(POETRY) run python -m fraud_detection_mlops.data.datasets.gold_dataset build

## Fit baseline candidates on train and compare validation rankings
.PHONY: baseline
baseline:
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment run

## Open local MLflow UI on port 5001 for existing or newly trained models
.PHONY: mlflow-ui
mlflow-ui:
	$(POETRY) run python -m fraud_detection_mlops.integrations.mlflow_tracking ui

## Verify Gold integrity and feature invariants
.PHONY: verify-gold
verify-gold:
	$(POETRY) run python -m fraud_detection_mlops.data.datasets.gold_dataset verify

## Verify Bronze offline
.PHONY: verify-data
verify-data:
	$(POETRY) run python -m fraud_detection_mlops.data.ingestion.handbook_download verify

.PHONY: experiments experiments_verify
experiments: ## Execute controlled ablations; requires BASELINE_PATH and committed inputs
	@test -n "$(BASELINE_PATH)" || (echo "Set BASELINE_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.terminal_feature_ablation run "$(BASELINE_PATH)"

experiments_verify: ## Verify ablation outputs offline; requires EXPERIMENT_PATH
	@test -n "$(EXPERIMENT_PATH)" || (echo "Set EXPERIMENT_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.terminal_feature_ablation verify "$(EXPERIMENT_PATH)"

.PHONY: freeze verify-freeze
## Freeze the existing reference; requires BASELINE_PATH and EXPERIMENT_PATH
freeze:
	@test -n "$(BASELINE_PATH)" || (echo "Set BASELINE_PATH"; exit 1)
	@test -n "$(EXPERIMENT_PATH)" || (echo "Set EXPERIMENT_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze build "$(BASELINE_PATH)" --experiment-path "$(EXPERIMENT_PATH)"

## Verify the frozen receipt and bound files offline
verify-freeze:
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze verify

.PHONY: evaluate-final verify-final
## Evaluate the committed frozen candidate on the reserved test
evaluate-final:
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.final_holdout_evaluation run

## Verify saved final evaluation offline; requires EVALUATION_PATH
verify-final:
	@test -n "$(EVALUATION_PATH)" || (echo "Set EVALUATION_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.final_holdout_evaluation verify "$(EVALUATION_PATH)"

.PHONY: hgb-optuna-prepare hgb-optuna-optimize hgb-optuna-verify
## Prepare authorized development features; requires committed code and protocol
hgb-optuna-prepare:
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization prepare

## Run or resume bounded Optuna search; requires DEVELOPMENT_PATH; default one new trial
hgb-optuna-optimize:
	@test -n "$(DEVELOPMENT_PATH)" || (echo "Set DEVELOPMENT_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization optimize "$(DEVELOPMENT_PATH)" --new-trials $(or $(NEW_TRIALS),1)

## Verify study artifacts offline; requires STUDY_PATH
hgb-optuna-verify:
	@test -n "$(STUDY_PATH)" || (echo "Set STUDY_PATH"; exit 1)
	$(POETRY) run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization verify "$(STUDY_PATH)"
