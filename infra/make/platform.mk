# Thin entrypoints only. Ownership, locking, manifests and receipts stay in Python.
# Serialize explicit multiple goals even when the caller supplies -j.
.NOTPARALLEL:
POETRY ?= poetry
PLATFORM_GRAFANA_PORT ?= 13001
export PLATFORM_BASELINE PLATFORM_TRACKING_REPORT PLATFORM_NO_OTHER_WRITERS
export PLATFORM_DELIVERY PLATFORM_SOURCE_SHA PLATFORM_DESCRIPTOR_SHA256 PLATFORM_STATE
export PLATFORM_GRAFANA_PORT

.PHONY: platform-status platform-host-check platform-templates platform-foundation platform-tracking
.PHONY: platform-datasets platform-data-jobs platform-replay-test platform-serving-prepare
.PHONY: platform-serving-up platform-rollout-test platform-monitoring-prepare platform-monitoring-up
.PHONY: platform-grafana platform-mlflow platform-api platform-baseline platform-stop platform-start
.PHONY: platform-verify platform-reboot-verify platform-operations-test

## Inspect only the owned node/resources, including a stopped node; not an HTTP health gate
platform-status:
	$(POETRY) run python -m scripts.platform.kind_operations status

## Check host inotify without changing sysctl
platform-host-check:
	$(POETRY) run python -m scripts.platform.kind_operations host-check

## Lint/render existing Helm charts; no live installation
platform-templates:
	$(POETRY) run python -m scripts.platform.kind_foundation validate
	$(POETRY) run python -m scripts.platform.kind_tracking validate
	$(POETRY) run python -m scripts.platform.kind_pipeline validate
	$(POETRY) run python -m scripts.platform.kind_replay validate
	$(POETRY) run python -m scripts.platform.kind_serving validate
	$(POETRY) run python -m scripts.platform.kind_observability validate

## Initial foundation provisioning: initialize/create the dedicated cluster and install Helm foundation
platform-foundation:
	$(POETRY) run python -m scripts.platform.kind_foundation init
	$(POETRY) run python -m scripts.platform.kind_operations host-check
	$(POETRY) run python -m scripts.platform.kind_foundation validate
	$(POETRY) run python -m scripts.platform.kind_foundation create
	$(POETRY) run python -m scripts.platform.kind_foundation up

## Install tracking on the existing owned foundation; build/load or verify its recorded image
platform-tracking:
	$(POETRY) run python -m scripts.platform.kind_tracking validate
	$(POETRY) run python -m scripts.platform.kind_tracking init
	$(POETRY) run python -m scripts.platform.kind_tracking build
	$(POETRY) run python -m scripts.platform.kind_tracking up

## Publish/reuse/restore prepared dataset bundles; repeats transfers, not feature computation
platform-datasets:
	$(POETRY) run python -m scripts.platform.kind_datasets build
	$(POETRY) run python -m scripts.platform.kind_datasets rehearse

## Run new Silver/Gold Jobs from prepared Bronze in S3; recomputes features, no model fit
platform-data-jobs:
	$(POETRY) run python -m scripts.platform.kind_pipeline validate
	$(POETRY) run python -m scripts.platform.kind_pipeline build
	$(POETRY) run python -m scripts.platform.kind_pipeline rehearse

## New synthetic replay recovery rehearsal; fits its synthetic fixture and retains a new PVC
platform-replay-test:
	$(POETRY) run python -m scripts.platform.kind_replay validate
	$(POETRY) run python -m scripts.platform.kind_replay build
	$(POETRY) run python -m scripts.platform.kind_replay rehearse

## Bind a qualified delivery; requires PLATFORM_DELIVERY, PLATFORM_SOURCE_SHA, PLATFORM_DESCRIPTOR_SHA256
platform-serving-prepare:
	@: "$${PLATFORM_DELIVERY:?Set PLATFORM_DELIVERY}" "$${PLATFORM_SOURCE_SHA:?Set PLATFORM_SOURCE_SHA}" "$${PLATFORM_DESCRIPTOR_SHA256:?Set PLATFORM_DESCRIPTOR_SHA256}"
	$(POETRY) run python -m scripts.platform.kind_serving prepare --directory "$${PLATFORM_DELIVERY}" --expected-source-sha "$${PLATFORM_SOURCE_SHA}" --expected-descriptor-sha256 "$${PLATFORM_DESCRIPTOR_SHA256}"

## Install or reconcile serving from its existing verified preparation
platform-serving-up:
	$(POETRY) run python -m scripts.platform.kind_serving up

