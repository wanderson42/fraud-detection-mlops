# Only local access processes; workloads and deployments stay under Kubernetes.
ACCESS_SERVICE ?= api
ACCESS_PORT ?=
ACCESS_LOG_LINES ?= 60
.PHONY: access-start access-stop access-status access-logs access-up access-stop-all

## Start one owned nohup access; ACCESS_SERVICE=api|mlflow|grafana|prometheus|exporter|rustfs|portal
access-start:
	$(POETRY) run python -m scripts.platform.local_services.controller start --service "$(ACCESS_SERVICE)" $(if $(ACCESS_PORT),--port "$(ACCESS_PORT)") --portal-id "$(PORTAL_ID)"

## Stop only the recorded process group of ACCESS_SERVICE; preserve its logs and workloads
access-stop:
	$(POETRY) run python -m scripts.platform.local_services.controller stop --service "$(ACCESS_SERVICE)"

## Inspect process identity and HTTP readiness for all managed local accesses
access-status:
	$(POETRY) run python -m scripts.platform.local_services.controller status

## Read the latest private access log; ACCESS_LOG_LINES defaults to 60
access-logs:
	$(POETRY) run python -m scripts.platform.local_services.controller logs --service "$(ACCESS_SERVICE)" --lines "$(ACCESS_LOG_LINES)"

## Open API, MLflow, Grafana and the prepared portal in the background; no provisioning
access-up:
	$(POETRY) run python -m scripts.platform.local_services.controller up --portal-id "$(PORTAL_ID)"

## Close only accesses recorded by this checkout; never stop Kubernetes or other projects
access-stop-all:
	$(POETRY) run python -m scripts.platform.local_services.controller stop-all
