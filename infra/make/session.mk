# A session owns a new causal checkpoint; historical accepted snapshots are immutable.
SESSION_ID ?= arrival-v1
SESSION_SOURCE_STATE ?= data/replay/paced-native-v1
SESSION_INTERVAL ?= 1
SESSION_PORT ?= 18502
SESSION_NO_OTHER_WRITERS ?= 0
SESSION_EVENT_SOURCE_ID ?= clean-events-v1
SESSION_SILVER_ROOT ?= data/interim/handbook
SESSION_EVENT_SOURCE_SHA256 ?=
SESSION_SOURCE_START ?= 2018-09-16
SESSION_SOURCE_END ?= 2018-09-17
.PHONY: session-source-plan session-source-prepare session-source-verify

## Check Silver metadata coverage before preparing a new source; no partition bytes opened
session-source-plan:
	$(POETRY) run python -m scripts.product_flow.event_source plan --source-id "$(SESSION_EVENT_SOURCE_ID)" --silver-root "$(SESSION_SILVER_ROOT)" --start "$(SESSION_SOURCE_START)" --end "$(SESSION_SOURCE_END)"

## Materialize verified Silver events only; no old replay, model or HTTP inference
session-source-prepare:
	$(POETRY) run python -m scripts.product_flow.event_source prepare --source-id "$(SESSION_EVENT_SOURCE_ID)" --silver-root "$(SESSION_SILVER_ROOT)" --start "$(SESSION_SOURCE_START)" --end "$(SESSION_SOURCE_END)"

## Verify the closed event source against the preparation's manifest SHA
session-source-verify:
	@test -n "$(SESSION_EVENT_SOURCE_SHA256)" || { echo 'Set SESSION_EVENT_SOURCE_SHA256 from preparation'; exit 2; }
	$(POETRY) run python -m scripts.product_flow.event_source verify --source-id "$(SESSION_EVENT_SOURCE_ID)" --expected-manifest-sha256 "$(SESSION_EVENT_SOURCE_SHA256)"
.PHONY: session-prepare session-run session-pause session-feedback session-status session-verify session-open

## Prepare an isolated current-champion session from original source events
session-prepare:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_session.controller prepare --session-id "$(SESSION_ID)" --source-state "$(SESSION_SOURCE_STATE)" --no-other-writers

## Arrive gradually through HTTP; repeat to resume the same checkpoint
session-run:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_session.controller run --session-id "$(SESSION_ID)" --interval "$(SESSION_INTERVAL)" --no-other-writers

## Ask the producer to pause after its current atomic event
session-pause:
	$(POETRY) run python -m scripts.product_session.controller pause --session-id "$(SESSION_ID)"

## After arrivals, explicitly advance compressed event time to delayed feedback
session-feedback:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_session.controller feedback --session-id "$(SESSION_ID)" --no-other-writers

## Read the consistent committed session watermark and counts
session-status:
	$(POETRY) run python -m scripts.product_session.controller status --session-id "$(SESSION_ID)"

## Reconcile the completed session with the existing independent causal verifier
session-verify:
	$(POETRY) run python -m scripts.product_session.controller verify --session-id "$(SESSION_ID)"

## Open the auto-refresh read-only investigation page on loopback
session-open:
	$(POETRY) run python -m scripts.product_session.controller open --session-id "$(SESSION_ID)" --port "$(SESSION_PORT)"

SESSION_START ?= 2018-09-16T00:00:00
SESSION_END ?= 2018-09-17T00:00:00
SESSION_TARGET_DECISIONS ?= 2000
.PHONY: session-prepare-expanded
## Prepare a NEW session with explicit time window and nominal volume; timestamp peers stay together
session-prepare-expanded:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_session.controller prepare --session-id "$(SESSION_ID)" --source-state "$(SESSION_SOURCE_STATE)" --start "$(SESSION_START)" --end "$(SESSION_END)" --target-decisions "$(SESSION_TARGET_DECISIONS)" --no-other-writers


LIVE_SESSION_ID ?= arrivals-day-v1
LIVE_SESSION_START ?= 2018-09-16T00:00:00
LIVE_SESSION_END ?= 2018-09-17T00:00:00
LIVE_BASELINE ?= data/storage/kind-platform/operations/baseline-arrivals-day-v1.json
.PHONY: session-daily-prepare session-live-test
## Prepare the full available daily window in a NEW session; does not start arrivals
session-daily-prepare:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.product_session.controller prepare --session-id "$(LIVE_SESSION_ID)" --source-state "$(SESSION_SOURCE_STATE)" --start "$(LIVE_SESSION_START)" --end "$(LIVE_SESSION_END)" --target-decisions 50000 --no-other-writers

## Run opt-in live daily rehearsal; bind portal/monitor, stop external watcher and save its baseline first
session-live-test:
	@test "$(SESSION_NO_OTHER_WRITERS)" = 1 || { echo 'Set SESSION_NO_OTHER_WRITERS=1'; exit 2; }
	FRAUD_LIVE_SESSION_ID="$(LIVE_SESSION_ID)" FRAUD_LIVE_BASELINE="$(LIVE_BASELINE)" FRAUD_SESSION_NO_OTHER_WRITERS=1 FRAUD_PORTAL_ID="$(PORTAL_ID)" .venv/bin/python -m tox -e live-operations