## Interrupt the synthetic candidate and exercise blue/green plus native Helm rollback
platform-rollout-test:
	$(POETRY) run python -m scripts.platform.kind_serving rehearse

## Prepare a completed replay copy; requires PLATFORM_STATE and PLATFORM_NO_OTHER_WRITERS=1
platform-monitoring-prepare:
	@: "$${PLATFORM_STATE:?Set PLATFORM_STATE}"
	@test "$${PLATFORM_NO_OTHER_WRITERS}" = 1 || { echo 'Set PLATFORM_NO_OTHER_WRITERS=1 after stopping writers'; exit 2; }
	$(POETRY) run python -m scripts.platform.kind_observability init
	$(POETRY) run python -m scripts.platform.kind_observability prepare --state "$${PLATFORM_STATE}" --no-other-writers

## Install monitoring from the prepared completed-replay snapshot; no replay ingestion
platform-monitoring-up:
	$(POETRY) run python -m scripts.platform.kind_observability build
	$(POETRY) run python -m scripts.platform.kind_observability up

## Open Grafana on loopback in this terminal; Ctrl+C closes only this forward
platform-grafana:
	$(POETRY) run python -m scripts.platform.kind_observability open --component grafana --port "$${PLATFORM_GRAFANA_PORT}"

## Open existing KinD MLflow on an ephemeral loopback URL; Ctrl+C closes this forward
platform-mlflow:
	$(POETRY) run python -m scripts.platform.local_access mlflow

## Open/check the existing synthetic API through its gateway; Ctrl+C closes this forward
platform-api:
	$(POETRY) run python -m scripts.platform.local_access api

## Check functional readbacks and save a new baseline; requires PLATFORM_BASELINE and PLATFORM_TRACKING_REPORT
platform-baseline:
	@: "$${PLATFORM_BASELINE:?Set PLATFORM_BASELINE}" "$${PLATFORM_TRACKING_REPORT:?Set PLATFORM_TRACKING_REPORT}"
	$(POETRY) run python -m scripts.platform.kind_operations prepare --baseline "$${PLATFORM_BASELINE}" --tracking-report "$${PLATFORM_TRACKING_REPORT}"

## Stop workloads then the owned node; requires saved PLATFORM_BASELINE and PLATFORM_NO_OTHER_WRITERS=1
platform-stop:
	@: "$${PLATFORM_BASELINE:?Set PLATFORM_BASELINE}"
	@test "$${PLATFORM_NO_OTHER_WRITERS}" = 1 || { echo 'Set PLATFORM_NO_OTHER_WRITERS=1 after stopping writers'; exit 2; }
	$(POETRY) run python -m scripts.platform.kind_operations stop --baseline "$${PLATFORM_BASELINE}" --no-other-writers

## Start the same node and restore saved replicas; requires PLATFORM_BASELINE and PLATFORM_NO_OTHER_WRITERS=1
platform-start:
	@: "$${PLATFORM_BASELINE:?Set PLATFORM_BASELINE}"
	@test "$${PLATFORM_NO_OTHER_WRITERS}" = 1 || { echo 'Set PLATFORM_NO_OTHER_WRITERS=1 after stopping writers'; exit 2; }
	$(POETRY) run python -m scripts.platform.kind_operations start --baseline "$${PLATFORM_BASELINE}" --no-other-writers

## Verify the recorded stop/start against PLATFORM_BASELINE; no new interruption
platform-verify:
	@: "$${PLATFORM_BASELINE:?Set PLATFORM_BASELINE}"
	$(POETRY) run python -m scripts.platform.kind_operations verify --baseline "$${PLATFORM_BASELINE}"

## Verify a real planned host reboot against PLATFORM_BASELINE; never reboots the machine
platform-reboot-verify:
	@: "$${PLATFORM_BASELINE:?Set PLATFORM_BASELINE}"
	$(POETRY) run python -m scripts.platform.kind_operations verify --baseline "$${PLATFORM_BASELINE}" --expect-reboot

## New disruptive stop/start rehearsal; requires PLATFORM_TRACKING_REPORT and PLATFORM_NO_OTHER_WRITERS=1
platform-operations-test:
	@: "$${PLATFORM_TRACKING_REPORT:?Set PLATFORM_TRACKING_REPORT}"
	@test "$${PLATFORM_NO_OTHER_WRITERS}" = 1 || { echo 'Set PLATFORM_NO_OTHER_WRITERS=1 after stopping writers'; exit 2; }
	$(POETRY) run python -m scripts.platform.kind_operations rehearse --tracking-report "$${PLATFORM_TRACKING_REPORT}" --no-other-writers
