SHELL := /bin/bash
#################################################################################
# GLOBALS                                                                       #
#################################################################################

PROJECT_NAME = fraud-detection-mlops
PYTHON_VERSION = 3.14
PYTHON_INTERPRETER = python

#################################################################################
# COMMANDS                                                                      #
#################################################################################


## Install Python dependencies
.PHONY: requirements
requirements:
	poetry install
	



## Delete all compiled Python files
.PHONY: clean
clean:
	find . -type f -name "*.py[co]" -delete
	find . -type d -name "__pycache__" -delete


## Lint using ruff (use `make format` to do formatting)
.PHONY: lint
lint:
	poetry run ruff format --check .
	poetry run ruff check .

## Format source code with ruff
.PHONY: format
format:
	poetry run ruff check --fix .
	poetry run ruff format .



## Run tests
.PHONY: test
test:
	poetry run pytest -q


## Run the same isolated quality checks as CI
.PHONY: validate
validate:
	poetry run tox -e py314


## Set up Python interpreter environment
.PHONY: create_environment
create_environment:
	poetry env use $(PYTHON_VERSION)
	@echo ">>> Poetry environment created. Activate with: "
	@echo '$$(poetry env activate)'
	@printf '>>> Or run commands with:\npoetry run <command>\n'




#################################################################################
# PROJECT RULES                                                                 #
#################################################################################


## Extract the full Bronze snapshot
.PHONY: data
data:
	poetry run python -m fraud_detection_mlops.dataset extract

## Generate descriptive EDA from the training window of verified Silver
.PHONY: eda
eda:
	poetry run python -m fraud_detection_mlops.eda build

## Build causal Gold features and temporal modeling splits
.PHONY: gold
gold:
	poetry run python -m fraud_detection_mlops.gold build

## Fit baseline candidates on train and compare validation rankings
.PHONY: baseline
baseline:
	poetry run python -m fraud_detection_mlops.modeling.train run

## Open local MLflow UI on port 5001 for existing or newly trained models
.PHONY: mlflow-ui
mlflow-ui:
	poetry run python -m fraud_detection_mlops.modeling.tracking ui

## Verify Gold integrity and feature invariants
.PHONY: verify-gold
verify-gold:
	poetry run python -m fraud_detection_mlops.gold verify

## Verify Bronze offline
.PHONY: verify-data
verify-data:
	poetry run python -m fraud_detection_mlops.dataset verify


#################################################################################
# Self Documenting Commands                                                     #
#################################################################################

.DEFAULT_GOAL := help

define PRINT_HELP_PYSCRIPT
import re, sys; \
lines = '\n'.join([line for line in sys.stdin]); \
matches = re.findall(r'\n## (.*)\n[\s\S]+?\n([a-zA-Z_-]+):', lines); \
print('Available rules:\n'); \
print('\n'.join(['{:25}{}'.format(*reversed(match)) for match in matches]))
endef
export PRINT_HELP_PYSCRIPT

help:
	@poetry run python -c "${PRINT_HELP_PYSCRIPT}" < $(MAKEFILE_LIST)

.PHONY: experiments experiments_verify
experiments: ## Execute controlled ablations; requires BASELINE_PATH and committed inputs
	@test -n "$(BASELINE_PATH)" || (echo "Set BASELINE_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.experiments run "$(BASELINE_PATH)"

experiments_verify: ## Verify ablation outputs offline; requires EXPERIMENT_PATH
	@test -n "$(EXPERIMENT_PATH)" || (echo "Set EXPERIMENT_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.experiments verify "$(EXPERIMENT_PATH)"

.PHONY: freeze verify-freeze
## Freeze the existing reference; requires BASELINE_PATH and EXPERIMENT_PATH
freeze:
	@test -n "$(BASELINE_PATH)" || (echo "Set BASELINE_PATH"; exit 1)
	@test -n "$(EXPERIMENT_PATH)" || (echo "Set EXPERIMENT_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.freeze build "$(BASELINE_PATH)" --experiment-path "$(EXPERIMENT_PATH)"

## Verify the frozen receipt and bound files offline
verify-freeze:
	poetry run python -m fraud_detection_mlops.modeling.freeze verify

.PHONY: evaluate-final verify-final
## Evaluate the committed frozen candidate on the reserved test
evaluate-final:
	poetry run python -m fraud_detection_mlops.modeling.evaluation run

## Verify saved final evaluation offline; requires EVALUATION_PATH
verify-final:
	@test -n "$(EVALUATION_PATH)" || (echo "Set EVALUATION_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.evaluation verify "$(EVALUATION_PATH)"

.PHONY: hgb-optuna-prepare hgb-optuna-optimize hgb-optuna-verify
## Prepare authorized development features; requires committed code and protocol
hgb-optuna-prepare:
	poetry run python -m fraud_detection_mlops.modeling.hgb_optuna prepare

## Run or resume bounded Optuna search; requires DEVELOPMENT_PATH; default one new trial
hgb-optuna-optimize:
	@test -n "$(DEVELOPMENT_PATH)" || (echo "Set DEVELOPMENT_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.hgb_optuna optimize "$(DEVELOPMENT_PATH)" --new-trials $(or $(NEW_TRIALS),1)

## Verify study artifacts offline; requires STUDY_PATH
hgb-optuna-verify:
	@test -n "$(STUDY_PATH)" || (echo "Set STUDY_PATH"; exit 1)
	poetry run python -m fraud_detection_mlops.modeling.hgb_optuna verify "$(STUDY_PATH)"
