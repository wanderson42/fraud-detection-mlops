# Existing commands, grouped by purpose; execution stays in the original modules.
## Install Python dependencies
.PHONY: requirements
requirements:
	$(POETRY) install


## Delete all compiled Python files
.PHONY: clean
clean:
	find . -type f -name "*.py[co]" -delete
	find . -type d -name "__pycache__" -delete

## Lint using ruff (use `make format` to do formatting)
.PHONY: lint
lint:
	$(POETRY) run ruff format --check .
	$(POETRY) run ruff check .

## Format source code with ruff
.PHONY: format
format:
	$(POETRY) run ruff check --fix .
	$(POETRY) run ruff format .

## Run tests
.PHONY: test
test:
	$(POETRY) run pytest -q

## Run the same isolated quality checks as CI
.PHONY: validate
validate:
	$(POETRY) run python -m tox -e py314

## Build and check the inference wheel outside Git; requires RELEASE_PATH and MANIFEST_SHA256
.PHONY: validate-serving-wheel
validate-serving-wheel:
	@test -n "$(RELEASE_PATH)" || (echo "Set RELEASE_PATH"; exit 1)
	@test -n "$(MANIFEST_SHA256)" || (echo "Set MANIFEST_SHA256"; exit 1)
	$(POETRY) build --format wheel
	$(POETRY) run python scripts/check_serving_wheel.py dist/fraud_detection_mlops-0.0.1-py3-none-any.whl --release "$(RELEASE_PATH)" --manifest-sha256 "$(MANIFEST_SHA256)"

SERVING_IMAGE ?= fraud-detection-mlops:serving-lab
PYTHON_IMAGE ?= python:3.14.4-slim-trixie
DOCKER_REPORT ?= data/serving/docker-check.json

## Build the inference image from locked dependencies; requires a local Docker engine
.PHONY: build-serving-image
build-serving-image:
	docker build --file docker/serving/Dockerfile --build-arg "PYTHON_IMAGE=$(PYTHON_IMAGE)" --tag "$(SERVING_IMAGE)" .

## Check the image against an immutable release and save a new report
.PHONY: validate-serving-docker
validate-serving-docker:
	@test -n "$(RELEASE_PATH)" || (echo "Set RELEASE_PATH"; exit 1)
	@test -n "$(MANIFEST_SHA256)" || (echo "Set MANIFEST_SHA256"; exit 1)
	$(POETRY) run python scripts/serving/check_docker_runtime.py --image "$(SERVING_IMAGE)" --release "$(RELEASE_PATH)" --manifest-sha256 "$(MANIFEST_SHA256)" --output "$(DOCKER_REPORT)"

## Set up Python interpreter environment
.PHONY: create_environment
create_environment:
	$(POETRY) env use $(PYTHON_VERSION)
	@echo ">>> Poetry environment created. Activate with: "
	@echo '$$(poetry env activate)'
	@printf '>>> Or run commands with:\n$(POETRY) run <command>\n'
