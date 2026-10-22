# Reuse the accepted champion and causal triage; do not train or modify deployment.
PRODUCT_FLOW_ID ?= champion-http-v1
PRODUCT_SOURCE_STATE ?= data/replay/paced-native-v1
PRODUCT_NO_OTHER_WRITERS ?= 0
PRODUCT_CASES_LIMIT ?= 5
.PHONY: product-validate product-prepare product-run product-verify product-rehearse product-cases

## Validate the bounded causal HTTP product flow policy
product-validate:
	$(POETRY) run python -m scripts.product_flow.controller validate

## Snapshot source events for the active champion; requires PRODUCT_NO_OTHER_WRITERS=1
product-prepare:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1 after stopping source/Registry/route writers'; exit 2; }
	$(POETRY) run python -m scripts.product_flow.controller prepare --flow-id "$(PRODUCT_FLOW_ID)" --source-state "$(PRODUCT_SOURCE_STATE)" --no-other-writers

## Run/resume causal events via the champion HTTP API with atomic decisions
product-run:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_flow.controller run --flow-id "$(PRODUCT_FLOW_ID)" --no-other-writers

## Reconcile all events, causal requests, HTTP responses, triage and feedback
product-verify:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_flow.controller verify --flow-id "$(PRODUCT_FLOW_ID)" --no-other-writers

## Exercise pause/resume and completed reinvocation; start with a fresh prepared flow
product-rehearse:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_flow.controller rehearse --flow-id "$(PRODUCT_FLOW_ID)" --no-other-writers

## Read bounded case details, scores, triage reasons and available feedback
product-cases:
	$(POETRY) run python -m scripts.product_flow.controller cases --flow-id "$(PRODUCT_FLOW_ID)" --limit "$(PRODUCT_CASES_LIMIT)"
