# The global .NOTPARALLEL declaration remains in platform.mk.
UPDATE_ID ?= successor-v1
UPDATE_POLICY ?= references/model_update_policy_v1.json
UPDATE_BASELINE_ROUND ?= portfolio-v1
UPDATE_GOLD_BUNDLE_ID ?=
UPDATE_NO_OTHER_WRITERS ?= 0
UPDATE_APPROVAL_SHA256 ?=
UPDATE_COMMAND = $(POETRY) run python -m scripts.model_updates.controller
UPDATE_ARGS = --update-id "$(UPDATE_ID)"
.PHONY: update-validate update-init update-build update-train update-qualify update-stage update-release update-recover update-rollback update-verify update-api

## Validate optional per-model search and recurring update policy
update-validate:
	$(UPDATE_COMMAND) validate --policy "$(UPDATE_POLICY)"

## Reserve the committed policy, Gold dataset and active incumbent
update-init:
	$(UPDATE_COMMAND) init $(UPDATE_ARGS) --policy "$(UPDATE_POLICY)" --baseline-round "$(UPDATE_BASELINE_ROUND)" $(if $(UPDATE_GOLD_BUNDLE_ID),--gold-bundle-id "$(UPDATE_GOLD_BUNDLE_ID)")

## Build/load the locked bounded update-training runtime
update-build:
	$(UPDATE_COMMAND) build $(UPDATE_ARGS)

## Train all candidates; optional search sees only inner training windows
update-train:
	$(UPDATE_COMMAND) train $(UPDATE_ARGS)

## Compare independently and register challenger only when all gates pass
update-qualify:
	@test "$(UPDATE_NO_OTHER_WRITERS)" = 1 || { echo 'Set UPDATE_NO_OTHER_WRITERS=1 after stopping other writers'; exit 2; }
	$(UPDATE_COMMAND) qualify $(UPDATE_ARGS) --no-other-writers

## Prepare inactive slot; leave champion and live route unchanged; print approval digest
update-stage:
	@test "$(UPDATE_NO_OTHER_WRITERS)" = 1 || { echo 'Set UPDATE_NO_OTHER_WRITERS=1 after stopping other writers'; exit 2; }
	$(UPDATE_COMMAND) stage $(UPDATE_ARGS) --no-other-writers

## Explicitly approve the exact stage digest, switch traffic and update champion
update-release:
	@test "$(UPDATE_NO_OTHER_WRITERS)" = 1 && test -n "$(UPDATE_APPROVAL_SHA256)" || { echo 'Exact stage digest and exclusive writer declaration required'; exit 2; }
	$(UPDATE_COMMAND) release $(UPDATE_ARGS) --approval-sha256 "$(UPDATE_APPROVAL_SHA256)" --no-other-writers

## Recover an interrupted authorized release to the previous route and alias
update-recover:
	@test "$(UPDATE_NO_OTHER_WRITERS)" = 1 || { echo 'Exclusive writer declaration required'; exit 2; }
	$(UPDATE_COMMAND) recover $(UPDATE_ARGS) --no-other-writers

## Explicitly approve the rollback digest printed by release
update-rollback:
	@test "$(UPDATE_NO_OTHER_WRITERS)" = 1 && test -n "$(UPDATE_APPROVAL_SHA256)" || { echo 'Exact rollback digest and exclusive writer declaration required'; exit 2; }
	$(UPDATE_COMMAND) rollback $(UPDATE_ARGS) --approval-sha256 "$(UPDATE_APPROVAL_SHA256)" --no-other-writers

## Verify Registry, active route, pinned model and latency without retraining
update-verify:
	$(UPDATE_COMMAND) verify $(UPDATE_ARGS)

## Open the active API after recurrent updates
update-api:
	$(UPDATE_COMMAND) api $(UPDATE_ARGS)
