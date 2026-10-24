# Analyst UI with its own locked environment; data/model components remain authoritative.
PORTAL_ID ?= investigation-v1
PORTAL_PORT ?= 18501
PORTAL_WITHOUT_EXPLANATIONS ?= 0
PORTAL_PERFORMANCE_ID ?= validation-v1
PORTAL_EVALUATION_ROUND ?= portfolio-v1
.PHONY: portal-install portal-prepare portal-open portal-verify portal-smoke portal-ui-test

## Install the isolated Streamlit/Plotly runtime without modifying the science environment
portal-install:
	$(POETRY) run python -m scripts.fraud_portal.controller install

## Prepare all accepted HTTP decisions and verified SHAP; requires source digest and no writers
portal-prepare:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
ifeq ($(PORTAL_WITHOUT_EXPLANATIONS),1)
	$(POETRY) run python -m scripts.fraud_portal.controller prepare --portal-id "$(PORTAL_ID)" --flow-id "$(PRODUCT_FLOW_ID)" --without-explanations --no-other-writers
else
	@test -n "$(PRODUCT_EXPLANATION_SHA256)" || { echo 'Set PRODUCT_EXPLANATION_SHA256'; exit 2; }
	$(POETRY) run python -m scripts.fraud_portal.controller prepare --portal-id "$(PORTAL_ID)" --flow-id "$(PRODUCT_FLOW_ID)" --explanation-id "$(PRODUCT_EXPLANATION_ID)" --explanation-sha256 "$(PRODUCT_EXPLANATION_SHA256)" --no-other-writers
endif

## Open the read-only analyst portal on loopback; Ctrl+C stops only the UI
portal-open:
	$(POETRY) run python -m scripts.fraud_portal.controller open --portal-id "$(PORTAL_ID)" --port "$(PORTAL_PORT)"

## Verify the prepared snapshot offline without MLflow, Kubernetes or model loading
portal-verify:
	$(POETRY) run python -m scripts.fraud_portal.controller verify --portal-id "$(PORTAL_ID)"

## Verify the live Streamlit HTTP health and its pinned snapshot
portal-smoke:
	$(POETRY) run python -m scripts.fraud_portal.controller smoke --portal-id "$(PORTAL_ID)" --port "$(PORTAL_PORT)"

## Test navigation, filters and SHAP screens in the isolated UI test environment
portal-ui-test:
	.venv/bin/python -m tox -e portal-ui

.PHONY: portal-performance-prepare portal-performance-verify
## Recompute only accepted validation evidence, bound to the model in the existing portal snapshot
portal-performance-prepare:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.fraud_portal.controller performance-prepare --portal-id "$(PORTAL_ID)" --performance-id "$(PORTAL_PERFORMANCE_ID)" --evaluation-round "$(PORTAL_EVALUATION_ROUND)" --no-other-writers

## Verify quality evidence offline without loading any model
portal-performance-verify:
	$(POETRY) run python -m scripts.fraud_portal.controller performance-verify --portal-id "$(PORTAL_ID)"

PORTAL_SESSION_ID ?= arrival-2k-v1
PORTAL_PREVIOUS_SESSION_SHA256 ?=
.PHONY: portal-session-link portal-session-verify portal-session-smoke
## Link one accepted causal session to the existing single portal (no scoring or training)
portal-session-link:
	$(POETRY) run python -m scripts.fraud_portal.controller session-link --portal-id "$(PORTAL_ID)" --session-id "$(PORTAL_SESSION_ID)" $(if $(PORTAL_PREVIOUS_SESSION_SHA256),--previous-session-sha256 "$(PORTAL_PREVIOUS_SESSION_SHA256)",)
## Verify current linked session read-only, with model and release identity
portal-session-verify:
	$(POETRY) run python -m scripts.fraud_portal.controller session-verify --portal-id "$(PORTAL_ID)"
## Check Streamlit HTTP health plus bound session; browser rendering is a separate gate
portal-session-smoke:
	$(POETRY) run python -m scripts.fraud_portal.controller session-smoke --portal-id "$(PORTAL_ID)" --port "$(PORTAL_PORT)"
