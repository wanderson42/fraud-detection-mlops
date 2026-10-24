# Explicit binding; monitoring never starts a producer or requests a new score.
SESSION_MONITOR_ID ?= arrival-2k-v1
SESSION_MONITOR_INTERVAL ?= 5
SESSION_MONITOR_PREVIOUS_SHA256 ?=
.PHONY: session-monitor-build session-monitor-prepare session-monitor-up session-monitor-watch session-monitor-rehearse
## Build and load the stdlib aggregate receiver into the owned KinD node
session-monitor-build:
	$(POETRY) run python -m scripts.session_monitoring.controller build
## Bind monitoring to one existing session receipt, model and serving release
session-monitor-prepare:
	$(POETRY) run python -m scripts.session_monitoring.controller prepare --session-id "$(SESSION_MONITOR_ID)" $(if $(SESSION_MONITOR_PREVIOUS_SHA256),--previous-session-sha256 "$(SESSION_MONITOR_PREVIOUS_SHA256)",)
## Extend the existing observability Helm release; retain historical replay and PVCs
session-monitor-up:
	$(POETRY) run python -m scripts.session_monitoring.controller up
## Observe the bound checkpoint read-only; Ctrl+C ends only the observer
session-monitor-watch:
	$(POETRY) run python -m scripts.session_monitoring.controller watch --session-id "$(SESSION_MONITOR_ID)" --interval "$(SESSION_MONITOR_INTERVAL)"
## Verify completed session -> Prometheus -> Grafana, collection expiry and recovery
session-monitor-rehearse:
	$(POETRY) run python -m scripts.session_monitoring.controller rehearse --session-id "$(SESSION_MONITOR_ID)"
