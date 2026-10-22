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
