# The canonical first round; scientific policy is committed before init.
ROUND_ID ?= portfolio-v1
ROUND_NO_OTHER_WRITERS ?= 0
.PHONY: round-validate round-init round-data round-build round-train round-promote round-deploy round-verify round-rollout round-api
# Global serialization is declared without prerequisites in platform.mk.
# Do not add prerequisites to .NOTPARALLEL: GNU Make 4.4 would then only
# serialize prerequisites of those targets, allowing explicit goals to overlap.

## Validate the new round policy and Helm training chart
round-validate:
	$(POETRY) run python -m scripts.operational_round.controller validate

## Initialize the committed new round; default ROUND_ID=portfolio-v1
round-init:
	$(POETRY) run python -m scripts.operational_round.controller init --round-id "$(ROUND_ID)"

## Reexecute Bronze to Silver/Gold Jobs and verify their new S3 outputs
round-data:
	$(POETRY) run python -m scripts.operational_round.controller data --round-id "$(ROUND_ID)"

## Build and load the locked, credential-free training runtime
round-build:
	$(POETRY) run python -m scripts.operational_round.controller build --round-id "$(ROUND_ID)"

## Train three new candidates in a Helm Job with mandatory MLflow dataset Inputs
round-train:
	$(POETRY) run python -m scripts.operational_round.controller train --round-id "$(ROUND_ID)"

## Verify data/model lineage and promote the new winner; requires ROUND_NO_OTHER_WRITERS=1
round-promote:
	@test "$(ROUND_NO_OTHER_WRITERS)" = 1 || { echo 'Set ROUND_NO_OTHER_WRITERS=1 after stopping other Registry writers'; exit 2; }
	$(POETRY) run python -m scripts.operational_round.controller promote --round-id "$(ROUND_ID)" --no-other-writers

## Build/qualify the current API and deploy the new fraud-detector champion
round-deploy:
	$(POETRY) run python -m scripts.operational_round.controller deploy --round-id "$(ROUND_ID)"

## Independently verify new datasets, three models, champion and HTTP identity
round-verify:
	$(POETRY) run python -m scripts.operational_round.controller verify --round-id "$(ROUND_ID)"

## Exercise blue/green switch, outage and rollback on the new champion; leaves one API replica
round-rollout:
	$(POETRY) run python -m scripts.operational_round.controller rehearse --round-id "$(ROUND_ID)"

## Open the canonical fraud-detector API; Ctrl+C closes only local access
round-api:
	$(POETRY) run python -m scripts.operational_round.controller api --round-id "$(ROUND_ID)"
