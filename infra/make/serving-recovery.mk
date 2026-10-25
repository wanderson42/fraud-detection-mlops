# Same-model carrier maintenance; explicit review before touching the active slot.
SERVING_MAINTENANCE_ID ?= seed-restart-v1
SERVING_NO_OTHER_WRITERS ?= 0
SERVING_APPROVAL_SHA256 ?=
.PHONY: serving-recovery-prepare serving-recovery-apply serving-recovery-verify

## Build/qualify the idempotent carrier with a forced isolated init restart
serving-recovery-prepare:
	@test "$(SERVING_NO_OTHER_WRITERS)" = 1 || { echo 'Set SERVING_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.platform.serving_recovery prepare --maintenance-id "$(SERVING_MAINTENANCE_ID)" --no-other-writers

## Apply the reviewed SAME-model carrier; brief API interruption is possible
serving-recovery-apply:
	@test "$(SERVING_NO_OTHER_WRITERS)" = 1 && test -n "$(SERVING_APPROVAL_SHA256)" || { echo 'Set SERVING_NO_OTHER_WRITERS=1 and the exact SERVING_APPROVAL_SHA256'; exit 2; }
	$(POETRY) run python -m scripts.platform.serving_recovery apply --maintenance-id "$(SERVING_MAINTENANCE_ID)" --approval-sha256 "$(SERVING_APPROVAL_SHA256)" --no-other-writers

## Verify Registry, route, image identity, HTTP and completed recovery receipt
serving-recovery-verify:
	$(POETRY) run python -m scripts.platform.serving_recovery verify --maintenance-id "$(SERVING_MAINTENANCE_ID)"

# Qualified SAME-model API runtime maintenance. Model promotion stays separate.
SERVING_RUNTIME_ID ?= api-trim-v1
SERVING_RUNTIME_DOCKER_REPORT ?= data/serving/image-footprint/candidate-http-v1.json
.PHONY: serving-runtime-prepare serving-runtime-apply serving-runtime-rollback serving-runtime-verify

## Preflight current baseline and qualify the pinned runtime in an isolated KinD pod
serving-runtime-prepare:
	@test "$(SERVING_NO_OTHER_WRITERS)" = 1 && test -n "$(PLATFORM_BASELINE)" || { echo 'Set SERVING_NO_OTHER_WRITERS=1 and PLATFORM_BASELINE'; exit 2; }
	$(POETRY) run python -m scripts.platform.runtime_maintenance prepare --maintenance-id "$(SERVING_RUNTIME_ID)" --docker-report "$(SERVING_RUNTIME_DOCKER_REPORT)" --baseline "$(PLATFORM_BASELINE)" --no-other-writers

## Apply the approved runtime in the SAME slot; Recreate may briefly interrupt API
serving-runtime-apply:
	@test "$(SERVING_NO_OTHER_WRITERS)" = 1 && test -n "$(SERVING_APPROVAL_SHA256)" || { echo 'Set writer declaration and exact runtime approval'; exit 2; }
	$(POETRY) run python -m scripts.platform.runtime_maintenance apply --maintenance-id "$(SERVING_RUNTIME_ID)" --approval-sha256 "$(SERVING_APPROVAL_SHA256)" --no-other-writers

## Restore previous runtime with its distinct rollback approval; keep the same weights
serving-runtime-rollback:
	@test "$(SERVING_NO_OTHER_WRITERS)" = 1 && test -n "$(SERVING_APPROVAL_SHA256)" || { echo 'Set writer declaration and exact rollback approval'; exit 2; }
	$(POETRY) run python -m scripts.platform.runtime_maintenance rollback --maintenance-id "$(SERVING_RUNTIME_ID)" --approval-sha256 "$(SERVING_APPROVAL_SHA256)" --no-other-writers

## Recheck canonical HTTP, Registry, resources and prior functional monitoring readbacks
serving-runtime-verify:
	$(POETRY) run python -m scripts.platform.runtime_maintenance verify --maintenance-id "$(SERVING_RUNTIME_ID)"
